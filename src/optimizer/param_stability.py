"""
参数稳定性验证 V8：二维热力图 + 鲁棒性评分。

核心问题：
  不是找到"最佳点"，而是验证"是否存在大面积稳定盈利区域"。
  如果只有尖峰点赚钱，说明过拟合了历史噪音。
  如果大片区域都不错，说明 Alpha 更可能真实存在。

输出：
  1. 控制台：鲁棒性摘要
  2. PNG 热力图：output/heatmap_*.png（4张）
  3. Excel：output/param_stability_v8.xlsx（全量数据）
"""

import os
from itertools import product

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "SimHei", "WenQuanYi Micro Hei"]
plt.rcParams["axes.unicode_minus"] = False

from src.config import START_DATE, END_DATE, OUTPUT_DIR
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_signals
from src.backtest.engine import run_backtest


# V7 最优参数作为锚
BASE_PARAMS = {
    "window": 20, "top_n": 5, "use_risk_adjusted": True,
    "use_market_state_machine": True, "use_correlation_filter": True,
    "market_ma_window": 120, "rebalance_freq": 1,
    "use_vol_target": True, "vol_target": 0.15,
    "state_bull_window": 10, "state_bull_top_n": 3,
    "state_sideways_window": 20, "state_sideways_top_n": 5,
    "state_bear_window": 40,
    "ma_trend_short": 20, "ma_trend_medium": 60,
    "correlation_window": 60, "correlation_threshold": 0.75,
}

# 二维扫描网格定义
HEATMAPS = [
    {
        "name": "广度阈值 × MA长周期",
        "x_param": "market_ma_window",
        "x_label": "MA 周期",
        "x_values": [80, 100, 120, 140, 160, 200],
        "y_param": "market_ma_window",  # 用广度阈值间接：state_sideways_top_n 代表保守度
        "y_label": "广度阈值",
        "y_values": [0.20, 0.25, 0.30, 0.35, 0.40, 0.45],
        "y_override": "breadth_threshold",
    },
    {
        "name": "持仓数 × 波动率目标",
        "x_param": "vol_target",
        "x_label": "波动率目标",
        "x_values": [0.08, 0.10, 0.12, 0.15, 0.18, 0.20],
        "y_param": "top_n",
        "y_label": "持仓数",
        "y_values": [2, 3, 4, 5, 6],
    },
    {
        "name": "动量窗口 × 调仓频率",
        "x_param": "rebalance_freq",
        "x_label": "调仓频率(周)",
        "x_values": [1, 2],
        "y_param": "window",
        "y_label": "动量窗口(天)",
        "y_values": [10, 15, 20, 30, 40, 60],
    },
    {
        "name": "极端波动阈值 × 相关性阈值",
        "x_param": "correlation_threshold",
        "x_label": "相关性上限",
        "x_values": [0.50, 0.60, 0.70, 0.75, 0.80, 0.90],
        "y_param": "vol_cap",
        "y_label": "仓位上限倍率",
        "y_values": [1.0, 1.25, 1.5, 2.0, 3.0],
        "y_param_name": "vol_cap",
    },
]


def _run_one(prices, benchmark, params: dict) -> dict | None:
    """运行一组参数，返回指标。"""
    try:
        signals = generate_signals(prices, **params)
        if signals.empty:
            return None
        r = run_backtest(prices, signals, benchmark)
        m = r["metrics"]
        return {
            "sharpe": float(m.get("夏普比率", "0")),
            "annual": float(m.get("年化收益率", "0%").strip("%")) / 100,
            "max_dd": float(m.get("最大回撤", "0%").strip("%")) / 100,
        }
    except Exception:
        return None


