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
    PREMIUM_K,
    PREMIUM_L,
    PREMIUM_WEIGHT_DECAY,
    PREMIUM_BAN_ABSOLUTE,
    PREMIUM_IGNORE,
    VOL_DISCRETE,
    VOL_TIER_HIGH,
    VOL_TIER_MID,
    VOL_TIER_LOW,
    MIN_REBALANCE_PCT,
    STATE_SMOOTHING,
    STATE_COOLDOWN,
    ASYMMETRIC_COOLDOWN,
    RISK_ON_CONFIRM_DAYS,
    POSITION_BUFFER,
    VOL_EWMA_HALFLIFE,
    VOL_NORMALIZE,
    MAX_SINGLE_WEIGHT,
    MAX_GROUP_EXPOSURE,
    MIN_WEIGHT_THRESHOLD,
    USE_TREND_FILTER_WEIGHT,
    CASH_EQUIVALENT_CODE,
    RECOVERY_WINDOW,
    RECOVERY_TOP_N,
    RECOVERY_MAX_WEIGHT,
)
from src.strategy.black_swan import evaluate_black_swan


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


# ─── 市场状态机 V6（广度增强）─────────────────────────────────

def _market_breadth(prices: pd.DataFrame, date: pd.Timestamp, window: int = 60) -> float:
    """攻击池中 > MA(N) 的 ETF 占比 (0~1)。"""
    codes = [c for c in prices.columns if c not in DEFENSE_ETF_CODES and c != BENCHMARK_CODE]
    codes = [c for c in codes if c in prices.columns]
    if not codes:
        return 0.5
    above = 0
    valid = 0
    for code in codes:
        s = prices[code]
        if date not in s.index:
            continue
        idx = s.index.get_loc(date)
        if idx < window:
            continue
        ma = s.iloc[max(0, idx - window + 1):idx + 1].mean()
        if s.iloc[idx] > ma:
            above += 1
        valid += 1
    return above / valid if valid > 0 else 0.5


def _classify_market(
    prices: pd.DataFrame, date: pd.Timestamp,
    ma_trend_short: int = 20, ma_trend_medium: int = 60, ma_market: int = 120,
    prev_state: str = "SIDEWAYS",
) -> tuple[str, int, int]:
    """
    四态判断：BULL / RECOVERY / SIDEWAYS / BEAR。

    BULL:      广度 > 0.6 + MA20>MA60 + CSI300>MA120 → 集中进攻
    RECOVERY:  刚从BEAR恢复 + CSI300>MA120 + 广度>0.25 → 试探性进攻
    SIDEWAYS:  CSI300>MA120 或 广度>0.35 → 正常震荡
    BEAR:      其余 → 防御
    """
    if BENCHMARK_CODE not in prices.columns:
        return ("SIDEWAYS", 20, 5)

    bm = prices[BENCHMARK_CODE]
    if date not in bm.index:
        return ("SIDEWAYS", 20, 5)

    idx = bm.index.get_loc(date)
    if idx < ma_market:
        return ("SIDEWAYS", 20, 5)

    price_now = bm.iloc[idx]
    ma_s = bm.iloc[max(0, idx - ma_trend_short + 1):idx + 1].mean()
    ma_m = bm.iloc[max(0, idx - ma_trend_medium + 1):idx + 1].mean()
    ma_l = bm.iloc[max(0, idx - ma_market + 1):idx + 1].mean()

    breadth = _market_breadth(prices, date, window=60)

    above_market = price_now > ma_l
    trending_up = ma_s > ma_m
    broad_healthy = breadth > 0.6

    # 顺序关键：RECOVERY必须在SIDEWAYS之前
    if above_market and trending_up and broad_healthy:
        return ("BULL", 10, 3)
    elif prev_state == "BEAR" and above_market and breadth > 0.25:
        return ("RECOVERY", RECOVERY_WINDOW, RECOVERY_TOP_N)
    elif above_market or breadth > 0.35:
        return ("SIDEWAYS", 20, 5)
    else:
        return ("BEAR", 40, 2)


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


# ─── 调仓日期（实盘规则）──────────────────────────────────────

