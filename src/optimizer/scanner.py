"""
参数扫描引擎：网格搜索动量窗口、持仓数、风险调整、趋势过滤的最优组合。

优化目标：最大化夏普比率。

搜索空间（80 组）：
  动量窗口      [5, 10, 20, 40, 60]
  持仓数        [1, 2, 3, 5]
  风险调整      [开, 关]
  趋势过滤      [开, 关]

输出：
  1. 控制台打印 Top 10 最优组合
  2. Excel 全量对比表（output/optimization_results.xlsx）
  3. 最优参数的净值曲线图（output/equity_curve.png）
"""

import os
import time
from itertools import product

import numpy as np
import pandas as pd

from src.config import START_DATE, END_DATE, OUTPUT_DIR
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_weekly_signals
from src.backtest.engine import run_backtest
from src.output.report import plot_equity_curve, export_to_excel


# 搜索空间定义
MOMENTUM_WINDOWS = [5, 10, 20, 40, 60]
TOP_N_VALUES = [1, 2, 3, 5]
RISK_ADJUSTED_OPTIONS = [False, True]
TREND_FILTER_OPTIONS = [False, True]


def _extract_sharpe(metrics: dict) -> float:
    """从指标字典中提取夏普比率（浮点数）。"""
    return float(metrics.get("夏普比率", "0"))


def _extract_annual_return(metrics: dict) -> float:
    """从指标字典中提取年化收益率（去掉百分号转为浮点数）。"""
    val = metrics.get("年化收益率", "0%")
    return float(val.strip("%")) / 100


def _extract_max_drawdown(metrics: dict) -> float:
    """从指标字典中提取最大回撤。"""
    val = metrics.get("最大回撤", "0%")
    return float(val.strip("%")) / 100


def _extract_num_signals(metrics: dict) -> int:
    """从指标字典中提取交易次数。"""
    return int(metrics.get("交易次数", "0"))


