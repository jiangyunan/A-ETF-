"""
集中管理所有配置常量，避免魔法数字散落各处。

修改策略参数（如动量窗口、回测日期）只需改这一个文件。
"""

# ---- ETF 池 ----
# 代码 -> 名称 的映射，既是数据拉取列表，也是策略的选股范围
ETF_POOL: dict[str, str] = {
    "510300": "沪深300ETF",
    "159915": "创业板ETF",
    "510880": "红利ETF",
    "512000": "券商ETF",
    "513100": "纳指ETF",
    "513050": "中概互联ETF",
    "159322": "黄金ETF平安",
    "561660": "通用航空ETF平安",
    "159873": "医疗设备ETF天弘",
    "516160": "新能源ETF南方",
    "159518": "标普油气ETF嘉实",
    "512760": "芯片ETF国泰",
    "513650": "标普500ETF南方",
}

# ---- 回测时间 ----
# akshare 要求日期格式为 YYYYMMDD（无分隔符）
START_DATE: str = "20220101"
END_DATE: str = "20250509"

# ---- 策略参数 ----
MOMENTUM_WINDOW: int = 20      # 动量计算窗口：过去 N 个交易日（V1 默认）
TOP_N: int = 1                 # 持仓数量：每周持有前 N 名（1 = 原策略）
USE_RISK_ADJUSTED: bool = False   # 是否用风险调整动量（动量/波动率）
USE_TREND_FILTER: bool = False    # 是否启用绝对动量趋势过滤（收盘 > MA）
TREND_WINDOW: int = 60         # 趋势过滤的 MA 窗口：默认 60 日（约一个季度）

# ---- 基准 ----
BENCHMARK_CODE: str = "510300"  # 沪深300ETF，用于对比

# ---- 输出 ----
OUTPUT_DIR: str = "output"  # 图表和 Excel 的输出目录

# ---- 缓存 ----
CACHE_DIR: str = "data/cache"  # CSV 缓存的存放目录
