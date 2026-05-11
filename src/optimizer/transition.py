"""
Transition Analytics：测量状态转移质量。

统计每次 BEAR→RECOVERY→(ACTIVE or BEAR) 的过渡：

关键指标：
  - 持续时间：RECOVERY 态停留周期数
  - 结果：晋升 ACTIVE / 退回 BEAR
  - 5日/20日收益：短期冲击 vs 真实趋势质量
  - MFE/MAE：最大浮盈/浮亏
  - 假突破率：RECOVERY→BEAR 比例
  - 晋升延迟：RECOVERY→ACTIVE 耗时（Promotion Lag）
"""

import os

import numpy as np
import pandas as pd

from src.config import OUTPUT_DIR
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_signals
from src.backtest.engine import run_backtest


def run_transition_analytics() -> dict:
    """运行 Transition Analytics。"""
    print("=" * 60)
    print("  Transition Analytics（状态转移质量分析）")
    print("=" * 60)

    prices, benchmark = fetch_all_etf_data()
    signals = generate_signals(
        prices, window=20, top_n=5, use_risk_adjusted=True,
        use_market_state_machine=True, use_correlation_filter=True,
        market_ma_window=120, rebalance_freq=1,
        use_vol_target=True, vol_target=0.15,
    )
    result = run_backtest(prices, signals, benchmark)
    nav = result["nav"]

    # 识别 BEAR→RECOVERY→(BULL/SIDEWAYS or BEAR) 序列
    states = signals[["date", "state"]].drop_duplicates().sort_values("date")
    events: list[dict] = []
    in_recovery = False
    rec_start = None
    rec_start_nav = 1.0

    for i, (_, row) in enumerate(states.iterrows()):
        dt = row["date"]
        st = row["state"]

        if not in_recovery and st == "RECOVERY":
            in_recovery = True
            rec_start = dt
            if dt in nav.index:
                rec_start_nav = nav.loc[dt]
        elif in_recovery and st != "RECOVERY":
            # RECOVERY 结束
            end_nav = nav.loc[dt] if dt in nav.index else rec_start_nav
            n_rec = len(states[(states["date"] >= rec_start) & (states["date"] < dt)])

            # 恢复期内 NAV 切片
            sub_nav = nav[rec_start:dt]
            peak = sub_nav.cummax()
            mfe = (peak.iloc[-1] / rec_start_nav - 1) if len(sub_nav) > 0 else 0
            mae = (sub_nav.iloc[-1] / rec_start_nav - 1) if len(sub_nav) > 0 else 0  # simple MAE

            # 5日和20日收益
            ret_5d = 0.0
            ret_20d = 0.0
            if dt in nav.index:
                idx = nav.index.get_loc(dt)
                if idx >= 5:
                    ret_5d = nav.iloc[idx] / nav.iloc[idx - 5] - 1
                if idx >= 20:
                    ret_20d = nav.iloc[idx] / nav.iloc[idx - 20] - 1

            events.append({
                "recovery_start": rec_start,
                "recovery_end": dt,
                "duration_weeks": n_rec,
                "result": st,
                "success": st in ("BULL", "SIDEWAYS"),
                "ret_5d": ret_5d,
                "ret_20d": ret_20d,
                "start_nav": rec_start_nav,
                "end_nav": end_nav,
                "total_return": end_nav / rec_start_nav - 1,
                "promotion_lag": n_rec,
            })
            in_recovery = False

    df = pd.DataFrame(events)
    if df.empty:
        print("\n  无 RECOVERY 事件（历史中未触发恢复态）")
        return {}

    success_rate = df["success"].mean()
    fake_breakout_rate = (df["result"] == "BEAR").mean()
    avg_duration = df["duration_weeks"].mean()
    avg_ret_20d = df["ret_20d"].mean()
    avg_promotion_lag = df[df["success"]]["duration_weeks"].mean() if df["success"].any() else 0

    # 输出
    print(f"\n{'=' * 60}")
    print(f"  TRANSITION 统计（{len(df)} 次 BEAR→RECOVERY 事件）")
    print(f"{'=' * 60}")
    print(f"  成功率 (RECOVERY→ACTIVE):   {success_rate:.0%}")
    print(f"  假突破率 (RECOVERY→BEAR):   {fake_breakout_rate:.0%}")
    print(f"  平均恢复周期:                {avg_duration:.1f} 周")
    print(f"  晋升平均延迟 (Promotion Lag): {avg_promotion_lag:.1f} 周")
    print(f"  恢复后20日平均收益:           {avg_ret_20d:.2%}")
    print(f"  恢复期平均总收益:             {df['total_return'].mean():.2%}")

    print(f"\n  最近 5 次 RECOVERY 事件:")
    for _, e in df.tail(5).iterrows():
        icon = "✅" if e["success"] else "❌"
        print(f"    {e['recovery_start'].date()} → {e['recovery_end'].date()}  "
              f"{e['duration_weeks']}周 → {e['result']:10}  "
              f"20d={e['ret_20d']:.1%}  total={e['total_return']:.1%} {icon}")

    # 导出
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    xlsx = os.path.join(OUTPUT_DIR, "transition_analytics.xlsx")
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="RECOVERY事件", index=False)
        summary = pd.DataFrame({
            "指标": ["成功率", "假突破率", "平均周期", "晋升延迟", "20日平均收益", "事件总数"],
            "值": [f"{success_rate:.0%}", f"{fake_breakout_rate:.0%}",
                  f"{avg_duration:.1f}周", f"{avg_promotion_lag:.1f}周",
                  f"{avg_ret_20d:.2%}", str(len(df))],
        })
        summary.to_excel(w, sheet_name="Transition摘要", index=False)
    print(f"\n[Excel] {xlsx}")

    return {"events": df, "success_rate": success_rate, "fake_breakout_rate": fake_breakout_rate}
