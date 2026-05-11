"""
盘前数据检查：信号生成前自动运行 7 项检查。

检查项:
  1. 数据完整性  — 所有 ETF 数据已更新到最新交易日
  2. 停牌检测    — 成交量=0 或振幅=0
  3. 流动性不足  — 日成交额 < 3000 万
  4. 溢价异常    — 单只 ETF 溢价 > 8%
  5. 数据跳变    — 涨跌幅绝对值 > 20%
  6. 买卖价差    — 买一卖一价差 > 0.5%
  7. 数据断更    — 最新数据 > 3 天

输出:
  - 控制台: 逐项 PASS/FAIL/WARN
  - ops/preflight 表: 详细记录
"""

from datetime import datetime

import numpy as np

from src.ops.db import insert_preflight, get_latest_preflight
from src.config import ETF_POOL


def run_preflight(
    prices,
    codes: list[str] | None = None,
    spot_data: dict | None = None,
) -> list[dict]:
    """
    执行盘前检查。

    Args:
        prices: 日线收盘价宽表 (DataFrame, 列=ETF代码, 行=日期)
        codes: 要检查的 ETF 列表 (默认=ETF_POOL全部)
        spot_data: 实时行情快照 (dict, key=code, value={price, volume, bid, ask})
    """
    if codes is None:
        codes = list(ETF_POOL.keys())

    # 确保 codes 都在 prices 中
    codes = [c for c in codes if c in prices.columns]
    if not codes:
        return [{"check": "数据完整性", "status": "FAIL", "detail": "无可用 ETF"}]

    latest_date = prices.index[-1]
    results: list[dict] = []

    # ── 1. 数据完整性 ──
    missing = [c for c in codes if prices[c].dropna().empty]
    if missing:
        results.append({"check": "数据完整性", "status": "FAIL",
                        "detail": f"缺失: {', '.join(missing[:5])}"})
    else:
        results.append({"check": "数据完整性", "status": "PASS",
                        "detail": f"全部 {len(codes)} 只 ETF 数据正常"})

    # ── 2. 停牌 & 流动性 & 跳变 (per-ETF) ──
    for code in codes:
        series = prices[code]
        if series.empty:
            continue

        # 最近 3 天数据
        recent = series.iloc[-5:]
        if len(recent) < 2:
            continue

        # 停牌检测 (成交量=0 → 价格不变 → 振幅=0)
        if recent.iloc[-1] == recent.iloc[-2]:
            results.append({"check": f"停牌 {code}", "status": "FAIL",
                            "detail": f"疑似停牌（连续价格不变）"})

        # 数据跳变
        chg = recent.pct_change().iloc[-1]
        if abs(chg) > 0.20:
            results.append({"check": f"跳变 {code}", "status": "WARN",
                            "detail": f"涨跌幅 {chg:.1%} > 20%"})

    # ── 3. 流动性 (from spot_data) ──
    if spot_data:
        for code in codes:
            if code not in spot_data:
                continue
            sd = spot_data[code]
            vol_m = sd.get("volume", 0) * sd.get("price", 0) / 1e6  # 百万
            if vol_m < 30:  # < 3000 万
                results.append({"check": f"流动性 {code}", "status": "FAIL",
                                "detail": f"日成交额 {vol_m:.1f}M < 30M"})
            elif vol_m < 50:
                results.append({"check": f"流动性 {code}", "status": "WARN",
                                "detail": f"日成交额 {vol_m:.1f}M 偏低"})

    # ── 4. 溢价异常 (from spot_data) ──
    if spot_data:
        for code in codes:
            if code not in spot_data:
                continue
            prem = abs(spot_data[code].get("premium", 0))
            if prem > 0.08:
                results.append({"check": f"溢价 {code}", "status": "FAIL",
                                "detail": f"溢价 {prem:.1%} > 8%，禁止买入"})
            elif prem > 0.06:
                results.append({"check": f"溢价 {code}", "status": "WARN",
                                "detail": f"溢价 {prem:.1%} > 6%"})

    # ── 5. 买卖价差 (from spot_data) ──
    if spot_data:
        for code in codes:
            if code not in spot_data:
                continue
            sd = spot_data[code]
            bid, ask = sd.get("bid", 0), sd.get("ask", 0)
            if ask > 0 and bid > 0:
                spread = (ask - bid) / ask
                if spread > 0.005:
                    results.append({"check": f"价差 {code}", "status": "WARN",
                                    "detail": f"买卖价差 {spread:.2%} > 0.5%"})

    # ── 6. 数据断更 ──
    days_behind = (datetime.now() - latest_date.to_pydatetime()).days
    if days_behind > 3:
        results.append({"check": "数据断更", "status": "FAIL",
                        "detail": f"最新数据 {latest_date.date()}（{days_behind} 天前）"})
    elif days_behind > 1:
        results.append({"check": "数据断更", "status": "WARN",
                        "detail": f"最新数据 {days_behind} 天前"})
    else:
        results.append({"check": "数据断更", "status": "PASS",
                        "detail": f"数据更新至 {latest_date.date()}"})

    # ── 7. 整体 PASS 判定 ──
    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    if fail_count > 0:
        results.append({"check": "整体判定", "status": "FAIL",
                        "detail": f"{fail_count} 项检查失败，建议停止运行"})
    else:
        results.append({"check": "整体判定", "status": "PASS",
                        "detail": "所有检查通过"})

    return results


def run_and_log_preflight(prices, spot_data=None) -> bool:
    """运行盘前检查并写入数据库。Returns: True=可继续, False=应停止。"""
    results = run_preflight(prices, spot_data=spot_data)
    today = datetime.now().strftime("%Y-%m-%d")
    insert_preflight(today, results)

    # 控制台输出
    print(f"\n{'=' * 60}")
    print(f"  盘前检查 — {today}")
    print(f"{'=' * 60}")
    fail_count = 0
    for r in results:
        icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}.get(r["status"], "?")
        print(f"  {icon} {r['check']:<20} {r.get('detail', '')}")
        if r["status"] == "FAIL":
            fail_count += 1
    print(f"{'=' * 60}")

    if fail_count > 0:
        print(f"\n  ❌ {fail_count} 项检查失败，建议停止运行。")
        return False
    else:
        print(f"\n  ✅ 所有检查通过，可以继续。")
        return True
