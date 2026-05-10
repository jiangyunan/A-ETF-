"""
交易来源拆解：每周调仓的触发原因分析。

回答核心问题：交易到底从哪来？
  - ETF 更换     → 动量排名变化
  - 权重微调     → 波动率控仓调整
  - 状态切换     → 市场状态变更
  - 波动率降仓   → 极端波动触发
  - 黑天鹅       → 全球崩盘强制防御
  - 最小阈值跳过 → 被 10% 阈值过滤

输出：
  output/trade_source_report.xlsx — 逐次调仓原因明细
  控制台 — 分类占比 + 趋势
"""

import os
from collections import Counter

import pandas as pd

from src.config import (
    START_DATE, END_DATE, OUTPUT_DIR, BENCHMARK_CODE,
    MIN_REBALANCE_PCT, POSITION_BUFFER, STATE_SMOOTHING,
)
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_signals


def classify_trade_change(
    new_sig: pd.DataFrame,
    old_sig: pd.DataFrame | None,
    prev_weights: dict[str, float] | None,
) -> str:
    """
    判断本周信号相比上周的变化原因。

    Returns:
        分类标签: ETF更换 | 权重调整 | 状态切换 | 波动率降仓 | 黑天鹅 | 无变化
    """
    if old_sig is None:
        return "初始建仓"

    # 1. 状态切换
    if "state" in new_sig.columns and "state" in old_sig.columns:
        new_st = new_sig["state"].iloc[0]
        old_st = old_sig["state"].iloc[0]
        if new_st != old_st:
            return "状态切换"

    # 2. ETF 更换
    new_codes = set(new_sig["code"].tolist())
    old_codes = set(old_sig["code"].tolist())
    if new_codes != old_codes:
        return "ETF更换"

    # 3. 权重调整
    if prev_weights:
        new_weights = dict(zip(new_sig["code"], new_sig["weight"]))
        for code in new_codes:
            old_w = prev_weights.get(code, 0)
            new_w = new_weights.get(code, 0)
            if abs(new_w - old_w) >= MIN_REBALANCE_PCT:
                return "权重调整"

    # 4. 无变化
    return "无变化（最小阈值跳过）"


def run_trade_source_analysis() -> dict:
    """执行交易来源拆解。"""
    print("=" * 60)
    print("  交易来源拆解（What drives each trade?）")
    print("=" * 60)

    print("\n[1/2] 获取数据 + 生成信号...")
    prices, benchmark = fetch_all_etf_data()
    signals = generate_signals(
        prices, window=20, top_n=5, use_risk_adjusted=True,
        use_market_state_machine=True, use_correlation_filter=True,
        market_ma_window=120, rebalance_freq=1,
        use_vol_target=True, vol_target=0.15,
    )

    # 逐周对比
    print("[2/2] 逐周对比信号...")
    dates = sorted(signals["date"].unique())
    records: list[dict] = []
    prev_weights: dict[str, float] = {}

    for i, dt in enumerate(dates):
        new_sig = signals[signals["date"] == dt]
        old_sig = signals[signals["date"] == dates[i - 1]] if i > 0 else None

        # 跳过被最小阈值过滤的周（权重没变化）
        reason = classify_trade_change(new_sig, old_sig, prev_weights if i > 0 else None)

        # 记录权重用于下次比较
        prev_weights = dict(zip(new_sig["code"], new_sig["weight"]))

        # 黑天鹅检测
        if reason == "状态切换" and new_sig["state"].iloc[0] == "BEAR":
            bm_seq = prices[BENCHMARK_CODE] if BENCHMARK_CODE in prices.columns else None
            if bm_seq is not None and dt in bm_seq.index:
                idx = bm_seq.index.get_loc(dt)
                if idx >= 3:
                    ret = bm_seq.iloc[idx] / bm_seq.iloc[idx - 3] - 1
                    if ret < -0.05:
                        reason = "黑天鹅（全球暴跌）"

        records.append({
            "日期": dt,
            "状态": new_sig["state"].iloc[0] if "state" in new_sig.columns else "?",
            "持仓数": len(new_sig),
            "持仓ETF": ", ".join(new_sig["code"].tolist()),
            "平均权重": round(new_sig["weight"].mean(), 4),
            "触发原因": reason,
        })

    df = pd.DataFrame(records)
    counts = Counter(r["触发原因"] for r in records)
    total = len(records)

    return {
        "records": df,
        "counts": counts,
        "total": total,
        "signals": signals,
    }


def run_and_export_trade_source() -> None:
    """运行并导出交易来源报告。"""
    result = run_trade_source_analysis()
    df = result["records"]
    counts = result["counts"]
    total = result["total"]

    # 控制台输出
    print(f"\n{'=' * 60}")
    print(f"  交易来源拆解（共 {total} 次调仓周期）")
    print(f"{'=' * 60}")

    bar_max = 30
    order = ["ETF更换", "权重调整", "状态切换", "波动率降仓", "黑天鹅（全球暴跌）",
             "无变化（最小阈值跳过）", "初始建仓"]
    for cat in order:
        cnt = counts.get(cat, 0)
        if cnt == 0:
            continue
        pct = cnt / total * 100
        bar = "█" * int(pct / 100 * bar_max)
        print(f"  {cat:<22} {bar:<{bar_max}} {pct:>5.1f}% ({cnt}次)")

    print(f"\n  💡 {'体重微调' if '权重调整' in counts and counts.get('权重调整', 0) / total > 0.15 else 'ETF更换'} 是最大交易来源")
    if counts.get("无变化（最小阈值跳过）", 0) > 0:
        print(f"  💡 阈值过滤已生效：{counts['无变化（最小阈值跳过）']} 次跳过微调")

    # 导出 Excel
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    xlsx = os.path.join(OUTPUT_DIR, "trade_source_report.xlsx")
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="逐次调仓原因", index=False)
        summary = pd.DataFrame([
            {"原因": cat, "次数": counts.get(cat, 0), "占比": f"{counts.get(cat,0)/total:.1%}"}
            for cat in order
        ])
        summary.to_excel(w, sheet_name="分类汇总", index=False)
    print(f"\n[Excel] {xlsx}")
