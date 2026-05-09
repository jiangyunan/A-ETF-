"""
策略模块：动量计算 + 风险/防御切换 + 波动率仓位控制。

V4 新增：
  1. 双层复合动量：短/中/长周期加权（去噪音、抗风格切换）
  2. 相对强弱过滤：仅选动量 > 沪深300 的 ETF（去假强势）
  3. 动态仓位：强牛时集中(3只)、一般时分散(5只)、熊市转防御
  4. 波动率仓位控制（高波动降仓，低波动加仓）
  5. 防御资产优先级（国债 > 黄金 > 现金）
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


def calc_composite_momentum(
    prices: pd.DataFrame,
    windows: list[int] = (10, 30, 60),
    weights: list[float] = (0.5, 0.3, 0.2),
    use_risk_adjusted: bool = True,
) -> pd.DataFrame:
    """
    双层复合动量 = Σ(权重_i × 风险调整动量_窗口i)

    原理：
      短期动量(10日)捕捉爆发力，中期(30日)确认趋势，长期(60日)过滤噪音。
      多周期加权平滑了单一窗口的偶然波动，Walk Forward 表现更稳定。

    例如 0.5×M10 + 0.3×M30 + 0.2×M60 既保留短期灵敏度又兼顾中期趋势。
    """
    # 统一计算各窗口的风险调整动量
    momentum_by_window: dict[int, pd.DataFrame] = {}
    for w in windows:
        if use_risk_adjusted:
            momentum_by_window[w] = calc_risk_adjusted_momentum(prices, w)
        else:
            momentum_by_window[w] = calc_momentum(prices, w)

    # 加权求和
    composite = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for w, weight in zip(windows, weights):
        composite += momentum_by_window[w].fillna(0) * weight

    return composite


def _get_rebalance_dates(dates: pd.DatetimeIndex, freq: int) -> pd.DatetimeIndex:
    """按指定频率提取调仓日期（1=周, 2=双周, 4=月）"""
    iso = dates.isocalendar()
    df = pd.DataFrame({"date": dates, "year": iso["year"], "week": iso["week"]})
    weekly = df.groupby(["year", "week"])["date"].max()
    vals = weekly.values
    if freq == 1:
        return pd.DatetimeIndex(vals)
    selected = [vals[i] for i in range(0, len(vals), freq)]
    return pd.DatetimeIndex(selected)


def _calc_vol_scaled_weights(
    prices: pd.DataFrame,
    selected_codes: list[str],
    base_weight: float,
    vol_target: float,
    vol_lookback: int,
    vol_cap: float,
    date: pd.Timestamp,
) -> dict[str, float]:
    """波动率目标仓位：高波降仓，低波加仓"""
    weights: dict[str, float] = {}
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
        realized_vol = daily_ret.std() * np.sqrt(252)
        scale = min(vol_target / realized_vol, vol_cap)
        weights[code] = round(base_weight * scale, 4)

    return weights


def generate_signals(
    prices: pd.DataFrame,
    window: int = MOMENTUM_WINDOW,
    top_n: int = 5,
    use_risk_adjusted: bool = True,
    use_composite_momentum: bool = True,
    composite_windows: list[int] | None = None,
    composite_weights: list[float] | None = None,
    use_dynamic_position: bool = True,
    top_n_aggressive: int = 3,
    use_relative_strength: bool = True,
    rs_benchmark: str = BENCHMARK_CODE,
    market_ma_window: int = 120,
    market_ma_aggressive: int = 200,
    rebalance_freq: int = 1,
    use_vol_target: bool = True,
    vol_target: float = 0.15,
    vol_lookback: int = VOL_LOOKBACK,
    vol_cap: float = 1.5,
) -> pd.DataFrame:
    """
    生成调仓信号（V4 全部优化参数）。

    流程：
      1. 计算动量（复合或单窗口，风险调整或原始）
      2. 相对强弱过滤：动量 < 基准的 ETF 排除
      3. 大盘择时：判断风险/防御/强牛 三种模式
      4. 动态仓位：根据市场模式决定 top_n
      5. 在资产池中按动量选 top_n
      6. 波动率仓位缩放
      7. 按调仓频率输出信号
    """
    if prices.empty:
        return pd.DataFrame(
            columns=["date", "code", "name", "momentum", "weight", "is_defense"]
        )

    if composite_windows is None:
        composite_windows = [10, 30, 60]
    if composite_weights is None:
        composite_weights = [0.5, 0.3, 0.2]

    # ---- Step 1: 动量计算 ----
    if use_composite_momentum:
        momentum_df = calc_composite_momentum(
            prices, composite_windows, composite_weights, use_risk_adjusted
        )
    elif use_risk_adjusted:
        momentum_df = calc_risk_adjusted_momentum(prices, window)
    else:
        momentum_df = calc_momentum(prices, window)

    # ---- Step 2: 相对强弱过滤 ----
    # 每只 ETF 的动量必须 > 基准（沪深300）的动量，否则排除
    if use_relative_strength and rs_benchmark in momentum_df.columns:
        bm_momentum = momentum_df[rs_benchmark]
        # 基准动量也要为正才是有效参照
        bm_positive = bm_momentum > 0
        # ETF 动量 > 基准动量，且基准为正
        for col in momentum_df.columns:
            if col == rs_benchmark:
                continue
            # 不满足条件的置 NaN
            mask = momentum_df[col] > bm_momentum
            momentum_df[col] = momentum_df[col].where(mask & bm_positive)

    # ---- Step 3: 大盘择时（三种模式） ----
    is_aggressive: pd.Series | None = None  # 强牛模式
    is_risk_on: pd.Series | None = None    # 风险模式
    if BENCHMARK_CODE in prices.columns:
        bm = prices[BENCHMARK_CODE]
        if market_ma_window > 0:
            bm_ma_short = bm.rolling(market_ma_window).mean()
            is_risk_on = bm > bm_ma_short
        if market_ma_aggressive > 0:
            bm_ma_long = bm.rolling(market_ma_aggressive).mean()
            is_aggressive = bm > bm_ma_long

    # ---- Step 4: 调仓日期 ----
    rebalance_dates = _get_rebalance_dates(prices.index, rebalance_freq)

    # ---- Step 5: 逐调仓日生成信号 ----
    signals: list[dict] = []
    attack_codes = [c for c in ETF_POOL if c not in DEFENSE_ETF_CODES]
    defense_codes = [c for c in DEFENSE_ETF_CODES if c in prices.columns]

    for date in rebalance_dates:
        if date not in momentum_df.index:
            continue

        # 判断市场模式
        risk_on = True
        aggressive = False
        if is_risk_on is not None and date in is_risk_on.index:
            risk_on = bool(is_risk_on.loc[date])
        if is_aggressive is not None and date in is_aggressive.index:
            aggressive = bool(is_aggressive.loc[date]) if risk_on else False

        # 动态仓位
        if use_dynamic_position:
            if not risk_on:
                n_pick = top_n  # 防御模式用默认数（但实际用防御池）
            elif aggressive:
                n_pick = top_n_aggressive  # 强牛集中
            else:
                n_pick = top_n  # 一般分散
        else:
            n_pick = top_n

        # 选资产池
        if risk_on:
            pool = [c for c in attack_codes if c in momentum_df.columns]
        else:
            pool = defense_codes
            n_pick = min(n_pick, len(pool))

        if not pool:
            continue

        # 池中选动量最强的
        row = momentum_df.loc[date, pool].dropna()
        row = row[row > 0]
        if row.empty:
            continue

        n_pick = min(n_pick, len(row))
        top = row.nlargest(n_pick)

        # 波动率仓位缩放
        selected_codes = list(top.index)
        base_weight = 1.0 / n_pick
        if use_vol_target and risk_on:
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
