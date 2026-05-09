"""
ETF 动量轮动系统 V2 —— 主入口

运行方式：
  python main.py              # 单次回测（使用 config.py 中的参数）
  python main.py --optimize   # 网格搜索最优参数

所有参数配置在 src/config.py 中统一管理。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import (
    ETF_POOL,
    START_DATE,
    END_DATE,
    MOMENTUM_WINDOW,
    TOP_N,
    USE_RISK_ADJUSTED,
    TREND_WINDOW,
    USE_TREND_FILTER,
    BENCHMARK_CODE,
    OUTPUT_DIR,
)
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_weekly_signals
from src.backtest.engine import run_backtest
from src.output.report import plot_equity_curve, export_to_excel


def run_single_backtest() -> None:
    """单次回测：使用 config.py 中的当前参数。"""
    print("=" * 60)
    print("  ETF 动量轮动系统 V2")
    print("=" * 60)

    # ---- 第一步：获取数据 ----
    print(f"\n[1/4] 获取 ETF 数据（{START_DATE} ~ {END_DATE}）...")
    print(f"      ETF 池: {len(ETF_POOL)} 只")
    prices, benchmark_prices = fetch_all_etf_data()
    print(f"      行情数据: {prices.shape[0]} 个交易日 × {prices.shape[1]} 只 ETF")

    # ---- 第二步：生成信号 ----
    print(f"\n[2/4] 生成调仓信号...")
    print(f"      参数: 窗口={MOMENTUM_WINDOW} 持仓={TOP_N} "
          f"风险调整={'开' if USE_RISK_ADJUSTED else '关'} "
          f"趋势过滤={'开' if USE_TREND_FILTER else '关'}")
    signals = generate_weekly_signals(
        prices,
        window=MOMENTUM_WINDOW,
        top_n=TOP_N,
        use_risk_adjusted=USE_RISK_ADJUSTED,
        use_trend_filter=USE_TREND_FILTER,
        trend_window=TREND_WINDOW,
    )
    if signals.empty:
        print("      错误：未生成任何信号，请检查数据或动量窗口设置")
        return
    print(f"      共生成 {len(signals)} 条调仓信号")
    print(f"      首条: {signals.iloc[0]['date'].date()} → {signals.iloc[0]['name']}")
    print(f"      末条: {signals.iloc[-1]['date'].date()} → {signals.iloc[-1]['name']}")

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
        result["signals"], result["metrics"],
        result["nav"], result["benchmark_nav"],
        excel_path,
    )

    print(f"\n{'=' * 60}")
    print(f"  完成！输出: {chart_path}, {excel_path}")
    print(f"{'=' * 60}")


def main() -> None:
    args = sys.argv[1:]

    if "--optimize" in args:
        from src.optimizer.scanner import run_optimizer
        run_optimizer()
    else:
        run_single_backtest()


if __name__ == "__main__":
    main()
