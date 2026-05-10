"""
收益归因分析：系统到底赚谁的钱。

输出维度：
  1. 按 ETF — 每只 ETF 贡献的累计收益
  2. 按状态 — BULL/SIDEWAYS/BEAR 各贡献多少
  3. 按年份 — 每年策略 vs 基准超额
  4. 按资产类型 — 宽基/行业/跨境/商品/债券

回答核心问题：Alpha 是否集中在少数资产，还是广泛分布。
"""

import os

import numpy as np
import pandas as pd

from src.config import ETF_POOL, BENCHMARK_CODE, START_DATE, END_DATE, OUTPUT_DIR
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_signals
from src.backtest.engine import run_backtest, _assign_daily_holdings


# 资产类型映射
ASSET_TYPES: dict[str, str] = {
    "510300": "A股宽基", "510500": "A股宽基", "159915": "A股宽基", "510880": "A股宽基",
    "512000": "A股行业", "512760": "A股行业", "512660": "A股行业", "512170": "A股行业",
    "561660": "A股行业", "159873": "A股行业", "516160": "A股行业",
    "513100": "跨境", "513500": "跨境", "513050": "跨境",
    "513520": "跨境", "513030": "跨境", "513120": "跨境",
    "159322": "商品", "159518": "商品",
    "511010": "债券", "511260": "债券",
}


def _attribution_by_etf(
    prices: pd.DataFrame, signals: pd.DataFrame, holdings: pd.DataFrame
) -> pd.DataFrame:
    """每只 ETF 的收益贡献。"""
    daily_rets = prices.pct_change()
    weighted_rets = holdings * daily_rets
    cumulative = weighted_rets.sum()
    total = cumulative.sum()
    rows = []
    for code in cumulative.index:
        rows.append({
            "ETF代码": code,
            "ETF名称": ETF_POOL.get(code, ""),
            "资产类型": ASSET_TYPES.get(code, "其他"),
            "累计收益贡献": cumulative[code],
            "占比": cumulative[code] / total if total != 0 else 0,
            "信号次数": (signals["code"] == code).sum() if "code" in signals.columns else 0,
        })
    df = pd.DataFrame(rows).sort_values("累计收益贡献", ascending=False)
    df["占比"] = df["占比"].apply(lambda x: f"{x:.1%}")
    return df


def _attribution_by_state(signals: pd.DataFrame, result: dict) -> pd.DataFrame:
    """各市场状态的收益贡献。"""
    nav = result["nav"]
    if "state" not in signals.columns:
        return pd.DataFrame()
    rows = []
    for st in signals["state"].unique():
        st_signals = signals[signals["state"] == st]
        st_dates = set(st_signals["date"].unique())
        # 估算该状态的收益（净值变化中该状态调仓周期的部分）
        nav_aligned = nav[nav.index.isin(st_dates) | nav.index.isin(
            [d + pd.Timedelta(days=1) for d in st_dates if d + pd.Timedelta(days=1) in nav.index]
        )]
        rows.append({
            "状态": st,
            "信号次数": len(st_signals),
            "信号占比": f"{len(st_signals)/max(len(signals),1):.0%}",
        })
    return pd.DataFrame(rows)


def _attribution_by_year(nav: pd.Series, benchmark_nav: pd.Series) -> pd.DataFrame:
    """每年策略 vs 基准收益。"""
    rows = []
    for year in range(nav.index[0].year, nav.index[-1].year + 1):
        strat_y = nav[str(year)]
        bench_y = benchmark_nav[str(year)]
        if len(strat_y) < 2 or len(bench_y) < 2:
            continue
        strat_ret = strat_y.iloc[-1] / strat_y.iloc[0] - 1
        bench_ret = bench_y.iloc[-1] / bench_y.iloc[0] - 1
        rows.append({
            "年份": year,
            "策略收益": f"{strat_ret:.1%}",
            "基准收益": f"{bench_ret:.1%}",
            "超额": f"{strat_ret - bench_ret:.1%}",
            "胜负": "✅ 跑赢" if strat_ret > bench_ret else "❌ 跑输",
        })
    return pd.DataFrame(rows)


def _attribution_by_asset_type(signals: pd.DataFrame, holdings: pd.DataFrame,
                               prices: pd.DataFrame) -> pd.DataFrame:
    """按资产类型归因。"""
    daily_rets = prices.pct_change()
    weighted = holdings * daily_rets
    rows = []
    for atype in set(ASSET_TYPES.values()):
        codes_in_type = [c for c, t in ASSET_TYPES.items() if t == atype and c in weighted.columns]
        if not codes_in_type:
            continue
        type_rets = weighted[codes_in_type].sum(axis=1)
        cum = (1 + type_rets.fillna(0)).prod() - 1
        n_signals = (signals["code"].isin(codes_in_type)).sum() if "code" in signals.columns else 0
        rows.append({
            "资产类型": atype,
            "累计收益": cum,
            "信号次数": n_signals,
        })
    df = pd.DataFrame(rows).sort_values("累计收益", ascending=False)
    df["累计收益"] = df["累计收益"].apply(lambda x: f"{x:.1%}")
    return df


def run_attribution() -> dict:
    """运行收益归因分析。"""
    print("=" * 60)
    print("  收益归因分析（Where does the alpha come from?）")
    print("=" * 60)

    prices, benchmark = fetch_all_etf_data()
    signals = generate_signals(
        prices, window=20, top_n=5, use_risk_adjusted=True,
        use_market_state_machine=True, use_correlation_filter=True,
        market_ma_window=120, rebalance_freq=1,
        use_vol_target=True, vol_target=0.15,
    )
    result = run_backtest(prices, signals, benchmark)
    holdings = _assign_daily_holdings(signals, prices)

    return {
        "by_etf": _attribution_by_etf(prices, signals, holdings),
        "by_state": _attribution_by_state(signals, result),
        "by_year": _attribution_by_year(result["nav"], result["benchmark_nav"]),
        "by_asset_type": _attribution_by_asset_type(signals, holdings, prices),
        "signals": signals,
        "result": result,
        "prices": prices,
    }


def run_and_export_attribution() -> None:
    """运行并导出归因报告。"""
    att = run_attribution()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, "attribution_report.xlsx")

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        att["by_etf"].to_excel(writer, sheet_name="ETF贡献", index=False)
        att["by_state"].to_excel(writer, sheet_name="状态分布", index=False)
        att["by_year"].to_excel(writer, sheet_name="年度收益", index=False)
        att["by_asset_type"].to_excel(writer, sheet_name="资产类型", index=False)

    # 控制台摘要
    print(f"\n{'=' * 60}")
    print(f"  收益归因摘要")
    print(f"{'=' * 60}")

    etf = att["by_etf"]
    print(f"\n  Top 5 贡献 ETF:")
    print(etf.head(5)[["ETF名称", "资产类型", "占比", "信号次数"]].to_string(index=False))

    print(f"\n  资产类型贡献:")
    print(att["by_asset_type"].to_string(index=False))

    year = att["by_year"]
    wins = sum(1 for v in year["胜负"] if "✅" in str(v))
    print(f"\n  年度胜率: {wins}/{len(year)} ({wins/max(len(year),1):.0%})")

    alphas = []
    for v in year["超额"]:
        try:
            alphas.append(float(str(v).strip("%")) / 100)
        except Exception:
            pass
    if alphas:
        print(f"  超额正年数: {sum(1 for a in alphas if a > 0)}/{len(alphas)}")
        print(f"  超额均值: {np.mean(alphas):.1%}")

    print(f"\n[Excel] {path}")
