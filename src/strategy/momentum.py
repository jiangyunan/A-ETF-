"""
策略模块：动量计算与周度信号生成。

核心思想（相对动量轮动）：
  在每周最后一个交易日收盘后，计算 ETF 池中每只 ETF 过去 N 个交易日的涨幅，
  选出涨幅最大的若干只，下周等权持有。

V2 新增优化：
  - 风险调整动量：动量 / 波动率，过滤高波动的虚假信号
  - 多仓位分散：前 N 名等权持有，降低单只集中风险
  - 趋势过滤：收盘价 > 均线才纳入候选，熊市自动空仓
"""

import numpy as np
import pandas as pd

from src.config import ETF_POOL, MOMENTUM_WINDOW


def calc_momentum(
    prices: pd.DataFrame,
    window: int = MOMENTUM_WINDOW,
) -> pd.DataFrame:
    """
    计算每只 ETF 每日的 N 日动量（过去 N 个交易日的累计涨跌幅）。

    Args:
        prices: 收盘价宽表，行索引=日期，列=ETF代码
        window: 回看窗口（交易日数）

    Returns:
        momentum: 同形状的 DataFrame，值为过去 window 日的涨跌幅
                  例如 0.05 表示过去 20 日涨了 5%

    实现原理：
        pandas 的 pct_change(window) 计算 (当日价格 - window日前价格) / window日前价格
        等价于过去 window 日的累计收益率。
    """
    momentum = prices.pct_change(periods=window)
    return momentum


def calc_risk_adjusted_momentum(
    prices: pd.DataFrame,
    window: int = MOMENTUM_WINDOW,
) -> pd.DataFrame:
    """
    计算风险调整后的动量 = N日涨跌幅 / N日日收益率的标准差。

    原理：
      两只 ETF 都涨了 5%，但一只每天稳稳涨 0.25%，另一只暴涨暴跌。
      风险调整后，前者的得分更高，因为它「趋势更平滑、更可信」。

    Args:
        prices: 收盘价宽表，行索引=日期，列=ETF代码
        window: 回看窗口

    Returns:
        ra_momentum: 风险调整后的动量值，形状与 prices 相同
    """
    raw_momentum = prices.pct_change(periods=window)

    # 计算过去 window 日的日收益率标准差（年化不是必须的，但统一量化更直观）
    daily_returns = prices.pct_change()
    rolling_std = daily_returns.rolling(window=window).std()

    # 风险调整动量 = 原始动量 / 波动率
    # 波动率为 0 或 NaN 的（新上市 ETF），直接给 0 避免除零
    ra_momentum = raw_momentum / rolling_std.replace(0, np.nan)
    return ra_momentum


def _apply_trend_filter(
    momentum_df: pd.DataFrame,
    prices: pd.DataFrame,
    trend_window: int,
) -> pd.DataFrame:
    """
    将不满足绝对动量条件的 ETF 动量值置为 NaN。

    绝对动量规则：
      收盘价 > 过去 trend_window 日均线 → 符合趋势，保留
      收盘价 <= 过去 trend_window 日均线 → 不符合趋势，排除

    Args:
        momentum_df: 动量值 DataFrame
        prices: 收盘价宽表
        trend_window: 均线窗口

    Returns:
        过滤后的动量 DataFrame（不符合条件的 ETF 动量变为 NaN）
    """
    ma = prices.rolling(window=trend_window).mean()
    above_ma = prices > ma

    # 趋势以下的位置 NaN，后续选股时会自动跳过
    filtered = momentum_df.where(above_ma)
    return filtered


