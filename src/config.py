"""
集中管理所有配置常量，避免魔法数字散落各处。

修改策略参数（如动量窗口、回测日期）只需改这一个文件。
"""

# ---- ETF 池 ----
# 代码 -> 名称 的映射，既是数据拉取列表，也是策略的选股范围
ETF_POOL: dict[str, str] = {
    # 中国宽基
    "510300": "沪深300ETF",
    "510500": "中证500ETF",
    "510880": "红利ETF",
    "159915": "创业板ETF",

    # 中国行业
    "512000": "券商ETF",
    "512760": "芯片ETF",
    "512660": "军工ETF",
    "512170": "医疗ETF",
    "561660": "通用航空ETF平安",
    "159873": "医疗设备ETF天弘",
    "516160": "新能源ETF南方",

    # 美国
    "513100": "纳指ETF",
    "513500": "标普500ETF",
    "513050": "中概互联ETF",

    # 国际
    "513520": "日经ETF",
    "513030": "德国ETF",
    "513120": "新兴市场ETF",

    # 商品
    "159322": "黄金ETF平安",
    "159518": "油气ETF",

    # 债券
    "511010": "国债ETF",
    "511260": "十年国债ETF",
}

# ---- 回测时间 ----
# akshare 要求日期格式为 YYYYMMDD（无分隔符）
START_DATE: str = "20160101"
END_DATE: str = "20260509"

# ---- 策略参数 ----
# V5: 市场状态机 + 相关性控制
MOMENTUM_WINDOW: int = 20      # 动量计算窗口（状态机覆盖时为基础值）
TOP_N: int = 5                 # 风险资产持仓数（状态机覆盖时为基础值）
USE_RISK_ADJUSTED: bool = True    # 风险调整动量
USE_TREND_FILTER: bool = False    # 单ETF绝对动量过滤
TREND_WINDOW: int = 60

# 市场状态机 — 根据趋势环境自动切换参数
USE_MARKET_STATE_MACHINE: bool = True
STATE_BULL_WINDOW: int = 10       # 牛市动量窗口（短=灵敏）
STATE_BULL_TOP_N: int = 3         # 牛市持仓数（集中火力）
STATE_SIDEWAYS_WINDOW: int = 20   # 震荡市动量窗口（中=平衡）
STATE_SIDEWAYS_TOP_N: int = 5     # 震荡市持仓数（分散防御）
STATE_BEAR_WINDOW: int = 40       # 熊市防御窗口（长=稳定）
MA_TREND_SHORT: int = 20          # 短期均线（判断趋势方向）
MA_TREND_MEDIUM: int = 60         # 中期均线（确认趋势）
MARKET_MA_WINDOW: int = 120       # 牛熊分界线
SYSTEM_MA_WINDOW: int = 120       # 市场整体趋势判断
MOMENTUM_WINDOWS_COMPOSITE: list[int] = [10, 30, 60]
MOMENTUM_WEIGHTS: list[float] = [0.5, 0.3, 0.2]
USE_COMPOSITE_MOMENTUM: bool = False

# 相关性控制 — 避免同质化资产集中
USE_CORRELATION_FILTER: bool = True
CORRELATION_WINDOW: int = 60      # 相关性计算窗口
CORRELATION_THRESHOLD: float = 0.75  # 最大允许相关性（超过则跳过）

# 其他特性开关
USE_DYNAMIC_POSITION: bool = False   # 被状态机取代，保持兼容
TOP_N_AGGRESSIVE: int = 3
TOP_N_NORMAL: int = 5
USE_RELATIVE_STRENGTH: bool = False
RELATIVE_STRENGTH_BENCHMARK: str = "510300"
MARKET_MA_AGGRESSIVE: int = 200
DEFENSE_ETF_CODES: list[str] = ["511010", "511260", "159322"]

# 调仓频率
REBALANCE_FREQ: int = 1

# 波动率仓位控制 — 离散化（减少微调仓）
USE_VOL_TARGET: bool = True
VOL_TARGET: float = 0.15
VOL_LOOKBACK: int = 20
VOL_CAP: float = 1.5
VOL_DISCRETE: bool = True           # 离散化仓位（100%/70%/40% 三档）
VOL_TIER_HIGH: float = 1.0          # 低波 → 满仓
VOL_TIER_MID: float = 0.7           # 中波 → 七成仓
VOL_TIER_LOW: float = 0.4           # 高波 → 四成仓

# 实盘降摩擦
MIN_REBALANCE_PCT: float = 0.05     # 权重变化 < 5% → 不交易（跳过微调）
STATE_SMOOTHING: bool = False       # 状态平滑（默认关闭，收益损失过大）
STATE_COOLDOWN: int = 2             # 状态冷却期（周数）

# 溢价限制 — 连续惩罚函数（仅在 > 3% 时生效）
# AdjustedScore = Momentum × max(0, 1 - k × premium^l)
# AdjustedWeight = Weight × max(0.1, 1 - decay × premium)
# QDII ETF（纳指/标普）常见 1~3% 溢价不受影响
USE_PREMIUM_FILTER: bool = True
PREMIUM_IGNORE: float = 0.03     # < 3% → 完全忽略
PREMIUM_K: float = 5.0           # 动量惩罚系数 k（降低以保留 Alpha）
PREMIUM_L: float = 2.0            # 指数 l（平方惩罚）
PREMIUM_WEIGHT_DECAY: float = 8.0   # 权重衰减率（3%→0.76, 6%→0.52, 10%→0.20）
PREMIUM_BAN_ABSOLUTE: float = 0.12  # >12% 绝对禁止

# ---- 模块优先级 ----
# Level 1  生存风控    → 溢价 > 12% 直接踢出
# Level 2  极端波动    → 波动率 > 90分位 → 仓位 × 0.5
# Level 3  状态机      → 判断牛/震/熊 → 选池 + 定窗口/持仓数
# Level 4  动量        → 风险调整动量 → 排名选前 N
# Level 5  溢价辅助    → 连续惩罚动量分 + 衰减权重

# ---- 基准 ----
BENCHMARK_CODE: str = "510300"  # 沪深300ETF，用于对比

# ---- 输出 ----
OUTPUT_DIR: str = "output"  # 图表和 Excel 的输出目录

# ---- 缓存 ----
CACHE_DIR: str = "data/cache"  # CSV 缓存的存放目录
