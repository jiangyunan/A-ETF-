"""
数据获取模块：从 akshare 拉取 ETF 日线行情，并支持本地 CSV 缓存。

工作流程：
  1. 检查 data/cache/ 下是否有缓存 CSV
  2. 对缺失或过期的数据，调用 akshare 接口拉取
  3. 拉取后更新本地缓存
  4. 将所有 ETF 的收盘价合并为一张宽表（行=日期，列=代码）

宽表格式便于后续动量计算和回测的向量化处理。

数据来源：新浪财经（akshare.fund_etf_hist_sina）
"""

import os
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

from src.config import ETF_POOL, START_DATE, END_DATE, CACHE_DIR, BENCHMARK_CODE


def _get_cache_path(code: str) -> str:
    """返回某只 ETF 的缓存文件路径，如 data/cache/510300.csv"""
    return os.path.join(CACHE_DIR, f"{code}.csv")


def _load_cache(code: str) -> pd.DataFrame | None:
    """尝试从本地 CSV 加载缓存数据，不存在则返回 None"""
    path = _get_cache_path(code)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, parse_dates=["日期"])
    return df


def _save_cache(code: str, df: pd.DataFrame) -> None:
    """将数据写入本地 CSV 缓存"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        df.to_csv(_get_cache_path(code), index=False)
    except OSError as e:
        print(f"[警告] 缓存写入失败 {code}: {e}")


def _to_sina_symbol(code: str) -> str:
    """将纯数字 ETF 代码转换为新浪接口所需的格式（sh/sz 前缀）"""
    if code.startswith(("5", "6")):
        return f"sh{code}"
    else:
        return f"sz{code}"


def _fetch_single(code: str, start: str, end: str) -> pd.DataFrame:
    """
    从 akshare（新浪财经）拉取单只 ETF 的历史日线数据。

    fund_etf_hist_sina 返回的 DataFrame 列名：
      date, open, high, low, close, volume, amount

    新浪接口不提供 start/end 日期参数和复权选项，
    此处拉取全部历史数据后按日期范围过滤。
    """
    symbol = _to_sina_symbol(code)
    df = ak.fund_etf_hist_sina(symbol=symbol)

    if df.empty:
        return pd.DataFrame(columns=["日期", "收盘"])

    # 新浪接口返回英文列名，统一映射为中文（与缓存/下游一致）
    column_map = {
        "date": "日期",
        "open": "开盘",
        "high": "最高",
        "low": "最低",
        "close": "收盘",
        "volume": "成交量",
        "amount": "成交额",
    }
    df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})

    # 保留下游需要的列
    keep_cols = [c for c in column_map.values() if c in df.columns]
    df = df[keep_cols]

    # 过滤日期范围
    df["日期"] = pd.to_datetime(df["日期"])
    start_dt = datetime.strptime(start, "%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d")
    df = df[(df["日期"] >= start_dt) & (df["日期"] <= end_dt)]

    return df.reset_index(drop=True)


def _smart_fetch(code: str, start: str, end: str) -> pd.DataFrame:
    """
    智能获取：优先用缓存，缺失部分增量拉取。

    策略：
    1. 如果无缓存 → 全量拉取
    2. 如果有缓存 → 检查最新日期是否覆盖到 end_date
       - 如果已覆盖 → 直接用缓存
       - 如果未覆盖 → 只拉取缓存最新日期之后的数据，然后拼接
    """
    cached = _load_cache(code)

    if cached is None:
        df = _fetch_single(code, start, end)
        _save_cache(code, df)
        return df

    latest_date = cached["日期"].max()
    end_dt = datetime.strptime(end, "%Y%m%d")

    if latest_date >= end_dt:
        return cached

    # 缓存不够新，增量拉取（这里仍拉全量因为 fund_etf_hist_sina 不支持分段，
    # 但只保留缓存之后的数据，避免重复）
    inc_start = (latest_date + timedelta(days=1)).strftime("%Y%m%d")
    new_data = _fetch_single(code, inc_start, end)

    if new_data.empty:
        return cached

    combined = pd.concat([cached, new_data], ignore_index=True)
    combined = combined.drop_duplicates(subset=["日期"]).sort_values("日期")
    combined = combined.reset_index(drop=True)

    _save_cache(code, combined)
    return combined


def fetch_all_etf_data(
    codes: list[str] | None = None,
    start: str = START_DATE,
    end: str = END_DATE,
) -> tuple[pd.DataFrame, pd.Series]:
    """
    拉取所有 ETF 的日线数据，返回收盘价宽表和基准序列。

    Args:
        codes: 要拉取的 ETF 代码列表，默认取 ETF_POOL 的全部
        start: 起始日期，格式 YYYYMMDD
        end: 截止日期，格式 YYYYMMDD

    Returns:
        prices: DataFrame，行索引=日期，列=ETF代码，值=收盘价
        benchmark: Series，行索引=日期，值=基准 ETF 收盘价

    使用示例：
        prices, benchmark = fetch_all_etf_data()
        print(prices.head())  # 每列是一只 ETF 的收盘价序列
    """
    if codes is None:
        codes = list(ETF_POOL.keys())

    all_data: dict[str, pd.DataFrame] = {}
    for code in codes:
        all_data[code] = _smart_fetch(code, start, end)

    # 将所有 ETF 的收盘价合并成一张宽表
    close_dfs = []
    for code, df in all_data.items():
        close_series = df.set_index("日期")["收盘"].rename(code)
        close_dfs.append(close_series)

    prices = pd.concat(close_dfs, axis=1)
    prices = prices.sort_index()

    # 前向填充：处理非交易日（如跨境ETF在国外假期时不交易）
    # limit=5：最多填充连续5天，避免退市/长期停牌ETF的过期价格被无限传播
    prices = prices.ffill(limit=5)

    # 提取基准序列（若基准代码不在价格表中则用第一列兜底）
    if BENCHMARK_CODE in prices.columns:
        benchmark = prices[BENCHMARK_CODE].dropna()
    elif len(prices.columns) > 0:
        col = prices.columns[0]
        print(f"[警告] 基准 {BENCHMARK_CODE} 不在价格表中，用 {col} 兜底")
        benchmark = prices[col].dropna()
    else:
        benchmark = pd.Series(dtype=float)

    return prices, benchmark
