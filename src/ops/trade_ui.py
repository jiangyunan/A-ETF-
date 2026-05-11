"""
实操交易记录界面 — 显示操作建议 + 手动录入实际交易。

使用方式:
  python main.py --trade-ui

界面流程:
  1. 自动拉取本周信号（操作建议）
  2. 显示当前持仓建议 vs 上次信号
  3. 互动录入：买入/卖出/调仓
  4. 写入 ops/trade_log.csv
"""

import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import (
    ETF_POOL, START_DATE, BENCHMARK_CODE, OUTPUT_DIR,
    MOMENTUM_WINDOW, TOP_N, USE_RISK_ADJUSTED,
    USE_MARKET_STATE_MACHINE, USE_CORRELATION_FILTER,
    MARKET_MA_WINDOW, REBALANCE_FREQ, USE_VOL_TARGET, VOL_TARGET,
    USE_PREMIUM_FILTER,
)
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_signals
from src.ops.trade_log import load_trade_log, create_empty_log, _parse_pct

TRADE_LOG_PATH = "ops/trade_log.csv"


def _get_signal() -> pd.DataFrame:
    """获取最新一周的操作建议。"""
    today = datetime.now().strftime("%Y%m%d")
    prices, _ = fetch_all_etf_data(start=START_DATE, end=today)

    signals = generate_signals(
        prices,
        window=MOMENTUM_WINDOW, top_n=TOP_N,
        use_risk_adjusted=USE_RISK_ADJUSTED,
        use_market_state_machine=USE_MARKET_STATE_MACHINE,
        use_correlation_filter=USE_CORRELATION_FILTER,
        market_ma_window=MARKET_MA_WINDOW,
        rebalance_freq=REBALANCE_FREQ,
        use_vol_target=USE_VOL_TARGET, vol_target=VOL_TARGET,
    )

    if signals.empty:
        return pd.DataFrame()

    latest_date = signals["date"].max()
    return signals[signals["date"] == latest_date]


def _show_current_holdings():
    """从交易日志推算当前持仓。"""
    if not os.path.exists(TRADE_LOG_PATH):
        return set()

    trades = load_trade_log(TRADE_LOG_PATH)
    if trades.empty:
        return set()

    holdings = {}
    for _, t in trades.iterrows():
        code = t["code"]
        if t["action"] == "BUY":
            holdings[code] = holdings.get(code, 0) + t["shares"]
        elif t["action"] == "SELL":
            holdings[code] = holdings.get(code, 0) - t["shares"]

    return {c: s for c, s in holdings.items() if s > 0}


