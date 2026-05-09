"""
策略模块 V5：市场状态机 + 相关性控制 + 波动率仓位。

核心升级：
  1. 市场状态机：牛/震荡/熊 三态 → 自动切换参数（窗口、持仓数、风险暴露）
  2. 相关性控制：避免同质化 ETF 集中（如纳指+芯片+创业板）
  3. 波动率仓位：高波降仓、低波加仓
  4. 防御资产优先级：市场走弱时自动切国债/黄金
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


# ─── 动量计算 ──────────────────────────────────────────────

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
    """复合动量 = Σ(权重 × 各窗口动量)"""
    composite = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for w, weight in zip(windows, weights):
        m = calc_risk_adjusted_momentum(prices, w) if use_risk_adjusted else calc_momentum(prices, w)
        composite += m.fillna(0) * weight
    return composite


# ─── 市场健康度评分（连续值，替代离散状态机）────────────────

def _calc_market_health(
    prices: pd.DataFrame,
    date: pd.Timestamp,
    breadth_window: int = 60,
    market_window: int = 120,
    trend_short: int = 20,
    trend_medium: int = 60,
) -> float:
    """
    计算市场健康度评分 (0~1)，融合两个维度：

    1. 市场广度 — 攻击池中站上 MA(N) 的 ETF 占比
       看的是「市场内部结构」，比单指数 MA 更稳定。
       80%以上 → 强牛，大部分走弱 → 熊市将至。

    2. 趋势强度 — 沪深300相对 MA 的偏离度
       看的是「大盘方向」，归一化到 0~1。

    合成：score = 0.5 × 广度 + 0.5 × 趋势

    返回: 0~1 之间的连续值（而非离散三态）
    """
    if BENCHMARK_CODE not in prices.columns:
        return 0.5

    bm = prices[BENCHMARK_CODE]
    if date not in bm.index:
        return 0.5

    idx = bm.index.get_loc(date)
    if idx < market_window:
        return 0.5

    # ── 1. 市场广度：攻击池中 > MA 的 ETF 占比 ──
    attack_codes = [c for c in prices.columns if c not in DEFENSE_ETF_CODES and c != BENCHMARK_CODE]
    if not attack_codes:
        breadth = 0.5
    else:
        above_count = 0
        for code in attack_codes:
            if code not in prices.columns:
                continue
            series = prices[code]
            if date not in series.index:
                continue
            idx_c = series.index.get_loc(date)
            if idx_c < breadth_window:
                continue
            ma = series.iloc[max(0, idx_c - breadth_window + 1):idx_c + 1].mean()
            if series.iloc[idx_c] > ma:
                above_count += 1
        breadth = above_count / len(attack_codes) if len(attack_codes) > 0 else 0.5

    # ── 2. 趋势强度：价格相对 MA 的偏离 ──
    price_now = bm.iloc[idx]
    ma_market = bm.iloc[max(0, idx - market_window + 1):idx + 1].mean()
    ma_short = bm.iloc[max(0, idx - trend_short + 1):idx + 1].mean()
    ma_medium = bm.iloc[max(0, idx - trend_medium + 1):idx + 1].mean()

    # 趋势偏离：价格在 MA 上方多少（归一化到 [-0.5, 0.5] 再映射到 [0, 1]）
    trend_deviation = (price_now / ma_market - 1) if ma_market > 0 else 0
    trend_deviation = max(-0.5, min(0.5, trend_deviation))
    trend_score = (trend_deviation + 0.5)  # → [0, 1]

    # 均线排列加分：短期>中期趋势确认
    if ma_short > ma_medium:
        trend_score = min(1.0, trend_score + 0.15)  # 趋势向上加分

    # ── 合成健康度 ──
    health = 0.5 * breadth + 0.5 * trend_score
    return round(health, 3)


# ─── 相关性控制 ──────────────────────────────────────────────

def _filter_by_correlation(
    selected_codes: list[str],
    prices: pd.DataFrame,
    date: pd.Timestamp,
    corr_window: int = 60,
    corr_threshold: float = 0.75,
) -> list[str]:
    """
    从已选 ETF 列表中剔除与已保留 ETF 高度相关的标的。

    逻辑：
      按动量排序逐个检查，如果候选 ETF 与任何已保留的 ETF
      在近 corr_window 日的收益率相关性 > corr_threshold，则跳过。

    为什么：
      纳指、芯片、创业板经常同涨同跌，同时重仓等于押注同一方向。
      相关性过滤确保组合内部真正分散。
    """
    if len(selected_codes) <= 1:
        return selected_codes

    date_pos = prices.index.get_loc(date)
    lookback = min(corr_window, date_pos)
    if lookback < 20:
        return selected_codes  # 数据不够不做过滤

    returns = prices.pct_change().iloc[max(0, date_pos - lookback):date_pos + 1]
    returns = returns.dropna(axis=1, how="all")

    kept: list[str] = [selected_codes[0]]  # 动量第一的一定保留
    for code in selected_codes[1:]:
        if code not in returns.columns:
            kept.append(code)
            continue
        # 检查与已保留 ETF 的相关性
        too_correlated = False
        for kept_code in kept:
            if kept_code not in returns.columns:
                continue
            corr = returns[code].corr(returns[kept_code])
            if pd.notna(corr) and abs(corr) > corr_threshold:
                too_correlated = True
                break
        if not too_correlated:
            kept.append(code)

    return kept


# ─── 辅助 ────────────────────────────────────────────────────

def _get_rebalance_dates(dates: pd.DatetimeIndex, freq: int) -> pd.DatetimeIndex:
    iso = dates.isocalendar()
    df = pd.DataFrame({"date": dates, "year": iso["year"], "week": iso["week"]})
    weekly = df.groupby(["year", "week"])["date"].max()
    vals = weekly.values
    if freq == 1:
        return pd.DatetimeIndex(vals)
    return pd.DatetimeIndex([vals[i] for i in range(0, len(vals), freq)])


def _calc_vol_scaled_weights(
    prices, selected_codes, base_weight, vol_target, vol_lookback, vol_cap, date,
) -> dict[str, float]:
    weights: dict[str, float] = {}
    date_pos = prices.index.get_loc(date)
    lookback_start = max(0, date_pos - vol_lookback)
    for code in selected_codes:
        if code not in prices.columns:
            weights[code] = base_weight; continue
        hist = prices[code].iloc[lookback_start:date_pos + 1]
        if len(hist) < 5:
            weights[code] = base_weight; continue
        daily_ret = hist.pct_change().dropna()
        if len(daily_ret) < 5 or daily_ret.std() == 0:
            weights[code] = base_weight; continue
        realized_vol = daily_ret.std() * np.sqrt(252)
        scale = min(vol_target / realized_vol, vol_cap)
        weights[code] = round(base_weight * scale, 4)
    return weights


# ─── 主信号生成 ──────────────────────────────────────────────

def generate_signals(
    prices: pd.DataFrame,
    window: int = MOMENTUM_WINDOW,
    top_n: int = 5,
    use_risk_adjusted: bool = True,
    use_composite_momentum: bool = False,
    composite_windows: list[int] | None = None,
    composite_weights: list[float] | None = None,
    use_market_state_machine: bool = True,
    state_bull_window: int = 10,
    state_bull_top_n: int = 3,
    state_sideways_window: int = 20,
    state_sideways_top_n: int = 5,
    state_bear_window: int = 40,
    ma_trend_short: int = 20,
    ma_trend_medium: int = 60,
    market_ma_window: int = 120,
    use_correlation_filter: bool = True,
    correlation_window: int = 60,
    correlation_threshold: float = 0.75,
    use_dynamic_position: bool = False,
    top_n_aggressive: int = 3,
    use_relative_strength: bool = False,
    rs_benchmark: str = BENCHMARK_CODE,
    market_ma_aggressive: int = 200,
    rebalance_freq: int = 1,
    use_vol_target: bool = True,
    vol_target: float = 0.15,
    vol_lookback: int = VOL_LOOKBACK,
    vol_cap: float = 1.5,
    **kwargs,
) -> pd.DataFrame:
    """生成调仓信号（V5 状态机+相关性控制）。"""
    if prices.empty:
        return pd.DataFrame(columns=["date", "code", "name", "momentum", "weight", "state"])

    # ── 动量计算 ──
    if use_composite_momentum:
        cw = composite_windows or [10, 30, 60]
        cwt = composite_weights or [0.5, 0.3, 0.2]
        momentum_df = calc_composite_momentum(prices, cw, cwt, use_risk_adjusted)
    elif use_risk_adjusted:
        momentum_df = calc_risk_adjusted_momentum(prices, window)
    else:
        momentum_df = calc_momentum(prices, window)

    # ── 相对强弱过滤 ──
    if use_relative_strength and rs_benchmark in momentum_df.columns:
        bm_mom = momentum_df[rs_benchmark]
        for col in momentum_df.columns:
            if col == rs_benchmark:
                continue
            momentum_df[col] = momentum_df[col].where(
                (momentum_df[col] > bm_mom) & (bm_mom > 0)
            )

    # ── 调仓日期 ──
    rebalance_dates = _get_rebalance_dates(prices.index, rebalance_freq)

    # ── 资产池 ──
    attack_codes = [c for c in ETF_POOL if c not in DEFENSE_ETF_CODES]
    defense_codes = [c for c in DEFENSE_ETF_CODES if c in prices.columns]

    signals: list[dict] = []

    for date in rebalance_dates:
        if date not in momentum_df.index:
            continue

        # ── 市场健康度评分（连续 0~1，替代离散三态）──
        if use_market_state_machine:
            health = _calc_market_health(
                prices, date, breadth_window=60, market_window=market_ma_window,
                trend_short=ma_trend_short, trend_medium=ma_trend_medium,
            )
            # 健康度 → 窗口（10~40天）、持仓数（2~7只）
            ef_window = 40 - int(health * 30)  # health=1→10, health=0→40
            ef_top_n = max(2, int(health * 7))  # health=1→7, health=0→2
            # 健康度 < 0.35 → 切防御池；否则攻击池
            if health < 0.35:
                pool = defense_codes
                risk_on = False
                ef_top_n = min(ef_top_n, len(pool))
            else:
                pool = [c for c in attack_codes if c in momentum_df.columns]
                risk_on = True
        elif use_dynamic_position:
            bm = prices[BENCHMARK_CODE] if BENCHMARK_CODE in prices.columns else None
            if bm is not None and date in bm.index:
                bm_ma = bm.rolling(market_ma_aggressive).mean()
                aggressive = bm.loc[date] > bm_ma.loc[date] if date in bm_ma.index else False
                ef_top_n = top_n_aggressive if aggressive else top_n
            else:
                ef_top_n = top_n
            ef_window = window
            pool = [c for c in attack_codes if c in momentum_df.columns]
            risk_on = True
        else:
            ef_window = window
            ef_top_n = top_n
            # 基础市场过滤（状态机关闭时的兜底防御）
            if market_ma_window > 0 and BENCHMARK_CODE in prices.columns:
                bm = prices[BENCHMARK_CODE]
                if date in bm.index and date in bm.rolling(market_ma_window).mean().index:
                    if bm.loc[date] > bm.rolling(market_ma_window).mean().loc[date]:
                        pool = [c for c in attack_codes if c in momentum_df.columns]
                        risk_on = True
                    else:
                        pool = defense_codes
                        risk_on = False
                else:
                    pool = [c for c in attack_codes if c in momentum_df.columns]
                    risk_on = True
            else:
                pool = [c for c in attack_codes if c in momentum_df.columns]
                risk_on = True

        if not pool:
            continue

        # 用状态对应的窗口重新计算动量（如果需要不同窗口）
        if use_market_state_machine and ef_window != window:
            state_momentum = (
                calc_risk_adjusted_momentum(prices, ef_window)
                if use_risk_adjusted else calc_momentum(prices, ef_window)
            )
            row = state_momentum.loc[date, pool].dropna()
        elif use_dynamic_position and ef_window != window:
            state_momentum = (
                calc_risk_adjusted_momentum(prices, ef_window)
                if use_risk_adjusted else calc_momentum(prices, ef_window)
            )
            row = state_momentum.loc[date, pool].dropna()
        else:
            row = momentum_df.loc[date, pool].dropna()

        row = row[row > 0]
        if row.empty:
            continue

        n_pick = min(ef_top_n, len(row))
        top = row.nlargest(max(n_pick * 2, n_pick))  # 多取一些给相关性过滤留余量

        # ── 相关性过滤 ──
        selected = list(top.index)
        if use_correlation_filter and risk_on and len(selected) > 1:
            selected = _filter_by_correlation(
                selected, prices, date, correlation_window, correlation_threshold,
            )
        selected = selected[:n_pick]

        # ── 波动率仓位 ──
        base_weight = 1.0 / len(selected) if selected else 1.0 / n_pick
        if use_vol_target and risk_on:
            scaled_weights = _calc_vol_scaled_weights(
                prices, selected, base_weight, vol_target, vol_lookback, vol_cap, date,
            )
        else:
            scaled_weights = {c: base_weight for c in selected}

        for code in selected:
            signals.append({
                "date": date,
                "code": code,
                "name": ETF_POOL.get(code, ""),
                "momentum": round(float(top.get(code, 0)), 4),
                "weight": round(scaled_weights.get(code, base_weight), 4),
                "health": round(health, 3) if use_market_state_machine else (1.0 if risk_on else 0.0),
            })

    signal_df = pd.DataFrame(signals)
    if signal_df.empty:
        return signal_df
    return signal_df.sort_values(["date", "code"]).reset_index(drop=True)


generate_weekly_signals = generate_signals
