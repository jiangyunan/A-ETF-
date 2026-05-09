# ETF 动量轮动优化 V2 — 实现计划

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** 四层策略优化 — 参数扫描 + 风险调整动量 + 多仓位分散 + 趋势过滤

**Architecture:** 修改现有3个模块 + 新增1个模块，保持向后兼容

**Tech Stack:** Python 3.12+, pandas, numpy, matplotlib

---

### Task 1: 更新 config.py — 新增优化参数
- File: `src/config.py`
- Add: TOP_N, USE_RISK_ADJUSTED, TREND_WINDOW, USE_TREND_FILTER

### Task 2: 增强 strategy/momentum.py
- Add: `calc_risk_adjusted_momentum(prices, window)` 
- Modify: `generate_weekly_signals` to support top_n, risk_adjusted, trend_filter params

### Task 3: 增强 backtest/engine.py
- Modify: `_assign_daily_holdings` to support multi-position (N ETFs, fractional weights)

### Task 4: 新建 optimizer/scanner.py
- Grid search over 80 parameter combinations
- Run backtest for each, collect Sharpe ratios
- Output top results table + optimal-param NAV chart

### Task 5: 更新 main.py
- Add `--optimize` CLI flag
- Optimize mode: run scanner and output best results
- Default mode: unchanged (single backtest)

### Task 6: 端到端验证
- Run `python main.py` (backward compatible)
- Run `python main.py --optimize` (80 combos)
- Verify outputs
