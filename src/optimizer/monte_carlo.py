"""
蒙特卡洛模拟：检验策略在现实扰动下的生存能力。

强策略即使在恶劣条件下仍能存活。

扰动类型：
  1. 滑点：每次调仓额外扣 0~0.15% 成本（随机）
  2. 延迟成交：信号延后 1~2 个交易日执行
  3. 信号缺失：随机跳过 5%~15% 的调仓周期
  4. 极端冲击：随机插入 -2%~-5% 的单日暴跌
  5. 波动膨胀：随机放大每日波动 1.0~1.5 倍

输出：
  1. 控制台：生存概率、极端分位数
  2. output/monte_carlo_equity_fan.png — 资金曲线扇形图
  3. output/monte_carlo_drawdown_hist.png — 最大回撤分布
  4. output/monte_carlo_results.xlsx — 1000次模拟明细
"""

import os
import time

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "SimHei", "WenQuanYi Micro Hei"]
plt.rcParams["axes.unicode_minus"] = False

from src.config import OUTPUT_DIR
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_signals
from src.backtest.engine import run_backtest

N_SIMULATIONS = 1000
SLIPPAGE_RANGE = (0.0, 0.0015)      # 0~0.15% 每次调仓
DELAY_RANGE = (0, 2)                 # 0~2天延迟
MISS_RATE_RANGE = (0.05, 0.15)      # 5%~15% 信号被跳过
SHOCK_PROB = 0.02                    # 2% 概率发生极端冲击
SHOCK_RANGE = (-0.05, -0.02)        # -2%~-5% 单日暴跌
VOL_MULTIPLIER_RANGE = (1.0, 1.5)   # 波动放大 1~1.5 倍


def _perturb_returns(original_returns: pd.Series, seed: int) -> pd.Series:
    """
    对原始日收益率序列施加随机扰动。

    Returns:
        扰动后的日收益率序列
    """
    rng = np.random.default_rng(seed)
    rets = original_returns.copy()

    # ── 1. 波动膨胀 ──
    vol_mult = rng.uniform(*VOL_MULTIPLIER_RANGE)
    nonzero = rets != 0
    rets.loc[nonzero] = rets.loc[nonzero] * vol_mult

    # ── 2. 极端冲击（随机插入单日暴跌） ──
    n = len(rets)
    n_shocks = int(n * SHOCK_PROB * rng.uniform(0.5, 1.5))
    shock_indices = rng.choice(n, size=max(0, n_shocks), replace=False)
    for idx in shock_indices:
        rets.iloc[idx] = rng.uniform(*SHOCK_RANGE)

    # ── 3. 添加微小噪音 ──
    noise = pd.Series(rng.normal(0, 0.0005, n), index=rets.index)
    rets = rets + noise

    return rets


def _perturb_signals(signals: pd.DataFrame, prices: pd.DataFrame, seed: int) -> pd.DataFrame:
    """
    对信号表施加随机扰动：
      - 信号延迟
      - 信号随机缺失
      - 滑点（下调权重）

    Returns:
        扰动后的信号表
    """
    rng = np.random.default_rng(seed)
    sigs = signals.copy()

    # ── 1. 滑点：每次调仓随机扣成本 ──
    slip = rng.uniform(*SLIPPAGE_RANGE)
    sigs["weight"] = sigs["weight"] * (1 - slip)

    # ── 2. 延迟成交：部分信号的日期延后 ──
    delay_days = rng.integers(*DELAY_RANGE)
    if delay_days > 0 and not sigs.empty:
        delay_mask = rng.random(len(sigs)) < 0.5  # 50% 的信号被延迟
        all_dates = prices.index
        for i in sigs[delay_mask].index:
            dt = sigs.loc[i, "date"]
            if dt in all_dates:
                pos = all_dates.get_loc(dt)
                new_pos = min(pos + delay_days, len(all_dates) - 1)
                sigs.loc[i, "date"] = all_dates[new_pos]

    # ── 3. 信号随机缺失：部分周期空仓 ──
    miss_rate = rng.uniform(*MISS_RATE_RANGE)
    if not sigs.empty:
        unique_dates = sigs["date"].unique()
        miss_dates = rng.choice(unique_dates, size=max(1, int(len(unique_dates) * miss_rate)),
                                replace=False)
        sigs = sigs[~sigs["date"].isin(miss_dates)]

    return sigs


