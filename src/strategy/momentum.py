"""
策略模块：动量计算 + 风险/防御切换 + 波动率仓位控制。

V3 新增：
  1. 大盘择时 v2：沪深300 > 120MA → 风险资产；否则 → 防御资产（国债/黄金）
  2. 风险调整动量（动量 / 波动率）
  3. 双周/月频调仓（减少噪音和交易成本）
  4. 防御资产优先级（国债 > 黄金 > 现金）
  5. 波动率仓位控制（高波动降仓，低波动加仓）
"""

import numpy as np
import pandas as pd

from src.config import (
    ETF_POOL,
    BENCHMARK_CODE,
    DEFENSE_ETF_CODES,
    MOMENTUM_WINDOW,
    VOL_LOOKBACK,
)


def calc_momentum(prices: pd.DataFrame, window: int = MOMENTUM_WINDOW) -> pd.DataFrame:
    """原始动量 = N日累计涨跌幅"""
    return prices.pct_change(periods=window)


def calc_risk_adjusted_momentum(prices: pd.DataFrame, window: int = MOMENTUM_WINDOW) -> pd.DataFrame:
    """风险调整动量 = N日涨跌幅 / N日收益率标准差"""
    raw = prices.pct_change(periods=window)
    daily = prices.pct_change()
    rolling_std = daily.rolling(window=window).std()
    return raw / rolling_std.replace(0, np.nan)


def _apply_trend_filter(momentum_df: pd.DataFrame, prices: pd.DataFrame, trend_window: int) -> pd.DataFrame:
    """单ETF绝对动量：收盘 > MA 才保留"""
    ma = prices.rolling(window=trend_window).mean()
    return momentum_df.where(prices > ma)


def _get_rebalance_dates(dates: pd.DatetimeIndex, freq: int) -> pd.DatetimeIndex:
    """
    按指定频率提取调仓日期。
    freq=1: 每周最后一个交易日
    freq=2: 双周最后一个交易日（每隔一周）
    freq=4: 每月最后一个交易日
    """
    iso = dates.isocalendar()
    df = pd.DataFrame({"date": dates, "year": iso["year"], "week": iso["week"]})

    if freq == 1:
        weekly = df.groupby(["year", "week"])["date"].max()
        return pd.DatetimeIndex(weekly.values)

    # 双周或更长：先取每周，再跳步
    weekly = df.groupby(["year", "week"])["date"].max()
    weekly_vals = weekly.values

    if freq == 2:
        # 每隔一周取
        selected = [weekly_vals[i] for i in range(0, len(weekly_vals), 2)]
        return pd.DatetimeIndex(selected)
    elif freq == 4:
        # 每月 = 约4周
        selected = [weekly_vals[i] for i in range(0, len(weekly_vals), 4)]
        return pd.DatetimeIndex(selected)
    else:
        return pd.DatetimeIndex(weekly_vals)


def _calc_vol_scaled_weights(
    prices: pd.DataFrame,
    selected_codes: list[str],
    base_weight: float,
    vol_target: float,
    vol_lookback: int,
    vol_cap: float,
    date: pd.Timestamp,
) -> dict[str, float]:
    """
    波动率目标仓位：高波降仓，低波加仓。

    公式：实际权重 = 基础权重 × min(目标波动率 / 实际波动率, cap)
    例如：目标15%波，ETF实际30%波 → 仓位减半
          目标15%波，ETF实际10%波 → 仓位 1.5x（受 cap 限制）

    Args:
        prices: 收盘价宽表（需要包含足够长的历史）
        selected_codes: 选中的 ETF 代码列表
        base_weight: 每只 ETF 的基础等权权重
        vol_target: 目标年化波动率
        vol_lookback: 波动率计算窗口
        vol_cap: 最大仓位倍率
        date: 调仓日期

    Returns:
        {code: scaled_weight} 字典
    """
    weights: dict[str, float] = {}
    # 确保有足够历史数据计算波动率
    date_pos = prices.index.get_loc(date)
    lookback_start = max(0, date_pos - vol_lookback)

    for code in selected_codes:
        if code not in prices.columns:
            weights[code] = base_weight
            continue

        hist = prices[code].iloc[lookback_start:date_pos + 1]
        if len(hist) < vol_lookback // 2:
            weights[code] = base_weight
            continue

        daily_ret = hist.pct_change().dropna()
        if len(daily_ret) < 5 or daily_ret.std() == 0:
            weights[code] = base_weight
            continue

        # 年化实际波动率
        realized_vol = daily_ret.std() * np.sqrt(252)

        # 仓位倍率
        scale = min(vol_target / realized_vol, vol_cap)
        weights[code] = round(base_weight * scale, 4)

    return weights


