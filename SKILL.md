---
name: etf-signal-and-log
description: |
  生成 ETF 动量轮动系统的本周实盘持仓建议，以及快捷录入实际交易。
  当用户说"信号/持仓/建议/调仓/买入/卖出/录入/记录/撤销/删除/写错"时使用。
---

# ETF 动量轮动 — 实盘操作

## 信号生成

运行后输出本周应持有的 ETF 及权重，同时显示建议买入股数（手）：

```bash
python main.py --signal
```

- 基于最新行情生成，不依赖历史调度日
- 输出：信号日期、市场状态（牛/震/熊）、ETF 代码、动量得分、权重、价格、建议股数/手数、预估金额
- 权重已含风险预算约束（逆波动率归一化、单只上限25%、大类上限40%）
- 建议股数基于资金台账中的可用现金计算（无台账时使用默认初始资金）
- 股数自动向下取整到最小交易单位（100股/手）

## 交易录入

命令行直接写入交易记录，不走交互面板。**自动更新资金台账**：

```bash
python main.py --log <BUY/SELL> <代码> <价格> <股数> [溢价%] [备注]
```

- BUY：自动扣减可用现金，增加持仓市值
- SELL：自动增加可用现金，扣减持仓市值
- 首次录入自动创建资金台账（使用默认初始资金）

## 资金台账

查看当前资金状况和历史记录：

```bash
python main.py --capital
```

- 显示最新总资产、可用现金、持仓市值
- 同时列出最近10条资金变动记录

## 交易录入

命令行直接写入交易记录，不走交互面板：

```bash
python main.py --log <BUY/SELL> <代码> <价格> <股数> [溢价%] [备注]
```

## 示例

生成本周信号：
```bash
python main.py --signal
```

买入：
```bash
python main.py --log BUY 513100 1.250 1000 1.2% 按信号买入
```

卖出：
```bash
python main.py --log SELL 510300 1.150 500
```

查看统计：
```bash
python main.py --track
```

查看资金台账：
```bash
python main.py --capital
```

## 参数说明

| 参数 | 必需 | 说明 |
|------|------|------|
| BUY/SELL | ✅ | 买入或卖出 |
| 代码 | ✅ | ETF 代码（如513100） |
| 价格 | ✅ | 成交单价 |
| 股数 | ✅ | 成交股数（100的整数倍） |
| 溢价% | ❌ | 买入时溢价率（如1.2%） |
| 备注 | ❌ | 自由文本 |

## 交易补救

写错时无需手动操作数据库：

```bash
# 查看最近20条交易（含ID）
python main.py --log-list

# 撤销最后一条
python main.py --log-undo

# 删除指定ID
python main.py --log-delete <ID>
```

## 工作流

```bash
# 1. 获取本周信号
python main.py --signal

# 2. 按信号执行交易
python main.py --log BUY 513100 1.250 1000
python main.py --log BUY 512760 1.180 800
python main.py --log SELL 510300 1.150 500

# 3. 写错了？撤销
python main.py --log-undo
python main.py --log-list

# 4. 确认统计
python main.py --track
```
