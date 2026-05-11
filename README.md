# ETF 动量轮动量化系统

A股 ETF 周度动量轮动回测系统，融合市场状态机、相关性控制、黑天鹅检测、溢价过滤、实盘交易记录等机构级技术。

## 快速开始

```bash
uv sync                          # 初始化
python main.py                   # 运行回测
python main.py --signal          # 本周持仓建议
python main.py --trade-ui        # 交互式交易录入面板
```

## 完整命令

| 命令 | 功能 | 耗时 |
|------|------|------|
| `python main.py` | 单次回测 | ~20s |
| `python main.py --signal` | 本周实盘持仓建议 | ~15s |
| `python main.py --log BUY/SELL 代码 价格 股数 [溢价%] [备注]` | 快捷交易录入 | 即时 |
| `python main.py --trade-ui` | 交互式交易录入面板 | 手动 |
| `python main.py --track` | 实盘交易统计（5维dashboard） | ~1s |
| `python main.py --preflight` | 盘前7项数据检查 | ~5s |
| `python main.py --alerts` | 6类风险告警检测 | ~5s |
| `python main.py --trade-source` | 交易来源拆解分析 | ~20s |
| `python main.py --attribution` | 收益归因分析 | ~20s |
| `python main.py --migrate` | CSV→SQLite 数据迁移 | ~1s |
| `python main.py --optimize` | 网格搜索最优参数 | ~5min |
| `python main.py --walk-forward` | 滚动回测（纯OOS） | ~5min |
| `python main.py --stability` | 参数稳定性热力图 | ~3min |
| `python main.py --monte-carlo` | 蒙特卡洛生存测试 | ~2min |

## 系统架构

```
src/
├── config.py              # 集中配置 + 模块优先级定义
├── data/
│   └── fetcher.py         # akshare 行情 + NAV 拉取 + CSV 缓存
├── strategy/
│   ├── momentum.py        # 动量计算 + 状态机 + 信号生成
│   └── black_swan.py      # 黑天鹅检测（VIX代理/全球崩盘/流动性）
├── backtest/
│   └── engine.py          # 回测引擎 + 绩效统计
├── output/
│   └── report.py          # 净值曲线图 + Excel 报告
├── optimizer/
│   ├── scanner.py         # 网格搜索最优参数
│   ├── walk_forward.py    # 滚动回测（纯OOS验证）
│   ├── param_stability.py # 二维参数稳定性热力图
│   ├── monte_carlo.py     # 蒙特卡洛生存测试
│   └── attribution.py     # 收益归因分析
├── ops/
│   ├── db.py              # SQLite 数据库（四表） + CRUD
│   ├── preflight.py       # 7项盘前检查
│   ├── risk_alerts.py     # 6类风险告警
│   ├── trade_log.py       # 交易日志 + 5维统计引擎
│   └── trade_ui.py        # 交互式交易录入面板
└── __init__.py
main.py                    # 主入口
```

## 核心策略

### 模块优先级

```
Level 0  黑天鹅      → VIX飙升降仓 / 全球崩盘强制防御
Level 1  溢价安全    → >12% 溢价禁止买入
Level 2  极端波动    → 波动率 > 90分位 → 仓位 × 0.5
Level 3  状态机      → 牛/震/熊 三态 → 选池 + 定窗口/持仓数
Level 4  动量        → 风险调整动量 → 排名选前 N
Level 5  溢价辅助    → 连续惩罚（<3%忽略，≥3%逐步衰减）
```

### 市场状态机

| 状态 | 条件 | 持仓数 | 动量窗口 |
|------|------|--------|----------|
| 牛市 | 广度>60% + MA20>MA60 + CSI300>MA120 | 3只 | 10天 |
| 震荡 | CSI300>MA120 或 广度>35% | 5只 | 20天 |
| 熊市 | — | 防御资产 | 40天 |

- **市场广度**：攻击池中站上 MA60 的 ETF 占比
- **防御资产**：国债ETF → 黄金ETF → 现金
- **相关性控制**：剔除与已选 ETF 相关性 > 0.75 的标的

### 溢价过滤

连续惩罚函数（仅溢价 > 3% 生效，QDII ETF 常驻 1~3% 不受影响）：

| 溢价 | 动量惩罚 | 权重衰减 |
|------|---------|---------|
| < 3% | 忽略 | 忽略 |
| 5% | ×0.875 | ×0.60 |
| 8% | ×0.68 | ×0.36 |
| > 12% | 踢出 | 踢出 |

### 黑天鹅检测