def generate_signals(
    prices: pd.DataFrame,
    window: int = MOMENTUM_WINDOW,
    top_n: int = 3,
    use_risk_adjusted: bool = True,
    use_trend_filter: bool = False,
    trend_window: int = 60,
    market_ma_window: int = 120,
    rebalance_freq: int = 2,
    use_vol_target: bool = True,
    vol_target: float = 0.15,
    vol_lookback: int = VOL_LOOKBACK,
    vol_cap: float = 1.5,
) -> pd.DataFrame:
    """
    生成调仓信号（V3 全部优化参数）。

    流程：
      1. 计算动量（风险调整或原始）
      2. 单ETF趋势过滤（可选）
      3. 大盘择时：沪深300 > MA → 风险资产池；否则 → 防御资产池
      4. 在选定的资产池中按动量选 top_n
      5. 波动率仓位缩放
      6. 按调仓频率输出

    Args:
        prices: 收盘价宽表
        window: 动量窗口
        top_n: 持仓数
        use_risk_adjusted: 风险调整动量
        use_trend_filter: 单ETF趋势过滤
        trend_window: 趋势过滤MA窗口
        market_ma_window: 大盘择时MA窗口（基于沪深300，0=关闭）
        rebalance_freq: 调仓频率（1=周,2=双周,4=月）
        use_vol_target: 波动率仓位控制
        vol_target: 目标年化波动率
        vol_lookback: 波动率回看窗口
        vol_cap: 最大仓位倍率

    Returns:
        signals: DataFrame [date, code, name, momentum, weight, is_defense]
    """
    if prices.empty:
        return pd.DataFrame(columns=["date", "code", "name", "momentum", "weight", "is_defense"])

    # ---- Step 1: 动量 ----
    if use_risk_adjusted:
        momentum_df = calc_risk_adjusted_momentum(prices, window)
    else:
        momentum_df = calc_momentum(prices, window)

    # ---- Step 2: 单ETF趋势过滤 ----
    if use_trend_filter:
        momentum_df = _apply_trend_filter(momentum_df, prices, trend_window)

    # ---- Step 3: 大盘择时（风险/防御切换） ----
    # 用沪深300作为市场代理
    is_risk_on: pd.Series | None = None
    if market_ma_window > 0 and BENCHMARK_CODE in prices.columns:
        bm = prices[BENCHMARK_CODE]
        bm_ma = bm.rolling(market_ma_window).mean()
        is_risk_on = bm > bm_ma

    # ---- Step 4: 调仓日期 ----
    rebalance_dates = _get_rebalance_dates(prices.index, rebalance_freq)

    # ---- Step 5: 逐调仓日生成信号 ----
    signals: list[dict] = []
    attack_codes = [c for c in ETF_POOL if c not in DEFENSE_ETF_CODES]
    defense_codes = DEFENSE_ETF_CODES
    base_weight = 1.0 / top_n

    for date in rebalance_dates:
        if date not in momentum_df.index:
            continue

        # 大盘择时：决定用哪个资产池
        if is_risk_on is not None and date in is_risk_on.index:
            risk_on = bool(is_risk_on.loc[date])
        else:
            risk_on = True  # 无择时信号时默认风险模式

        pool = attack_codes if risk_on else defense_codes
        pool = [c for c in pool if c in momentum_df.columns]

        # 从池中取动量
        row = momentum_df.loc[date, pool].dropna()
        row = row[row > 0]  # 只要正动量
        if row.empty:
            continue

        n_pick = min(top_n, len(row))
        top = row.nlargest(n_pick)

        # 波动率仓位缩放
        selected_codes = list(top.index)
        if use_vol_target and date is not None:
            scaled_weights = _calc_vol_scaled_weights(
                prices, selected_codes, base_weight,
                vol_target, vol_lookback, vol_cap, date,
            )
        else:
            scaled_weights = {c: base_weight for c in selected_codes}

        for code in selected_codes:
            signals.append({
                "date": date,
                "code": code,
                "name": ETF_POOL.get(code, ""),
                "momentum": round(float(top[code]), 4),
                "weight": round(scaled_weights.get(code, base_weight), 4),
                "is_defense": not risk_on,
            })

    signal_df = pd.DataFrame(signals)
    if signal_df.empty:
        return signal_df

    return signal_df.sort_values(["date", "code"]).reset_index(drop=True)


# 保留旧名兼容
generate_weekly_signals = generate_signals
