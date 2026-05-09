"""
滚动回测（Walk Forward Analysis）：避免参数过拟合的核心验证手段。

原理：
  将历史数据切分为多个「训练→测试」滚动窗口。
  每个窗口内，用训练期（In-Sample）做网格搜索找最优参数，
  再用该参数在测试期（Out-of-Sample）跑回测，记录结果。
  最后将所有 OOS 片段拼接，得到「实时交易中你会拿到的」真实表现。

和普通回测的区别：
  - 普通回测：在全部历史上找最优参数 → 用同一段历史评估 → 过拟合风险
  - 滚动回测：每段只用过去数据选参 → 对未来数据评估 → 真实外推能力

参数设置：
  - 训练窗口: 3 年（756 个交易日）
  - 测试窗口: 6 个月（126 个交易日）
  - 步长: 与测试窗口一致（不重叠），或可选重叠
"""

import time
from itertools import product

import numpy as np
import pandas as pd

from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_weekly_signals
from src.backtest.engine import run_backtest, _calc_metrics
from src.config import START_DATE, END_DATE, OUTPUT_DIR


# 滚动窗口参数
TRAIN_YEARS = 3
TEST_MONTHS = 6
TRAIN_DAYS = int(TRAIN_YEARS * 252)  # 约 756 个交易日
TEST_DAYS = int(TEST_MONTHS / 12 * 252)  # 约 126 个交易日

# 搜索空间（与 optimizer 一致，但可以缩小以加速）
SEARCH_WINDOWS = [10, 20, 40]
SEARCH_TOP_NS = [3, 5]
SEARCH_RISK_ADJUSTED = [True]
SEARCH_REBALANCE_FREQ = [1, 2]
SEARCH_MARKET_MA = [60, 120, 200]
SEARCH_VOL_TARGET = [False, True]
SEARCH_COMPOSITE = [False, True]
SEARCH_DYNAMIC_POS = [False, True]
SEARCH_REL_STRENGTH = [False, True]


def _grid_search_fold(prices_is: pd.DataFrame, benchmark_is: pd.Series) -> dict:
    """在训练期数据上做网格搜索，返回最优参数。"""
    best_sharpe = -np.inf
    best_params = {}

    for window, top_n, use_ra, freq, market_ma, use_vol, composite, dyn_pos, rel_str in product(
        SEARCH_WINDOWS, SEARCH_TOP_NS, SEARCH_RISK_ADJUSTED,
        SEARCH_REBALANCE_FREQ, SEARCH_MARKET_MA, SEARCH_VOL_TARGET,
        SEARCH_COMPOSITE, SEARCH_DYNAMIC_POS, SEARCH_REL_STRENGTH,
    ):
        signals = generate_weekly_signals(
            prices_is,
            window=window, top_n=top_n,
            use_risk_adjusted=use_ra,
            use_composite_momentum=composite,
            use_dynamic_position=dyn_pos,
            use_relative_strength=rel_str,
            market_ma_window=market_ma,
            rebalance_freq=freq,
            use_vol_target=use_vol,
        )
        if signals.empty:
            continue
        result = run_backtest(prices_is, signals, benchmark_is)
        sharpe = float(result["metrics"].get("夏普比率", "-99"))
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_params = {
                "window": window, "top_n": top_n,
                "use_risk_adjusted": use_ra,
                "use_composite_momentum": composite,
                "use_dynamic_position": dyn_pos,
                "use_relative_strength": rel_str,
                "market_ma_window": market_ma,
                "rebalance_freq": freq,
                "use_vol_target": use_vol,
            }
    return best_params


