# ETF 动量轮动系统 V1 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个 A 股 ETF 周度动量轮动回测系统，每周选过去20日涨幅最强的1只 ETF 持有，输出净值曲线图和 Excel 持仓明细。

**Architecture:** 模块化分层 — 配置层(config) → 数据层(data/fetcher) → 策略层(strategy/momentum) → 回测层(backtest/engine) → 输出层(output/report)，由 main.py 线性编排。

**Tech Stack:** Python 3.12+, akshare (数据), pandas (处理), numpy (计算), matplotlib (图表)

---

## 文件结构

```
src/
├── __init__.py              (已有，保持)
├── config.py                (新建)  ETF池/日期/参数
├── data/
│   ├── __init__.py          (新建)
│   └── fetcher.py           (新建)  数据拉取+缓存
├── strategy/
│   ├── __init__.py          (新建)
│   └── momentum.py          (新建)  动量计算+信号生成
├── backtest/
│   ├── __init__.py          (新建)
│   └── engine.py            (新建)  回测引擎+绩效统计
├── output/
│   ├── __init__.py          (新建)
│   └── report.py            (新建)  图表+Excel
main.py                       (修改)  入口主流程
```

---

### Task 1: 项目骨架 — 目录与 `__init__.py`

**Files:**
- Create: `src/data/__init__.py`
- Create: `src/strategy/__init__.py`
- Create: `src/backtest/__init__.py`
- Create: `src/output/__init__.py`

- [ ] **Step 1: 创建子目录结构**

```bash
mkdir -p src/data src/strategy src/backtest src/output data/cache
ls -R src/
```

- [ ] **Step 2: 创建各包的 `__init__.py`**

```python
# src/data/__init__.py
"""数据模块：从 akshare 拉取 ETF 日线行情，支持本地 CSV 缓存。"""
```

```python
# src/strategy/__init__.py
"""策略模块：动量计算、信号生成。"""
```

```python
# src/backtest/__init__.py
"""回测模块：模拟调仓、净值计算、绩效统计。"""
```

```python
# src/output/__init__.py
"""输出模块：净值曲线图、Excel 持仓报告。"""
```

- [ ] **Step 3: 提交**

```bash
git add src/data/ src/strategy/ src/backtest/ src/output/
git commit -m "chore: create project package structure"
```

---

### Task 2: `src/config.py` — 配置常量

**Files:**
- Create: `src/config.py`

- [ ] **Step 1: 写入配置模块**

```python
"""
集中管理所有配置常量，避免魔法数字散落各处。

修改策略参数（如动量窗口、回测日期）只需改这一个文件。
"""

# ---- ETF 池 ----
# 代码 -> 名称 的映射，既是数据拉取列表，也是策略的选股范围
ETF_POOL: dict[str, str] = {
    "510300": "沪深300ETF",
    "159915": "创业板ETF",
    "510880": "红利ETF",
    "512000": "券商ETF",
    "513100": "纳指ETF",
    "513050": "中概互联ETF",
    "159322": "黄金ETF平安",
    "561660": "通用航空ETF平安",
    "159873": "医疗设备ETF天弘",
    "516160": "新能源ETF南方",
    "159518": "标普油气ETF嘉实",
    "512760": "芯片ETF国泰",
    "513650": "标普500ETF南方",
}

# ---- 回测时间 ----
# akshare 要求日期格式为 YYYYMMDD（无分隔符）
START_DATE: str = "20220101"
END_DATE: str = "20250509"

# ---- 策略参数 ----
MOMENTUM_WINDOW: int = 20  # 动量计算窗口：过去 N 个交易日

# ---- 基准 ----
BENCHMARK_CODE: str = "510300"  # 沪深300ETF，用于对比

# ---- 输出 ----
OUTPUT_DIR: str = "output"  # 图表和 Excel 的输出目录

# ---- 缓存 ----
CACHE_DIR: str = "data/cache"  # CSV 缓存的存放目录
```

- [ ] **Step 2: 验证模块可导入**

```bash
python -c "from src.config import ETF_POOL, MOMENTUM_WINDOW; print(f'池中 ETF 数量: {len(ETF_POOL)}, 动量窗口: {MOMENTUM_WINDOW}')"
```

