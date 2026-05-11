# Recovery Engine — 实现规格

## 三层状态机

```
BEAR ──(close>MA120 + breadth>0.25)──→ RECOVERY ──(2~5周确认)──→ BULL/SIDEWAYS
  ↑                                      │
  └────────────(close<MA120)─────────────┘
```

## 状态判断顺序（关键修复）

```python
# RECOVERY 必须在 SIDEWAYS 之前匹配
if above_market and trending_up and broad_healthy:
    return ("BULL", 10, 3)
elif committed_state == "BEAR" and above_market and breadth > 0.25:
    return ("RECOVERY", 40, 3)
elif above_market or breadth > 0.35:
    return ("SIDEWAYS", 20, 5)
else:
    return ("BEAR", 40, 2)
```

## RECOVERY 态参数

| 参数 | 值 | 理由 |
|------|-----|------|
| RECOVERY_WINDOW | 40 | 长窗口防假突破 |
| RECOVERY_TOP_N | 3 | 试探性进攻 |
| RECOVERY_MAX_WEIGHT | 0.15 | 半风险预算 |

## 动态 N（volatility-smoothed）

| 波动率等级 | 条件 | 所需确认周数 |
|-----------|------|------------|
| 低波 | EWMA vol < 中位 | 2 |
| 中波 | < 75分位 | 3 |
| 高波 | ≥ 75分位 | 5 |

波动率用 10 周期滚动中位数平滑，不自抖。

## RECOVERY→ACTIVE 晋升条件（收紧）

```python
if close > ma120 * 1.02 and trending_up:
    state = "BULL"
else:
    state = "SIDEWAYS"
```

ma_slope > 0 太松，改为 close > MA120 × 1.02。

## Transition Analytics 字段

| 字段 | 含义 |
|------|------|
| RECOVERY 持续周期 | 停留长度 |
| 结果 | 晋升 ACTIVE / 退回 BEAR |
| 5日/20日收益 | 短期冲击 / 真实趋势质量 |
| MFE/MAE | 最大浮盈/浮亏 |
| 晋升延迟 | RECOVERY→ACTIVE 耗时 |
| 假突破率 | RECOVERY→BEAR 比例 |

## 改动

| 文件 | 行数 |
|------|------|
| config.py | +6 |
| strategy/momentum.py | ~35 |
| optimizer/transition.py（新建） | ~100 |
| main.py | +2 |

## 不做

- breadth 阈值现在不调（等 Transition Dataset 数据反推）
- 不加 HMM / Bayesian / confidence score
- 不加更多状态层级
