# ETF 动量轮动量化系统

A股 ETF 周度动量轮动回测系统，融合市场状态机、相关性控制、波动率仓位管理等机构级技术。

## 快速开始

```bash
# 初始化（首次）
uv sync

# 运行回测
python main.py

# 查看本周持仓建议
python main.py --signal
```

## 系统架构

```
src/
├── config.py              # 集中配置（ETF池、参数、日期）
├── data/
│   └── fetcher.py         # akshare 数据拉取 + CSV 缓存
├── strategy/
│   └── momentum.py        # 动量计算 + 状态机 + 信号生成
├── backtest/
│   └── engine.py          # 回测引擎 + 绩效统计
├── output/
│   └── report.py          # 净值曲线图 + Excel 报告
├── optimizer/
│   ├── scanner.py         # 网格搜索最优参数
│   ├── walk_forward.py    # 滚动回测（纯OOS验证）
│   ├── param_stability.py # 参数稳定性热力图
│   └── monte_carlo.py     # 蒙特卡洛生存测试
└── __init__.py
main.py                    # 主入口
```

## 完整命令

| 命令 | 功能 | 耗时 |
|------|------|------|
| `python main.py` | 单次回测（使用 config.py 当前参数） | ~20秒 |
| `python main.py --signal` | 生成本周实盘持仓建议 | ~5秒 |
| `python main.py --optimize` | 网格搜索最优参数组合 | ~5分钟 |
| `python main.py --walk-forward` | 滚动回测（纯OOS，防过拟合） | ~5分钟 |
| `python main.py --stability` | 二维参数稳定性热力图 | ~3分钟 |
| `python main.py --monte-carlo` | 蒙特卡洛生存测试（1000次扰动） | ~2分钟 |

## 核心策略

### 动量选股
每周期计算 ETF 池中所有 ETF 的风险调整动量（动量/波动率），按动量排名选前 N 名等权持有。

### 市场状态机（V7）
根据三个维度判断市场状态，自动切换参数：

| 状态 | 条件 | 持仓数 | 动量窗口 |
|------|------|--------|----------|
| 牛市 | 广度>60% + MA20>MA60 + CSI300>MA120 | 3只（集中） | 10天（灵敏） |
| 震荡 | CSI300>MA120 或 广度>35% | 5只（分散） | 20天（平衡） |
| 熊市 | — | 防御资产 | 40天（保守） |

- **市场广度**：攻击池中站上 MA60 的 ETF 占比，比单指数 MA 更稳定
- **防御资产**：国债ETF → 黄金ETF → 现金

### 相关性控制
按动量排序选 ETF 时，剔除与已选 ETF 相关性 > 0.75 的标的，避免同质化集中（如纳指+芯片+创业板）。

### 波动率仓位控制
- 目标波动率仓位：高波降仓、低波加仓
- 极端波动自动降仓：当前波动率 > 历史 90%分位时仓位减半

## 参数配置

所有参数集中在 `src/config.py`，修改后直接 `python main.py` 生效：

```python
MOMENTUM_WINDOW = 20          # 动量窗口
TOP_N = 5                     # 持仓数
USE_RISK_ADJUSTED = True      # 风险调整动量
USE_MARKET_STATE_MACHINE = True  # 市场状态机
USE_CORRELATION_FILTER = True # 相关性过滤
USE_VOL_TARGET = True         # 波动率仓位控制
MARKET_MA_WINDOW = 120        # 牛熊分界线
REBALANCE_FREQ = 1            # 调仓频率（1=周）
```

## ETF 池

```
中国宽基：510300(沪深300)  510500(中证500)  159915(创业板)  510880(红利)
中国行业：512000(券商)     512760(芯片)     512660(军工)    512170(医疗)
         561660(通用航空)  159873(医疗设备)  516160(新能源)
跨境：    513100(纳指)     513500(标普500)   513050(中概互联) 513120(新兴市场)
         513520(日经)     513030(德国)
商品：    159322(黄金)     159518(油气)
债券：    511010(国债)     511260(十年国债)
```

## 输出文件

```
output/
├── equity_curve.png              # 策略 vs 基准净值对比
├── trade_details.xlsx            # 持仓明细 + 绩效汇总
├── optimization_results.xlsx     # 全量参数对比（--optimize）
├── param_stability_v8.xlsx       # 稳定性全量数据（--stability）
├── heatmap_*.png                 # 二维热力图（--stability）
├── walk_forward_folds.xlsx       # 各折明细（--walk-forward）
├── monte_carlo_equity_fan.png    # 资金曲线扇形（--monte-carlo）
├── monte_carlo_drawdown_hist.png # 回撤分布（--monte-carlo）
└── monte_carlo_results.xlsx      # 1000次模拟明细
```

## 绩效概览（10年回测 2016-2026）

| 指标 | 策略 | 基准(沪深300) |
|------|------|---------------|
| 累计收益 | +283% | +37% |
| 年化收益 | 14.2% | 3.2% |
| 最大回撤 | -17.1% | — |
| 夏普比率 | 1.20 | — |
| 胜率(vs基准) | 54.6% | — |

## 版本历史

- **v1.0** — 原始动量轮动，每周选1只最强 ETF
- **v2.0** — 风险调整动量 + 多仓位 + 大盘择时，夏普 1.06
- **v3.0** — 防御资产 + 波动率控仓，夏普 1.16，回撤 -6.4%
- **v4.0** — 复合动量 + 动态仓位 + 强弱过滤，夏普 1.14
- **v5.0** — 市场状态机 + 相关性控制，自适应切换
- **v6.0** — 广度增强状态机，夏普 1.19
- **v7.0** — 极波自动降仓，夏普 1.20
- **v8.0** — 参数稳定性热力图 + 蒙特卡洛生存测试

## 环境

- Python >= 3.12
- akshare（数据）
- pandas / numpy（计算）
- matplotlib（图表）
- openpyxl（Excel 导出）