- [ ] **Step 3: 提交**

```bash
git add src/config.py
git commit -m "feat: add config module with ETF pool and strategy parameters"
```

---

### Task 3: `src/data/fetcher.py` — 数据获取与缓存

**Files:**
- Create: `src/data/fetcher.py`

**核心逻辑:**
1. 遍历 ETF_POOL，调用 akshare 接口获取每只 ETF 的历史日线
2. 首次拉取后写入 CSV 缓存到 `data/cache/` 目录，后续只做增量更新
3. 将所有 ETF 的收盘价合并为一张宽表：行 = 日期，列 = ETF 代码
4. 同时返回基准 ETF 的收盘价序列

- [ ] **Step 1: 写入 `src/data/fetcher.py`**

```python
"""
数据获取模块：从 akshare 拉取 ETF 日线行情，并支持本地 CSV 缓存。

工作流程：
  1. 检查 data/cache/ 下是否有缓存 CSV
  2. 对缺失或过期的数据，调用 akshare 接口拉取
  3. 拉取后更新本地缓存
  4. 将所有 ETF 的收盘价合并为一张宽表（行=日期，列=代码）

宽表格式便于后续动量计算和回测的向量化处理。
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
    # parse_dates 确保「日期」列被解析为 datetime 类型
    df = pd.read_csv(path, parse_dates=["日期"])
    return df


def _save_cache(code: str, df: pd.DataFrame) -> None:
    """将数据写入本地 CSV 缓存"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_csv(_get_cache_path(code), index=False)


def _fetch_single(code: str, start: str, end: str) -> pd.DataFrame:
    """
    从 akshare 拉取单只 ETF 的历史日线数据。

    akshare 的 fund_etf_hist_em 返回的 DataFrame 包含以下列：
      日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率

    adjust="qfq" 表示前复权，确保价格连续可比。
    """
    df = ak.fund_etf_hist_em(
        symbol=code,
        period="daily",
        start_date=start,
        end_date=end,
        adjust="qfq",
    )
    # akshare 返回的日期列可能是字符串，统一转为 datetime
    df["日期"] = pd.to_datetime(df["日期"])
    return df


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
        # 无缓存，全量拉取
        df = _fetch_single(code, start, end)
        _save_cache(code, df)
        return df

    # 有缓存，检查是否需要增量更新
    latest_date = cached["日期"].max()
    end_dt = datetime.strptime(end, "%Y%m%d")

    if latest_date >= end_dt:
        # 缓存已覆盖目标日期，直接使用
        return cached

    # 缓存不够新，增量拉取
    inc_start = (latest_date + timedelta(days=1)).strftime("%Y%m%d")
    new_data = _fetch_single(code, inc_start, end)

    # 拼接新旧数据，去重后按日期排序
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
        prices: DataFrame，行索引=日期，列=ETF 代码，值=收盘价（前复权）
        benchmark: Series，行索引=日期，值=基准 ETF 收盘价

    使用示例：
        prices, benchmark = fetch_all_etf_data()
        print(prices.head())  # 每列是一只 ETF 的收盘价序列
    """
    if codes is None:
        codes = list(ETF_POOL.keys())

    # 字典推导：遍历所有代码，key=代码，value=该 ETF 的完整 DataFrame
    all_data: dict[str, pd.DataFrame] = {}
    for code in codes:
        all_data[code] = _smart_fetch(code, start, end)

    # 将所有 ETF 的收盘价合并成一张宽表
    # 只保留 [日期, 收盘] 两列，以代码为列名做 pivot
    close_dfs = []
    for code, df in all_data.items():
        close_series = df.set_index("日期")["收盘"].rename(code)
        close_dfs.append(close_series)

    # axis=1 按列拼接（横向），join="outer" 确保所有日期的并集都被保留
    prices = pd.concat(close_dfs, axis=1)
    prices = prices.sort_index()

    # 前向填充：处理非交易日（如跨境ETF在国外假期时不交易）
    prices = prices.ffill()

    # 提取基准序列
    benchmark = prices[BENCHMARK_CODE].dropna()

    return prices, benchmark
```

- [ ] **Step 2: 验证数据拉取（会触发 akshare 网络请求）**