def run_grid_search(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    verbose: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """
    执行网格搜索，遍历所有参数组合。

    Args:
        prices: 收盘价宽表
        benchmark: 基准 ETF 收盘价序列
        verbose: 是否打印进度

    Returns:
        results_df: 全量结果表，每行一组参数 + 绩效指标
        best_params: 最优参数的字典
    """
    total_combos = (
        len(MOMENTUM_WINDOWS)
        * len(TOP_N_VALUES)
        * len(RISK_ADJUSTED_OPTIONS)
        * len(TREND_FILTER_OPTIONS)
    )
    if verbose:
        print(f"搜索空间: {len(MOMENTUM_WINDOWS)}×{len(TOP_N_VALUES)}"
              f"×{len(RISK_ADJUSTED_OPTIONS)}×{len(TREND_FILTER_OPTIONS)}"
              f" = {total_combos} 组参数")
        print(f"优化目标: 最大化夏普比率\n")

    results: list[dict] = []
    best_sharpe = -np.inf
    best_params: dict = {}
    best_nav: pd.Series | None = None
    best_benchmark_nav: pd.Series | None = None
    best_signals: pd.DataFrame | None = None

    start_time = time.time()

    for i, (window, top_n, use_ra, use_tf) in enumerate(
        product(MOMENTUM_WINDOWS, TOP_N_VALUES, RISK_ADJUSTED_OPTIONS, TREND_FILTER_OPTIONS),
        start=1,
    ):
        # 生成信号
        signals = generate_weekly_signals(
            prices,
            window=window,
            top_n=top_n,
            use_risk_adjusted=use_ra,
            use_trend_filter=use_tf,
        )

        # 运行回测
        result = run_backtest(prices, signals, benchmark)
        metrics = result["metrics"]

        sharpe = _extract_sharpe(metrics)
        annual_ret = _extract_annual_return(metrics)
        max_dd = _extract_max_drawdown(metrics)
        num_signals = _extract_num_signals(metrics)

        # 记录结果
        results.append({
            "排名": 0,
            "动量窗口": window,
            "持仓数": top_n,
            "风险调整": "开" if use_ra else "关",
            "趋势过滤": "开" if use_tf else "关",
            "夏普比率": sharpe,
            "年化收益率": annual_ret,
            "最大回撤": max_dd,
            "交易次数": num_signals,
        })

        # 追踪最优
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_params = {
                "window": window,
                "top_n": top_n,
                "use_risk_adjusted": use_ra,
                "use_trend_filter": use_tf,
            }
            best_nav = result["nav"]
            best_benchmark_nav = result["benchmark_nav"]
            best_signals = signals

        if verbose and i % 10 == 0:
            elapsed = time.time() - start_time
            print(f"  进度: {i}/{total_combos} ({elapsed:.1f}s)")

    elapsed = time.time() - start_time

    # 构造结果表并排名
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values("夏普比率", ascending=False).reset_index(drop=True)
    results_df["排名"] = range(1, len(results_df) + 1)

    if verbose:
        print(f"\n搜索完成！耗时 {elapsed:.1f}s")
        print(f"\n{'='*70}")
        print(f"  最优参数")
        print(f"{'='*70}")
        print(f"  动量窗口: {best_params['window']}")
        print(f"  持仓数:   {best_params['top_n']}")
        print(f"  风险调整: {'开' if best_params['use_risk_adjusted'] else '关'}")
        print(f"  趋势过滤: {'开' if best_params['use_trend_filter'] else '关'}")
        print(f"  夏普比率: {best_sharpe:.4f}")
        print(f"\n  Top 10 组合:")
        print(results_df.head(10).to_string(index=False))

    return results_df, {
        "best_params": best_params,
        "best_sharpe": best_sharpe,
        "best_nav": best_nav,
        "best_benchmark_nav": best_benchmark_nav,
        "best_signals": best_signals,
        "results_df": results_df,
        "elapsed": elapsed,
    }


def run_optimizer() -> None:
    """
    运行完整优化流程：
    1. 获取数据
    2. 网格搜索
    3. 输出最优结果
    """
    print("=" * 60)
    print("  ETF 动量轮动系统 V2 — 参数优化")
    print("=" * 60)

    # 第一步：获取数据
    print(f"\n[1/3] 获取 ETF 数据（{START_DATE} ~ {END_DATE}）...")
    prices, benchmark = fetch_all_etf_data()
    print(f"      行情数据: {prices.shape[0]} 个交易日 × {prices.shape[1]} 只 ETF")

    # 第二步：网格搜索
    print(f"\n[2/3] 网格搜索最优参数...")
    results_df, best = run_grid_search(prices, benchmark)

    # 第三步：输出
    print(f"\n[3/3] 生成报告...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 全量对比表
    excel_path = os.path.join(OUTPUT_DIR, "optimization_results.xlsx")
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        results_df.to_excel(writer, sheet_name="参数对比", index=False)

        # 最优组合的绩效明细
        bp = best["best_params"]
        summary = pd.DataFrame({
            "参数": ["动量窗口", "持仓数", "风险调整", "趋势过滤", "夏普比率"],
            "最优值": [
                str(bp["window"]),
                str(bp["top_n"]),
                "开" if bp["use_risk_adjusted"] else "关",
                "开" if bp["use_trend_filter"] else "关",
                f"{best['best_sharpe']:.4f}",
            ],
        })
        summary.to_excel(writer, sheet_name="最优参数", index=False)
    print(f"[Excel] 全量对比: {excel_path}")

    # 最优净值图
    chart_path = os.path.join(OUTPUT_DIR, "equity_curve.png")
    plot_equity_curve(best["best_nav"], best["best_benchmark_nav"], chart_path)

    # 最优交易明细
    trade_path = os.path.join(OUTPUT_DIR, "trade_details.xlsx")
    metrics = {
        "累计收益率": "",
        "年化收益率": "",
        "最大回撤": "",
        "夏普比率": "",
        "胜率(vs基准)": "",
        "回测年数": "",
        "交易次数": "",
        "最优参数": (f"窗口={bp['window']} 持仓={bp['top_n']} "
                    f"风险调整={'开' if bp['use_risk_adjusted'] else '关'} "
                    f"趋势过滤={'开' if bp['use_trend_filter'] else '关'}"),
    }
    export_to_excel(
        best["best_signals"], metrics,
        best["best_nav"], best["best_benchmark_nav"],
        trade_path,
    )

    print(f"\n{'=' * 60}")
    print(f"  完成！最优夏普比 = {best['best_sharpe']:.4f}")
    print(f"    对比表: {excel_path}")
    print(f"    净值图: {chart_path}")
    print(f"{'=' * 60}")