def _plot_heatmap(data: np.ndarray, x_vals, y_vals, x_label, y_label, title, path):
    """绘制夏普热力图。"""
    fig, ax = plt.subplots(figsize=(10, 6))
    im = ax.imshow(data, aspect="auto", origin="lower", cmap="RdYlGn",
                   vmin=max(data[~np.isnan(data)].min(), -0.5) if np.any(~np.isnan(data)) else -0.5,
                   vmax=data[~np.isnan(data)].max() if np.any(~np.isnan(data)) else 1.5)

    ax.set_xticks(range(len(x_vals)))
    ax.set_xticklabels(x_vals)
    ax.set_yticks(range(len(y_vals)))
    ax.set_yticklabels(y_vals)
    ax.set_xlabel(x_label, fontsize=12)
    ax.set_ylabel(y_label, fontsize=12)
    ax.set_title(title, fontsize=13, fontweight="bold")

    # 标注数值
    for i in range(len(y_vals)):
        for j in range(len(x_vals)):
            val = data[i, j]
            if not np.isnan(val):
                color = "white" if abs(val) > 0.5 else "black"
                ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                        fontsize=9, color=color, fontweight="bold")

    cbar = plt.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Sharpe Ratio", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run_2d_stability(prices: pd.DataFrame, benchmark: pd.Series) -> dict:
    """执行二维参数稳定性扫描。"""
    results = {}

    for hm in HEATMAPS:
        title = hm["name"]
        x_param, x_vals = hm["x_param"], hm["x_values"]
        y_param_or_key, y_vals = hm.get("y_param_name", hm["y_param"]), hm["y_values"]
        x_label, y_label = hm["x_label"], hm["y_label"]
        override_key = hm.get("y_override", None)

        print(f"\n  扫描: {title} ({len(x_vals)}×{len(y_vals)})...")

        grid = np.full((len(y_vals), len(x_vals)), np.nan)
        rows = []

        for yi, yv in enumerate(y_vals):
            for xi, xv in enumerate(x_vals):
                p = BASE_PARAMS.copy()
                p[x_param] = xv

                if override_key == "breadth_threshold":
                    # 广度阈值通过 state 参数间接控制
                    pass
                else:
                    p[y_param_or_key] = yv

                # breadth_threshold 的特殊处理：修改 _classify_market 中的阈值
                # 我们通过 state_sideways_window 控制保守度
                if override_key == "breadth_threshold":
                    # 用 state_bear_window 代表防御激进程度
                    p["state_bear_window"] = int(80 - (yv - 0.2) * 100)
                    p[y_param_or_key] = yv

                metric = _run_one(prices, benchmark, p)
                if metric:
                    grid[yi, xi] = metric["sharpe"]
                    rows.append({
                        x_label: xv, y_label: yv,
                        **metric,
                    })

        results[title] = {
            "grid": grid,
            "x_vals": x_vals, "y_vals": y_vals,
            "x_label": x_label, "y_label": y_label,
            "title": title,
            "rows": pd.DataFrame(rows),
        }

    return results


def run_and_export_stability_v8() -> None:
    """运行 V8 稳定性测试并输出。"""
    print("=" * 60)
    print("  参数稳定性验证 V8（二维热力图）")
    print("=" * 60)

    print(f"\n[1/2] 获取数据...")
    prices, benchmark = fetch_all_etf_data()

    print(f"[2/2] 扫描二维参数网格...")
    result = run_2d_stability(prices, benchmark)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 绘制热力图
    print(f"\n  生成热力图...")
    for key, data in result.items():
        path = os.path.join(OUTPUT_DIR, f"heatmap_{key.replace(' ', '_').replace('×','x')}.png")
        _plot_heatmap(data["grid"], data["x_vals"], data["y_vals"],
                      data["x_label"], data["y_label"], data["title"], path)
        print(f"    {path}")

    # 导出 Excel
    excel_path = os.path.join(OUTPUT_DIR, "param_stability_v8.xlsx")
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        # 摘要
        summary_rows = []
        for key, data in result.items():
            g = data["grid"]
            valid = g[~np.isnan(g)]
            if len(valid) == 0:
                continue
            profitable = (valid > 0).mean()
            high_quality = (valid > 0.8).mean()
            summary_rows.append({
                "热力图": key,
                "总格数": g.size,
                "有效格数": len(valid),
                "夏普均值": f"{valid.mean():.2f}",
                "夏普范围": f"{valid.min():.2f} ~ {valid.max():.2f}",
                "盈利区域比例": f"{profitable:.0%}",
                "优质区域(夏普>0.8)": f"{high_quality:.0%}",
                "尖峰风险": "低" if profitable > 0.6 and high_quality > 0.2 else ("中" if profitable > 0.4 else "高"),
            })
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="稳定性摘要", index=False)

        # 各热力图明细
        for key, data in result.items():
            sheet_name = key[:31]
            data["rows"].to_excel(writer, sheet_name=sheet_name, index=False)

    # 控制台总结
    print(f"\n{'=' * 60}")
    print(f"  鲁棒性摘要")
    print(f"{'=' * 60}")
    for s in summary_rows:
        flag = "✅" if s["盈利区域比例"] in ["60%", "67%", "70%", "75%", "80%", "83%", "86%", "90%", "93%", "97%", "100%"] else "⚠️"
        if float(s["盈利区域比例"].strip("%")) / 100 > 0.5:
            flag = "✅"
        elif float(s["盈利区域比例"].strip("%")) / 100 > 0.3:
            flag = "⚠️"
        else:
            flag = "❌"
        print(f"  {flag} {s['热力图']}: 盈利区={s['盈利区域比例']} 优质区={s['优质区域(夏普>0.8)']} 尖峰={s['尖峰风险']}")

    print(f"\n[Excel] {excel_path}")
    print(f"[PNG]  {OUTPUT_DIR}/heatmap_*.png")
