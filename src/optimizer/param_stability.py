"""
参数稳定性验证：检验策略在参数小幅变化时是否仍然盈利。

如果只有精确的一组合适参数能赚钱，说明过拟合了历史噪音。
如果一大片参数区域都能稳定盈利，说明策略的底层逻辑是可靠的。

输出：
  1. 控制台：各参数维度的稳定性摘要
  2. Excel：param_stability_heatmaps.xlsx（多张热力图）
"""

import os
from itertools import product

import numpy as np
import pandas as pd

from src.config import START_DATE, END_DATE, OUTPUT_DIR
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_signals
from src.backtest.engine import run_backtest


def run_stability_test(
    prices: pd.DataFrame,
    benchmark: pd.Series,
) -> dict:
    """
    在最佳参数附近扫描每个维度的灵敏度。

    固定其他参数为最优值，单独变化一个维度，观察夏普和回撤的变化。
    """

    # 锚定参数 (V4 最优附近)
    base = {
        "window": 20, "top_n": 5, "use_risk_adjusted": True,
        "use_composite_momentum": False, "use_dynamic_position": False,
        "use_relative_strength": False,
        "market_ma_window": 120, "rebalance_freq": 1,
        "use_vol_target": True, "vol_target": 0.15,
    }

    # ---- 各维度扫描 ----
    scans = {
        "动量窗口": {"window": [5, 10, 15, 20, 30, 40, 60]},
        "持仓数": {"top_n": [1, 2, 3, 5, 7, 10]},
        "大盘择时MA": {"market_ma_window": [0, 40, 60, 80, 100, 120, 150, 200]},
        "调仓频率": {"rebalance_freq": [1, 2, 3, 4]},
        "波动率目标": {"vol_target": [0.05, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25]},
        "复合动量开关": {"use_composite_momentum": [False, True]},
        "动态仓位开关": {"use_dynamic_position": [False, True]},
        "强弱过滤开关": {"use_relative_strength": [False, True]},
    }

    results: dict[str, pd.DataFrame] = {}
    summaries: list[dict] = []

    for dim_name, dim_scan in scans.items():
        param_name = list(dim_scan.keys())[0]
        values = dim_scan[param_name]

        rows = []
        for val in values:
            params = base.copy()
            params[param_name] = val

            signals = generate_signals(prices, **params)
            if signals.empty:
                continue
            r = run_backtest(prices, signals, benchmark)
            m = r["metrics"]
            sharpe = float(m.get("夏普比率", "0"))
            ann = float(m.get("年化收益率", "0%").strip("%")) / 100
            dd = float(m.get("最大回撤", "0%").strip("%")) / 100
            rows.append({param_name: val, "夏普": sharpe, "年化": ann, "回撤": dd})

        df = pd.DataFrame(rows).sort_values(param_name)
        results[dim_name] = df

        # 稳定性评分 = 最低夏普/最高夏普 + 盈利比例
        if len(df) > 0 and df["夏普"].max() > 0:
            stability = df["夏普"].min() / df["夏普"].max() if df["夏普"].max() != 0 else 0
            win_pct = (df["夏普"] > 0).mean()
        else:
            stability, win_pct = 0, 0
        summaries.append({
            "参数维度": dim_name,
            "夏普范围": f"{df['夏普'].min():.2f} ~ {df['夏普'].max():.2f}",
            "回撤范围": f"{df['回撤'].min():.1%} ~ {df['回撤'].max():.1%}",
            "夏普稳定性": f"{stability:.2f}",
            "盈利比例": f"{win_pct:.0%}",
        })

    return {
        "results": results,
        "summaries": pd.DataFrame(summaries),
    }


def run_and_export_stability() -> None:
    """运行参数稳定性验证并输出结果。"""
    print("=" * 60)
    print("  参数稳定性验证（Parameter Stability Test）")
    print("=" * 60)

    print(f"\n[1/2] 获取数据...")
    prices, benchmark = fetch_all_etf_data()

    print(f"[2/2] 扫描各参数维度...")
    result = run_stability_test(prices, benchmark)

    # 控制台输出
    print(f"\n{'=' * 70}")
    print(f"  稳定性摘要（数字越接近 1.0 意味着参数越不敏感）")
    print(f"{'=' * 70}")
    print(result["summaries"].to_string(index=False))

    # 导出 Excel
    excel_path = os.path.join(OUTPUT_DIR, "param_stability.xlsx")
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        result["summaries"].to_excel(writer, sheet_name="稳定性摘要", index=False)
        for dim_name, df in result["results"].items():
            sheet_name = dim_name[:31]  # Excel sheet name limit
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    print(f"\n[Excel] 已保存: {excel_path}")

    # 高亮不稳定维度
    unstable = result["summaries"]
    low_stability = unstable[
        unstable["夏普稳定性"].apply(lambda x: float(x)) < 0.5
    ]
    if len(low_stability) > 0:
        print(f"\n⚠️  敏感参数（稳定性<0.5）:")
        for _, row in low_stability.iterrows():
            print(f"    {row['参数维度']}: 稳定性={row['夏普稳定性']}")
    else:
        print(f"\n✅ 所有参数稳定性良好（≥0.5），策略鲁棒性强。")