def run_walk_forward(verbose: bool = True) -> dict:
    """
    执行完整的滚动回测流程。

    Returns:
        字典包含：
          - oos_nav:       拼接后的 OOS 净值序列
          - benchmark_nav: 对应期基准净值
          - oos_returns:   OOS 日收益率序列
          - metrics:       绩效指标
          - fold_details:  每折的详细记录
    """
    print("=" * 60)
    print("  Walk Forward Analysis（滚动回测）")
    print(f"  训练窗口: {TRAIN_YEARS}年 ({TRAIN_DAYS}天)")
    print(f"  测试窗口: {TEST_MONTHS}个月 ({TEST_DAYS}天)")
    print("=" * 60)

    # 获取全部数据
    print(f"\n[1/3] 获取全量数据...")
    prices, benchmark = fetch_all_etf_data()
    all_dates = prices.index
    n_dates = len(all_dates)
    print(f"      共 {n_dates} 个交易日")

    # 计算可以切多少折
    first_test_start = TRAIN_DAYS  # 第一折测试从第 TRAIN_DAYS 天开始
    folds = []
    test_start = first_test_start
    while test_start + TEST_DAYS <= n_dates:
        train_end = test_start
        train_start = max(0, train_end - TRAIN_DAYS)
        test_end = min(n_dates, test_start + TEST_DAYS)

        folds.append({
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            "train_dates": (all_dates[train_start].date(), all_dates[train_end - 1].date()),
            "test_dates": (all_dates[test_start].date(), all_dates[test_end - 1].date()),
        })
        test_start += TEST_DAYS

    n_folds = len(folds)
    print(f"      可切 {n_folds} 个滚动窗口\n")

    # 逐折执行
    all_oos_returns: list[pd.Series] = []
    all_benchmark_returns: list[pd.Series] = []
    fold_details: list[dict] = []

    start_time = time.time()

    for fi, fold in enumerate(folds, start=1):
        fold_start = time.time()

        # 切分数据
        prices_is = prices.iloc[fold["train_start"]:fold["train_end"]]
        prices_oos = prices.iloc[fold["test_start"]:fold["test_end"]]
        benchmark_is = benchmark.iloc[fold["train_start"]:fold["train_end"]]

        # 训练期网格搜索
        best_params = _grid_search_fold(prices_is, benchmark_is)

        # 测试期回测
        signals_oos = generate_weekly_signals(
            prices_oos,
            window=best_params["window"],
            top_n=best_params["top_n"],
            use_risk_adjusted=best_params["use_risk_adjusted"],
            use_composite_momentum=best_params.get("use_composite_momentum", False),
            use_dynamic_position=best_params.get("use_dynamic_position", False),
            use_relative_strength=best_params.get("use_relative_strength", False),
            market_ma_window=best_params["market_ma_window"],
            rebalance_freq=best_params["rebalance_freq"],
            use_vol_target=best_params["use_vol_target"],
        )

        result_oos = run_backtest(prices_oos, signals_oos, benchmark.iloc[fold["test_start"]:fold["test_end"]])
        oos_nav = result_oos["nav"]
        # 日收益率从 nav 反算（避免 OOS 测试起点不连续问题）
        oos_rets = oos_nav.pct_change().fillna(0)
        bm_rets = result_oos["benchmark_nav"].pct_change().fillna(0)

        all_oos_returns.append(oos_rets)
        all_benchmark_returns.append(bm_rets)

        fold_sharpe = float(result_oos["metrics"].get("夏普比率", "0"))
        fold_ann = float(result_oos["metrics"].get("年化收益率", "0%").strip("%")) / 100
        fold_dd = float(result_oos["metrics"].get("最大回撤", "0%").strip("%")) / 100

        fold_details.append({
            "折数": fi,
            "训练期": f"{fold['train_dates'][0]} ~ {fold['train_dates'][1]}",
            "测试期": f"{fold['test_dates'][0]} ~ {fold['test_dates'][1]}",
            "IS最优参数": (f"w={best_params['window']} n={best_params['top_n']} "
                       f"freq={best_params['rebalance_freq']}w "
                       f"MA={best_params['market_ma_window']} "
                       f"vol={'Y' if best_params['use_vol_target'] else 'N'}"),
            "OOS夏普": fold_sharpe,
            "OOS年化": fold_ann,
            "OOS回撤": fold_dd,
        })

        fold_time = time.time() - fold_start
        if verbose:
            print(f"  折 {fi:>2}/{n_folds}: "
                  f"IS={fold['train_dates'][0]}~{fold['train_dates'][1]} "
                  f"→ OOS={fold['test_dates'][0]}~{fold['test_dates'][1]} "
                  f"夏普={fold_sharpe:.2f} 年化={fold_ann:.1%} "
                  f"({fold_time:.1f}s)")

    # ---- 拼接 OOS 结果 ----
    # 按日期索引拼接，拼接点可能有微小跳跃，用累计乘积求净值
    oos_concat = pd.concat(all_oos_returns).sort_index()
    # 去重：同一天出现两次说明折之间有重叠，取平均或保留第一个
    oos_concat = oos_concat[~oos_concat.index.duplicated(keep="first")]

    bm_concat = pd.concat(all_benchmark_returns).sort_index()
    bm_concat = bm_concat[~bm_concat.index.duplicated(keep="first")]

    # 对齐两个序列到共同日期
    common = oos_concat.index.intersection(bm_concat.index)
    oos_concat = oos_concat.loc[common]
    bm_concat = bm_concat.loc[common]

    # 净值曲线
    oos_nav_final = (1 + oos_concat).cumprod()
    bm_nav_final = (1 + bm_concat).cumprod()

    # 绩效指标
    metrics = _calc_metrics(oos_nav_final, oos_concat, bm_nav_final, len(common))

    elapsed = time.time() - start_time

    # ---- 输出 ----
    print(f"\n{'=' * 60}")
    print(f"  Walk Forward 汇总（纯 OOS，无未来信息）")
    print(f"{'=' * 60}")
    print(f"  总 OOS 天数: {len(common)}")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print(f"  总耗时: {elapsed:.1f}s")

    # 导出各折明细
    fold_df = pd.DataFrame(fold_details)
    excel_path = os.path.join(OUTPUT_DIR, "walk_forward_folds.xlsx")
    os.makedirs(os.path.dirname(excel_path), exist_ok=True)
    fold_df.to_excel(excel_path, index=False, sheet_name="各折明细")
    print(f"\n  各折明细: {excel_path}")

    return {
        "oos_nav": oos_nav_final,
        "benchmark_nav": bm_nav_final,
        "oos_returns": oos_concat,
        "metrics": metrics,
        "fold_details": pd.DataFrame(fold_details),
    }
