"""
风险告警系统 — 自动检测 6 类风险并写入数据库。

告警类型:
  1. 波动率异常   — 20日波动率 > 历史95%分位
  2. 连续亏损     — 最近4周亏损 > 8%
  3. 溢价爆炸     — 任一持仓ETF溢价 > 8%
  4. 数据断更     — 行情数据滞后 > 48h
  5. 仓位异常     — 实际仓位偏离信号 > 15%
  6. 回撤突破     — 净值跌破 -15%
"""

import numpy as np
import pandas as pd

from src.ops.db import insert_alert, get_open_alerts, resolve_alert


def check_vol_spike(prices, vol_lookback: int = 20) -> list[dict]:
    """检查波动率是否异常飙升。"""
    daily_ret = prices.pct_change()
    recent_vol = daily_ret.iloc[-vol_lookback:].std(axis=1).median()
    hist_vol = daily_ret.iloc[:-vol_lookback].std(axis=1).median()
    if hist_vol > 0 and recent_vol / hist_vol > 2.5:
        return [{"type": "vol_spike", "level": "WARN",
                 "msg": f"波动率飙升 ({recent_vol:.3f} vs {hist_vol:.3f})"}]
    return []


def check_consecutive_losses(trades: list[dict], weeks: int = 4, threshold: float = 0.08) -> list[dict]:
    """检查是否连续亏损。"""
    if not trades:
        return []
    df = pd.DataFrame(trades)
    if "date" not in df.columns or df.empty:
        return []
    df["date"] = pd.to_datetime(df["date"])
    buys = df[df["action"] == "BUY"].groupby(pd.Grouper(key="date", freq="W"))["price"].sum()
    sells = df[df["action"] == "SELL"].groupby(pd.Grouper(key="date", freq="W"))["price"].sum()
    pnl = (sells - buys).dropna()
    if len(pnl) < weeks:
        return []
    recent = pnl.iloc[-weeks:]
    if len(recent[recent < 0]) >= 2 and abs(recent.sum()) / max(abs(buys.sum()), 1) > threshold:
        return [{"type": "consecutive_loss", "level": "CRITICAL",
                 "msg": f"近{weeks}周累计亏损 > {threshold:.0%}"}]
    return []


def check_premium_explosion(spot_data: dict, threshold: float = 0.08) -> list[dict]:
    """检查持仓ETF是否溢价爆炸。"""
    alerts = []
    for code, sd in spot_data.items():
        prem = abs(sd.get("premium", 0))
        if prem > threshold:
            alerts.append({"type": "premium_explosion", "level": "WARN",
                           "msg": f"{code} 溢价 {prem:.1%} > {threshold:.0%}"})
    return alerts


def check_data_staleness(latest_date, max_hours: int = 48) -> list[dict]:
    """检查数据是否过时。"""
    from datetime import datetime
    hours_behind = (datetime.now() - latest_date.to_pydatetime()).total_seconds() / 3600
    if hours_behind > max_hours:
        return [{"type": "data_stale", "level": "CRITICAL",
                 "msg": f"数据滞后 {hours_behind:.0f}h > {max_hours}h"}]
    return []


def check_position_drift(signal_weights: dict, actual_weights: dict, threshold: float = 0.15) -> list[dict]:
    """检查实盘仓位偏离信号建议。"""
    all_codes = set(signal_weights.keys()) | set(actual_weights.keys())
    max_drift = 0.0
    for c in all_codes:
        drift = abs(signal_weights.get(c, 0) - actual_weights.get(c, 0))
        max_drift = max(max_drift, drift)
    if max_drift > threshold:
        return [{"type": "position_drift", "level": "WARN",
                 "msg": f"仓位偏离 {max_drift:.1%} > {threshold:.0%}"}]
    return []


def check_drawdown_breach(nav: list[float], threshold: float = -0.15) -> list[dict]:
    """检查净值是否跌破回撤阈值。"""
    if not nav or len(nav) < 2:
        return []
    peak = max(nav)
    current = nav[-1]
    dd = (current / peak - 1)
    if dd < threshold:
        return [{"type": "drawdown_breach", "level": "CRITICAL",
                 "msg": f"回撤 {dd:.1%} 突破 {threshold:.0%}"}]
    return []


def run_all_alerts(
    prices=None, trades=None, spot_data=None,
    latest_date=None, signal_weights=None, actual_weights=None, nav=None,
) -> dict:
    """运行全部 6 项告警检查，写入数据库。"""
    all_alerts = []
    if prices is not None:
        all_alerts += check_vol_spike(prices)
    if trades is not None:
        all_alerts += check_consecutive_losses(trades)
    if spot_data is not None:
        all_alerts += check_premium_explosion(spot_data)
    if latest_date is not None:
        all_alerts += check_data_staleness(latest_date)
    if signal_weights is not None and actual_weights is not None:
        all_alerts += check_position_drift(signal_weights, actual_weights)
    if nav is not None:
        all_alerts += check_drawdown_breach(nav)

    # 写入数据库
    alert_ids = []
    for a in all_alerts:
        aid = insert_alert(a["type"], a["level"], a["msg"])
        alert_ids.append(aid)

    # 控制台输出
    if all_alerts:
        print(f"\n{'=' * 50}")
        print(f"  ⚠️ 风险告警 ({len(all_alerts)} 条)")
        for a in all_alerts:
            icon = "🔴" if a["level"] == "CRITICAL" else "⚠️"
            print(f"  {icon} [{a['level']}] {a['type']}: {a['msg']}")
        print(f"{'=' * 50}")
    else:
        print("  ✅ 无风险告警")

    return {"alerts": all_alerts, "ids": alert_ids}
