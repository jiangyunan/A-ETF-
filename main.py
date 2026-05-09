"""
ETF 动量轮动系统 V1 —— 主入口

运行方式：
  python main.py

执行流程：
  1. 数据获取 → 2. 信号生成 → 3. 回测模拟 → 4. 结果输出

所有参数配置在 src/config.py 中统一管理，修改参数无需改代码。
"""

import os
import sys

# 确保项目根目录在 sys.path 中，支持从任意位置运行
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import (
    ETF_POOL,
    START_DATE,
    END_DATE,
    MOMENTUM_WINDOW,
    BENCHMARK_CODE,
    OUTPUT_DIR,
)
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_weekly_signals
from src.backtest.engine import run_backtest
from src.output.report import plot_equity_curve, export_to_excel


def main() -> None:
    print("=" * 60)
    print("  ETF 动量轮动系统 V1")
    print("=" * 60)

    # ---- 第一步：获取数据 ----
    print(f"\n[1/4] 获取 ETF 数据（{START_DATE} ~ {END_DATE}）...")
    print(f"      ETF 池: {len(ETF_POOL)} 只（{', '.join(ETF_POOL.keys())}）")
    prices, benchmark_prices = fetch_all_etf_data()
    print(f"      行情数据: {prices.shape[0]} 个交易日 × {prices.shape[1]} 只 ETF")

    # ---- 第二步：生成信号 ----
    print(f"\n[2/4] 计算 {MOMENTUM_WINDOW} 日动量，生成周度调仓信号...")
    signals = generate_weekly_signals(prices)
    if signals.empty:
        print("      错误：未生成任何信号，请检查数据或动量窗口设置")
        return
    print(f"      共生成 {len(signals)} 条调仓信号")
    print(f"      首条信号: {signals.iloc[0]['date'].date()} → {signals.iloc[0]['name']}")
    print(f"      末条信号: {signals.iloc[-1]['date'].date()} → {signals.iloc[-1]['name']}")

    # ---- 第三步：回测 ----
    print(f"\n[3/4] 执行回测（基准: {BENCHMARK_CODE} 买入持有）...")
    result = run_backtest(prices, signals, benchmark_prices)

    print("\n      === 绩效指标 ===")
    for key, value in result["metrics"].items():
        print(f"      {key}: {value}")

    # ---- 第四步：输出 ----
    print(f"\n[4/4] 生成报告...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    chart_path = os.path.join(OUTPUT_DIR, "equity_curve.png")
    excel_path = os.path.join(OUTPUT_DIR, "trade_details.xlsx")

    plot_equity_curve(result["nav"], result["benchmark_nav"], chart_path)
    export_to_excel(
        result["signals"],
        result["metrics"],
        result["nav"],
        result["benchmark_nav"],
        excel_path,
    )

    print(f"\n{'=' * 60}")
    print(f"  完成！输出文件:")
    print(f"    图表: {chart_path}")
    print(f"    Excel: {excel_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
