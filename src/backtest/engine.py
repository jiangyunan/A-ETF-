"""
回测引擎：根据信号表模拟每周调仓，计算策略净值和绩效指标。

核心思路（向量化回测）：
  不需要逐日循环，而是利用 pandas 的向量化操作：
  1. 计算所有 ETF 的每日收益率矩阵
  2. 根据信号表，将策略每天「实际持有」的 ETF 收益率取出
  3. 累积乘积 → 策略净值曲线

为什么用向量化？
  逐日 for 循环在 3 年数据（约 750 个交易日）上也能跑，但向量化更简洁高效。
"""

import numpy as np
import pandas as pd


def _get_weekly_dates(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """从日线日期中提取每周最后一个交易日（与 strategy 模块保持一致）。"""
    iso = dates.isocalendar()
    df = pd.DataFrame({"date": dates, "year": iso["year"], "week": iso["week"]})
    weekly_last = df.groupby(["year", "week"])["date"].max()
    return pd.DatetimeIndex(weekly_last.values)


def _assign_daily_holdings(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
) -> pd.DataFrame:
    """
    将周度信号展开为每日持仓矩阵。

    Args:
        signals: 周度信号表（date, code, name, momentum）
        prices: 收盘价宽表

    Returns:
        holdings: DataFrame，行=日期，列=ETF代码，值=0或1

    逻辑：
      以每周最后一个交易日为界，逐周分配持仓。
      有信号的周 → 持有对应 ETF；无信号的周（全部下跌跳过）→ 空仓。
      每次持仓从「本周最后一个交易日+1」（即下周第一个交易日）开始，
      持续到「下周最后一个交易日」为止。
    """
    all_dates = prices.index
    holdings = pd.DataFrame(0, index=all_dates, columns=prices.columns)

    if signals.empty:
        return holdings

    # 日期 → ETF 代码 的快速查找表
    signal_map: dict[pd.Timestamp, str] = dict(zip(signals["date"], signals["code"]))

    # 获取每周最后一个交易日（与信号生成的周划分一致）
    weekly_dates = _get_weekly_dates(all_dates)

    for i in range(len(weekly_dates)):
        week_end = weekly_dates[i]

        # 持有期从本周最后一个交易日之后开始
        start_pos = all_dates.get_loc(week_end) + 1
        if start_pos >= len(all_dates):
            continue

        # 持有期到下周最后一个交易日为止
        if i + 1 < len(weekly_dates):
            end_pos = all_dates.get_loc(weekly_dates[i + 1])
        else:
            end_pos = len(all_dates)

        # 本周有信号才持仓，无信号则自动空仓（holdings 默认为 0）
        if week_end in signal_map:
            code = signal_map[week_end]
            col_idx = holdings.columns.get_loc(code)
            holdings.iloc[start_pos:end_pos, col_idx] = 1

    return holdings


def run_backtest(
    prices: pd.DataFrame,
    signals: pd.DataFrame,
    benchmark_prices: pd.Series,
) -> dict:
    """
    执行回测，返回净值序列、交易记录和绩效指标。

    Args:
        prices: 收盘价宽表
        signals: 周度信号表
        benchmark_prices: 基准 ETF 收盘价序列

    Returns:
        字典包含：
          - nav:            策略净值 Series
          - benchmark_nav:  基准净值 Series
          - daily_returns:  策略日收益率 Series
          - signals:        信号表（含增补信息）
          - metrics:        绩效指标 dict
    """
    # ---- 计算每日收益率矩阵 ----
    # 每只 ETF 的日收益率 = (今收 - 昨收) / 昨收
    daily_returns = prices.pct_change()

    # ---- 将信号转为每日持仓 ----
    holdings = _assign_daily_holdings(signals, prices)

    # ---- 策略每日收益率 = 当天持有 ETF 的收益率 ----
    # holdings * daily_returns：持有 ETF 的收益为实际值，其余为 0
    # sum(axis=1)：对每行求和，因为每行只有一个 1，等价于取出持有 ETF 的收益率
    strategy_daily_returns = (holdings * daily_returns).sum(axis=1)

    # 空仓期（没有信号的日子）收益率为 0
    strategy_daily_returns = strategy_daily_returns.fillna(0)

    # ---- 计算净值曲线 ----
    # 净值 = (1 + 日收益率) 的累积乘积，初始净值为 1.0
    strategy_nav = (1 + strategy_daily_returns).cumprod()
    strategy_nav.name = "strategy"

    # 基准净值同理：买入持有沪深300
    benchmark_returns = benchmark_prices.pct_change().fillna(0)
    benchmark_nav = (1 + benchmark_returns).cumprod()
    benchmark_nav.name = "benchmark"

    # ---- 计算绩效指标 ----
    metrics = _calc_metrics(strategy_nav, strategy_daily_returns, benchmark_nav, len(signals))

    return {
        "nav": strategy_nav,
        "benchmark_nav": benchmark_nav,
        "daily_returns": strategy_daily_returns,
        "signals": signals,
        "metrics": metrics,
    }


def _calc_metrics(
    nav: pd.Series,
    daily_returns: pd.Series,
    benchmark_nav: pd.Series,
    num_signals: int,
) -> dict:
    """
    计算策略绩效指标。

    指标说明：
      - 年化收益率：将总收益率折算为年化，公式 (最终净值)^(1/年数) - 1
      - 最大回撤：净值从最高点到之后最低点的最大跌幅百分比
      - 夏普比率：(年化收益 - 无风险利率) / 年化波动率，衡量风险调整后收益
      - 胜率：策略周收益跑赢基准的周数比例
    """
    # 年化收益率
    total_days = (nav.index[-1] - nav.index[0]).days
    years = total_days / 365.25
    total_return = nav.iloc[-1] / nav.iloc[0] - 1
    annual_return = (1 + total_return) ** (1 / years) - 1

    # 基准年化收益
    bm_total = benchmark_nav.iloc[-1] / benchmark_nav.iloc[0] - 1
    bm_annual = (1 + bm_total) ** (1 / years) - 1

    # 最大回撤
    rolling_max = nav.cummax()
    drawdown = (nav - rolling_max) / rolling_max
    max_drawdown = drawdown.min()

    # 夏普比率（无风险利率设为 2%）
    risk_free = 0.02
    excess_returns = daily_returns - risk_free / 252  # 252 个交易日
    sharpe = np.sqrt(252) * excess_returns.mean() / excess_returns.std() if excess_returns.std() > 0 else 0

    # 胜率（周度）
    strategy_weekly = nav.resample("W").last().pct_change().dropna()
    benchmark_weekly = benchmark_nav.resample("W").last().pct_change().dropna()
    common_weeks = strategy_weekly.index.intersection(benchmark_weekly.index)
    win_count = (strategy_weekly.loc[common_weeks] > benchmark_weekly.loc[common_weeks]).sum()
    total_weeks = len(common_weeks)
    win_rate = win_count / total_weeks if total_weeks > 0 else 0

    return {
        "累计收益率": f"{total_return:.2%}",
        "年化收益率": f"{annual_return:.2%}",
        "基准年化收益率": f"{bm_annual:.2%}",
        "最大回撤": f"{max_drawdown:.2%}",
        "夏普比率": f"{sharpe:.2f}",
        "胜率(vs基准)": f"{win_rate:.2%}（{win_count}/{total_weeks}周）",
        "回测年数": f"{years:.1f}",
        "交易次数": str(num_signals),
    }
