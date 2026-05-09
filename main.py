"""
ETF 动量轮动系统 V2 —— 主入口

运行方式：
  python main.py              # 单次回测（使用 config.py 中的参数）
  python main.py --optimize   # 网格搜索最优参数
  python main.py --signal     # 生成本周实盘信号（持仓建议）

所有参数配置在 src/config.py 中统一管理。
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import (
    ETF_POOL, START_DATE, END_DATE,
    MOMENTUM_WINDOW, TOP_N, USE_RISK_ADJUSTED,
    USE_TREND_FILTER, TREND_WINDOW,
    USE_COMPOSITE_MOMENTUM, MOMENTUM_WINDOWS_COMPOSITE, MOMENTUM_WEIGHTS,
    USE_DYNAMIC_POSITION, TOP_N_AGGRESSIVE, TOP_N_NORMAL,
    USE_RELATIVE_STRENGTH, RELATIVE_STRENGTH_BENCHMARK,
    USE_MARKET_STATE_MACHINE, STATE_BULL_WINDOW, STATE_BULL_TOP_N,
    STATE_SIDEWAYS_WINDOW, STATE_SIDEWAYS_TOP_N, STATE_BEAR_WINDOW,
    MA_TREND_SHORT, MA_TREND_MEDIUM,
    USE_CORRELATION_FILTER, CORRELATION_WINDOW, CORRELATION_THRESHOLD,
    MARKET_MA_WINDOW, MARKET_MA_AGGRESSIVE,
    REBALANCE_FREQ,
    USE_VOL_TARGET, VOL_TARGET,
    BENCHMARK_CODE, OUTPUT_DIR,
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
    print(f"      参数: 状态机={'开' if USE_MARKET_STATE_MACHINE else '关'} "
          f"相关性过滤={'开' if USE_CORRELATION_FILTER else '关'} "
          f"波动率控仓={'开' if USE_VOL_TARGET else '关'}")
    signals = generate_weekly_signals(
        prices,
        window=MOMENTUM_WINDOW, top_n=TOP_N,
        use_risk_adjusted=USE_RISK_ADJUSTED,
        use_composite_momentum=USE_COMPOSITE_MOMENTUM,
        composite_windows=MOMENTUM_WINDOWS_COMPOSITE,
        composite_weights=MOMENTUM_WEIGHTS,
        use_market_state_machine=USE_MARKET_STATE_MACHINE,
        state_bull_window=STATE_BULL_WINDOW,
        state_bull_top_n=STATE_BULL_TOP_N,
        state_sideways_window=STATE_SIDEWAYS_WINDOW,
        state_sideways_top_n=STATE_SIDEWAYS_TOP_N,
        state_bear_window=STATE_BEAR_WINDOW,
        ma_trend_short=MA_TREND_SHORT,
        ma_trend_medium=MA_TREND_MEDIUM,
        market_ma_window=MARKET_MA_WINDOW,
        use_correlation_filter=USE_CORRELATION_FILTER,
        correlation_window=CORRELATION_WINDOW,
        correlation_threshold=CORRELATION_THRESHOLD,
        use_dynamic_position=USE_DYNAMIC_POSITION,
        top_n_aggressive=TOP_N_AGGRESSIVE,
        use_relative_strength=USE_RELATIVE_STRENGTH,
        rebalance_freq=REBALANCE_FREQ,
        use_vol_target=USE_VOL_TARGET, vol_target=VOL_TARGET,
    )
    if signals.empty:
        print("      错误：未生成任何信号，请检查数据或动量窗口设置")
        return
    print(f"      共生成 {len(signals)} 条调仓信号")
    if 'state' in signals.columns:
        n_def = (signals['state'] == 'BEAR').sum()
        if n_def > 0:
            print(f"      防御期: {n_def}条 ({n_def/len(signals)*100:.0f}%)")
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


def run_signal() -> None:
    """生成本周的实盘信号：只输出持仓建议，不做回测。"""
    today = datetime.now().strftime("%Y%m%d")

    print("=" * 60)
    print("  ETF 动量轮动 V2 — 本周实盘信号")
    print("=" * 60)

    # 拉取最新数据（到今天就够，只要覆盖20日窗口）
    print(f"\n正在拉取最新行情...")
    prices, _ = fetch_all_etf_data(start=START_DATE, end=today)

    # 生成全部信号，取最后一条
    signals = generate_weekly_signals(
        prices,
        window=MOMENTUM_WINDOW, top_n=TOP_N,
        use_risk_adjusted=USE_RISK_ADJUSTED,
        use_composite_momentum=USE_COMPOSITE_MOMENTUM,
        composite_windows=MOMENTUM_WINDOWS_COMPOSITE,
        composite_weights=MOMENTUM_WEIGHTS,
        use_market_state_machine=USE_MARKET_STATE_MACHINE,
        state_bull_window=STATE_BULL_WINDOW,
        state_bull_top_n=STATE_BULL_TOP_N,
        state_sideways_window=STATE_SIDEWAYS_WINDOW,
        state_sideways_top_n=STATE_SIDEWAYS_TOP_N,
        state_bear_window=STATE_BEAR_WINDOW,
        ma_trend_short=MA_TREND_SHORT, ma_trend_medium=MA_TREND_MEDIUM,
        market_ma_window=MARKET_MA_WINDOW,
        use_correlation_filter=USE_CORRELATION_FILTER,
        correlation_window=CORRELATION_WINDOW,
        correlation_threshold=CORRELATION_THRESHOLD,
        use_dynamic_position=USE_DYNAMIC_POSITION,
        top_n_aggressive=TOP_N_AGGRESSIVE,
        use_relative_strength=USE_RELATIVE_STRENGTH,
        rebalance_freq=REBALANCE_FREQ,
        use_vol_target=USE_VOL_TARGET, vol_target=VOL_TARGET,
    )

    if signals.empty:
        print("\n  当前无信号（全部 ETF 下跌趋势中，建议空仓观望）")
        return

    # 取最新一期的信号
    latest_date = signals["date"].max()
    latest_signals = signals[signals["date"] == latest_date]

    print(f"\n{'=' * 60}")
    print(f"  信号日期: {latest_date.date()}")
    st = latest_signals['state'].iloc[0] if 'state' in latest_signals.columns else 'RISK_ON'
    state_labels = {'BULL': '⚔️ 牛市 (集中)', 'SIDEWAYS': '📊 震荡 (分散)', 'BEAR': '🛡️ 熊市 (防御)', 'RISK_ON': '⚔️ 风险模式'}
    print(f"  市场状态: {state_labels.get(st, st)}")
    print(f"  策略: 状态机={'开' if USE_MARKET_STATE_MACHINE else '关'} "
          f"相关过滤={'开' if USE_CORRELATION_FILTER else '关'} "
          f"波动率控仓={'开' if USE_VOL_TARGET else '关'}")
    print(f"  建议持仓: {len(latest_signals)} 只 ETF")
    print(f"{'=' * 60}")
    print(f"  {'代码':<8} {'名称':<18} {'动量得分':>8}  {'权重':>8}")
    print(f"  {'-' * 42}")
    for _, row in latest_signals.iterrows():
        print(f"  {row['code']:<8} {row['name']:<18} {row['momentum']:>8.4f}  {row['weight']:>7.1%}")
    print(f"{'=' * 60}")

    # 提示
    if not USE_RISK_ADJUSTED:
        print("\n  提示: 当前未开启风险调整动量，建议在 config.py 中开启以提升夏普比。")
    print(f"  下次更新: 每周最后一个交易日收盘后运行 python main.py --signal\n")


def main() -> None:
    args = sys.argv[1:]

    if "--optimize" in args:
        from src.optimizer.scanner import run_optimizer
        run_optimizer()
    elif "--walk-forward" in args:
        from src.optimizer.walk_forward import run_walk_forward
        from src.output.report import plot_equity_curve
        import os
        result = run_walk_forward()
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        chart_path = os.path.join(OUTPUT_DIR, "equity_curve.png")
        plot_equity_curve(result["oos_nav"], result["benchmark_nav"], chart_path)
    elif "--stability" in args:
        from src.optimizer.param_stability import run_and_export_stability
        run_and_export_stability()
    elif "--signal" in args:
        run_signal()
    else:
        run_single_backtest()


if __name__ == "__main__":
    main()
