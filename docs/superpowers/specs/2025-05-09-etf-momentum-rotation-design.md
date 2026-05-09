# ETF 动量轮动系统 V1 — 设计文档

## 概述

一个面向量化新手的 A 股 ETF 周度动量轮动系统。每周计算 ETF 池中每只 ETF 过去 20 个交易日的涨幅，选择涨幅最强的 1 只持有，下周重新排名换仓。

## 环境与依赖

- Python >= 3.12
- `akshare` — A 股/ETF 数据接口
- `pandas` — 数据处理
- `numpy` — 数值计算
- `matplotlib` — 图表输出

## ETF 池（13 只）

| 代码 | 名称 | 类别 |
|------|------|------|
| 510300 | 沪深300ETF | A股宽基 |
| 159915 | 创业板ETF | A股宽基 |
| 510880 | 红利ETF | A股策略 |
| 512000 | 券商ETF | A股行业 |
| 513100 | 纳指ETF | 跨境 |
| 513050 | 中概互联ETF | 跨境 |
| 159322 | 黄金ETF平安 | 商品 |
| 561660 | 通用航空ETF平安 | A股行业 |
| 159873 | 医疗设备ETF天弘 | A股行业 |
| 516160 | 新能源ETF南方 | A股行业 |
| 159518 | 标普油气ETF嘉实 | 跨境/商品 |
| 512760 | 芯片ETF国泰 | A股行业 |
| 513650 | 标普500ETF南方 | 跨境 |

## 模块架构

```
src/
├── config.py              # 集中管理参数常量（ETF池、日期、窗口）
├── data/
│   ├── __init__.py
│   └── fetcher.py         # akshare数据拉取 + 本地CSV缓存
├── strategy/
│   ├── __init__.py
│   └── momentum.py        # 20日动量计算 + 周度信号生成
├── backtest/
│   ├── __init__.py
│   └── engine.py          # 逐周模拟调仓 + 绩效统计
├── output/
│   ├── __init__.py
│   └── report.py          # 净值对比图 + Excel持仓明细
└── __init__.py
main.py                     # 入口：顺序调用各模块
```

### 数据流

```
akshare API
  → data/fetcher.py（拉取+本地CSV缓存）
  → strategy/momentum.py（计算20日动量，周频排名）
  → backtest/engine.py（模拟调仓，生成净值序列）
  → output/report.py（图表 + Excel）
```

### 各模块接口

#### `config.py`
- `ETF_POOL: dict[str, str]` — 代码→名称映射
- `START_DATE / END_DATE` — 回测起止日期
- `MOMENTUM_WINDOW: int` — 动量窗口（20个交易日）
- `BENCHMARK_CODE: str` — 基准代码（510300）

#### `data/fetcher.py`
- `fetch_all_etf_data(codes: list[str], start: str, end: str) -> pd.DataFrame`
  - 拉取所有 ETF 日线，缓存到 `data/cache/` 下的 CSV
  - 返回 MultiIndex DataFrame（code, date）或合并后的宽表
- `load_or_fetch(codes, start, end) -> dict[str, pd.DataFrame]`
  - 先检查缓存，缺失的才请求 akshare

#### `strategy/momentum.py`
- `calc_momentum(prices: pd.DataFrame, window: int) -> pd.DataFrame`
  - 输入：日线收盘价（列=ETF代码，行=日期）
  - 输出：每只 ETF 每天过去 N 日涨幅
- `generate_weekly_signals(momentum: pd.DataFrame) -> pd.DataFrame`
  - 每周最后一个交易日选动量最强的 ETF
  - 输出：每周持仓信号（date, code, weight=1.0）

#### `backtest/engine.py`
- `run_backtest(prices: pd.DataFrame, signals: pd.DataFrame, benchmark_prices: pd.Series) -> dict`
  - 模拟每日净值计算
  - 换仓逻辑：周五生成信号，下周一开盘价买入
  - 返回净值序列、交易记录、绩效统计
- `calc_metrics(nav: pd.Series, benchmark_nav: pd.Series) -> dict`
  - 年化收益率、最大回撤、夏普比率、胜率

#### `output/report.py`
- `plot_equity_curve(strategy_nav, benchmark_nav)`
  - 双线净值图（策略 vs 基准）
- `export_to_excel(records, metrics, filepath)`
  - 输出持仓明细表 + 绩效汇总

## 回测逻辑细节

1. **数据频率**：日线
2. **调仓频率**：每周一次
3. **换仓日**：每周五收盘后计算排名，下周一以开盘价买入新头寸
4. **初始资金**：1.0（净值化）
5. **交易成本**：零（V1 忽略）
6. **基准**：沪深300（510300），买入持有

## 绩效指标

- 累计净值曲线
- 年化收益率
- 最大回撤（MDD）
- 夏普比率（无风险利率=0.02）
- 跑赢基准胜率（策略周收益 > 基准周收益 的周数比例）

## 非功能性要求

- **教学友好**：每行关键逻辑有中文注释，解释"为什么这样做"
- **类型注解**：所有函数参数和返回值标注类型
- **配置集中**：魔法数字不散落，统一在 config.py
- **线性流程**：main.py 按模块顺序调用，新手可逐行跟踪

## 回测时间范围

2022-01-01 至 2025-05-09（约3年）

## 适用范围

V1 仅支持「每周持有1只 ETF」的简单版本。后续可扩展为：
- 持有前 N 名 / 等权组合
- 灵活仓位（按动量强度加权）
- 增加交易成本模拟
- 更多 ETF 纳入池子