```bash
python -c "
from src.data.fetcher import fetch_all_etf_data
prices, benchmark = fetch_all_etf_data()
print(f'价格表形状: {prices.shape}（行=交易日, 列=ETF数量）')
print(f'日期范围: {prices.index[0].date()} ~ {prices.index[-1].date()}')
print(f'前5行:')
print(prices.head())
print(f'缓存文件数: $(ls data/cache/*.csv | wc -l)')
"
```

确保输出显示 13 列（13只ETF），日期范围覆盖 2022-2025。

- [ ] **Step 3: 提交**

```bash
git add src/data/fetcher.py data/cache/
git commit -m "feat: add data fetcher with akshare integration and CSV cache"
```

---

### Task 4: `src/strategy/momentum.py` — 动量计算与信号生成

**Files:**
- Create: `src/strategy/momentum.py`

**核心逻辑:**
1. 用收盘价宽表计算每只 ETF 过去 N 日的涨跌幅（向量化 `pct_change`）
2. 定位每周最后一个交易日（周五），选出动量最强的一只
3. 输出信号表：每个周五对应下周持有哪些 ETF

- [ ] **Step 1: 写入 `src/strategy/momentum.py`**

```python
"""
策略模块：动量计算与周度信号生成。

核心思想（相对动量轮动）：
  在每周最后一个交易日收盘后，计算 ETF 池中每只 ETF 过去 N 个交易日的涨幅，
  选出涨幅最大的 1 只，下周全仓持有。

为什么用 20 日动量？
  20 个交易日 ≈ 1 个自然月，既能捕捉中期趋势，又不会太滞后。
"""

import pandas as pd

from src.config import MOMENTUM_WINDOW


def calc_momentum(
    prices: pd.DataFrame,
    window: int = MOMENTUM_WINDOW,
) -> pd.DataFrame:
    """
    计算每只 ETF 每日的 N 日动量（过去 N 个交易日的累计涨跌幅）。

    Args:
        prices: 收盘价宽表，行索引=日期，列=ETF代码
        window: 回看窗口（交易日数）

    Returns:
        momentum: 同形状的 DataFrame，值为过去 window 日的涨跌幅
                  例如 0.05 表示过去 20 日涨了 5%

    实现原理：
        pandas 的 pct_change(window) 计算 (当日价格 - window日前价格) / window日前价格
        等价于过去 window 日的累计收益率。
    """
    # 向量化计算：一次 pct_change 算出所有 ETF 所有日期的动量
    momentum = prices.pct_change(periods=window)
    return momentum


def _get_weekly_last_dates(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """
    从日线日期序列中，提取每周最后一个交易日。

    逻辑：
      按「年-周」分组，取每组最后一个日期。
      这样即使某周周五休市，也能正确取到当周最后交易日（如周四）。
    """
    # isocalendar() 返回 (ISO_year, ISO_week, ISO_weekday)
    iso = dates.isocalendar()
    week_groups = dates.groupby([iso["year"].values, iso["week"].values])
    weekly_last = week_groups.last()
    return pd.DatetimeIndex(weekly_last.values)


def generate_weekly_signals(
    prices: pd.DataFrame,
    window: int = MOMENTUM_WINDOW,
) -> pd.DataFrame:
    """
    生成周度调仓信号。

    Args:
        prices: 收盘价宽表
        window: 动量窗口

    Returns:
        signals: DataFrame，包含三列：
          - date:       信号生成日期（周五）
          - code:       被选中的 ETF 代码
          - name:       ETF 名称（便于阅读）
          - momentum:   该 ETF 当时的动量值

    信号逻辑：
      每周五收盘后 → 看各 ETF 过去 20 日涨幅 → 选涨幅最大的 → 下周一买入
    """
    from src.config import ETF_POOL

    # Step 1: 计算所有 ETF 的动量
    momentum_df = calc_momentum(prices, window)

    # Step 2: 找出每周最后一个交易日
    weekly_dates = _get_weekly_last_dates(prices.index)

    # Step 3: 只取每周最后一天的动量行
    # reindex 后 ffill 是因为 momentum 列的日期可能略少于 prices（窗口期）
    weekly_momentum = momentum_df.reindex(weekly_dates, method="ffill")

    # Step 4: 对每一行（每周），用 idxmax 找出动量最强的 ETF 代码
    # idxmax(axis=1) 沿列方向找最大值，返回列名（即 ETF 代码）
    signals: list[dict] = []
    for date in weekly_momentum.index:
        row = weekly_momentum.loc[date]
        # 跳过全为 NaN 的行（窗口期尚未满足）
        if row.isna().all():
            continue
        best_code = row.idxmax()
        best_momentum = row[best_code]

        # 只选正动量的 ETF，若全部下跌则空仓
        if best_momentum <= 0:
            continue

        signals.append({
            "date": date,
            "code": best_code,
            "name": ETF_POOL.get(best_code, ""),
            "momentum": round(float(best_momentum), 4),
        })

    signal_df = pd.DataFrame(signals)
    if signal_df.empty:
        return signal_df

    return signal_df.sort_values("date").reset_index(drop=True)
```