def run_monte_carlo(n_sim: int = N_SIMULATIONS) -> dict:
    """
    执行蒙特卡洛模拟。

    Returns:
        sim_navs: 所有模拟的净值 (DataFrame, columns=sim编号)
        sim_sharpes: 夏普比率列表
        sim_dds: 最大回撤列表
        sim_annuals: 年化收益列表
        baseline_nav: 原始净值序列
        baseline_metrics: 原始指标
    """
    print("=" * 60)
    print(f"  蒙特卡洛模拟（{n_sim} 次）")
    print("=" * 60)

    # ── 获取原始数据 ──
    print("[1/3] 获取数据 + 生成原始信号...")
    prices, benchmark = fetch_all_etf_data()
    original_signals = generate_signals(
        prices,
        window=20, top_n=5, use_risk_adjusted=True,
        use_market_state_machine=True, use_correlation_filter=True,
        market_ma_window=120, rebalance_freq=1,
        use_vol_target=True, vol_target=0.15,
    )
    baseline = run_backtest(prices, original_signals, benchmark)

    original_returns = baseline["daily_returns"]
    baseline_metrics = baseline["metrics"]
    all_dates = original_returns.index

    # ── 逐次模拟 ──
    sim_results = {
        "sharpes": np.zeros(n_sim),
        "annuals": np.zeros(n_sim),
        "max_dds": np.zeros(n_sim),
        "total_returns": np.zeros(n_sim),
    }
    sim_navs = pd.DataFrame(index=all_dates)

    start_time = time.time()

    for i in range(n_sim):
        seed = i * 7 + 1

        # 1. 扰动收益
        pert_rets = _perturb_returns(original_returns, seed)

        # 2. 扰动信号
        pert_sigs = _perturb_signals(original_signals.copy(), prices, seed + 1)

        # 3. 计算净值
        nav = (1 + pert_rets).cumprod()
        nav.iloc[0] = 1.0

        # 4. 重新跑回测（跳过信号生成，直接用扰动后的收益+信号）
        result = run_backtest(prices, pert_sigs, baseline["benchmark_nav"])
        nav = result["nav"]
        rets = result["daily_returns"]

        sim_navs[i] = nav

        # 5. 统计
        sim_results["sharpes"][i] = float(result["metrics"].get("夏普比率", "0"))
        sim_results["annuals"][i] = float(result["metrics"].get("年化收益率", "0%").strip("%")) / 100
        sim_results["max_dds"][i] = float(result["metrics"].get("最大回撤", "0%").strip("%")) / 100
        sim_results["total_returns"][i] = nav.iloc[-1] / nav.iloc[0] - 1

        if (i + 1) % 200 == 0:
            elapsed = time.time() - start_time
            print(f"    进度: {i + 1}/{n_sim} ({elapsed:.1f}s)")

    elapsed = time.time() - start_time
    print(f"  完成！耗时 {elapsed:.1f}s")

    return {
        "sim_navs": sim_navs,
        "sim_sharpes": sim_results["sharpes"],
        "sim_annuals": sim_results["annuals"],
        "sim_dds": sim_results["max_dds"],
        "sim_total_returns": sim_results["total_returns"],
        "baseline_nav": baseline["nav"],
        "baseline_benchmark_nav": baseline["benchmark_nav"],
        "baseline_metrics": baseline_metrics,
    }


