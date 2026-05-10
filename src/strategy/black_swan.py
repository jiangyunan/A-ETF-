"""
黑天鹅响应机制：极端市场事件检测与自动应对。

三个检测器：
  1. VIX 代理   — 市场恐慌指数（ETF 池波动率中位数）
  2. 全球崩盘   — 跨境 ETF 同步暴跌检测
  3. 流动性恶化 — ETF 成交量萎缩检测

检测到事件时自动执行保护动作：
  - 降仓（恐慌指数飙升）
  - 强制防御（全球同步暴跌）
  - 暂停交易（流动性枯竭）
"""

import numpy as np
import pandas as pd

from src.config import BENCHMARK_CODE


# ─── 1. VIX 代理（恐慌指数）────────────────────────────────

def detect_vix_spike(
    prices: pd.DataFrame, date: pd.Timestamp,
    vol_lookback: int = 20, spike_threshold: float = 2.5,
) -> tuple[bool, float]:
    """
    检测市场恐慌是否飙升。

    VIX 代理 = ETF 池内所有 ETF 日收益率标准差的均值。

    条件：当前 5 日波动率 > spike_threshold × 过去 60 日中位数

    Returns:
        is_spike: 是否触发恐慌
        risk_multiplier: 仓位倍率（1.0=不变, 0.5=减半, 0.3=大幅降仓）
    """
    if prices.empty or date not in prices.index:
        return False, 1.0

    idx = prices.index.get_loc(date)
    if idx < 60:
        return False, 1.0

    daily_rets = prices.pct_change().iloc[max(0, idx - 60):idx + 1]

    # 每只 ETF 的近期波动率
    recent_vol = daily_rets.iloc[-vol_lookback:].std().median()
    hist_vol = daily_rets.iloc[:-5].std().median()

    if hist_vol == 0 or pd.isna(hist_vol):
        return False, 1.0

    ratio = recent_vol / hist_vol

    if ratio > spike_threshold * 1.5:
        return True, 0.3   # 极端恐慌 → 仓位缩到 30%
    elif ratio > spike_threshold:
        return True, 0.5   # 恐慌 → 仓位减半
    else:
        return False, 1.0


# ─── 2. 全球同步崩盘检测 ──────────────────────────────────

def detect_global_crash(
    prices: pd.DataFrame, date: pd.Timestamp,
    crash_pct: float = -0.03, lookback_days: int = 3,
    min_markets: int = 3,
) -> tuple[bool, float]:
    """
    检测跨境 ETF 是否同步暴跌。

    看纳指/标普/日经/德国/新兴市场等跨境 ETF。
    如果 majority 在 lookback_days 内跌幅 > crash_pct → 全球崩盘。

    Returns:
        is_crash: 是否全球崩盘
        force_defense: 是否强制切防御模式
    """
    global_codes = ["513100", "513500", "513520", "513030", "513120"]

    available = [c for c in global_codes if c in prices.columns]
    if len(available) < min_markets:
        return False, False

    if date not in prices.index:
        return False, False

    idx = prices.index.get_loc(date)
    if idx < lookback_days:
        return False, False

    crash_count = 0
    for code in available:
        close_recent = prices[code].iloc[idx]
        close_before = prices[code].iloc[idx - lookback_days]
        if close_before > 0:
            ret = (close_recent / close_before - 1)
            if ret < crash_pct:
                crash_count += 1

    if crash_count >= min_markets:
        return True, True
    return False, False


# ─── 3. 流动性恶化检测 ─────────────────────────────────────

def detect_liquidity_collapse(
    volumes: pd.DataFrame, date: pd.Timestamp,
    vol_window: int = 20, collapse_ratio: float = 0.2,
) -> dict[str, bool]:
    """
    检测 ETF 成交量是否严重萎缩。

    条件：最近 3 日均量 < collapse_ratio × 过去 vol_window 日均量。

    Returns:
        {code: True/False} 每只 ETF 是否被暂停（True = 流动性不足）
    """
    if volumes.empty or date not in volumes.index:
        return {}

    idx = volumes.index.get_loc(date)
    if idx < vol_window + 3:
        return {}

    suspended: dict[str, bool] = {}

    for code in volumes.columns:
        if code not in volumes.columns:
            continue
        vol_col = volumes[code].iloc[max(0, idx - vol_window):idx + 1]
        if vol_col.mean() == 0:
            continue
        recent_avg = vol_col.iloc[-3:].mean()
        hist_avg = vol_col.iloc[:-3].mean()
        if hist_avg > 0 and recent_avg / hist_avg < collapse_ratio:
            suspended[code] = True

    return suspended


# ─── 综合响应 ──────────────────────────────────────────────

def evaluate_black_swan(
    prices: pd.DataFrame,
    volumes: pd.DataFrame | None,
    date: pd.Timestamp,
) -> dict:
    """
    综合评估当前是否处于黑天鹅状态。

    Returns:
        {
            "panic": bool,         # 是否触发恐慌模式
            "risk_mult": float,    # 全局仓位倍率
            "force_defense": bool, # 是否强制防御
            "suspended": set[str], # 暂停交易的 ETF 代码
            "vix_spike": bool,
            "global_crash": bool,
            "liquidity_halt": list[str],
        }
    """
    vix_spike, panic_mult = detect_vix_spike(prices, date)
    global_crash, force_def = detect_global_crash(prices, date)

    suspended = {}
    if volumes is not None:
        suspended = detect_liquidity_collapse(volumes, date)

    # 综合判断
    # 全球崩盘优先级最高 → 强制防御
    if global_crash:
        return {
            "panic": True,
            "risk_mult": 0.0,       # 0 = 完全降仓
            "force_defense": True,
            "suspended": set(),
            "vix_spike": vix_spike,
            "global_crash": True,
            "liquidity_halt": list(suspended.keys()),
        }

    # VIX 飙升 → 大幅降仓但未必全部防御
    if vix_spike:
        return {
            "panic": True,
            "risk_mult": panic_mult,
            "force_defense": panic_mult < 0.4,
            "suspended": set(suspended.keys()),
            "vix_spike": True,
            "global_crash": False,
            "liquidity_halt": list(suspended.keys()),
        }

    # 仅流动性问题 → 暂停个别 ETF
    if suspended:
        return {
            "panic": False,
            "risk_mult": 1.0,
            "force_defense": False,
            "suspended": set(suspended.keys()),
            "vix_spike": False,
            "global_crash": False,
            "liquidity_halt": list(suspended.keys()),
        }

    return {
        "panic": False,
        "risk_mult": 1.0,
        "force_defense": False,
        "suspended": set(),
        "vix_spike": False,
        "global_crash": False,
        "liquidity_halt": [],
    }
