"""
策略模块：动量计算与周度信号生成。

核心思想（相对动量轮动）：
  在每周最后一个交易日收盘后，计算 ETF 池中每只 ETF 过去 N 个交易日的涨幅，
  选出涨幅最大的 1 只，下周全仓持有。

为什么用 20 日动量？
  20 个交易日 ≈ 1 个自然月，既能捕捉中期趋势，又不会太滞后。
"""

import pandas as pd

from src.config import MOMENTUM_WINDOW


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
    # 向量化计算：一次 pct_change 算出所有 ETF 所有日期的动量
    momentum = prices.pct_change(periods=window)
    return momentum


def _get_weekly_last_dates(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """
    从日线日期序列中，提取每周最后一个交易日。

    逻辑：
      按「年-周」分组，取每组最后一个日期。
      这样即使某周周五休市，也能正确取到当周最后交易日（如周四）。
    """
    # pandas 3.0 DatetimeIndex.groupby 已废弃，改用 DataFrame groupby
    iso = dates.isocalendar()
    df = pd.DataFrame({"date": dates, "year": iso["year"], "week": iso["week"]})
    weekly_last = df.groupby(["year", "week"])["date"].max()
    return pd.DatetimeIndex(weekly_last.values)


def generate_weekly_signals(
    prices: pd.DataFrame,
    window: int = MOMENTUM_WINDOW,
) -> pd.DataFrame:
    """
    生成周度调仓信号。

    Args:
        prices: 收盘价宽表
        window: 动量窗口

    Returns:
        signals: DataFrame，包含三列：
          - date:       信号生成日期（周五）
          - code:       被选中的 ETF 代码
          - name:       ETF 名称（便于阅读）
          - momentum:   该 ETF 当时的动量值

    信号逻辑：
      每周五收盘后 → 看各 ETF 过去 20 日涨幅 → 选涨幅最大的 → 下周一买入
    """
    from src.config import ETF_POOL

    # Step 1: 计算所有 ETF 的动量
    momentum_df = calc_momentum(prices, window)

    # Step 2: 找出每周最后一个交易日
    weekly_dates = _get_weekly_last_dates(prices.index)

    # Step 3: 只取每周最后一天的动量行
    # reindex 后 ffill 是因为 momentum 列的日期可能略少于 prices（窗口期）
    weekly_momentum = momentum_df.reindex(weekly_dates, method="ffill")

    # Step 4: 对每一行（每周），用 idxmax 找出动量最强的 ETF 代码
    # idxmax(axis=1) 沿列方向找最大值，返回列名（即 ETF 代码）
    signals: list[dict] = []
    for date in weekly_momentum.index:
        row = weekly_momentum.loc[date]
        # 跳过全为 NaN 的行（窗口期尚未满足）
        if row.isna().all():
            continue
        best_code = row.idxmax()
        best_momentum = row[best_code]

        # 只选正动量的 ETF，若全部下跌则空仓
        if best_momentum <= 0:
            continue

        signals.append({
            "date": date,
            "code": best_code,
            "name": ETF_POOL.get(best_code, ""),
            "momentum": round(float(best_momentum), 4),
        })

    signal_df = pd.DataFrame(signals)
    if signal_df.empty:
        return signal_df

    return signal_df.sort_values("date").reset_index(drop=True)
