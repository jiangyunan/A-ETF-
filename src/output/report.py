"""
输出模块：生成净值曲线对比图和 Excel 持仓明细报告。

输出文件：
  - output/equity_curve.png   净值对比图
  - output/trade_details.xlsx  交易明细 + 绩效汇总
"""

import os

import matplotlib.pyplot as plt
import pandas as pd

# 设置中文字体，避免图表中文乱码
plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "SimHei", "WenQuanYi Micro Hei"]
plt.rcParams["axes.unicode_minus"] = False  # 解决负号显示问题


def plot_equity_curve(
    strategy_nav: pd.Series,
    benchmark_nav: pd.Series,
    save_path: str = "output/equity_curve.png",
) -> None:
    """
    绘制策略 vs 基准的净值对比曲线。

    Args:
        strategy_nav: 策略净值序列
        benchmark_nav: 基准净值序列
        save_path: 图片保存路径
    """
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    fig, ax = plt.subplots(figsize=(14, 6))

    # 对齐两条曲线到共同的日期范围
    common = strategy_nav.dropna().index.intersection(benchmark_nav.dropna().index)

    ax.plot(strategy_nav.loc[common], label="Strategy (Momentum Rotation)", linewidth=1.5, color="#1f77b4")
    ax.plot(benchmark_nav.loc[common], label="Benchmark (CSI 300)", linewidth=1.2, color="#d62728", alpha=0.8)

    # 填充策略 vs 基准之间的差异区域
    ax.fill_between(
        strategy_nav.loc[common].index,
        strategy_nav.loc[common].values,
        benchmark_nav.loc[common].values,
        where=(strategy_nav.loc[common].values >= benchmark_nav.loc[common].values),
        color="#1f77b4", alpha=0.1, interpolate=True,
    )
    ax.fill_between(
        strategy_nav.loc[common].index,
        strategy_nav.loc[common].values,
        benchmark_nav.loc[common].values,
        where=(strategy_nav.loc[common].values < benchmark_nav.loc[common].values),
        color="#d62728", alpha=0.1, interpolate=True,
    )

    ax.set_title("ETF Momentum Rotation vs. CSI 300 Benchmark", fontsize=14, fontweight="bold")
    ax.set_xlabel("Date")
    ax.set_ylabel("Net Asset Value (NAV)")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.5, alpha=0.5)

    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)
    print(f"[图表] 已保存: {save_path}")


def export_to_excel(
    signals: pd.DataFrame,
    metrics: dict,
    strategy_nav: pd.Series,
    benchmark_nav: pd.Series,
    save_path: str = "output/trade_details.xlsx",
) -> None:
    """
    导出 Excel 报告，包含：
      Sheet1「持仓明细」：每次调仓的日期、代码、名称、动量值
      Sheet2「绩效汇总」：策略 vs 基准的各项指标
      Sheet3「净值序列」：每日策略净值和基准净值

    Args:
        signals: 周度信号表
        metrics: 绩效指标字典
        strategy_nav: 策略净值序列
        benchmark_nav: 基准净值序列
        save_path: Excel 保存路径
    """
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    with pd.ExcelWriter(save_path, engine="openpyxl") as writer:
        # Sheet 1: 持仓明细
        if not signals.empty:
            signals_out = signals.copy()
            signals_out["date"] = signals_out["date"].dt.strftime("%Y-%m-%d")
            signals_out.to_excel(writer, sheet_name="持仓明细", index=False)

        # Sheet 2: 绩效汇总
        metrics_df = pd.DataFrame(
            {"指标": list(metrics.keys()), "数值": list(metrics.values())}
        )
        metrics_df.to_excel(writer, sheet_name="绩效汇总", index=False)

        # Sheet 3: 净值序列
        common = strategy_nav.dropna().index.intersection(benchmark_nav.dropna().index)
        nav_df = pd.DataFrame({
            "日期": common,
            "策略净值": strategy_nav.loc[common].values,
            "基准净值": benchmark_nav.loc[common].values,
        })
        nav_df.to_excel(writer, sheet_name="净值序列", index=False)

    print(f"[Excel] 已保存: {save_path}")
