"""
SQLite 交易数据库 — 统一存储交易记录、盘前检查、风险告警、信号快照。

数据库: ops/trade_log.db

表结构:
  trades      — 核心交易记录（20 字段）
  preflight   — 每日盘前检查结果
  risk_alerts — 风险告警历史
  signals     — 每周信号快照
"""

import sqlite3
import os
from datetime import datetime
from pathlib import Path

DB_PATH = "ops/trade_log.db"


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─── Schema ───────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    action      TEXT NOT NULL CHECK(action IN ('BUY','SELL')),
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    price       REAL NOT NULL,
    shares      INTEGER NOT NULL,
    premium_pct REAL DEFAULT 0.0,
    signal_date TEXT,
    state       TEXT DEFAULT '?',
    market_breadth   REAL,         -- 市场广度值 (0~1)
    vol_level        TEXT,         -- 波动率等级: L/M/H/E (Low/Medium/High/Extreme)
    momentum_rank    INTEGER,      -- 该ETF在池中动量排名
    momentum_score   REAL,         -- 该ETF动量分数
    trigger_reason   TEXT,         -- 调仓原因: ETF更换/状态切换/权重调整/黑天鹅
    risk_trigger     TEXT,         -- 风控触发: VIX_SPIKE/GLOBAL_CRASH/PREMIUM_BAN/NONE
    slippage_pct     REAL DEFAULT 0.0,
    volume_m         REAL,         -- 日成交额(百万)
    status_flags     TEXT DEFAULT '', -- 状态位: SUSPEND/LOW_LIQ/HIGH_PREM
    notes           TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS preflight (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT NOT NULL,
    check_name  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK(status IN ('PASS','FAIL','WARN')),
    detail      TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS risk_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_date  TEXT NOT NULL,
    alert_type  TEXT NOT NULL,
    level       TEXT NOT NULL CHECK(level IN ('INFO','WARN','CRITICAL')),
    message     TEXT NOT NULL,
    resolved    INTEGER DEFAULT 0,
    resolved_at TEXT,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date TEXT NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT,
    momentum    REAL,
    weight      REAL,
    shares      INTEGER DEFAULT 0,
    price       REAL DEFAULT 0.0,
    state       TEXT,
    holiday_delay INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date);
CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code);
CREATE INDEX IF NOT EXISTS idx_preflight_date ON preflight(run_date);
CREATE INDEX IF NOT EXISTS idx_risk_alerts_date ON risk_alerts(alert_date);
CREATE TABLE IF NOT EXISTS capital (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    total       REAL NOT NULL,          -- 总资产（元）
    cash        REAL NOT NULL,          -- 可用现金（元）
    market_value REAL DEFAULT 0.0,      -- 持仓市值（元）
    notes       TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_capital_date ON capital(date);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(signal_date);
"""


def init_db() -> None:
    """初始化数据库（幂等），含列迁移。"""
    conn = _connect()
    conn.executescript(SCHEMA)
    # 迁移: 为已有 signals 表添加 shares/price 列
    existing = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    if "shares" not in existing:
        conn.execute("ALTER TABLE signals ADD COLUMN shares INTEGER DEFAULT 0")
    if "price" not in existing:
        conn.execute("ALTER TABLE signals ADD COLUMN price REAL DEFAULT 0.0")
    conn.commit()
    conn.close()


# ─── Trades CRUD ───────────────────────────────────────────

def insert_trade(data: dict) -> int:
    """插入一条交易记录，返回 id。"""
    conn = _connect()
    fields = [
        "date", "action", "code", "name", "price", "shares",
        "premium_pct", "signal_date", "state",
        "market_breadth", "vol_level", "momentum_rank", "momentum_score",
        "trigger_reason", "risk_trigger", "slippage_pct", "volume_m",
        "status_flags", "notes",
    ]
    values = {f: data.get(f) for f in fields}
    columns = ", ".join(values.keys())
    placeholders = ", ".join(f":{k}" for k in values)
    cur = conn.execute(f"INSERT INTO trades ({columns}) VALUES ({placeholders})", values)
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_trades(start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """查询交易记录。"""
    conn = _connect()
    sql = "SELECT * FROM trades WHERE 1=1"
    params = {}
    if start_date:
        sql += " AND date >= :start"
        params["start"] = start_date
    if end_date:
        sql += " AND date <= :end"
        params["end"] = end_date
    sql += " ORDER BY date, id"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_last_trade() -> dict | None:
    """删除最近一条交易记录，返回被删除的内容。"""
    conn = _connect()
    row = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        conn.close()
        return None
    conn.execute("DELETE FROM trades WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return dict(row)


def delete_trade_by_id(trade_id: int) -> dict | None:
    """按 ID 删除一条交易记录。"""
    conn = _connect()
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if row is None:
        conn.close()
        return None
    conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.commit()
    conn.close()
    return dict(row)


def get_trade_stats() -> dict:
    """交易宏观统计（SQL 聚合）。"""
    conn = _connect()
    stats = {}
    for metric, query in [
        ("total_trades", "SELECT COUNT(*) FROM trades"),
        ("buy_count", "SELECT COUNT(*) FROM trades WHERE action='BUY'"),
        ("sell_count", "SELECT COUNT(*) FROM trades WHERE action='SELL'"),
        ("unique_etfs", "SELECT COUNT(DISTINCT code) FROM trades"),
        ("total_amount", "SELECT COALESCE(SUM(price*shares),0) FROM trades"),
        ("avg_premium_buy", "SELECT AVG(ABS(premium_pct)) FROM trades WHERE action='BUY'"),
        ("state_dist", "SELECT state, COUNT(*) as cnt FROM trades GROUP BY state ORDER BY cnt DESC"),
        ("trigger_dist", "SELECT trigger_reason, COUNT(*) as cnt FROM trades GROUP BY trigger_reason ORDER BY cnt DESC"),
        ("risk_count", "SELECT COUNT(*) FROM trades WHERE risk_trigger IS NOT NULL AND risk_trigger != 'NONE' AND risk_trigger != ''"),
        ("avg_slippage", "SELECT AVG(ABS(slippage_pct)) FROM trades WHERE slippage_pct != 0"),
    ]:
        try:
            rows = conn.execute(query).fetchall()
            if "dist" in metric or "count" in metric:
                stats[metric] = rows[0][0]
            elif len(rows) == 1 and len(rows[0]) == 1:
                stats[metric] = rows[0][0] or 0
            else:
                stats[metric] = [dict(r) for r in rows] if rows else []
        except Exception:
            stats[metric] = 0
    conn.close()
    return stats


# ─── Preflight CRUD ────────────────────────────────────────

def insert_preflight(run_date: str, results: list[dict]) -> None:
    """批量写入盘前检查结果。"""
    conn = _connect()
    for r in results:
        conn.execute(
            "INSERT INTO preflight (run_date, check_name, status, detail) VALUES (?,?,?,?)",
            (run_date, r["check"], r["status"], r.get("detail", "")),
        )
    conn.commit()
    conn.close()


def get_latest_preflight() -> list[dict]:
    """获取最近一次盘前检查结果。"""
    conn = _connect()
    rows = conn.execute("""
        SELECT * FROM preflight WHERE run_date = (SELECT MAX(run_date) FROM preflight)
        ORDER BY id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Risk Alerts CRUD ──────────────────────────────────────

def insert_alert(alert_type: str, level: str, message: str, alert_date: str | None = None) -> int:
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO risk_alerts (alert_date, alert_type, level, message) VALUES (?,?,?,?)",
        (alert_date or datetime.now().strftime("%Y-%m-%d"), alert_type, level, message),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def resolve_alert(alert_id: int) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE risk_alerts SET resolved=1, resolved_at=? WHERE id=?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), alert_id),
    )
    conn.commit()
    conn.close()