- [ ] **Step 2: 用真实数据验证信号生成**

```bash
python -c "
import sys
sys.path.insert(0, '.')
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_weekly_signals

prices, benchmark = fetch_all_etf_data()
signals = generate_weekly_signals(prices)
print(f'共生成 {len(signals)} 条周度信号')
print(f'信号日期范围: {signals[\"date\"].min().date()} ~ {signals[\"date\"].max().date()}')
print(f'信号示例（最近10条）:')
print(signals.tail(10))
# 检查每只 ETF 被选中的次数
print(f'\n各 ETF 被选中次数:')
print(signals['name'].value_counts())
"
```

- [ ] **Step 3: 提交**

```bash
git add src/strategy/momentum.py
git commit -m "feat: add momentum calculation and weekly signal generation"
```

---

### Task 5: `src/backtest/engine.py` — 回测引擎

**Files:**
- Create: `src/backtest/engine.py`

**核心逻辑:**
1. 根据周度信号表，为每个交易日分配策略持仓
2. 每个交易日，策略的收益率 = 当天持有 ETF 的日收益率
3. 策略净值 = (1 + 日收益率) 的累积乘积
4. 计算绩效指标：年化收益、最大回撤、夏普比率、胜率

- [ ] **Step 1: 写入 `src/backtest/engine.py`**

