"""
MySQL 存储层 — 每日选股结果持久化

数据库: baofu
表: stock_picks
    id          INT AUTO_INCREMENT PRIMARY KEY
    pick_date   DATE            选出日期
    symbol      VARCHAR(10)     股票代码
    name        VARCHAR(32)     股票名称
    board       VARCHAR(12)     板块 (sh_main/sz_main/chinext)
    board_name  VARCHAR(12)     板块中文名
    total_score FLOAT           综合得分
    tech_score  FLOAT           技术得分
    money_score FLOAT           资金得分
    concept_score FLOAT         概念得分
    hot_concepts VARCHAR(255)   命中热点概念
    pick_close  DECIMAL(10,3)   选出日收盘价
    detail      TEXT            明细
    created_at  DATETIME        入库时间
"""

import os
import logging
from datetime import datetime

import pymysql
from pymysql.cursors import DictCursor

from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_picks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    pick_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    name VARCHAR(32) DEFAULT '',
    board VARCHAR(12) DEFAULT '',
    board_name VARCHAR(12) DEFAULT '',
    total_score FLOAT DEFAULT 0,
    tech_score FLOAT DEFAULT 0,
    money_score FLOAT DEFAULT 0,
    concept_score FLOAT DEFAULT 0,
    hot_concepts VARCHAR(255) DEFAULT '',
    pick_close DECIMAL(10,3) DEFAULT NULL,
    main_inflow_wan DECIMAL(12,2) DEFAULT NULL,
    large_inflow_wan DECIMAL(12,2) DEFAULT NULL,
    detail TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_pick_symbol (pick_date, symbol),
    KEY idx_pick_date (pick_date),
    KEY idx_symbol (symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _conn():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, charset='utf8mb4', cursorclass=DictCursor,
        autocommit=True,
    )


def ensure_database():
    """创建数据库（如不存在）并建表。"""
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        charset='utf8mb4', autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                        f"DEFAULT CHARACTER SET utf8mb4")
    finally:
        conn.close()

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            # Migration: add money-flow columns if missing (existing installs)
            cur.execute("SHOW COLUMNS FROM stock_picks LIKE 'main_inflow_wan'")
            if cur.fetchone() is None:
                cur.execute("ALTER TABLE stock_picks "
                            "ADD COLUMN main_inflow_wan DECIMAL(12,2) DEFAULT NULL, "
                            "ADD COLUMN large_inflow_wan DECIMAL(12,2) DEFAULT NULL")
                logger.info("Migrated stock_picks: added main/large inflow columns")
    finally:
        conn.close()
    logger.info(f"DB ready: {DB_NAME}.stock_picks")


def save_picks(picks: list) -> int:
    """
    保存当日选股结果（按 (pick_date, symbol) 去重，重复则更新）。
    picks: 与 selector.run() 返回结构一致的 dict 列表。
    返回写入行数。
    """
    if not picks:
        return 0
    ensure_database()

    from strategy.boards import board_label
    today = datetime.now().date()
    rows = []
    for s in picks:
        raw = s.get("raw_indicators", {}) or {}
        concept_raw = raw.get("concept", {}) or {}
        money_raw = raw.get("money", {}) or {}
        board = s.get("board") or ''
        rows.append({
            "pick_date": today,
            "symbol": str(s.get("symbol", "")).zfill(6),
            "name": s.get("name") or "",
            "board": board,
            "board_name": board_label(board) if board else "",
            "total_score": s.get("total_score", 0),
            "tech_score": s.get("tech_score", 0),
            "money_score": s.get("money_score", 0),
            "concept_score": s.get("concept_score", 0),
            "hot_concepts": "|".join(concept_raw.get("hot_concepts", [])[:5]),
            "pick_close": s.get("pick_close"),
            "main_inflow_wan": money_raw.get("main_net_inflow", 0),
            "large_inflow_wan": money_raw.get("large_net_inflow", 0),
            "detail": " | ".join(s.get("detail_items", [])[:8]),
        })

    conn = _conn()
    inserted = 0
    try:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """INSERT INTO stock_picks
                       (pick_date, symbol, name, board, board_name,
                        total_score, tech_score, money_score, concept_score,
                        hot_concepts, pick_close, main_inflow_wan,
                        large_inflow_wan, detail)
                       VALUES (%(pick_date)s, %(symbol)s, %(name)s, %(board)s,
                               %(board_name)s, %(total_score)s, %(tech_score)s,
                               %(money_score)s, %(concept_score)s,
                               %(hot_concepts)s, %(pick_close)s,
                               %(main_inflow_wan)s, %(large_inflow_wan)s,
                               %(detail)s)
                       ON DUPLICATE KEY UPDATE
                        name=VALUES(name), board=VALUES(board),
                        board_name=VALUES(board_name),
                        total_score=VALUES(total_score),
                        tech_score=VALUES(tech_score),
                        money_score=VALUES(money_score),
                        concept_score=VALUES(concept_score),
                        hot_concepts=VALUES(hot_concepts),
                        pick_close=VALUES(pick_close),
                        main_inflow_wan=VALUES(main_inflow_wan),
                        large_inflow_wan=VALUES(large_inflow_wan),
                        detail=VALUES(detail)""",
                    r,
                )
                inserted += 1
    finally:
        conn.close()
    logger.info(f"Saved {inserted} picks to MySQL ({today})")
    return inserted


def query_picks(pick_date: str = None, board: str = None,
                symbol: str = None, limit: int = 500) -> list:
    """查询选股记录。条件均为可选。"""
    ensure_database()
    where = []
    params = []
    if pick_date:
        where.append("pick_date = %s")
        params.append(pick_date)
    if board:
        where.append("board = %s")
        params.append(board)
    if symbol:
        where.append("symbol LIKE %s")
        params.append(f"%{symbol}%")
    sql = "SELECT * FROM stock_picks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pick_date DESC, id ASC LIMIT %s"
    params.append(limit)

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()


def list_dates(limit: int = 30) -> list:
    """最近可选日期列表（去重）。"""
    ensure_database()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT pick_date FROM stock_picks "
                "ORDER BY pick_date DESC LIMIT %s", (limit,))
            return [r["pick_date"].strftime("%Y-%m-%d") for r in cur.fetchall()]
    finally:
        conn.close()