def _get_rebalance_dates(dates: pd.DatetimeIndex, freq: int) -> pd.DatetimeIndex:
    """
    按实盘规则提取信号生成日期。

    规则: 每周第一个交易日收盘后生成信号
      - 通常 = 周一收盘
      - 周一休市 → 周二收盘
      - 长假后（3天以上休市）→ 开市第一天仅观察，第二天执行

    Returns:
        信号日期序列（每个调仓周期的第一天）
    """
    iso = dates.isocalendar()
    df = pd.DataFrame({"date": dates, "year": iso["year"], "week": iso["week"]})
    # 每周第一个交易日（通常是周一）
    weekly_first = df.groupby(["year", "week"])["date"].min()
    vals = weekly_first.values

    if freq == 1:
        return pd.DatetimeIndex(vals)
    return pd.DatetimeIndex([vals[i] for i in range(0, len(vals), freq)])


def _get_holiday_delays(dates: pd.DatetimeIndex) -> set:
    """
    检测长假：连续 3 天以上无交易 → 假期后的第一个调仓日需要延迟执行。

    规则: 上次交易日距今 >= 3 个自然日 → 长假，第一天仅观察不执行。

    Returns:
        需要延迟执行（跳过第一个交易日）的信号日期集合
    """
    delay_dates: set = set()
    if len(dates) < 2:
        return delay_dates

    for i in range(1, len(dates)):
        gap = (dates[i] - dates[i - 1]).days
        if gap >= 5:  # 自然日间隔 >=5 天（如周五→下周二=4天自然日，实际只休2天）
            delay_dates.add(dates[i])

    return delay_dates
    return pd.DatetimeIndex([vals[i] for i in range(0, len(vals), freq)])


def _calc_vol_scaled_weights(
    prices, selected_codes, base_weight, vol_target, vol_lookback, vol_cap, date,
    discrete: bool = VOL_DISCRETE,
    tier_high: float = VOL_TIER_HIGH,
    tier_mid: float = VOL_TIER_MID,
    tier_low: float = VOL_TIER_LOW,
    normalize: bool = VOL_NORMALIZE,
    ewma_halflife: int = VOL_EWMA_HALFLIFE,
    max_single: float = MAX_SINGLE_WEIGHT,
    max_group: float = MAX_GROUP_EXPOSURE,
    min_weight: float = MIN_WEIGHT_THRESHOLD,
    trend_filter_weight: bool = USE_TREND_FILTER_WEIGHT,
) -> dict[str, float]:
    """
    V3: EWMA波动率 + 趋势过滤 + 逆波动率归一化 + 风险预算约束。

    流程：1.TrendFilter → 2.EWMA vol → 3.逆波缩放 → 4.归一化 → 5.单只上限 → 6.大类上限 → 7.最小阈值 → 8.离散化
    """
    weights: dict[str, float] = {}
    date_pos = prices.index.get_loc(date)

    GROUP_MAP = {
        "成长": ["512760", "513100", "513500", "159915", "513050", "516160"],
        "A股宽基": ["510300", "510500", "510880"],
        "A股行业": ["512000", "512660", "512170", "561660", "159873"],
        "跨境": ["513520", "513030", "513120"],
        "商品": ["159322", "159518"],
        "债券": ["511010", "511260"],
    }

    # Step 1: 趋势过滤
    valid_codes = []
    if trend_filter_weight:
        ma120 = prices.rolling(window=120).mean()
        for code in selected_codes:
            if code not in prices.columns:
                continue
            c = prices[code].loc[date] if date in prices[code].index else None
            m = ma120[code].loc[date] if code in ma120.columns and date in ma120.index else None
            if c is not None and m is not None and c > m:
                valid_codes.append(code)
            elif c is None:
                valid_codes.append(code)
    else:
        valid_codes = [c for c in selected_codes if c in prices.columns]

    if not valid_codes:
        cash_code = CASH_EQUIVALENT_CODE
        if cash_code in prices.columns:
            return {cash_code: 1.0}
        return {}

    # Step 2: EWMA volatility → inverse vol weight
    alpha = 2.0 / (ewma_halflife + 1)
    for code in valid_codes:
        hist = prices[code].iloc[max(0, date_pos - 120):date_pos + 1]
        if len(hist) < 20:
            weights[code] = base_weight; continue
        daily_ret = hist.pct_change().dropna()
        if len(daily_ret) < 10:
            weights[code] = base_weight; continue
        ewma_var = daily_ret.iloc[0] ** 2
        for r in daily_ret.iloc[1:]:
            ewma_var = alpha * (r ** 2) + (1 - alpha) * ewma_var
        realized_vol = np.sqrt(ewma_var) * np.sqrt(252)
        if realized_vol == 0:
            weights[code] = base_weight; continue
        weights[code] = base_weight * min(vol_target / realized_vol, vol_cap)

    if not weights:
        return {}

    # Step 3: normalize → sum = 1.0
    if normalize:
        total = sum(weights.values())
        if total > 0:
            weights = {c: w / total for c, w in weights.items()}

    # Step 4: single cap 25%
    excess = 0.0
    for c in list(weights.keys()):
        if weights[c] > max_single:
            excess += weights[c] - max_single
            weights[c] = max_single
    if excess > 0:
        eligible = [c for c in weights if weights[c] < max_single]
        if eligible:
            spread = excess / len(eligible)
            for c in eligible:
                weights[c] = min(weights[c] + spread, max_single)

    # Step 5: group cap 40%
    for gname, gcodes in GROUP_MAP.items():
        gw = [c for c in gcodes if c in weights]
        if not gw:
            continue
        gw_sum = sum(weights[c] for c in gw)
        if gw_sum > max_group:
            scale = max_group / gw_sum
            for c in gw:
                weights[c] *= scale

    # Step 6: min threshold 5%
    weights = {c: w for c, w in weights.items() if w >= min_weight}

    # Step 7: re-normalize
    if normalize and weights:
        total = sum(weights.values())
        if total > 0:
            weights = {c: w / total for c, w in weights.items()}

    # Step 8: discrete tiers (if enabled)
    if discrete and weights:
        for c in list(weights.keys()):
            w = weights[c]
            if w >= 0.20:
                weights[c] = round(w, 4)
            elif w >= 0.12:
                weights[c] = round(w * 0.7, 4)
            else:
                weights[c] = round(w * 0.4, 4)

    return weights


