"""
实盘交易日志与多维统计引擎。

功能：
  1. 理论 vs 实盘偏差 — 策略信号预测收益率 vs 实际成交收益率
  2. 调仓滑点 — 每次换仓的隐性成本
  3. 溢价真实影响 — 高溢价 ETF 的买入价 vs 理论价偏差
  4. 状态切换频率 — 状态机在实盘中的切换行为
  5. 回撤恢复时间 — 实盘净值从高点回落后多久恢复

交易日志格式 (CSV):
  date, action, code, name, price, shares, premium, signal_date, state, notes

示例:
  2026-05-11, BUY, 513100, 纳指ETF, 1.250, 1000, 1.2%, 2026-05-08, BULL, 按信号买入
  2026-05-18, SELL, 513100, 纳指ETF, 1.310, 1000, 0.3%, 2026-05-15, SIDEWAYS, 调仓卖出
"""

import os
from datetime import datetime

import numpy as np
import pandas as pd

from src.config import OUTPUT_DIR

TRADE_LOG_COLUMNS = [
    "date", "action", "code", "name", "price", "shares",
    "premium", "signal_date", "state", "notes",
]


def create_empty_log(path: str = "ops/trade_log.csv") -> None:
    """创建空白交易日志模板。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        pd.DataFrame(columns=TRADE_LOG_COLUMNS).to_csv(path, index=False)
        print(f"[模板] 交易日志已创建: {path}")


def load_trade_log(path: str = "ops/trade_log.csv") -> pd.DataFrame:
    """加载交易日志，自动解析日期和数值列。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"交易日志不存在: {path}。请先运行 --track 创建模板。")

    df = pd.read_csv(path, parse_dates=["date", "signal_date"])

    # 解析溢价列 (如 "1.2%" → 0.012)
    if "premium" in df.columns:
        df["premium_pct"] = df["premium"].apply(_parse_pct)
    else:
        df["premium_pct"] = 0.0

    # 计算每笔交易金额
    df["amount"] = df["price"] * df["shares"]

    return df.sort_values("date").reset_index(drop=True)


def _parse_pct(val) -> float:
    """解析百分比字符串，如 '1.2%' → 0.012, '0.3%' → 0.003"""
    if pd.isna(val):
        return 0.0
    s = str(val).strip().replace("%", "")
    try:
        return float(s) / 100
    except ValueError:
        return 0.0


# ─── 统计指标 ──────────────────────────────────────────────

def _calc_theory_vs_actual(trades: pd.DataFrame) -> dict:
    """
    理论 vs 实盘偏差。

    策略信号在 signal_date 给出理论持仓，实盘中在 date 执行。
    偏差 = 实盘累计收益 - 理论信号收益。
    """
    if trades.empty:
        return {"理论偏离": "无数据"}

    # 实盘 PnL：按每笔成交价计算的买入卖出差价
    buys = trades[trades["action"] == "BUY"]
    sells = trades[trades["action"] == "SELL"]

    total_bought = (buys["price"] * buys["shares"]).sum()
    total_sold = (sells["price"] * sells["shares"]).sum()

    # 简化版：已平仓部分
    pnl_realized = total_sold - total_bought

    # 未平仓（仍有持仓的部分）
    open_buys = buys[~buys["code"].isin(sells["code"])]
    open_value = (open_buys["price"] * open_buys["shares"]).sum() if not open_buys.empty else 0

    return {
        "已实现盈亏": round(pnl_realized, 2),
        "未平仓价值": round(open_value, 2),
        "买入笔数": len(buys),
        "卖出笔数": len(sells),
    }


def _calc_slippage(trades: pd.DataFrame) -> dict:
    """
    调仓滑点分析。

    滑点 = 实际成交价偏离信号理论价的比例。
    由于我们无实时理论价，用同代码同信号日期的均价作为参考。
    """
    if trades.empty:
        return {"滑点": "无数据"}

    slips = []
    for code in trades["code"].unique():
        code_trades = trades[trades["code"] == code].sort_values("date")
        for i in range(1, len(code_trades)):
            prev = code_trades.iloc[i - 1]
            curr = code_trades.iloc[i]
            if prev["action"] == "BUY" and curr["action"] == "SELL":
                # 简单滑点：卖出价相对买入价的偏差（不计时间价值）
                slip = (curr["price"] / prev["price"] - 1) - (curr["premium_pct"] - prev["premium_pct"])
                slips.append(slip)

    if not slips:
        return {"滑点均值": "无数据", "滑点笔数": 0}

    return {
        "滑点均值": f"{np.mean(slips):.3%}",
        "滑点标准差": f"{np.std(slips):.3%}",
        "滑点最大": f"{max(slips):.3%}",
        "滑点最小": f"{min(slips):.3%}",
        "滑点笔数": len(slips),
    }


def _calc_premium_impact(trades: pd.DataFrame) -> dict:
    """
    溢价真实影响。

    对比高溢价 (>3%) 买入和低溢价 (<3%) 买入的后续表现。
    """
    if trades.empty or "premium_pct" not in trades.columns:
        return {"溢价影响": "无数据"}

    buys = trades[trades["action"] == "BUY"].copy()
    if buys.empty:
        return {"溢价影响": "无买入数据"}

    high = buys[buys["premium_pct"] > 0.03]
    low = buys[buys["premium_pct"] <= 0.03]

    # 检查每笔买入的溢价水平
    return {
        "高溢价买入(>3%)": f"{len(high)} 笔",
        "低溢价买入(<3%)": f"{len(low)} 笔",
        "高溢价均值": f"{high['premium_pct'].mean():.2%}" if not high.empty else "N/A",
        "低溢价均值": f"{low['premium_pct'].mean():.2%}" if not low.empty else "N/A",
        "最高单笔溢价": f"{buys['premium_pct'].max():.2%}" if not buys.empty else "N/A",
    }