def run_trade_ui():
    """互动交易界面。"""
    # ── 1. 拉取并显示操作建议 ──
    print()
    print("=" * 65)
    print("  实操交易面板")
    print("=" * 65)

    print("\n  [加载] 获取本周操作建议...")
    try:
        signal = _get_signal()
    except Exception as e:
        print(f"  [错误] 信号获取失败: {e}")
        print("  离线模式：仅录入交易")
        signal = pd.DataFrame()

    if not signal.empty:
        st = signal["state"].iloc[0] if "state" in signal.columns else "?"
        labels = {"BULL": "⚔️ 牛市集中", "SIDEWAYS": "📊 震荡分散", "BEAR": "🛡️ 熊市防御", "RISK_ON": "⚔️ 风险"}
        mode = labels.get(st, st)
        print(f"\n  ┌{'─'*59}┐")
        print(f"  │ 信号日期: {signal['date'].iloc[0].date()}  市场: {mode}")
        print(f"  │ 建议持仓: {len(signal)} 只 ETF")
        print(f"  ├{'─'*59}┤")
        print(f"  │ {'代码':<8} {'名称':<16} {'建议权重':>8}  {'动量':>8} │")
        for _, r in signal.iterrows():
            print(f"  │ {r['code']:<8} {r['name']:<16} {r['weight']:>7.1%}  {r['momentum']:>8.4f} │")
        print(f"  └{'─'*59}┘")
    else:
        print("  [无信号] 当前无操作建议（可能全部下跌或数据不足）")

    # ── 2. 显示当前持仓 ──
    holdings = _show_current_holdings()
    if holdings:
        print(f"\n  ┌{'─'*40}┐")
        print(f"  │ 当前持仓（从交易日志推算）")
        for code, shares in holdings.items():
            name = ETF_POOL.get(code, code)
            print(f"  │  {code} {name:<20} {shares} 股")
        if signal.empty:
            pass
        else:
            sugg_codes = set(signal["code"].tolist())
            to_sell = set(holdings.keys()) - sugg_codes
            to_buy = sugg_codes - set(holdings.keys())
            if to_sell:
                print(f"  │  ⚠️ 建议卖出: {', '.join(to_sell)}")
            if to_buy:
                print(f"  │  ⚡ 建议买入: {', '.join(to_buy)}")
        print(f"  └{'─'*40}┘")
    else:
        print("\n  [无持仓] 交易日志为空或已全部平仓")

    # ── 3. 互动录入 ──
    print(f"\n{'=' * 65}")
    print("  录入交易（输入 q 退出）")
    print(f"{'=' * 65}")
    print("  格式: <买入/卖出> <代码> <价格> <股数> [溢价%] [备注]")
    print("  示例: BUY 513100 1.250 1000 1.2% 按信号买入")
    print("        SELL 512760 1.18 500")
    print()

    create_empty_log(TRADE_LOG_PATH)

    while True:
        try:
            inp = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  退出。")
            break

        if inp.lower() in ("q", "quit", "exit"):
            print("  退出。")
            break
        if not inp:
            continue

        parts = inp.split()
        if len(parts) < 4:
            print("  [错误] 格式: <BUY/SELL> <代码> <价格> <股数> [溢价%] [备注]")
            continue

        action = parts[0].upper()
        if action not in ("BUY", "SELL"):
            print("  [错误] 第一项必须是 BUY 或 SELL")
            continue

        code = parts[1]
        if code not in ETF_POOL:
            print(f"  [警告] {code} 不在 ETF 池中，继续录入")

        try:
            price = float(parts[2])
            shares = int(parts[3])
        except ValueError:
            print("  [错误] 价格和股数必须是数字")
            continue

        premium = parts[4] if len(parts) > 4 and "%" in parts[4] else "0%"
        notes = " ".join(parts[5:]) if len(parts) > 5 else ""

        # 写入 SQLite + CSV
        today = datetime.now().strftime("%Y-%m-%d")
        signal_date = signal["date"].iloc[0].strftime("%Y-%m-%d") if not signal.empty else today
        state_val = signal["state"].iloc[0] if not signal.empty and "state" in signal.columns else "?"

        from src.ops.db import insert_trade
        insert_trade({
            "date": today, "action": action, "code": code,
            "name": ETF_POOL.get(code, code), "price": price, "shares": shares,
            "premium_pct": _parse_pct(premium), "signal_date": signal_date,
            "state": state_val, "notes": notes,
        })

        # 同步写 CSV（兼容旧格式）
        new_row = pd.DataFrame([{
            "date": today, "action": action, "code": code,
            "name": ETF_POOL.get(code, code), "price": price, "shares": shares,
            "premium": premium, "signal_date": signal_date,
            "state": state_val, "notes": notes,
        }])
        log_path = TRADE_LOG_PATH
        if os.path.exists(log_path):
            existing = pd.read_csv(log_path)
            combined = pd.concat([existing, new_row], ignore_index=True)
        else:
            combined = new_row

        combined.to_csv(log_path, index=False)
        amount = price * shares
        print(f"  ✅ 已记录: {action} {code} {ETF_POOL.get(code, code)} @{price:.3f} ×{shares} = ¥{amount:.0f}")

    # ── 4. 退出时显示统计 ──
    print(f"\n  交易日志: {TRADE_LOG_PATH}")
    print(f"  查看统计: python main.py --track")
    print()
