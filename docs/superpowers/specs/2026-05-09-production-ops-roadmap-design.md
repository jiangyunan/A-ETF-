# 交易日志系统 & 实盘基础设施 — 开发计划

## 分支

```
feature/production-ops  ← 从 master 新建
master                  ← V11 生产基线
research                ← 补充系统计划
```

## Phase 1: 增强日志 + 数据检查（本周）

### 1.1 升级交易日志 Schema

扩展 `TRADE_LOG_COLUMNS` — 现有 10 列 → 20 列：

| 字段 | 旧 | 新 | 说明 |
|------|----|----|------|
| date | ✅ | ✅ | 调仓日期 |
| action | ✅ | ✅ | BUY/SELL |
| code | ✅ | ✅ | ETF 代码 |
| name | ✅ | ✅ | ETF 名称 |
| price | ✅ | ✅ | 成交价 |
| shares | ✅ | ✅ | 成交股数 |
| premium | ✅ | ✅ | 溢价率 |
| signal_date | ✅ | ✅ | 信号日期 |
| state | ✅ | ✅ | 市场状态 |
| notes | ✅ | ✅ | 备注 |
| **market_breadth** | — | **NEW** | 当前广度值 |
| **vol_level** | — | **NEW** | 波动率等级(L/M/H/E) |
| **momentum_rank** | — | **NEW** | 该 ETF 动量排名/总分 |
| **trigger_reason** | — | **NEW** | 调仓原因(ETF更换/状态切换/权重调整) |
| **risk_trigger** | — | **NEW** | 是否触发风控(黑天鹅/极端波/溢价禁入) |
| **slippage_pct** | — | **NEW** | 实际滑点 |
| **volume_m** | — | **NEW** | 日成交额(百万) |
| **status_flags** | — | **NEW** | 状态位掩码(停牌/低流动性/溢价异常) |

### 1.2 交易日志自动填充

`src/ops/trade_logger.py` 新增模块：
- `auto_fill_log(signals, prices, breadth, vol_level)` — 从信号和行情数据自动预填增强字段
- `validate_log(log_path)` — 校验日志完整性
- `export_enhanced_log()` — 导出含增强字段的完整日志

### 1.3 盘前数据检查

`src/ops/preflight.py` 新建独立模块：

```python
def run_preflight(prices, spot_data) -> list[dict]:
    """
    盘前检查 — 运行在信号生成之前
    
    Returns: [{"check": "数据完整性", "status": "PASS/FAIL/WARN", "detail": "..."}]
    """
```

检查项：

| 检查项 | 条件 | 动作 |
|--------|------|------|
| 数据完整性 | 所有 ETF 数据更新到今天 | FAIL→停止运行 |
| 停牌检测 | 成交量=0 或 振幅=0 | 剔除该 ETF |
| 流动性不足 | 日成交额 < 3000 万 | 剔除 + 警告 |
| 溢价异常 | 单只溢价 > 8% | 禁止买入 |
| 数据跳变 | 涨跌幅 > 20% | 警告 |
| 买卖价差 | 买卖一档价差 > 0.5% | 风险提示 |
| 数据断更 | 最新数据 > 3 天前 | 警告 |

### 1.4 流动性过滤

集成到 `preflight.py`，利用 `fund_etf_spot_em` 的成交量和买卖一档价：

```
条件                      动作
日成交额 < 3000 万        踢出本次信号候选
买卖一档价差 > 0.5%       标记为高风险
停牌（振幅=0或成交量=0）   踢出
```

## Phase 2: 自动化风险告警

`src/ops/risk_alerts.py` 新建模块：

### 告警类型

| 告警 | 触发条件 | 级别 |
|------|---------|------|
| 波动率异常 | 20日波动率 > 历史 95%分位 | ⚠️ WARN |
| 连续亏损 | 最近4周亏损 > 8% | 🔴 CRITICAL |
| 溢价爆炸 | 任一持仓 ETF 溢价 > 8% | ⚠️ WARN |
| 数据断更 | 行情数据滞后 > 48h | 🔴 CRITICAL |
| 仓位异常 | 实际仓位偏离信号 > 15% | ⚠️ WARN |
| 回撤突破 | 净值跌破 -15% | 🔴 CRITICAL |

### 输出

- 控制台：彩色告警摘要
- `ops/alerts.log` — 告警历史
- `output/alert_report.xlsx` — 告警明细

## Phase 3: 模拟实盘（Paper Trading）

`src/ops/paper_trading.py` 新建模块：

1. 读取 `--signal` 输出
2. 模拟按信号执行买入/卖出（用 T+1 开盘价）
3. 跟踪模拟持仓 + 模拟净值曲线
4. 记录模拟交易日志 `ops/paper_trades.csv`
5. 对比模拟 vs 理论：`python main.py --paper`

## Phase 4: 半自动执行

- `--trade-ui` 增强：一键填充当前信号到交易表单
- 加入确认步骤：「检查无误后按 Enter 确认下单」
- 自动计算买入股数（根据账户资金和权重）

## Phase 5: 小资金真实实盘

- 连接券商 API（如 easytrader/xtquant）
- 手动确认后半自动下单
- 每日自动运行 `--preflight` `--signal` `--track`

---

## 文件变更总览

```
src/ops/
├── __init__.py
├── trade_log.py          ← 升级 schema + 自动填充
├── trade_logger.py       ← 新建：增强日志自动填充
├── trade_ui.py           ← 升级：显示增强字段
├── preflight.py          ← 新建：盘前数据检查
├── risk_alerts.py        ← 新建：自动风险告警
├── paper_trading.py      ← 新建：模拟实盘
└── alerts.log            ← 生成：告警历史

ops/
└── trade_log.csv         ← 扩展字段
```

## 优先级

1. **Phase 1.1 + 1.2** — 增强日志 Schema → 立即建立完整记录
2. **Phase 1.3 + 1.4** — 盘前检查 + 流动性过滤 → 防止实盘踩坑
3. **Phase 2** — 风险告警 → 自动化监控
4. **Phase 3** — 模拟实盘 → 零风险验证
5. **Phase 4-5** — 半自动 → 真实实盘 → 逐步推进