```python
"""
回测引擎：根据信号表模拟每周调仓，计算策略净值和绩效指标。

核心思路（向量化回测）：
  不需要逐日循环，而是利用 pandas 的向量化操作：
  1. 计算所有 ETF 的每日收益率矩阵
  2. 根据信号表，将策略每天「实际持有」的 ETF 收益率取出
  3. 累积乘积 → 策略净值曲线

为什么用向量化？
  逐日 for 循环在 3 年数据（约 750 个交易日）上也能跑，但向量化更简洁高效。
"""

import numpy as np
import pandas as pd


def _assign_daily_holdings(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
) -> pd.DataFrame:
    """
    将周度信号展开为每日持仓矩阵。

    Args:
        signals: 周度信号表（date, code, name, momentum）
        prices: 收盘价宽表

    Returns:
        holdings: DataFrame，行=日期，列=ETF代码，值=0或1
                  每行有且仅有一个 1，表示当天持有哪只 ETF

    逻辑：
      从信号日期（周五）的下一个交易日起，到下一个信号日之前，
      都持有该信号选中的 ETF。
    """
    all_dates = prices.index
    holdings = pd.DataFrame(0, index=all_dates, columns=prices.columns)

    if signals.empty:
        return holdings

    for i, row in signals.iterrows():
        signal_date = row["date"]
        code = row["code"]

        # 确定持有期的起止日期
        # 信号在周五收盘后生成，从下一个交易日（通常是下周一）开始持有
        signal_pos = all_dates.get_loc(signal_date)
        start_idx = signal_pos + 1  # 下一交易日

        if i + 1 < len(signals):
            next_signal_date = signals.iloc[i + 1]["date"]
            end_idx = all_dates.get_loc(next_signal_date) + 1
        else:
            end_idx = len(all_dates)

        # 边界保护：防止 start_idx 超出范围
        if start_idx >= len(all_dates):
            continue

        # 持有期内，该 ETF 标记为 1
        holdings.iloc[start_idx:end_idx, holdings.columns.get_loc(code)] = 1

    return holdings


def run_backtest(
    prices: pd.DataFrame,
    signals: pd.DataFrame,
    benchmark_prices: pd.Series,
) -> dict:
    """
    执行回测，返回净值序列、交易记录和绩效指标。

    Args:
        prices: 收盘价宽表
        signals: 周度信号表
        benchmark_prices: 基准 ETF 收盘价序列

    Returns:
        字典包含：
          - nav:            策略净值 Series
          - benchmark_nav:  基准净值 Series
          - daily_returns:  策略日收益率 Series
          - signals:        信号表（含增补信息）
          - metrics:        绩效指标 dict
    """
    # ---- 计算每日收益率矩阵 ----
    # 每只 ETF 的日收益率 = (今收 - 昨收) / 昨收
    daily_returns = prices.pct_change()

    # ---- 将信号转为每日持仓 ----
    holdings = _assign_daily_holdings(signals, prices)

    # ---- 策略每日收益率 = 当天持有 ETF 的收益率 ----
    # holdings * daily_returns：持有 ETF 的收益为实际值，其余为 0
    # sum(axis=1)：对每行求和，因为每行只有一个 1，等价于取出持有 ETF 的收益率
    strategy_daily_returns = (holdings * daily_returns).sum(axis=1)

    # 空仓期（没有信号的日子）收益率为 0
    strategy_daily_returns = strategy_daily_returns.fillna(0)

    # ---- 计算净值曲线 ----
    # 净值 = (1 + 日收益率) 的累积乘积，初始净值为 1.0
    strategy_nav = (1 + strategy_daily_returns).cumprod()
    strategy_nav.name = "strategy"

    # 基准净值同理：买入持有沪深300
    benchmark_returns = benchmark_prices.pct_change().fillna(0)
    benchmark_nav = (1 + benchmark_returns).cumprod()
    benchmark_nav.name = "benchmark"

    # ---- 计算绩效指标 ----
    metrics = _calc_metrics(strategy_nav, strategy_daily_returns, benchmark_nav, len(signals))

    return {
        "nav": strategy_nav,
        "benchmark_nav": benchmark_nav,
        "daily_returns": strategy_daily_returns,
        "signals": signals,
        "metrics": metrics,
    }


def _calc_metrics(
    nav: pd.Series,
    daily_returns: pd.Series,
    benchmark_nav: pd.Series,
    num_signals: int,
) -> dict:
    """
    计算策略绩效指标。

    指标说明：
      - 年化收益率：将总收益率折算为年化，公式 (最终净值)^(1/年数) - 1
      - 最大回撤：净值从最高点到之后最低点的最大跌幅百分比
      - 夏普比率：(年化收益 - 无风险利率) / 年化波动率，衡量风险调整后收益
      - 胜率：策略周收益跑赢基准的周数比例
    """
    # 年化收益率
    total_days = (nav.index[-1] - nav.index[0]).days
    years = total_days / 365.25
    total_return = nav.iloc[-1] / nav.iloc[0] - 1
    annual_return = (1 + total_return) ** (1 / years) - 1

    # 基准年化收益
    bm_total = benchmark_nav.iloc[-1] / benchmark_nav.iloc[0] - 1
    bm_annual = (1 + bm_total) ** (1 / years) - 1

    # 最大回撤
    rolling_max = nav.cummax()
    drawdown = (nav - rolling_max) / rolling_max
    max_drawdown = drawdown.min()

    # 夏普比率（无风险利率设为 2%）
    risk_free = 0.02
    excess_returns = daily_returns - risk_free / 252  # 252 个交易日
    sharpe = np.sqrt(252) * excess_returns.mean() / excess_returns.std() if excess_returns.std() > 0 else 0

    # 胜率（周度）
    strategy_weekly = nav.resample("W").last().pct_change().dropna()
    benchmark_weekly = benchmark_nav.resample("W").last().pct_change().dropna()
    common_weeks = strategy_weekly.index.intersection(benchmark_weekly.index)
    win_count = (strategy_weekly.loc[common_weeks] > benchmark_weekly.loc[common_weeks]).sum()
    total_weeks = len(common_weeks)
    win_rate = win_count / total_weeks if total_weeks > 0 else 0

    return {
        "累计收益率": f"{total_return:.2%}",
        "年化收益率": f"{annual_return:.2%}",
        "基准年化收益率": f"{bm_annual:.2%}",
        "最大回撤": f"{max_drawdown:.2%}",
        "夏普比率": f"{sharpe:.2f}",
        "胜率(vs基准)": f"{win_rate:.2%}（{win_count}/{total_weeks}周）",
        "回测年数": f"{years:.1f}",
        "交易次数": str(num_signals),
    }
```