def _plot_fan_chart(sim_navs: pd.DataFrame, baseline_nav: pd.Series,
                    benchmark_nav: pd.Series, path: str):
    """绘制资金曲线扇形图（中位数 + 5/25/75/95 分位数）。"""
    fig, ax = plt.subplots(figsize=(14, 6))

    # 分位数
    p5 = sim_navs.quantile(0.05, axis=1)
    p25 = sim_navs.quantile(0.25, axis=1)
    p50 = sim_navs.quantile(0.50, axis=1)
    p75 = sim_navs.quantile(0.75, axis=1)
    p95 = sim_navs.quantile(0.95, axis=1)

    common = p50.dropna().index.intersection(benchmark_nav.dropna().index)

    ax.fill_between(common, p5.loc[common], p95.loc[common],
                    alpha=0.1, color="#1f77b4", label="5%~95%")
    ax.fill_between(common, p25.loc[common], p75.loc[common],
                    alpha=0.15, color="#1f77b4", label="25%~75%")
    ax.plot(common, p50.loc[common], linewidth=1.5, color="#1f77b4",
            label="Median (MC)")
    ax.plot(common, baseline_nav.loc[common], linewidth=1.2, color="#2ca02c",
            linestyle="--", label="Baseline (No Perturb)")
    ax.plot(common, benchmark_nav.loc[common], linewidth=1.0, color="#d62728",
            alpha=0.6, label="CSI 300")

    ax.set_title("Monte Carlo Simulation — Equity Curve Fan", fontsize=14, fontweight="bold")
    ax.set_xlabel("Date")
    ax.set_ylabel("NAV")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.5, alpha=0.5)

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_drawdown_hist(sim_dds: np.ndarray, path: str):
    """绘制最大回撤分布直方图。"""
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(sim_dds * 100, bins=40, color="#1f77b4", alpha=0.7, edgecolor="white")
    ax.axvline(np.median(sim_dds) * 100, color="#d62728", linestyle="--",
               linewidth=2, label=f"Median: {np.median(sim_dds):.1%}")
    ax.axvline(np.percentile(sim_dds, 95) * 100, color="orange", linestyle="--",
               linewidth=2, label=f"95%ile: {np.percentile(sim_dds, 95):.1%}")

    ax.set_title("Monte Carlo — Max Drawdown Distribution", fontsize=13, fontweight="bold")
    ax.set_xlabel("Max Drawdown (%)")
    ax.set_ylabel("Frequency")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run_and_export_monte_carlo(n_sim: int = N_SIMULATIONS) -> None:
    """运行蒙特卡洛并导出所有报告。"""
    result = run_monte_carlo(n_sim)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── 统计摘要 ──
    sharpe = result["sim_sharpes"]
    annual = result["sim_annuals"]
    dd = result["sim_dds"]
    total_ret = result["sim_total_returns"]

    print(f"\n{'=' * 60}")
    print(f"  生存概率摘要")
    print(f"{'=' * 60}")
    print(f"  夏普比率:     中位={np.median(sharpe):.2f}  5%={np.percentile(sharpe,5):.2f}  95%={np.percentile(sharpe,95):.2f}")
    print(f"  年化收益:     中位={np.median(annual):.1%}  5%={np.percentile(annual,5):.1%}  95%={np.percentile(annual,95):.1%}")
    print(f"  最大回撤:     中位={np.median(dd):.1%}  5%={np.percentile(dd,5):.1%}  95%={np.percentile(dd,95):.1%}")
    print(f"  累计收益:     中位={np.median(total_ret):.1%}  5%={np.percentile(total_ret,5):.1%}")

    # 生存判断
    annual_positive = (annual > 0).mean()
    sharpe_ok = (sharpe > 0.3).mean()
    dd_ok = (dd > -0.25).mean()

    print(f"\n  年化>0:     {annual_positive:.0%}")
    print(f"  夏普>0.3:   {sharpe_ok:.0%}")
    print(f"  回撤<25%:   {dd_ok:.0%}")
    if annual_positive > 0.9 and sharpe_ok > 0.8 and dd_ok > 0.7:
        print(f"\n  ✅ 策略在扰动下高度稳健，可考虑实盘验证")
    elif annual_positive > 0.8:
        print(f"\n  ⚠️ 策略基本稳健，极端条件下需注意风险")
    else:
        print(f"\n  ❌ 策略在扰动下不稳定，不建议实盘")

    # ── 图表 ──
    print(f"\n  生成图表...")
    fan_path = os.path.join(OUTPUT_DIR, "monte_carlo_equity_fan.png")
    _plot_fan_chart(result["sim_navs"], result["baseline_nav"],
                    result["baseline_benchmark_nav"], fan_path)
    print(f"    {fan_path}")

    dd_path = os.path.join(OUTPUT_DIR, "monte_carlo_drawdown_hist.png")
    _plot_drawdown_hist(result["sim_dds"], dd_path)
    print(f"    {dd_path}")

    # ── Excel ──
    excel_path = os.path.join(OUTPUT_DIR, "monte_carlo_results.xlsx")
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        summary = pd.DataFrame({
            "指标": ["夏普中位数", "夏普5%分位", "夏普95%分位",
                    "年化中位数", "年化5%分位", "年化95%分位",
                    "回撤中位数", "回撤95%分位",
                    "累计收益中位数", "累计收益5%分位",
                    "年化>0概率", "夏普>0.3概率", "回撤<25%概率"],
            "数值": [f"{np.median(sharpe):.2f}", f"{np.percentile(sharpe,5):.2f}",
                    f"{np.percentile(sharpe,95):.2f}",
                    f"{np.median(annual):.1%}", f"{np.percentile(annual,5):.1%}",
                    f"{np.percentile(annual,95):.1%}",
                    f"{np.median(dd):.1%}", f"{np.percentile(dd,95):.1%}",
                    f"{np.median(total_ret):.1%}", f"{np.percentile(total_ret,5):.1%}",
                    f"{annual_positive:.0%}", f"{sharpe_ok:.0%}", f"{dd_ok:.0%}"],
        })
        summary.to_excel(writer, sheet_name="蒙特卡洛摘要", index=False)
    print(f"    {excel_path}")