# ─── 溢价过滤 ──────────────────────────────────────────────

def _apply_premium_filter(
    mom_row: pd.Series,
    prem: pd.DataFrame,
    date: pd.Timestamp,
    ignore_below: float = PREMIUM_IGNORE,
    k: float = PREMIUM_K,
    l_exp: float = PREMIUM_L,
    decay: float = PREMIUM_WEIGHT_DECAY,
    ban_absolute: float = PREMIUM_BAN_ABSOLUTE,
) -> tuple[pd.Series, dict[str, float]]:
    """
    连续溢价惩罚（仅 premium > 3% 生效）。

    < 3% → 完全忽略（QDII ETF 常驻 1~3% 属正常）
    ≥ 3% → AdjustedScore = Momentum × max(0, 1 - k × p^l)
            AdjustedWeight = Weight × max(0.2, 1 - decay × p)
    > 12% → 绝对禁止
    """
    row = mom_row.copy()
    weight_penalty: dict[str, float] = {}

    if prem.empty or date not in prem.index:
        return row, weight_penalty

    prem_row = prem.loc[date]
    for code in row.index:
        if code not in prem_row.index:
            continue
        p = prem_row[code]
        if pd.isna(p):
            continue
        if p <= ignore_below or p <= 0:
            continue

        if p >= ban_absolute:
            row[code] = np.nan
            continue

        momentum_factor = max(0.0, 1.0 - k * (p ** l_exp))
        if not pd.isna(row[code]):
            row[code] *= momentum_factor

        weight_factor = max(0.2, 1.0 - decay * p)
        if weight_factor < 1.0:
            weight_penalty[code] = weight_factor

    return row, weight_penalty


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
    premium_data: pd.DataFrame | None = None,
    **kwargs,
) -> pd.DataFrame:
    """生成调仓信号（V5 状态机+相关性控制+溢价过滤）。"""
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

    # ── 调仓日期 + 长假检测 ──
    rebalance_dates = _get_rebalance_dates(prices.index, rebalance_freq)
    holiday_delays = _get_holiday_delays(prices.index)

    # ── 资产池 ──
    attack_codes = [c for c in ETF_POOL if c not in DEFENSE_ETF_CODES]
    defense_codes = [c for c in DEFENSE_ETF_CODES if c in prices.columns]

    signals: list[dict] = []

    # ── 状态平滑变量 ──
    committed_state: str = "SIDEWAYS"
    reentry_count: int = 0  # BEAR→RISK 重入计数器
    recovery_weeks: int = 0  # RECOVERY 已持续周期数

    # ── 最小调仓变量 ──
    prev_weights: dict[str, float] = {}

    for date in rebalance_dates:
        if date not in momentum_df.index:
            continue

        # ── 市场状态判断（广度增强 + RECOVERY）──
        if use_market_state_machine:
            raw_state, dyn_window, dyn_top_n = _classify_market(
                prices, date, ma_trend_short, ma_trend_medium, market_ma_window,
                prev_state=committed_state,
            )
            state = raw_state

            # ── RECOVERY 管理：BEAR→RECOVERY 需确认，RECOVERY内动态N ──
            if raw_state == "RECOVERY":
                if committed_state == "BEAR":
                    reentry_count += 1
                    if reentry_count < RISK_ON_CONFIRM_DAYS:
                        state = "BEAR"
                    else:
                        reentry_count = 0
                        recovery_weeks = 0
                else:
                    recovery_weeks += 1

                    # 动态N：按波动率等级决定所需确认周数
                    bm = prices[BENCHMARK_CODE] if BENCHMARK_CODE in prices.columns else None
                    vol_level = 1  # 默认中波
                    if bm is not None and date in bm.index:
                        idx = bm.index.get_loc(date)
                        if idx >= 60:
                            rets = bm.pct_change().iloc[idx-60:idx+1].dropna()
                            if len(rets) > 10:
                                ewma_alpha = 2.0 / (VOL_EWMA_HALFLIFE + 1)
                                ewma_var = rets.iloc[0] ** 2
                                for r in rets.iloc[1:]:
                                    ewma_var = ewma_alpha * (r**2) + (1-ewma_alpha) * ewma_var
                                current_vol = np.sqrt(ewma_var)
                                hist_vols = rets.rolling(10).std().median()
                                if current_vol < hist_vols * 0.8:
                                    vol_level = 0
                                elif current_vol > hist_vols * 1.3:
                                    vol_level = 2
                    required_weeks = {0: 2, 1: 3, 2: 5}[vol_level]

                    if recovery_weeks >= required_weeks:
                        # 晋升：MA120×1.02 确认趋势才进BULL
                        bm_p = prices[BENCHMARK_CODE] if BENCHMARK_CODE in prices.columns else None
                        if bm_p is not None and date in bm_p.index:
                            idx2 = bm_p.index.get_loc(date)
                            if idx2 >= 120:
                                close = bm_p.iloc[idx2]
                                ma120 = bm_p.iloc[idx2-119:idx2+1].mean()
                                ma_s = bm_p.iloc[max(0,idx2-20):idx2+1].mean()
                                ma_m = bm_p.iloc[max(0,idx2-60):idx2+1].mean()
                                if close > ma120 * 1.02 and ma_s > ma_m:
                                    state = "BULL"
                                else:
                                    state = "SIDEWAYS"
                            else:
                                state = "SIDEWAYS"
                        else:
                            state = "SIDEWAYS"
                        recovery_weeks = 0
            elif raw_state == "BEAR":
                reentry_count = 0
                recovery_weeks = 0
            else:
                # 从RECOVERY晋升后继续自由切换
                if committed_state == "RECOVERY":
                    pass  # 已经在上面晋升了
                elif committed_state == "BEAR" and raw_state != "BEAR":
                    reentry_count += 1
                    if reentry_count < RISK_ON_CONFIRM_DAYS:
                        state = "BEAR"
                    else:
                        reentry_count = 0

            committed_state = state

            if state == "BULL":
                ef_window = state_bull_window
                ef_top_n = state_bull_top_n
            elif state == "RECOVERY":
                ef_window = RECOVERY_WINDOW
                ef_top_n = RECOVERY_TOP_N
            elif state == "BEAR":
                ef_window = state_bear_window
                ef_top_n = dyn_top_n
            else:
                ef_window = state_sideways_window
                ef_top_n = state_sideways_top_n
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
            state = "RISK_ON"
            # 基础市场过滤兜底
            if market_ma_window > 0 and BENCHMARK_CODE in prices.columns:
                bm = prices[BENCHMARK_CODE]
                if date in bm.index and date in bm.rolling(market_ma_window).mean().index:
                    state = "RISK_ON" if bm.loc[date] > bm.rolling(market_ma_window).mean().loc[date] else "BEAR"

        # ── 选池 ──
        if state == "BEAR":
            pool = defense_codes
            risk_on = False
        else:
            pool = [c for c in attack_codes if c in momentum_df.columns]
            risk_on = True

        # ── 黑天鹅检测（Level 0 — 最高优先级）──
        black_swan_risk_mult = 1.0
        if risk_on:
            bs = evaluate_black_swan(prices, None, date)
            if bs["global_crash"]:
                pool = defense_codes
                risk_on = False
            if bs["suspended"]:
                pool = [c for c in pool if c not in bs["suspended"]]
            black_swan_risk_mult = bs["risk_mult"]

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

        # ── 溢价过滤 ──
        weight_penalty_prem: dict[str, float] = {}
        if premium_data is not None and not premium_data.empty:
            row, weight_penalty_prem = _apply_premium_filter(row, premium_data, date)
            row = row.dropna()
            row = row[row > 0]
            if row.empty:
                continue

        n_pick = min(ef_top_n, len(row))

        # ── 持仓缓冲区（hysteresis）──
        # 买入条件: Top N，卖出条件: 跌出 Top N+buffer
        force_keep: list[str] = []
        if POSITION_BUFFER > 0 and prev_weights:
            for code in prev_weights:
                if code in row.index:
                    rank = row.rank(ascending=False)[code]
                    if rank <= n_pick + POSITION_BUFFER:
                        force_keep.append(code)

        top = row.nlargest(max(n_pick * 2, n_pick))  # 多取一些给相关性过滤留余量

        # ── 相关性过滤 ──
        selected = list(top.index)
        if use_correlation_filter and risk_on and len(selected) > 1:
            selected = _filter_by_correlation(
                selected, prices, date, correlation_window, correlation_threshold,
            )
        # 缓冲区优先：force_keep 的 ETF 确保入选
        for code in force_keep:
            if code not in selected and code in row.index:
                selected.insert(0, code)  # 排到最前面
        selected = selected[:n_pick]

        # ── 波动率仓位 ──
        base_weight = 1.0 / len(selected) if selected else 1.0 / n_pick
        if use_vol_target and risk_on:
            scaled_weights = _calc_vol_scaled_weights(
                prices, selected, base_weight, vol_target, vol_lookback, vol_cap, date,
            )
        else:
            scaled_weights = {c: base_weight for c in selected}

        # ── 极端波动自动降仓（vol > 90分位 → 仓位 ×0.5）──
        if risk_on:
            nav_ret = prices.pct_change()
            # 用策略组合中各ETF平均波动率判断（比单指数更准确）
            pool_vol = nav_ret[selected].std(axis=1) if len(selected) > 0 else pd.Series()
            if len(pool_vol) > vol_lookback and date in pool_vol.index:
                recent_vol = pool_vol.loc[:date].iloc[-vol_lookback:].mean()
                hist_vol = pool_vol.loc[:date].dropna()
                if len(hist_vol) > vol_lookback * 2:
                    pct_rank = (hist_vol.iloc[-vol_lookback:].mean() > hist_vol).mean()
                    if pct_rank > 0.90:
                        for code in scaled_weights:
                            scaled_weights[code] = round(scaled_weights[code] * 0.5, 4)

        # ── 溢价仓位惩罚 ──
        for code, penalty in weight_penalty_prem.items():
            if code in scaled_weights:
                scaled_weights[code] = round(scaled_weights[code] * penalty, 4)

        # ── 黑天鹅全局降仓 ──
        if black_swan_risk_mult < 1.0:
            for code in scaled_weights:
                scaled_weights[code] = round(scaled_weights[code] * black_swan_risk_mult, 4)

        # ── RECOVERY 半风险预算：单只上限降到正常的一半 ──
        if state == "RECOVERY":
            for code in list(scaled_weights.keys()):
                if scaled_weights[code] > RECOVERY_MAX_WEIGHT:
                    scaled_weights[code] = RECOVERY_MAX_WEIGHT

        # ── 最小调仓阈值：权重变化 < 5% → 跳过本周 ──
        if MIN_REBALANCE_PCT > 0 and prev_weights:
            # 合并新旧权重字典（旧仓位可能持有新仓位没有的 ETF）
            all_codes = set(scaled_weights.keys()) | set(prev_weights.keys())
            max_change = 0.0
            for c in all_codes:
                old = prev_weights.get(c, 0.0)
                new = scaled_weights.get(c, 0.0)
                max_change = max(max_change, abs(new - old))
            if max_change < MIN_REBALANCE_PCT:
                continue  # 跳过本周，不记录信号

        for code in selected:
            signals.append({
                "date": date,
                "code": code,
                "name": ETF_POOL.get(code, ""),
                "momentum": round(float(top.get(code, 0)), 4),
                "weight": round(scaled_weights.get(code, base_weight), 4),
                "state": state if use_market_state_machine else ("RISK_ON" if risk_on else "BEAR"),
                "holiday_delay": date in holiday_delays,
            })
        # 更新上周权重用于下轮比较
        prev_weights = scaled_weights.copy()

    signal_df = pd.DataFrame(signals)
    if signal_df.empty:
        return signal_df
    return signal_df.sort_values(["date", "code"]).reset_index(drop=True)


generate_weekly_signals = generate_signals
