# ETF 动量轮动优化 V2 — 设计文档

## 概述

在 V1 的基础上增加四层优化：参数扫描、风险调整动量、多仓位分散、趋势过滤。目标是找到夏普比率最优的参数组合。

## 四层优化

### L1: 参数扫描优化器 (`src/optimizer/scanner.py`)

- 网格搜索 80 组参数组合
- 每组运行完整回测，记录夏普比率
- 输出：最优参数 + 全量对比 Excel + 最优策略的净值图
- 搜索空间：动量窗口 [5,10,20,40,60] × 持仓数 [1,2,3,5] × 风险调整 [开,关] × 趋势过滤 [开,关]

### L2: 风险调整动量 (`src/strategy/momentum.py`)

- 新增 `calc_risk_adjusted_momentum(prices, window)` 函数
- 公式：20日涨跌幅 / 20日日收益率标准差
- 波动越平稳的 ETF 得分越高
- 通过 `src/config.py` 的 `USE_RISK_ADJUSTED` 开关控制

### L3: 多仓位分散 (`src/strategy/momentum.py` + `src/backtest/engine.py`)

- `generate_weekly_signals` 支持 `top_n` 参数，返回前N名
- `_assign_daily_holdings` 支持多持仓矩阵（每行 N 个 1/N 权重，其余0）
- 通过 `src/config.py` 的 `TOP_N` 控制

### L4: 趋势过滤 (`src/strategy/momentum.py`)

- 绝对动量：仅持仓收盘价 > 过去M日均线的 ETF
- 不满足的 ETF 直接从候选池排除；全部不满足则空仓
- 通过 `src/config.py` 的 `TREND_WINDOW` 和 `USE_TREND_FILTER` 控制

## 新增配置项 (`src/config.py`)

```python
TOP_N: int = 1            # 持仓数量（1=原策略）
USE_RISK_ADJUSTED: bool = False  # 是否启用风险调整
TREND_WINDOW: int = 60    # 趋势过滤的MA窗口
USE_TREND_FILTER: bool = False  # 是否启用绝对动量过滤
```

## 文件变更

| 文件 | 操作 | 内容 |
|------|------|------|
| `src/config.py` | 修改 | 新增 4 个配置项 |
| `src/strategy/momentum.py` | 修改 | 新增风险调整动量、top_n支持、趋势过滤 |
| `src/backtest/engine.py` | 修改 | 支持多持仓矩阵 |
| `src/optimizer/__init__.py` | 新建 | 包初始化 |
| `src/optimizer/scanner.py` | 新建 | 网格搜索引擎 |
| `main.py` | 修改 | 增加 `--optimize` 模式 |

## 入口

```
python main.py             # V1 单次回测（向后兼容）
python main.py --optimize  # 80组网格扫描 → 最优参数
```