| 检测器 | 代理指标 | 触发动作 |
|--------|---------|---------|
| VIX 代理 | ETF 池 5日波动率 > 2.5× 历史中位 | 仓位 × 0.5 / × 0.3 |
| 全球崩盘 | 纳指/标普/日经/德国 3日内同步跌 > 3% | 强制切防御 |
| 流动性枯竭 | 3日均量 < 20% × 20日均量 | 暂停该 ETF |

### 波动率仓位控制

- 目标波动率仓位：高波降仓、低波加仓
- 极端波动自动降仓：波动率 > 90分位 → 仓位 × 0.5

## 实盘运维

### 交易录入

```bash
# 交互面板（自动显示操作建议）
python main.py --trade-ui

# 命令行快捷录入
python main.py --log BUY 513100 1.250 1000 1.2% 按信号买入
python main.py --log SELL 510300 1.150 500
```

自动写入 SQLite 数据库 `ops/trade_log.db`。

### 交易统计

```bash
python main.py --track
```

输出 5 维度 dashboard：
- 理论 vs 实盘偏差
- 调仓滑点
- 溢价真实影响
- 状态切换频率
- 回撤恢复时间

### 收益归因

```bash
python main.py --attribution
```

输出 4 维度：按 ETF / 按状态 / 按年份 / 按资产类型。

## 参数配置

集中在 `src/config.py`：

```python
MOMENTUM_WINDOW = 20             # 动量窗口
TOP_N = 5                        # 持仓数
USE_MARKET_STATE_MACHINE = True  # 市场状态机
USE_CORRELATION_FILTER = True    # 相关性过滤
USE_VOL_TARGET = True            # 波动率仓位控制
USE_PREMIUM_FILTER = True        # 溢价过滤
MARKET_MA_WINDOW = 120           # 牛熊分界线
REBALANCE_FREQ = 1               # 调仓频率（1=周）
```

## ETF 池

```
中国宽基：510300(沪深300)  510500(中证500)  159915(创业板)  510880(红利)
中国行业：512000(券商)     512760(芯片)     512660(军工)    512170(医疗)
         561660(通用航空)  159873(医疗设备)  516160(新能源)
跨境：    513100(纳指)     513500(标普500)  513050(中概互联)  513120(新兴市场)
         513520(日经)     513030(德国)
商品：    159322(黄金)     159518(油气)
债券：    511010(国债)     511260(十年国债)
```

## 输出文件

```
output/
├── equity_curve.png               # 策略 vs 基准净值
├── trade_details.xlsx             # 持仓明细 + 绩效
├── optimization_results.xlsx      # 全量参数对比
├── param_stability_v8.xlsx        # 稳定性全量数据
├── heatmap_*.png                  # 二维热力图
├── walk_forward_folds.xlsx        # 滚动回测各折明细
├── monte_carlo_*.png/xlsx         # 蒙特卡洛报告
├── attribution_report.xlsx        # 收益归因
└── trade_ops_report.xlsx          # 实盘统计

ops/
├── trade_log.db                  # 交易数据库（SQLite）
├── trade_log.csv                 # 交易日志（CSV兼容）
└── alerts.log                    # 告警历史
```

## 绩效概览（10年 2016-2026）

| 指标 | 策略 | 基准(沪深300) |
|------|------|---------------|
| 累计收益 | +283% | +37% |
| 年化收益 | 14.2% | 3.2% |
| 最大回撤 | -17.1% | — |
| 夏普比率 | 1.20 | — |
| 年度胜率 | 73% | — |

## 分支与版本

| 分支 | 用途 |
|------|------|
| `master` | 生产版本（v10.1-stable） |
| `research` | 实验分支 |
| `feature/trade-ops` | 实盘运维功能开发 |

| 版本 | 核心特性 | 夏普 |
|------|---------|------|
| v1.0 | 原始动量轮动 | 0.26 |
| v2.0 | 风险调整 + 多仓位 + 择时 | 1.06 |
| v3.0 | 防御资产 + 波动率控仓 | 1.16 |
| v4.0 | 复合动量 + 动态仓位 | 1.14 |
| v5.0 | 市场状态机 + 相关性控制 | — |
| v6.0 | 广度增强状态机 | 1.19 |
| **v7.0** | **极波降仓（稳定基线）** | **1.20** |
| v8.0 | 参数热力图 + 蒙特卡洛 | — |
| v9.0 | 溢价硬阶梯过滤 | 1.05 |
| v10.0 | 连续溢价惩罚 | 1.04 |
| v10.1 | 溢价放松 + 归因分析 | 1.12 |

## 环境

- Python >= 3.12
- akshare / pandas / numpy / matplotlib / openpyxl