def _calc_state_switches(trades: pd.DataFrame) -> dict:
    """
    状态切换频率。

    分析信号状态列中 BULL/SIDEWAYS/BEAR 的切换次数和分布。
    """
    if trades.empty or "state" not in trades.columns:
        return {"状态切换": "无数据"}

    # 按信号日期聚合状态
    states = trades[["signal_date", "state"]].drop_duplicates().sort_values("signal_date")
    if len(states) < 2:
        return {"状态切换": "数据不足"}

    switches = sum(1 for i in range(1, len(states)) if states.iloc[i]["state"] != states.iloc[i - 1]["state"])
    state_counts = states["state"].value_counts().to_dict()

    return {
        "总信号周期": len(states),
        "状态切换次数": switches,
        "切换频率": f"{switches / max(len(states) - 1, 1):.0%}",
        "BULL周期": state_counts.get("BULL", 0),
        "SIDEWAYS周期": state_counts.get("SIDEWAYS", 0),
        "BEAR周期": state_counts.get("BEAR", 0),
    }


def _calc_drawdown_recovery(trades: pd.DataFrame) -> dict:
    """
    回撤恢复时间。

    从交易日志重建净值曲线，计算每次回撤的持续天数。
    """
    if trades.empty:
        return {"回撤": "无数据"}

    # 从交易构建简化净值
    buys = trades[trades["action"] == "BUY"]
    sells = trades[trades["action"] == "SELL"]

    # 按日聚合现金流
    cash_in = buys.groupby("date")["amount"].sum().rename("cash_out")
    cash_out = sells.groupby("date")["amount"].sum().rename("cash_in")

    flow = pd.concat([cash_in, cash_out], axis=1).fillna(0)
    flow["net"] = flow["cash_in"] - flow["cash_out"]
    flow = flow.sort_index()
    flow["nav"] = flow["net"].cumsum() + 1.0  # 假设初始资金归一化

    if len(flow) < 2:
        return {"回撤": "数据不足"}

    # 计算回撤序列
    rolling_peak = flow["nav"].cummax()
    dd = (flow["nav"] - rolling_peak) / rolling_peak

    # 识别回撤事件
    events = []
    in_dd = False
    dd_start = None
    for dt, d in zip(dd.index, dd.values):
        if not in_dd and d < -0.02:
            in_dd = True
            dd_start = dt
        elif in_dd and d > -0.01:
            days = (dt - dd_start).days
            events.append({"start": dd_start, "end": dt, "days": days, "max_dd": dd.loc[dd_start:dt].min()})
            in_dd = False
    if in_dd:
        events.append({"start": dd_start, "end": dd.index[-1],
                       "days": (dd.index[-1] - dd_start).days,
                       "max_dd": dd.loc[dd_start:].min()})

    if not events:
        return {"回撤": "无显著回撤 (>=2%)"}

    return {
        "回撤事件数": len(events),
        "平均恢复天数": f"{np.mean([e['days'] for e in events]):.0f}",
        "最长恢复天数": f"{max(e['days'] for e in events)}",
        "最深回撤": f"{min(e['max_dd'] for e in events):.1%}",
    }


# ─── 综合统计 ──────────────────────────────────────────────

def analyze_trade_log(log_path: str = "ops/trade_log.csv") -> dict:
    """
    运行全部五项统计，返回结果字典。
    """
    create_empty_log(log_path)
    trades = load_trade_log(log_path)

    if trades.empty:
        print("交易日志为空。请在 ops/trade_log.csv 中记录实际买卖后再运行。")
        return {}

    return {
        "theory_vs_actual": _calc_theory_vs_actual(trades),
        "slippage": _calc_slippage(trades),
        "premium_impact": _calc_premium_impact(trades),
        "state_switches": _calc_state_switches(trades),
        "drawdown_recovery": _calc_drawdown_recovery(trades),
    }


# ─── 入口 ──────────────────────────────────────────────────

def run_trade_ops() -> None:
    """运行实盘交易统计并输出报告。"""
    print("=" * 60)
    print("  实盘交易统计（Trade Operations Dashboard）")
    print("=" * 60)

    log_path = "ops/trade_log.csv"
    result = analyze_trade_log(log_path)

    if not result:
        return

    sections = [
        ("理论 vs 实盘偏差", "theory_vs_actual"),
        ("调仓滑点", "slippage"),
        ("溢价真实影响", "premium_impact"),
        ("状态切换频率", "state_switches"),
        ("回撤恢复时间", "drawdown_recovery"),
    ]

    for title, key in sections:
        print(f"\n{'─' * 40}")
        print(f"  {title}")
        print(f"{'─' * 40}")
        data = result.get(key, {})
        if not data:
            print("  无数据")
        for k, v in data.items():
            print(f"  {k}: {v}")

    # 导出 Excel
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    xlsx = os.path.join(OUTPUT_DIR, "trade_ops_report.xlsx")
    try:
        trades = load_trade_log(log_path)
        if not trades.empty:
            with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
                trades.to_excel(w, sheet_name="交易明细", index=False)
                for title, key in sections:
                    data = result.get(key, {})
                    if data:
                        pd.DataFrame(list(data.items()), columns=["指标", "数值"]).to_excel(
                            w, sheet_name=title[:31], index=False)
            print(f"\n[Excel] {xlsx}")
    except Exception as e:
        print(f"\n[警告] Excel 导出失败: {e}")

    print(f"\n交易日志: ops/trade_log.csv")
    print(f"每笔交易后手动记录一行，再次运行 python main.py --track 更新统计。")