def _get_weekly_last_dates(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """
    从日线日期序列中，提取每周最后一个交易日。

    逻辑：
      按「年-周」分组，取每组最后一个日期。
      这样即使某周周五休市，也能正确取到当周最后交易日（如周四）。
    """
    iso = dates.isocalendar()
    df = pd.DataFrame({"date": dates, "year": iso["year"], "week": iso["week"]})
    weekly_last = df.groupby(["year", "week"])["date"].max()
    return pd.DatetimeIndex(weekly_last.values)


def generate_weekly_signals(
    prices: pd.DataFrame,
    window: int = MOMENTUM_WINDOW,
    top_n: int = 1,
    use_risk_adjusted: bool = False,
    use_trend_filter: bool = False,
    trend_window: int = 60,
    market_ma_window: int = 0,
) -> pd.DataFrame:
    """
    生成周度调仓信号（支持 V2 所有优化参数）。

    Args:
        prices: 收盘价宽表
        window: 动量窗口（交易日数）
        top_n: 每周持有的 ETF 数量（1 = 原策略）
        use_risk_adjusted: 是否用风险调整动量
        use_trend_filter: 是否启用趋势过滤
        trend_window: 趋势过滤的均线窗口
        market_ma_window: 大盘择时窗口（0=关闭）。
            用所有 ETF 等权均价作为大盘代理，仅在均价 > MA 时生成信号

    Returns:
        signals: DataFrame，包含五列：
          - date:       信号生成日期
          - code:       被选中的 ETF 代码
          - name:       ETF 名称
          - momentum:   该 ETF 当时的动量值
          - weight:     该 ETF 在组合中的权重（等权 = 1/top_n）

    信号逻辑：
      每周最后一个交易日收盘后 → 计算动量 → 过滤趋势 → 大盘择时 → 选前 N 名 → 等权持有
    """
    if prices.empty:
        return pd.DataFrame(columns=["date", "code", "name", "momentum", "weight"])

    # Step 1: 计算动量（风险调整 或 原始）
    if use_risk_adjusted:
        momentum_df = calc_risk_adjusted_momentum(prices, window)
    else:
        momentum_df = calc_momentum(prices, window)

    # Step 2: 趋势过滤（可选）
    if use_trend_filter:
        momentum_df = _apply_trend_filter(momentum_df, prices, trend_window)

    # Step 3: 找出每周最后一个交易日
    weekly_dates = _get_weekly_last_dates(prices.index)
    weekly_momentum = momentum_df.loc[weekly_dates]
    weekly_momentum = weekly_momentum.dropna(how="all")

    if weekly_momentum.empty:
        return pd.DataFrame(columns=["date", "code", "name", "momentum", "weight"])

    # Step 3.5: 大盘择时（可选）
    # 用 ETF 池等权均价作为市场代理，仅在均价高于 MA 的周才保留信号
    market_ok_dates: set | None = None
    if market_ma_window > 0:
        market_price = prices.mean(axis=1)  # 所有 ETF 的等权均价
        market_ma = market_price.rolling(market_ma_window).mean()
        market_ok_dates = set(
            weekly_dates[market_price.loc[weekly_dates] > market_ma.loc[weekly_dates]]
        )

    # Step 4: 每周选前 top_n 名（向量化）
    signals: list[dict] = []
    weight_per = 1.0 / top_n  # 等权分配

    for date in weekly_momentum.index:
        # 大盘择时：市场在 MA 之下时跳过本周
        if market_ok_dates is not None and date not in market_ok_dates:
            continue

        row = weekly_momentum.loc[date].dropna()

        # 动量 <= 0 的排除（规避下跌趋势）
        row = row[row > 0]
        if row.empty:
            continue

        # 取前 top_n 名（如果候选不足 top_n，有多少取多少）
        n_pick = min(top_n, len(row))
        top = row.nlargest(n_pick)

        for code, mom in top.items():
            signals.append({
                "date": date,
                "code": code,
                "name": ETF_POOL.get(code, ""),
                "momentum": round(float(mom), 4),
                "weight": round(weight_per, 4),
            })

    signal_df = pd.DataFrame(signals)
    if signal_df.empty:
        return signal_df

    return signal_df.sort_values(["date", "code"]).reset_index(drop=True)