- [ ] **Step 2: 运行完整回测**

```bash
python -c "
import sys
sys.path.insert(0, '.')
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_weekly_signals
from src.backtest.engine import run_backtest

# 完整数据流
prices, benchmark = fetch_all_etf_data()
signals = generate_weekly_signals(prices)
result = run_backtest(prices, signals, benchmark)

print('=== 绩效指标 ===')
for k, v in result['metrics'].items():
    print(f'  {k}: {v}')
print(f'\n=== 最新净值 ===')
print(f'  策略: {result[\"nav\"].iloc[-1]:.4f}')
print(f'  基准: {result[\"benchmark_nav\"].iloc[-1]:.4f}')
"
```

- [ ] **Step 3: 提交**

```bash
git add src/backtest/engine.py
git commit -m "feat: add backtest engine with vectorized simulation and performance metrics"
```

---

### Task 6: `src/output/report.py` — 图表与 Excel 输出

**Files:**
- Create: `src/output/report.py`

- [ ] **Step 1: 写入 `src/output/report.py`**

```python
"""
输出模块：生成净值曲线对比图和 Excel 持仓明细报告。

输出文件：
  - output/equity_curve.png   净值对比图
  - output/trade_details.xlsx  交易明细 + 绩效汇总
"""

import os

import matplotlib.pyplot as plt
import pandas as pd

# 设置中文字体，避免图表中文乱码
plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "SimHei", "WenQuanYi Micro Hei"]
plt.rcParams["axes.unicode_minus"] = False  # 解决负号显示问题


def plot_equity_curve(
    strategy_nav: pd.Series,
    benchmark_nav: pd.Series,
    save_path: str = "output/equity_curve.png",
) -> None:
    """
    绘制策略 vs 基准的净值对比曲线。

    Args:
        strategy_nav: 策略净值序列
        benchmark_nav: 基准净值序列
        save_path: 图片保存路径
    """
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    fig, ax = plt.subplots(figsize=(14, 6))

    # 对齐两条曲线到共同的日期范围
    common = strategy_nav.dropna().index.intersection(benchmark_nav.dropna().index)

    ax.plot(strategy_nav.loc[common], label="Strategy (Momentum Rotation)", linewidth=1.5, color="#1f77b4")
    ax.plot(benchmark_nav.loc[common], label="Benchmark (CSI 300)", linewidth=1.2, color="#d62728", alpha=0.8)

    # 填充策略 vs 基准之间的差异区域
    ax.fill_between(
        strategy_nav.loc[common].index,
        strategy_nav.loc[common].values,
        benchmark_nav.loc[common].values,
        where=(strategy_nav.loc[common].values >= benchmark_nav.loc[common].values),
        color="#1f77b4", alpha=0.1, interpolate=True,
    )
    ax.fill_between(
        strategy_nav.loc[common].index,
        strategy_nav.loc[common].values,
        benchmark_nav.loc[common].values,
        where=(strategy_nav.loc[common].values < benchmark_nav.loc[common].values),
        color="#d62728", alpha=0.1, interpolate=True,
    )

    ax.set_title("ETF Momentum Rotation vs. CSI 300 Benchmark", fontsize=14, fontweight="bold")
    ax.set_xlabel("Date")
    ax.set_ylabel("Net Asset Value (NAV)")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.axhline(y=1.0, color="gray", linestyle="--", linewidth=0.5, alpha=0.5)

    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)
    print(f"[图表] 已保存: {save_path}")


def export_to_excel(
    signals: pd.DataFrame,
    metrics: dict,
    strategy_nav: pd.Series,
    benchmark_nav: pd.Series,
    save_path: str = "output/trade_details.xlsx",
) -> None:
    """
    导出 Excel 报告，包含：
      Sheet1「持仓明细」：每次调仓的日期、代码、名称、动量值
      Sheet2「绩效汇总」：策略 vs 基准的各项指标
      Sheet3「净值序列」：每日策略净值和基准净值

    Args:
        signals: 周度信号表
        metrics: 绩效指标字典
        strategy_nav: 策略净值序列
        benchmark_nav: 基准净值序列
        save_path: Excel 保存路径
    """
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    with pd.ExcelWriter(save_path, engine="openpyxl") as writer:
        # Sheet 1: 持仓明细
        if not signals.empty:
            signals_out = signals.copy()
            signals_out["date"] = signals_out["date"].dt.strftime("%Y-%m-%d")
            signals_out.to_excel(writer, sheet_name="持仓明细", index=False)

        # Sheet 2: 绩效汇总
        metrics_df = pd.DataFrame(
            {"指标": list(metrics.keys()), "数值": list(metrics.values())}
        )
        metrics_df.to_excel(writer, sheet_name="绩效汇总", index=False)

        # Sheet 3: 净值序列
        common = strategy_nav.dropna().index.intersection(benchmark_nav.dropna().index)
        nav_df = pd.DataFrame({
            "日期": common,
            "策略净值": strategy_nav.loc[common].values,
            "基准净值": benchmark_nav.loc[common].values,
        })
        nav_df.to_excel(writer, sheet_name="净值序列", index=False)

    print(f"[Excel] 已保存: {save_path}")
```

