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

import pandas as pd

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
    USE_PREMIUM_FILTER,
    DEFENSE_ETF_CODES,
    BENCHMARK_CODE, OUTPUT_DIR,
)
from src.data.fetcher import fetch_all_etf_data, fetch_etf_nav
from src.strategy.momentum import generate_weekly_signals
from src.backtest.engine import run_backtest
from src.output.report import plot_equity_curve, export_to_excel
from src.ops.console import (
    console, print_header, print_subheader, print_success, print_warning,
    print_error, print_info, print_dim, print_money, print_metric,
    print_key_value, make_table, print_panel, print_divider,
)


def run_single_backtest() -> None:
    """单次回测：使用 config.py 中的当前参数。"""
    print_header("ETF 动量轮动系统 V2")

    # ---- 第一步：获取数据 ----
    print_subheader("获取 ETF 数据")
    console.print(f"  [dim]区间: {START_DATE} ~ {END_DATE}  |  池: {len(ETF_POOL)} 只 ETF[/dim]")
    prices, benchmark_prices = fetch_all_etf_data()
    print_info(f"行情数据: {prices.shape[0]} 个交易日 × {prices.shape[1]} 只 ETF")

    # ---- 第二步：生成信号 ----
    print_subheader("生成调仓信号")
    features = []
    features.append(f"状态机={'开' if USE_MARKET_STATE_MACHINE else '关'}")
    features.append(f"相关性过滤={'开' if USE_CORRELATION_FILTER else '关'}")
    features.append(f"波动率控仓={'开' if USE_VOL_TARGET else '关'}")
    features.append(f"溢价过滤={'开' if USE_PREMIUM_FILTER else '关'}")
    console.print(f"  [dim]参数: {' | '.join(features)}[/dim]")

    # 溢价数据（从净值历史计算）
    premium_data = None
    if USE_PREMIUM_FILTER:
        try:
            nav = fetch_etf_nav()
            if not nav.empty and BENCHMARK_CODE in prices.columns:
                # 溢价率 = (收盘价 / 前一日净值 - 1)
                nav_aligned = nav.reindex(prices.index, method="ffill")
                premium_data = (prices / nav_aligned.shift(1) - 1).fillna(0)
        except Exception as e:
            print_warning(f"溢价数据获取失败（跳过过滤）: {e}")

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
        premium_data=premium_data,
    )
    if signals.empty:
        print_error("未生成任何信号，请检查数据或动量窗口设置")
        return
    print_info(f"共生成 {len(signals)} 条调仓信号")
    if 'state' in signals.columns:
        n_def = (signals['state'] == 'BEAR').sum()
        if n_def > 0:
            print_info(f"防御期: {n_def}条 ({n_def/len(signals)*100:.0f}%)")
    print_dim(f"首条: {signals.iloc[0]['date'].date()} → {signals.iloc[0]['name']}")
    print_dim(f"末条: {signals.iloc[-1]['date'].date()} → {signals.iloc[-1]['name']}")

    # ---- 第三步：回测 ----
    print_subheader(f"执行回测（基准: {BENCHMARK_CODE} 买入持有）")
    result = run_backtest(prices, signals, benchmark_prices)

    console.print("\n[bold]绩效指标:[/bold]")
    for key, value in result["metrics"].items():
        print_metric(key, str(value))

    # ---- 第四步：输出 ----
    print_subheader("生成报告")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    chart_path = os.path.join(OUTPUT_DIR, "equity_curve.png")
    excel_path = os.path.join(OUTPUT_DIR, "trade_details.xlsx")

    plot_equity_curve(result["nav"], result["benchmark_nav"], chart_path)
    export_to_excel(
        result["signals"], result["metrics"],
        result["nav"], result["benchmark_nav"],
        excel_path,
    )

    print_success(f"完成！输出: {chart_path}, {excel_path}")


