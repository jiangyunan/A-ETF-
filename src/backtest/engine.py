"""
回测引擎：根据信号表模拟每周调仓，计算策略净值和绩效指标。

核心思路（向量化回测）：
  不需要逐日循环，而是利用 pandas 的向量化操作：
  1. 计算所有 ETF 的每日收益率矩阵
  2. 根据信号表，将策略每天「实际持有」的 ETF 收益率按权重取出
  3. 累积乘积 → 策略净值曲线

V2 支持：多仓位等权/加权组合（持仓矩阵的每行可有多个非零权重）
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
    将信号展开为每日持仓矩阵（实盘规则）。

    执行规则:
      - 普通周: 周一收盘生成信号 → 周二执行（signal_date + 1）
      - 周一休市: 周二信号 → 周三执行
      - 长假后: 第一天信号 → 第二天执行（holiday_delay=True → +2交易日）
    """
    all_dates = prices.index
    holdings = pd.DataFrame(0.0, index=all_dates, columns=prices.columns)

    if signals.empty:
        return holdings

    # 日期 → [(code, weight), ...]
    signal_groups: dict[pd.Timestamp, list[tuple[str, float]]] = {}
    holiday_delay_set: set = set()
    for _, row in signals.iterrows():
        dt = row["date"]
        if dt not in signal_groups:
            signal_groups[dt] = []
        w = float(row.get("weight", 1.0))
        signal_groups[dt].append((row["code"], w))
        if row.get("holiday_delay", False):
            holiday_delay_set.add(dt)

    signal_dates = sorted(signal_groups.keys())

    for i, sig_date in enumerate(signal_dates):
        if sig_date not in all_dates:
            continue

        # 执行日 = 信号日 + 1（正常）或 + 2（长假后）
        sig_pos = all_dates.get_loc(sig_date)
        delay = 2 if sig_date in holiday_delay_set else 1
        start_pos = sig_pos + delay
        if start_pos >= len(all_dates):
            continue

        # 持有期: 到下一个信号日（或到数据末尾）
        if i + 1 < len(signal_dates):
            next_date = signal_dates[i + 1]
            if next_date in all_dates:
                end_pos = all_dates.get_loc(next_date) + (2 if next_date in holiday_delay_set else 1)
            else:
                end_pos = len(all_dates)
        else:
            end_pos = len(all_dates)

        for code, weight in signal_groups[sig_date]:
            if code in holdings.columns:
                col_idx = holdings.columns.get_loc(code)
                holdings.iloc[start_pos:end_pos, col_idx] = weight

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