- [ ] **Step 2: 运行生成图表和 Excel**

```bash
python -c "
import sys
sys.path.insert(0, '.')
from src.data.fetcher import fetch_all_etf_data
from src.strategy.momentum import generate_weekly_signals
from src.backtest.engine import run_backtest
from src.output.report import plot_equity_curve, export_to_excel

prices, benchmark = fetch_all_etf_data()
signals = generate_weekly_signals(prices)
result = run_backtest(prices, signals, benchmark)

plot_equity_curve(result['nav'], result['benchmark_nav'])
export_to_excel(result['signals'], result['metrics'], result['nav'], result['benchmark_nav'])
print('Done!')
ls -lh output/
"
```

确保 `output/equity_curve.png` 和 `output/trade_details.xlsx` 都生成成功。

- [ ] **Step 3: 提交**

```bash
git add src/output/report.py
git commit -m "feat: add report module with NAV chart and Excel export"
```

---

### Task 7: `main.py` — 主入口

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 重写 `main.py`**

```python
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

from src.config import ETF_POOL, START_DATE, END_DATE, MOMENTUM_WINDOW, BENCHMARK_CODE, OUTPUT_DIR
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
    export_to_excel(result["signals"], result["metrics"], result["nav"], result["benchmark_nav"], excel_path)

    print(f"\n{'=' * 60}")
    print(f"  完成！输出文件:")
    print(f"    图表: {chart_path}")
    print(f"    Excel: {excel_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 完整运行**

```bash
python main.py
```

确保四个步骤依次执行，无报错，最终输出净值图和 Excel。

- [ ] **Step 3: 提交**

```bash
git add main.py
git commit -m "feat: wire up main entry point for end-to-end backtest run"
```

---

### Task 8: 验证与清理

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: 更新 `.gitignore`**

确保 output/ 和 data/cache/ 不提交（缓存文件、图表为运行产物）：

读取 `.gitignore`，追加以下内容：

```
# Output directory (generated files)
output/

# Data cache (generated by fetcher)
data/cache/
```

- [ ] **Step 2: 最终验证**

```bash
# 清理可能的残留文件后重新运行
rm -rf data/cache/ output/
python main.py

# 验证产出文件
echo "--- 输出文件 ---"
ls -lh output/
echo "--- 缓存文件数 ---"
ls data/cache/ | wc -l
echo "--- Git 状态 ---"
git status
```

- [ ] **Step 3: 最终提交**

```bash
git add .gitignore
git commit -m "chore: update gitignore for cache and output dirs"
```