def run_signal() -> None:
    """生成本周的实盘信号：基于最新交易日数据，强制评估。"""
    today_str = datetime.now().strftime("%Y%m%d")

    print_header("ETF 动量轮动 V2 — 本周实盘信号")

    # 获取可用资金
    from src.config import INITIAL_CAPITAL, MIN_LOT_SIZE
    from src.ops.db import get_latest_capital
    latest_cap = get_latest_capital()
    if latest_cap:
        available_capital = latest_cap["cash"]
        print_money("可用资金", available_capital)
        print_dim("来源: 资金台账")
    else:
        available_capital = INITIAL_CAPITAL
        print_money("可用资金", available_capital)
        print_dim("来源: 默认初始资金")

    # 拉取最新数据
    print_subheader("拉取最新行情")
    prices, _ = fetch_all_etf_data(start=START_DATE, end=today_str)
    latest_date = prices.index[-1]
    print_info(f"数据更新至: {latest_date.date()}")

    # 实时溢价数据
    premium_data = None
    if USE_PREMIUM_FILTER:
        try:
            import akshare as ak
            spot = ak.fund_etf_spot_em()
            if '基金折价率' in spot.columns and '代码' in spot.columns:
                spot_map = dict(zip(spot['代码'], spot['基金折价率']))
                prem_series = pd.Series({c: abs(float(spot_map.get(c, 0))) / 100
                                         for c in prices.columns
                                         if c in spot_map}, dtype=float)
                if not prem_series.empty:
                    premium_data = pd.DataFrame([prem_series], index=[latest_date])
                    print_info(f"实时溢价: {len(prem_series)} 只 ETF")
        except Exception as e:
            print_warning(f"溢价数据获取失败: {e}")

    # 强制用最新交易日生成信号（覆盖调度频率，始终取最后一天）
    import src.config as cfg
    orig_freq = cfg.REBALANCE_FREQ
    cfg.REBALANCE_FREQ = 1

    # 从策略模块直接取动量计算函数，用最新日期强制评估
    from src.strategy.momentum import (
        calc_risk_adjusted_momentum, calc_composite_momentum,
        _classify_market, _filter_by_correlation, _market_breadth,
        _apply_premium_filter,
    )
    from src.strategy.black_swan import evaluate_black_swan
    import numpy as np

    # 动量计算
    momentum_df = calc_risk_adjusted_momentum(prices, MOMENTUM_WINDOW)

    # 状态判断
    state, _, _ = _classify_market(prices, latest_date,
        ma_trend_short=MA_TREND_SHORT, ma_trend_medium=MA_TREND_MEDIUM,
        ma_market=MARKET_MA_WINDOW)

    # 选池
    attack_codes = [c for c in ETF_POOL if c not in DEFENSE_ETF_CODES]
    defense_codes = [c for c in DEFENSE_ETF_CODES if c in prices.columns]

    if state == "BEAR":
        pool = defense_codes
    else:
        pool = [c for c in attack_codes if c in momentum_df.columns]

    # 取最后一天的动量排名
    if latest_date not in momentum_df.index:
        latest_date = momentum_df.index[-1]
    row = momentum_df.loc[latest_date, pool].dropna()
    row = row[row > 0]

    # 溢价过滤
    weight_penalty_prem = {}
    if premium_data is not None and not premium_data.empty:
        row, weight_penalty_prem = _apply_premium_filter(row, premium_data, latest_date)
        row = row.dropna()
        row = row[row > 0]

    # 动态持仓数
    n_pick = { "BULL": STATE_BULL_TOP_N, "SIDEWAYS": STATE_SIDEWAYS_TOP_N, "BEAR": 2 }.get(state, TOP_N)
    n_pick = min(n_pick, len(row))

    top = row.nlargest(n_pick * 2)
    selected = list(top.index)

    # 相关性过滤
    if USE_CORRELATION_FILTER and state != "BEAR" and len(selected) > 1:
        selected = _filter_by_correlation(selected, prices, latest_date)

    selected = selected[:n_pick]
    base_weight = 1.0 / len(selected) if selected else 0

    # 构建信号表
    latest_signals_list = []
    for code in selected:
        penalty = weight_penalty_prem.get(code, 1.0)
        latest_signals_list.append({
            "date": latest_date,
            "code": code,
            "name": ETF_POOL.get(code, ""),
            "momentum": round(float(row.get(code, 0)), 4),
            "weight": round(base_weight * penalty, 4),
            "state": state,
            "holiday_delay": False,
        })

    cfg.REBALANCE_FREQ = orig_freq

    if not latest_signals_list:
        print_warning("当前无信号（全部 ETF 下跌趋势中，建议空仓观望）")
        return

    latest_signals = pd.DataFrame(latest_signals_list)
    latest_date = latest_signals["date"].iloc[0]

    # 状态标签
    st = latest_signals['state'].iloc[0] if 'state' in latest_signals.columns else 'RISK_ON'
    state_labels = {
        'BULL': '牛市 (集中进攻)',
        'SIDEWAYS': '震荡 (分散防御)',
        'BEAR': '熊市 (全面防御)',
        'RISK_ON': '风险模式',
    }
    state_colors = {'BULL': 'green', 'SIDEWAYS': 'yellow', 'BEAR': 'red', 'RISK_ON': 'cyan'}

    print_header("信号摘要", width=80)
    print_key_value("信号日期", f"{latest_date.date()}")
    console.print(f"  [bold]市场状态:[/bold] [{state_colors.get(st, 'white')}]{state_labels.get(st, st)}[/{state_colors.get(st, 'white')}]")
    features = []
    features.append(f"状态机={'开' if USE_MARKET_STATE_MACHINE else '关'}")
    features.append(f"相关过滤={'开' if USE_CORRELATION_FILTER else '关'}")
    features.append(f"波动率控仓={'开' if USE_VOL_TARGET else '关'}")
    print_dim(f"策略: {' | '.join(features)}")
    # 计算建议买入股数（手）
    latest_signals["shares"] = 0
    latest_signals["lots"] = 0
    for i in range(len(latest_signals)):
        code = latest_signals.iloc[i]["code"]
        weight = latest_signals.iloc[i]["weight"]
        # 获取最新价格
        if code in prices.columns and latest_date in prices.index:
            px = float(prices.loc[latest_date, code])
        else:
            px = 0.0
        if px > 0 and weight > 0:
            raw_shares = int(weight * available_capital / px)
            rounded = (raw_shares // MIN_LOT_SIZE) * MIN_LOT_SIZE
            latest_signals.at[latest_signals.index[i], "shares"] = rounded
            latest_signals.at[latest_signals.index[i], "lots"] = rounded // MIN_LOT_SIZE
        latest_signals.at[latest_signals.index[i], "price"] = px

    total_cost = sum(latest_signals["shares"] * latest_signals["price"])

    # 用 Rich 表格展示
    table_rows = []
    for _, row in latest_signals.iterrows():
        px = row.get("price", 0)
        cost = row["shares"] * px
        table_rows.append([
            f"[bold]{row['code']}[/bold]",
            row['name'],
            f"{row['momentum']:.4f}",
            f"{row['weight']:.1%}",
            f"{px:.3f}",
            f"{row['shares']:,}",
            f"{row['lots']}",
            f"[bright_green]¥{cost:,.0f}[/bright_green]",
        ])

    signal_table = make_table(
        f"建议持仓: {len(latest_signals)} 只 ETF",
        ["代码", "名称", "动量得分", "权重", "价格", "股数", "手数", "金额"],
        table_rows,
        styles=["left", "left", "right", "right", "right", "right", "right", "right"],
    )
    console.print(signal_table)
    print_money("可用资金", available_capital)
    print_money("预估总成本", total_cost)
    console.print(f"  [dim]资金利用率: {total_cost/available_capital*100:.0f}%[/dim]")

    # 执行提示
    is_holiday = latest_signals["holiday_delay"].iloc[0] if "holiday_delay" in latest_signals.columns else False
    if is_holiday:
        print_warning("长假后首个交易日 — 明日仅观察，后天执行")
    else:
        print_info("执行日: 下一交易日（信号日+1）")
    print_dim("下次更新: 下周一收盘后运行 python main.py --signal")


def _check_data_freshness() -> bool:
    """
    检查最新K线数据是否新鲜。
    允许周五→周一间隙（3天=正常周末），超过则报警。
    """
    from datetime import datetime
    now = datetime.now()

    try:
        prices, _ = fetch_all_etf_data(
            start=(now.replace(year=now.year - 1)).strftime("%Y%m%d"),
            end=now.strftime("%Y%m%d"),
        )
    except Exception as e:
        print_error(f"数据获取失败: {e}")
        return False

    if prices.empty:
        print_error("无可用K线数据")
        return False

    latest_kline = prices.index[-1].to_pydatetime()
    days_behind = (now - latest_kline).days

    # 计算缺失的交易日（跳过周末）
    missing_trading = 0
    d = latest_kline
    while d < now:
        d = d + pd.Timedelta(days=1)
        if d.weekday() < 5 and d < now:  # Mon-Fri, 且不能是未来
            missing_trading += 1

    weekdays_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = weekdays_cn[latest_kline.weekday()]

    print_divider()
    console.print(f"  [bold]当前时间:[/bold] {now.strftime('%Y-%m-%d %H:%M')}")
    console.print(f"  [bold]最新K线:[/bold]  {latest_kline.strftime('%Y-%m-%d')} ({wd})")

    # 周一前的最新数据是上周五 → 正常（周末无交易）
    if missing_trading <= 1:
        print_success("数据正常")
        print_divider()
        return True

    if missing_trading >= 3:
        print_error(f"数据缺失 {missing_trading} 个交易日 ({days_behind} 自然日)")
        print_warning("超过3个交易日，建议停止交易")
        print_divider()
        return False
    else:
        print_warning(f"数据缺失 {missing_trading} 个交易日 ({days_behind} 自然日)")
        print_dim("可能是假期或数据源延迟")
        print_divider()
        return True  # 1-2天落后可能是假期

def main() -> None:
    args = sys.argv[1:]

    # ── 数据新鲜度检查（每次运行前）──
    if not _check_data_freshness():
        return

    # ── 快捷录入：python main.py --log BUY 513100 1.250 1000 1.2% 备注 ──
    if "--log" in args:
        from datetime import datetime
        from src.ops.db import insert_trade, get_latest_capital, update_capital
        from src.config import ETF_POOL, INITIAL_CAPITAL
        idx = args.index("--log")
        log_args = args[idx + 1:]
        if len(log_args) < 4:
            print_header("交易录入")
            console.print("  [bold yellow]用法:[/bold yellow] python main.py --log BUY/SELL 代码 价格 股数 [溢价%] [备注]")
            console.print("  [dim]示例: python main.py --log BUY 513100 1.250 1000 1.2% 按信号买入[/dim]")
            return
        action = log_args[0].upper()
        if action not in ("BUY", "SELL"):
            print_error(f"第一个参数必须是 BUY 或 SELL，收到 {action}")
            return
        code = log_args[1]
        try:
            price = float(log_args[2])
            shares = int(log_args[3])
        except ValueError:
            print_error("价格和股数必须是数字")
            return
        premium = log_args[4] if len(log_args) > 4 else "0%"
        if "%" in premium:
            premium_pct = float(premium.strip("%")) / 100
        else:
            premium_pct = float(premium) / 100 if premium.replace('.','',1).isdigit() else 0.0
        notes = " ".join(log_args[5:]) if len(log_args) > 5 else ""
        today = datetime.now().strftime("%Y-%m-%d")
        trade_amount = price * shares

        # 写入交易记录
        insert_trade({
            "date": today, "action": action, "code": code,
            "name": ETF_POOL.get(code, code), "price": price, "shares": shares,
            "premium_pct": premium_pct, "signal_date": today,
            "state": "?", "notes": notes,
        })
        action_icon = "🟢" if action == "BUY" else "🔴"
        console.print(f"\n  {action_icon} [bold]已录入:[/bold] {action} [bold]{code}[/bold] {ETF_POOL.get(code,code)} @{price:.3f} ×{shares} = [bright_green]¥{trade_amount:,.0f}[/bright_green]")

        # 自动更新资金台账
        latest_cap = get_latest_capital()
        if latest_cap:
            new_cash = latest_cap["cash"]
            new_market = latest_cap["market_value"]
            if action == "BUY":
                new_cash -= trade_amount
                new_market += trade_amount
            else:  # SELL
                new_cash += trade_amount
                new_market -= trade_amount
            new_total = new_cash + new_market
            cap_notes = f"{action} {code} {ETF_POOL.get(code,code)} @{price:.3f} ×{shares}"
            update_capital(new_total, new_cash, new_market, notes=cap_notes, date=today)
            print_money("资金台账: 现金", new_cash)
            print_money("资金台账: 市值", new_market)
            print_money("资金台账: 总资产", new_total)
        else:
            # 首次录入，用初始资金
            if action == "BUY":
                new_cash = INITIAL_CAPITAL - trade_amount
                new_market = trade_amount
            else:
                new_cash = INITIAL_CAPITAL + trade_amount
                new_market = 0
            cap_notes = f"初始资金 ¥{INITIAL_CAPITAL:,.0f} → {action} {code}"
            update_capital(new_cash + new_market, new_cash, new_market, notes=cap_notes, date=today)
            print_success(f"已创建资金台账: 初始资金 ¥{INITIAL_CAPITAL:,.0f}")
            print_money("现金", new_cash)
            print_money("市值", new_market)
        return

    # ── 交易撤销/查看 ──
    if "--log-undo" in args:
        from src.ops.db import delete_last_trade
        deleted = delete_last_trade()
        if deleted:
            print_success(f"已撤销: {deleted['action']} {deleted['code']} {deleted['name']} @{deleted['price']} ×{deleted['shares']} (ID={deleted['id']})")
        else:
            print_warning("无交易记录可撤销")
        return

    if "--log-list" in args:
        from src.ops.db import get_trades
        trades = get_trades()
        if not trades:
            print_warning("无交易记录")
            return
        table_rows = []
        for t in trades[-20:]:
            action_icon = "🟢" if t['action'] == 'BUY' else '🔴'
            table_rows.append([
                str(t['id']),
                t['date'],
                f"{action_icon} {t['action']}",
                f"[bold]{t['code']}[/bold]",
                t['name'],
                f"{t['price']:.3f}",
                f"{t['shares']:,}",
                t.get('notes', '')[:15],
            ])
        log_table = make_table(
            "交易记录 (最近20条)",
            ["ID", "日期", "操作", "代码", "名称", "价格", "股数", "备注"],
            table_rows,
            styles=["right", "left", "left", "left", "left", "right", "right", "left"],
        )
        console.print(log_table)
        return

    if "--log-delete" in args:
        from src.ops.db import delete_trade_by_id
        idx = args.index("--log-delete")
        if idx + 1 >= len(args):
            print_header("删除交易")
            console.print("  [bold yellow]用法:[/bold yellow] python main.py --log-delete <ID>")
            console.print("  [dim]先用 --log-list 查看 ID[/dim]")
            return
        try:
            tid = int(args[idx + 1])
        except ValueError:
            print_error("ID 必须是数字")
            return
        deleted = delete_trade_by_id(tid)
        if deleted:
            print_success(f"已删除: ID={tid}  {deleted['action']} {deleted['code']} {deleted['name']} @{deleted['price']}")
        else:
            print_error(f"ID={tid} 不存在")
        return

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
        from src.optimizer.param_stability import run_and_export_stability_v8
        run_and_export_stability_v8()
    elif "--monte-carlo" in args:
        from src.optimizer.monte_carlo import run_and_export_monte_carlo
        run_and_export_monte_carlo()
    elif "--attribution" in args:
        from src.optimizer.attribution import run_and_export_attribution
        run_and_export_attribution()
    elif "--track" in args:
        from src.ops.trade_log import run_trade_ops
        run_trade_ops()
    elif "--trade-source" in args:
        from src.optimizer.trade_source import run_and_export_trade_source
        run_and_export_trade_source()
    elif "--transition" in args:
        from src.optimizer.transition import run_transition_analytics
        run_transition_analytics()
    elif "--trade-ui" in args or "--track-ui" in args:
        from src.ops.trade_ui import run_trade_ui
        run_trade_ui()
    elif "--signal" in args:
        run_signal()
    elif "--preflight" in args:
        from src.data.fetcher import fetch_all_etf_data
        from src.ops.preflight import run_and_log_preflight
        prices, _ = fetch_all_etf_data()
        run_and_log_preflight(prices)
    elif "--alerts" in args:
        from src.data.fetcher import fetch_all_etf_data
        from src.ops.risk_alerts import run_all_alerts
        from src.ops.db import get_trades
        prices, _ = fetch_all_etf_data()
        trades = get_trades()
        run_all_alerts(prices=prices, trades=trades, latest_date=prices.index[-1])
    elif "--capital" in args:
        from src.ops.db import get_latest_capital, get_capital_history
        from src.config import INITIAL_CAPITAL
        latest = get_latest_capital()
        if latest:
            print_header("资金台账")
            print_key_value("日期", latest['date'])
            print_money("总资产", latest['total'])
            print_money("可用现金", latest['cash'])
            print_money("持仓市值", latest['market_value'])
            if latest.get('notes'):
                print_dim(f"备注: {latest['notes']}")

            # 显示历史
            history = get_capital_history(10)
            if len(history) > 1:
                hist_rows = []
                for h in history:
                    hist_rows.append([
                        h['date'],
                        f"[bright_green]¥{h['total']:,.0f}[/bright_green]",
                        f"[bright_green]¥{h['cash']:,.0f}[/bright_green]",
                        f"[bright_green]¥{h['market_value']:,.0f}[/bright_green]",
                        h.get('notes', '')[:20],
                    ])
                hist_table = make_table(
                    f"最近 {len(history)} 条记录",
                    ["日期", "总资产", "现金", "市值", "备注"],
                    hist_rows,
                    styles=["left", "right", "right", "right", "left"],
                )
                console.print(hist_table)
        else:
            print_warning("暂无资金记录")
            print_dim(f"默认初始资金: ¥{INITIAL_CAPITAL:,.0f}")
            print_dim("录入第一笔交易后自动创建资金台账。")
        return

    if "--migrate" in args:
        from src.ops.db import migrate_from_csv
        n = migrate_from_csv()
        print_success(f"迁移完成: {n} 条记录从 CSV → SQLite")
        return

    run_single_backtest()


if __name__ == "__main__":
    main()
