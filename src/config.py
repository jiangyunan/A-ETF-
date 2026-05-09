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
# 以下为 V3 网格搜索最优参数（夏普 1.16，回撤 -6.39%）
MOMENTUM_WINDOW: int = 10      # 动量计算窗口
TOP_N: int = 5                 # 风险资产持仓数
USE_RISK_ADJUSTED: bool = True    # 风险调整动量（动量/波动率）
USE_TREND_FILTER: bool = False    # 单ETF绝对动量过滤（V3最优中关闭）
TREND_WINDOW: int = 60         # 单ETF趋势过滤的MA窗口

# 大盘择时（风险/防御切换）
MARKET_MA_WINDOW: int = 120       # 沪深300 > N日均线 → 风险资产；否则 → 防御资产
DEFENSE_ETF_CODES: list[str] = ["511010", "511260", "159322"]  # 防御资产优先级：国债→黄金

# 调仓频率
REBALANCE_FREQ: int = 1        # 1=每周, 2=双周, 4=月（最优=1）

# 波动率仓位控制
USE_VOL_TARGET: bool = True       # 是否启用波动率目标仓位
VOL_TARGET: float = 0.15       # 目标年化波动率（15%）
VOL_LOOKBACK: int = 20         # 波动率计算回看窗口
VOL_CAP: float = 1.5           # 单只 ETF 最大仓位倍率

# ---- 基准 ----
BENCHMARK_CODE: str = "510300"  # 沪深300ETF，用于对比

# ---- 输出 ----
OUTPUT_DIR: str = "output"  # 图表和 Excel 的输出目录

# ---- 缓存 ----
CACHE_DIR: str = "data/cache"  # CSV 缓存的存放目录