def get_open_alerts() -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM risk_alerts WHERE resolved=0 ORDER BY alert_date DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Signals CRUD ──────────────────────────────────────────

def save_signals(signals_data: list[dict]) -> None:
    """保存信号快照（先清空再写入当前）。"""
    conn = _connect()
    conn.execute("DELETE FROM signals")
    for s in signals_data:
        conn.execute(
            "INSERT INTO signals (signal_date,code,name,momentum,weight,shares,price,state,holiday_delay) VALUES (?,?,?,?,?,?,?,?,?)",
            (s["date"], s["code"], s.get("name", ""), s.get("momentum", 0),
             s.get("weight", 0), s.get("shares", 0), s.get("price", 0),
             s.get("state", "?"), int(s.get("holiday_delay", 0))),
        )
    conn.commit()
    conn.close()


def get_latest_signals() -> list[dict]:
    conn = _connect()
    rows = conn.execute("""
        SELECT * FROM signals WHERE signal_date = (SELECT MAX(signal_date) FROM signals)
        ORDER BY code
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Migration ─────────────────────────────────────────────

def migrate_from_csv(csv_path: str = "ops/trade_log.csv") -> int:
    """从旧 CSV 迁移到 SQLite。"""
    import pandas as pd
    if not os.path.exists(csv_path):
        return 0
    df = pd.read_csv(csv_path)
    if df.empty:
        return 0

    init_db()
    count = 0
    for _, row in df.iterrows():
        data = {
            "date": str(row.get("date", "")),
            "action": str(row.get("action", "BUY")),
            "code": str(row.get("code", "")),
            "name": str(row.get("name", "")),
            "price": float(row.get("price", 0)),
            "shares": int(row.get("shares", 0)),
            "premium_pct": float(str(row.get("premium", "0%")).replace("%", "")) / 100,
            "signal_date": str(row.get("signal_date", "")),
            "state": str(row.get("state", "?")),
            "notes": str(row.get("notes", "")),
        }
        insert_trade(data)
        count += 1
    return count


# ─── Capital CRUD ──────────────────────────────────────────

def get_latest_capital() -> dict | None:
    """获取最近一条资金记录。"""
    conn = _connect()
    row = conn.execute("SELECT * FROM capital ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def update_capital(total: float, cash: float, market_value: float = 0.0,
                   notes: str = "", date: str | None = None) -> int:
    """写入一条资金快照，返回 id。"""
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO capital (date, total, cash, market_value, notes) VALUES (?,?,?,?,?)",
        (date or datetime.now().strftime("%Y-%m-%d"), total, cash, market_value, notes),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_capital_history(limit: int = 30) -> list[dict]:
    """获取资金历史记录。"""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM capital ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Init on import ────────────────────────────────────────
init_db()
