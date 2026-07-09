from __future__ import annotations

import csv
import base64
import hashlib
import io
import os
import random
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import streamlit as st

try:
    import libsql
except ImportError:  # Local fallback when Turso credentials are not configured.
    libsql = None


APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("LOTTERY_DB_PATH", APP_DIR / "lottery.db"))
DEFAULT_ADMIN_PIN = os.getenv("LOTTERY_ADMIN_PIN", "1688")
TAIPEI_TZ_LABEL = "Asia/Taipei"

POT_THEMES = [
    {"name": "好運紅鍋", "icon": "福", "accent": "#e54532", "glow": "rgba(217,68,50,.46)", "poster_a": "#ff6b57", "poster_b": "#e53b2d", "poster_c": "#ffb03c", "image": "assets/pot_card_0.webp"},
    {"name": "黃金旺鍋", "icon": "旺", "accent": "#f0a92d", "glow": "rgba(230,170,60,.45)", "poster_a": "#ffb321", "poster_b": "#ff7a18", "poster_c": "#ffd157", "image": "assets/pot_card_1.webp"},
    {"name": "招財辣鍋", "icon": "辣", "accent": "#e6aa3c", "glow": "rgba(239,91,56,.48)", "poster_a": "#ffe05e", "poster_b": "#f6a51f", "poster_c": "#f05622", "image": "assets/pot_card_2.webp"},
    {"name": "幸福暖鍋", "icon": "暖", "accent": "#264973", "glow": "rgba(216,139,81,.46)", "poster_a": "#173f6d", "poster_b": "#0e2749", "poster_c": "#f0a928", "image": "assets/pot_card_3.webp"},
]

DEFAULT_PRIZES = [
    ("免費招待一鍋", "🎉", 1.0, 5, 0, 1, 1, "恭喜！本次餐點招待一鍋"),
    ("肉盤一份", "🥩", 5.0, 20, 0, 1, 1, "恭喜獲得肉盤一份"),
    ("霜淇淋兌換", "🍦", 10.0, 50, 0, 1, 1, "恭喜獲得霜淇淋一份"),
    ("下次折價 20 元", "🎫", 24.0, 100, 0, 1, 1, "恭喜獲得下次消費折價 20 元"),
    ("好運正在熬煮中", "🍲", 60.0, 0, 0, 1, 0, "這鍋好運還在熬煮中，謝謝參加！"),
]


st.set_page_config(
    page_title="藝鍋物｜開鍋抽好禮",
    page_icon="🍲",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@contextmanager
def db_connection() -> Iterator[Any]:
    db_url = get_config_value("TURSO_DATABASE_URL", "LIBSQL_URL")
    db_token = get_config_value("TURSO_AUTH_TOKEN", "LIBSQL_AUTH_TOKEN")
    if db_url and db_token and libsql is not None:
        conn = libsql.connect(database=db_url, auth_token=db_token)
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
    try:
        yield conn
    finally:
        conn.close()


def rows_to_dicts(cursor: Any) -> list[dict[str, Any]]:
    rows = cursor.fetchall()
    if not rows:
        return []
    if hasattr(rows[0], "keys"):
        return [dict(row) for row in rows]
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def row_to_dict(cursor: Any) -> dict[str, Any] | None:
    row = cursor.fetchone()
    if row is None:
        return None
    if hasattr(row, "keys"):
        return dict(row)
    columns = [column[0] for column in cursor.description]
    return dict(zip(columns, row))


def get_config_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
        try:
            if name in st.secrets:
                return str(st.secrets[name])
        except Exception:
            pass
    return ""


def pin_hash(pin: str) -> str:
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def init_database() -> None:
    with db_connection() as conn:
        schema_statements = [
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS prizes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                emoji TEXT NOT NULL DEFAULT '🎁',
                probability REAL NOT NULL CHECK (probability >= 0),
                quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                issued INTEGER NOT NULL DEFAULT 0 CHECK (issued >= 0),
                enabled INTEGER NOT NULL DEFAULT 1,
                is_win INTEGER NOT NULL DEFAULT 1,
                result_text TEXT NOT NULL DEFAULT ''
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS draws (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_no INTEGER NOT NULL,
                draw_no INTEGER NOT NULL,
                pot_name TEXT NOT NULL,
                prize_id INTEGER,
                prize_name TEXT NOT NULL,
                prize_emoji TEXT NOT NULL,
                is_win INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                redeemed INTEGER NOT NULL DEFAULT 0,
                redeemed_at TEXT DEFAULT ''
            )
            """,
        ]
        for statement in schema_statements:
            conn.execute(statement)
        ensure_column(conn, "draws", "redeemed", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "draws", "redeemed_at", "TEXT DEFAULT ''")

        defaults = {
            "activity_name": "藝起開鍋抽好禮",
            "activity_subtitle": "選一鍋，讓今天的好運滾起來",
            "pot_count": "4",
            "draws_per_customer": "1",
            "activity_enabled": "1",
            "current_customer_no": "1",
            "current_draws_used": "0",
            "admin_pin_hash": pin_hash(DEFAULT_ADMIN_PIN),
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )

        prize_count = conn.execute("SELECT COUNT(*) FROM prizes").fetchone()[0]
        if prize_count == 0:
            conn.executemany(
                """
                INSERT INTO prizes
                    (name, emoji, probability, quantity, issued, enabled, is_win, result_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                DEFAULT_PRIZES,
            )


def ensure_column(conn: Any, table: str, column: str, definition: str) -> None:
    existing = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def get_setting(key: str, default: str = "") -> str:
    with db_connection() as conn:
        row = row_to_dict(conn.execute("SELECT value FROM settings WHERE key = ?", (key,)))
    return str(row["value"]) if row else default


def get_setting_in_transaction(conn: Any, key: str, default: str = "") -> str:
    row = row_to_dict(conn.execute("SELECT value FROM settings WHERE key = ?", (key,)))
    return str(row["value"]) if row else default


def get_settings() -> dict[str, str]:
    with db_connection() as conn:
        rows = rows_to_dicts(conn.execute("SELECT key, value FROM settings"))
    return {str(row["key"]): str(row["value"]) for row in rows}


def set_settings(values: dict[str, Any]) -> None:
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO settings(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, str(value)),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def load_prizes(include_disabled: bool = True) -> list[dict[str, Any]]:
    query = "SELECT * FROM prizes"
    if not include_disabled:
        query += " WHERE enabled = 1"
    query += " ORDER BY id"
    with db_connection() as conn:
        cursor = conn.execute(query)
        return rows_to_dicts(cursor)


def current_status() -> dict[str, Any]:
    settings = get_settings()
    total = max(1, int(settings.get("draws_per_customer", "1")))
    used = max(0, int(settings.get("current_draws_used", "0")))
    customer_no = max(1, int(settings.get("current_customer_no", "1")))
    return {
        "customer_no": customer_no,
        "used": used,
        "total": total,
        "remaining": max(0, total - used),
        "enabled": settings.get("activity_enabled", "1") == "1",
        "pot_count": 2 if settings.get("pot_count", "4") == "2" else 4,
        "activity_name": settings.get("activity_name", "藝起開鍋抽好禮"),
        "activity_subtitle": settings.get("activity_subtitle", "選一鍋，讓今天的好運滾起來"),
    }


def weighted_pick(rows: list[sqlite3.Row]) -> sqlite3.Row:
    total = sum(float(row["probability"]) for row in rows)
    if total <= 0:
        raise ValueError("目前沒有可抽取的獎品機率。")
    target = random.SystemRandom().uniform(0, total)
    cumulative = 0.0
    for row in rows:
        cumulative += float(row["probability"])
        if target <= cumulative:
            return row
    return rows[-1]


def perform_draw(pot_name: str) -> dict[str, Any]:
    """Atomically validate draw count, pick a prize, deduct inventory, and log the draw."""
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            settings_rows = rows_to_dicts(conn.execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?, ?, ?)",
                ("activity_enabled", "current_customer_no", "current_draws_used", "draws_per_customer"),
            ))
            settings = {row["key"]: row["value"] for row in settings_rows}

            if settings.get("activity_enabled", "1") != "1":
                raise ValueError("活動目前未開放。")

            total = max(1, int(settings.get("draws_per_customer", "1")))
            used = max(0, int(settings.get("current_draws_used", "0")))
            customer_no = max(1, int(settings.get("current_customer_no", "1")))
            if used >= total:
                raise ValueError("本位客人的抽獎次數已用完。")

            candidates = rows_to_dicts(conn.execute(
                """
                SELECT * FROM prizes
                WHERE enabled = 1 AND probability > 0
                ORDER BY id
                """
            ))
            if not candidates:
                raise ValueError("目前沒有可抽取的獎品，請通知店員檢查後台。")

            selected = weighted_pick(candidates)
            selected_is_exhausted = (
                int(selected["quantity"]) > 0
                and int(selected["issued"]) >= int(selected["quantity"])
            )

            # 限量獎品用完後，原本那段機率落到可用的「未中獎」項目，
            # 不重新分配給其他獎品，避免其他獎項的實際中獎率被提高。
            if selected_is_exhausted:
                prize = row_to_dict(conn.execute(
                    """
                    SELECT * FROM prizes
                    WHERE enabled = 1
                      AND is_win = 0
                      AND (quantity = 0 OR issued < quantity)
                    ORDER BY CASE WHEN quantity = 0 THEN 0 ELSE 1 END, id
                    LIMIT 1
                    """
                ))
                if prize is None:
                    raise ValueError(
                        "限量獎品已抽完，但後台沒有可用的『未中獎』項目承接機率。"
                    )
            else:
                prize = selected

            if int(prize["quantity"]) > 0:
                updated = conn.execute(
                    """
                    UPDATE prizes
                    SET issued = issued + 1
                    WHERE id = ? AND issued < quantity
                    """,
                    (int(prize["id"]),),
                ).rowcount
                if updated != 1:
                    raise RuntimeError("獎品庫存剛好用完，請再抽一次。")

            new_used = used + 1
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = 'current_draws_used'",
                (str(new_used),),
            )
            created_at = datetime.now().astimezone().isoformat(timespec="seconds")
            conn.execute(
                """
                INSERT INTO draws
                    (customer_no, draw_no, pot_name, prize_id, prize_name, prize_emoji, is_win, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_no,
                    new_used,
                    pot_name,
                    int(prize["id"]),
                    str(prize["name"]),
                    str(prize["emoji"]),
                    int(prize["is_win"]),
                    created_at,
                ),
            )
            draw_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute("COMMIT")

            return {
                "id": draw_id,
                "customer_no": customer_no,
                "draw_no": new_used,
                "pot_name": pot_name,
                "prize_id": int(prize["id"]),
                "name": str(prize["name"]),
                "emoji": str(prize["emoji"]),
                "is_win": bool(prize["is_win"]),
                "result_text": str(prize["result_text"] or prize["name"]),
                "remaining": max(0, total - new_used),
            }
        except Exception:
            conn.execute("ROLLBACK")
            raise


def next_customer() -> None:
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = int(get_setting_in_transaction(conn, "current_customer_no", "1"))
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = 'current_customer_no'",
                (str(current + 1),),
            )
            conn.execute(
                "UPDATE settings SET value = '0' WHERE key = 'current_draws_used'"
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def undo_last_draw() -> str:
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = row_to_dict(conn.execute("SELECT * FROM draws ORDER BY id DESC LIMIT 1"))
            if row is None:
                raise ValueError("目前沒有可撤銷的抽獎紀錄。")

            prize = row_to_dict(conn.execute("SELECT quantity, issued FROM prizes WHERE id = ?", (row["prize_id"],)))
            if prize and int(prize["quantity"]) > 0 and int(prize["issued"]) > 0:
                conn.execute("UPDATE prizes SET issued = issued - 1 WHERE id = ?", (row["prize_id"],))

            current_customer = int(get_setting_in_transaction(conn, "current_customer_no", "1"))
            if int(row["customer_no"]) == current_customer:
                used = int(get_setting_in_transaction(conn, "current_draws_used", "0"))
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = 'current_draws_used'",
                    (str(max(0, used - 1)),),
                )

            conn.execute("DELETE FROM draws WHERE id = ?", (row["id"],))
            conn.execute("COMMIT")
            return str(row["prize_name"])
        except Exception:
            conn.execute("ROLLBACK")
            raise


def reset_activity() -> None:
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM draws")
            conn.execute("UPDATE prizes SET issued = 0")
            conn.execute("UPDATE settings SET value = '1' WHERE key = 'current_customer_no'")
            conn.execute("UPDATE settings SET value = '0' WHERE key = 'current_draws_used'")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def customer_draws(customer_no: int) -> list[dict[str, Any]]:
    with db_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM draws WHERE customer_no = ? ORDER BY draw_no",
            (customer_no,),
        )
        return rows_to_dicts(cursor)


def all_draws(limit: int = 500) -> pd.DataFrame:
    with db_connection() as conn:
        cursor = conn.execute(
            """
            SELECT id, created_at, customer_no, draw_no, pot_name, prize_emoji,
                   prize_name, CASE WHEN is_win = 1 THEN '是' ELSE '否' END AS is_win,
                   redeemed,
                   CASE
                       WHEN is_win = 0 THEN '免核銷'
                       WHEN redeemed = 1 THEN '已核銷'
                       ELSE '未核銷'
                   END AS redeem_status,
                   redeemed_at
            FROM draws ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        return pd.DataFrame(rows_to_dicts(cursor))


def set_draw_redeemed(draw_id: int, redeemed: bool) -> None:
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = row_to_dict(conn.execute("SELECT is_win FROM draws WHERE id = ?", (draw_id,)))
            if row is None:
                raise ValueError("找不到這筆抽獎紀錄。")
            if int(row["is_win"]) != 1:
                raise ValueError("未中獎紀錄不需要核銷。")
            conn.execute(
                "UPDATE draws SET redeemed = ?, redeemed_at = ? WHERE id = ?",
                (
                    1 if redeemed else 0,
                    datetime.now().astimezone().isoformat(timespec="seconds") if redeemed else "",
                    draw_id,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def prize_summary() -> pd.DataFrame:
    prizes = load_prizes(include_disabled=True)
    rows = []
    for prize in prizes:
        quantity = int(prize["quantity"])
        issued = int(prize["issued"])
        remaining = "不限量" if quantity == 0 else max(0, quantity - issued)
        status = "停用"
        if int(prize["enabled"]) == 1:
            status = "抽完" if quantity > 0 and issued >= quantity else "可抽"
        rows.append(
            {
                "獎品": f"{prize['emoji']} {prize['name']}",
                "狀態": status,
                "機率%": float(prize["probability"]),
                "總量": "不限量" if quantity == 0 else str(quantity),
                "已抽出": str(issued),
                "剩餘": str(remaining),
                "需核銷": "是" if int(prize["is_win"]) == 1 else "否",
            }
        )
    return pd.DataFrame(rows)


def is_no_win_prize_name(name: str) -> bool:
    return any(keyword in name for keyword in ("未中獎", "沒中", "再接再厲", "謝謝"))


def calculate_prize_quantity(total_quantity: int, probability: float, name: str) -> int:
    if total_quantity <= 0 or is_no_win_prize_name(name):
        return 0
    return max(0, int(round(total_quantity * probability / 100)))


def save_prizes(edited: pd.DataFrame) -> None:
    clean_rows: list[dict[str, Any]] = []
    for _, raw in edited.iterrows():
        name = str(raw.get("name", "")).strip()
        if not name or name.lower() == "nan":
            continue
        emoji = str(raw.get("emoji", "🎁")).strip()
        if not emoji or emoji.lower() == "nan":
            emoji = "🎁"
        result_text = str(raw.get("result_text", "")).strip()
        if result_text.lower() == "nan":
            result_text = ""
        is_win_value = raw.get("is_win", None)
        if pd.isna(is_win_value):
            is_win = not is_no_win_prize_name(name)
        else:
            is_win = bool(is_win_value)
        row_id = raw.get("id")
        clean_rows.append(
            {
                "id": None if pd.isna(row_id) else int(row_id),
                "name": name,
                "emoji": emoji,
                "probability": round(float(raw.get("probability", 0.0)), 4),
                "quantity": int(raw.get("quantity", 0)),
                "enabled": 1 if bool(raw.get("enabled", True)) else 0,
                "is_win": 1 if is_win else 0,
                "result_text": result_text or name,
            }
        )

    if not clean_rows:
        raise ValueError("至少需要保留一個獎項。")
    if any(row["probability"] < 0 for row in clean_rows):
        raise ValueError("中獎機率不能小於 0。")
    if any(row["quantity"] < 0 for row in clean_rows):
        raise ValueError("獎品數量不能小於 0。")

    enabled_total = sum(row["probability"] for row in clean_rows if row["enabled"])
    if abs(enabled_total - 100.0) > 0.001:
        raise ValueError(f"啟用獎項的機率加總必須是 100%，目前為 {enabled_total:.2f}%。")

    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing_ids = {
                int(row["id"]) for row in rows_to_dicts(conn.execute("SELECT id FROM prizes"))
            }
            submitted_ids: set[int] = set()
            for row in clean_rows:
                if row["id"] is None:
                    conn.execute(
                        """
                        INSERT INTO prizes
                            (name, emoji, probability, quantity, issued, enabled, is_win, result_text)
                        VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                        """,
                        (
                            row["name"], row["emoji"], row["probability"], row["quantity"],
                            row["enabled"], row["is_win"], row["result_text"],
                        ),
                    )
                else:
                    submitted_ids.add(row["id"])
                    conn.execute(
                        """
                        UPDATE prizes
                        SET name = ?, emoji = ?, probability = ?, quantity = ?,
                            enabled = ?, is_win = ?, result_text = ?
                        WHERE id = ?
                        """,
                        (
                            row["name"], row["emoji"], row["probability"], row["quantity"],
                            row["enabled"], row["is_win"], row["result_text"], row["id"],
                        ),
                    )
            removed_ids = existing_ids - submitted_ids
            if removed_ids:
                placeholders = ",".join("?" for _ in removed_ids)
                conn.execute(
                    f"UPDATE prizes SET enabled = 0, probability = 0 WHERE id IN ({placeholders})",
                    tuple(removed_ids),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def apply_global_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --coal: #0d0b0a;
            --ink: #171210;
            --red: #b93428;
            --bright-red: #e14b36;
            --gold: #e3ae56;
            --cream: #fff4df;
            --muted: #d0bfa8;
        }
        html, body, [data-testid="stAppViewContainer"], .stApp {
            background:
                radial-gradient(circle at 50% -5%, rgba(182,52,39,.28), transparent 38%),
                radial-gradient(circle at 15% 55%, rgba(227,174,86,.08), transparent 34%),
                linear-gradient(150deg, #080706 0%, #17100d 55%, #090807 100%);
            color: var(--cream);
        }
        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stToolbar"], #MainMenu, footer { visibility: hidden; }
        [data-testid="stSidebar"] { background: #15100e; }
        .block-container {
            max-width: 1160px;
            padding-top: .25rem;
            padding-bottom: .65rem;
        }
        h1, h2, h3, p, label, .stMarkdown { color: var(--cream); }
        .brand-mark {
            display:none;
        }
        .brand-seal {
            width: 58px;
            height: 58px;
            display:inline-flex;
            align-items:center;
            justify-content:center;
            border: 3px solid #dc4a38;
            border-radius: 50%;
            box-shadow: 0 0 0 7px rgba(220,74,56,.10), 0 0 34px rgba(220,74,56,.35);
            color:#f7d59a;
            font-size: 2.1rem;
            font-family: "DFKai-SB", "KaiTi", serif;
            font-weight: 800;
            line-height:1;
            transform: rotate(-4deg);
        }
        .event-title {
            text-align:center;
            margin:0 0 .03rem;
            font-size: clamp(1.15rem, 2vw, 1.6rem);
            letter-spacing:.12em;
            font-weight:900;
            color:#ffe6b5;
            text-shadow: 0 2px 0 #6c1d17, 0 0 26px rgba(225,75,54,.42);
        }
        .event-subtitle {
            display:none;
            text-align:center;
            color:#d8c5ac;
            font-size: clamp(.9rem, 1.6vw, 1.08rem);
            letter-spacing:.08em;
            margin-bottom:.3rem;
        }
        .status-pill {
            width:fit-content;
            margin:.03rem auto .12rem;
            padding:.18rem .58rem;
            border:1px solid rgba(227,174,86,.35);
            border-radius:999px;
            background:rgba(20,14,12,.7);
            color:#f7d59a;
            font-weight:700;
            box-shadow: inset 0 0 20px rgba(227,174,86,.06);
        }
        .lottery-prompt {
            display:none;
        }
        .pot-grid {
            width:min(95vw, 586px);
            margin:.15rem auto 0;
            display:grid;
            grid-template-columns:repeat(2, minmax(0, 1fr));
            gap:6px;
        }
        .pot-card {
            position:relative;
            aspect-ratio:1 / 1;
            height:auto;
            min-height:0;
            padding:0;
            margin:0;
            overflow:hidden;
            border-radius:8px 8px 0 0;
            border:3px solid rgba(255,248,223,.95);
            border-bottom:0;
            background:#130d09;
            box-shadow:0 10px 24px rgba(0,0,0,.35);
            text-align:center;
            transition:transform .16s ease, filter .16s ease, box-shadow .16s ease;
        }
        .pot-card img {
            width:100%;
            height:108%;
            display:block;
            object-fit:cover;
            transform:translateY(-4%);
        }
        .pot-link {
            display:block;
            text-decoration:none !important;
            color:inherit !important;
        }
        .pot-link:hover .pot-card {
            transform:translateY(-3px) scale(1.01);
            filter:saturate(1.08) brightness(1.04);
            box-shadow:0 14px 30px rgba(0,0,0,.42), inset 0 0 0 2px rgba(255,255,255,.2);
        }
        .pot-link:active .pot-card {
            transform:scale(.97);
            filter:brightness(1.16) saturate(1.18);
        }
        .pot-tile {
            overflow:hidden;
            border-radius:8px;
            margin-bottom:.85rem;
            background:#130d09;
            box-shadow:0 10px 24px rgba(0,0,0,.35);
        }
        .pot-tile div[data-testid="stButton"] {
            margin-top:0;
        }
        .pot-tile div[data-testid="stButton"] > button {
            min-height:1.95rem;
            width:100%;
            border-radius:0 0 8px 8px;
            border:2px solid rgba(255,245,220,.75);
            border-top:0;
            background:linear-gradient(180deg, #f15a38, #9c2118);
            box-shadow:none;
            font-size:.78rem;
            line-height:1;
        }
        .pot-tile div[data-testid="stButton"] > button:hover {
            transform:none;
            box-shadow:0 8px 18px rgba(213,70,49,.24);
        }
        .pot-tile div[data-testid="stButton"] > button:focus,
        .pot-tile div[data-testid="stButton"] > button:focus-visible {
            outline:3px solid rgba(255,216,154,.85);
            outline-offset:2px;
        }
        .pot-card:before {
            content:"";
            position:absolute;
            inset:-35%;
            background:linear-gradient(115deg, transparent 35%, rgba(255,255,255,.7) 48%, transparent 62%);
            transform:translateX(-75%) rotate(8deg);
            animation:card-shine 2.8s ease-in-out infinite;
            pointer-events:none;
            z-index:2;
        }
        .pot-card:after {
            content:"";
            position:absolute;
            inset:0;
            background:linear-gradient(180deg, rgba(255,255,255,.08), transparent 34%, rgba(0,0,0,.08));
            pointer-events:none;
        }
        @keyframes card-shine { 0%,55%{transform:translateX(-75%) rotate(8deg)} 82%,100%{transform:translateX(75%) rotate(8deg)} }
        .poster-title {
            position:relative;
            z-index:4;
            margin-top:.25rem;
            font-size:clamp(2.35rem, 5vw, 4.25rem);
            line-height:1;
            font-weight:950;
            letter-spacing:.02em;
            color:#fff9dc;
            -webkit-text-stroke:2px #2b120d;
            text-shadow:4px 5px 0 #6e2018, 7px 8px 0 rgba(0,0,0,.26);
            font-family:"Microsoft JhengHei","Noto Sans TC",sans-serif;
        }
        .poster-start {
            position:relative;
            z-index:4;
            display:inline-block;
            margin:.55rem auto 0;
            padding:.05rem 1.1rem .2rem;
            color:white;
            font-size:clamp(1.9rem, 3.8vw, 3rem);
            font-weight:950;
            font-style:italic;
            transform:rotate(-5deg);
            text-shadow:3px 4px 0 rgba(0,0,0,.26);
        }
        .poster-food {
            position:absolute;
            z-index:3;
            left:50%;
            bottom:-18px;
            width:min(72%, 315px);
            aspect-ratio:1.35;
            transform:translateX(-50%);
            border-radius:50% 50% 18% 18%;
            background:
                radial-gradient(circle at 52% 42%, rgba(255,238,187,.95) 0 7%, transparent 8%),
                radial-gradient(circle at 32% 52%, #b62d24 0 6%, #fff1d1 7% 10%, transparent 11%),
                radial-gradient(circle at 46% 58%, #d14531 0 6%, #fff1d1 7% 10%, transparent 11%),
                radial-gradient(circle at 61% 58%, #b62d24 0 6%, #fff1d1 7% 10%, transparent 11%),
                radial-gradient(circle at 72% 42%, #e9d9a8 0 5%, transparent 6%),
                radial-gradient(circle at 24% 42%, #8f4c2f 0 5%, transparent 6%),
                linear-gradient(180deg, #283028 0 18%, #15100e 19% 100%);
            border:10px solid #1b1512;
            box-shadow:0 -10px 0 rgba(255,255,255,.18) inset, 0 16px 28px rgba(0,0,0,.45);
        }
        .poster-food:before {
            content:"";
            position:absolute;
            left:5%;
            right:5%;
            top:20%;
            height:44%;
            background:
                repeating-linear-gradient(90deg, #e9eed0 0 8px, #96b35a 8px 13px, transparent 13px 18px);
            border-radius:45% 45% 30% 30%;
            opacity:.86;
        }
        .poster-side {
            position:absolute;
            z-index:2;
            width:94px;
            height:145px;
            left:-18px;
            bottom:42px;
            transform:rotate(-18deg);
            border-radius:12px;
            background:repeating-linear-gradient(90deg, #ba231a 0 11px, #fff6dc 11px 17px);
            border:5px solid #fff3d5;
            box-shadow:0 8px 16px rgba(0,0,0,.3);
        }
        .poster-herb {
            position:absolute;
            z-index:2;
            right:-16px;
            bottom:28px;
            width:105px;
            height:128px;
            border-radius:80% 20% 80% 20%;
            background:repeating-linear-gradient(105deg, #2c7d34 0 10px, #e9edc7 10px 15px);
            transform:rotate(18deg);
            opacity:.95;
        }
        .steam { position:absolute; z-index:5; left:50%; top:52%; width:130px; height:65px; transform:translateX(-50%); }
        .steam i {
            position:absolute; bottom:0; width:11px; height:46px; border-radius:50%;
            border-left:5px solid rgba(255,245,226,.46);
            filter:blur(1px); opacity:.66;
            animation:steam 2.6s ease-in-out infinite;
        }
        .steam i:nth-child(1){left:26px; animation-delay:-.7s}.steam i:nth-child(2){left:58px; height:58px}.steam i:nth-child(3){left:91px; animation-delay:-1.3s}
        @keyframes steam { 0%{transform:translateY(14px) scale(.8);opacity:0} 35%{opacity:.7} 100%{transform:translateY(-25px) translateX(10px) scale(1.25);opacity:0} }
        .pot-lid {
            position:absolute; z-index:3; left:50%; top:77px; transform:translateX(-50%);
            width:156px; height:40px; border-radius:80px 80px 18px 18px;
            background:linear-gradient(#463a34, #171312);
            border:3px solid var(--accent);
            box-shadow:0 8px 18px rgba(0,0,0,.45), inset 0 3px 4px rgba(255,255,255,.1);
        }
        .pot-lid:before { content:""; position:absolute; width:48px; height:21px; border-radius:22px 22px 6px 6px; background:#211a17; border:3px solid var(--accent); left:50%; top:-20px; transform:translateX(-50%); }
        .pot-body {
            position:absolute; z-index:2; left:50%; top:113px; transform:translateX(-50%);
            width:186px; height:83px; border-radius:15px 15px 70px 70px;
            background:linear-gradient(120deg, #3b312c, #100d0c 67%);
            border:4px solid var(--accent);
            box-shadow:0 14px 25px rgba(0,0,0,.5), inset 0 -8px 16px rgba(0,0,0,.55);
        }
        .pot-body:before,.pot-body:after { content:""; position:absolute; top:16px; width:32px; height:13px; border:4px solid var(--accent); border-radius:12px; }
        .pot-body:before { left:-35px; } .pot-body:after { right:-35px; }
        .pot-icon { position:absolute; z-index:4; top:130px; left:50%; transform:translateX(-50%); color:#ffe9bd; font-size:2rem; font-weight:900; font-family:"DFKai-SB","KaiTi",serif; text-shadow:0 0 18px var(--accent); }
        .pot-name { position:absolute; left:0; right:0; bottom:10px; z-index:4; color:#f9dfb4; font-weight:850; font-size:1.15rem; letter-spacing:.1em; }
        .pot-link.is-opening {
            pointer-events:none;
        }
        .pot-link.is-opening .pot-card {
            animation:draw-card-pulse .75s ease-in-out infinite alternate;
            filter:brightness(1.12) saturate(1.18);
        }
        .pot-link.is-opening .pot-card:after {
            content:"開鍋中...";
            position:absolute;
            inset:0;
            z-index:9;
            display:flex;
            align-items:center;
            justify-content:center;
            color:#fff7d8;
            font-size:1.6rem;
            font-weight:950;
            letter-spacing:.12em;
            text-shadow:0 3px 8px rgba(0,0,0,.5);
            background:rgba(116,24,18,.32);
            backdrop-filter:blur(1px);
        }
        @keyframes draw-card-pulse { to { transform:scale(.97); box-shadow:0 0 38px rgba(255,205,105,.55); } }
        div[data-testid="stButton"] > button {
            min-height:2.5rem;
            border-radius:8px;
            border:2px solid rgba(255,245,220,.65);
            background:linear-gradient(180deg, #f15a38, #9c2118);
            color:white;
            font-weight:850;
            font-size:1rem;
            letter-spacing:.05em;
            box-shadow:0 6px 14px rgba(0,0,0,.25), inset 0 1px 0 rgba(255,255,255,.22);
            transition:.18s ease;
        }
        div[data-testid="stButton"] > button:hover {
            border-color:#ffd89a;
            color:#fff8e9;
            transform:translateY(-2px);
            box-shadow:0 12px 28px rgba(213,70,49,.3);
        }
        .opening-stage {
            aspect-ratio:1 / 1;
            height:auto;
            min-height:0;
            display:flex;
            flex-direction:column;
            justify-content:center;
            align-items:center;
            text-align:center;
            border-radius:8px;
            border:3px solid rgba(255,236,190,.92);
            background:radial-gradient(circle, rgba(255,220,104,.55), rgba(209,54,33,.96) 44%, rgba(88,19,16,.98));
            overflow:hidden;
        }
        .opening-pot { position:relative; width:220px; height:170px; animation:shake .2s linear 5, pulse 1.4s ease-in-out infinite; }
        .opening-pot .lid { position:absolute; left:40px; top:38px; width:145px; height:38px; border-radius:95px 95px 18px 18px; background:#211b18; border:5px solid var(--accent, #df4b36); animation:lid-open 2.1s ease-in-out forwards; z-index:3; }
        .opening-pot .lid:before{content:"";position:absolute;width:55px;height:25px;border:5px solid #df4b36;border-bottom:0;border-radius:28px 28px 0 0;left:60px;top:-28px}
        .opening-pot .body { position:absolute; left:18px; top:76px; width:185px; height:82px; border-radius:17px 17px 90px 90px; background:linear-gradient(120deg,#483b35,#0c0a09); border:6px solid var(--accent, #df4b36); box-shadow:0 24px 36px rgba(0,0,0,.5),0 0 50px rgba(223,75,54,.25); }
        .opening-pot .flare { position:absolute; left:50%; top:58px; transform:translateX(-50%); width:145px; height:82px; border-radius:50%; background:radial-gradient(circle,#ffe29c 0%,#e95034 32%,transparent 68%); filter:blur(5px); animation:flare 1.7s ease-in-out infinite; }
        @keyframes shake { 25%{transform:translateX(-5px) rotate(-1deg)} 75%{transform:translateX(5px) rotate(1deg)} }
        @keyframes pulse { 50%{filter:drop-shadow(0 0 22px rgba(231,78,55,.8))} }
        @keyframes lid-open { 0%,35%{transform:translate(0,0) rotate(0)} 70%,100%{transform:translate(36px,-72px) rotate(24deg)} }
        @keyframes flare { 50%{transform:translateX(-50%) scale(1.25);opacity:.65} }
        .opening-text { color:#fff7d8; font-size:1.45rem; font-weight:950; letter-spacing:.12em; text-shadow:0 3px 0 rgba(0,0,0,.25); animation:blink .75s ease-in-out infinite alternate; }
        @keyframes blink{to{opacity:.58}}
        .result-card {
            max-width:780px;
            margin:.8rem auto 1rem;
            padding:2.1rem 1.4rem 1.6rem;
            text-align:center;
            border-radius:22px;
            border:4px solid rgba(255,239,184,.92);
            background:
                radial-gradient(circle at 50% 10%, rgba(255,218,88,.42), transparent 32%),
                linear-gradient(155deg, rgba(202,49,33,.96), rgba(90,22,17,.98) 48%, rgba(17,12,10,.98));
            box-shadow:0 24px 58px rgba(0,0,0,.45),0 0 38px rgba(220,72,50,.22),inset 0 1px 0 rgba(255,255,255,.14);
        }
        .result-emoji { font-size:5rem; line-height:1.05; filter:drop-shadow(0 8px 12px rgba(0,0,0,.35)); }
        .result-kicker { color:#d3b98e; font-weight:700; letter-spacing:.13em; margin-top:.6rem; }
        .result-name { color:#ffe2a8; font-size:clamp(2rem,6vw,3.6rem); font-weight:950; margin:.25rem 0 .65rem; text-shadow:0 2px 0 #77261c,0 0 24px rgba(231,78,55,.4); }
        .result-copy { color:#fff2dc; font-size:1.18rem; }
        .ticket-meta {
            width:fit-content;
            margin:1rem auto 0;
            padding:.45rem .85rem;
            border-radius:999px;
            background:rgba(255,243,205,.12);
            color:#ffe7b2;
            border:1px solid rgba(255,230,170,.35);
            font-weight:800;
        }
        .redeem-hint {
            margin-top:.75rem;
            color:#fff7e8;
            font-size:1.05rem;
            font-weight:800;
        }
        .stock-ok { color:#52d273; font-weight:900; }
        .stock-empty { color:#ff6d5f; font-weight:900; }
        .stock-off { color:#c8bba8; font-weight:900; }
        .summary-card { max-width:720px; margin:1rem auto; padding:1.2rem 1.3rem; border-radius:22px; background:rgba(27,19,16,.88); border:1px solid rgba(230,174,86,.25); }
        .summary-row { display:flex; align-items:center; gap:.8rem; padding:.65rem .3rem; border-bottom:1px dashed rgba(255,255,255,.12); }
        .summary-row:last-child{border-bottom:0}.summary-row .emoji{font-size:1.8rem}.summary-row .label{font-weight:750;color:#ffe5b6}
        .finish-note { text-align:center; color:#dbc6a8; margin:1.2rem 0 .3rem; font-size:1.08rem; }
        .admin-panel { padding:1.1rem 1.2rem; border-radius:20px; background:rgba(23,17,14,.86); border:1px solid rgba(255,255,255,.1); margin-bottom:1rem; }
        [data-testid="stDataFrame"], [data-testid="stDataEditor"] { border-radius:16px; overflow:hidden; }
        .tiny-admin { text-align:center; margin-top:1.5rem; opacity:.58; }
        .tiny-admin a { color:#cdbb9d; text-decoration:none; font-size:.9rem; }
        .admin-return-link {
            display:block;
            width:100%;
            min-height:2.5rem;
            line-height:2.5rem;
            text-align:center;
            border-radius:8px;
            border:2px solid rgba(255,245,220,.65);
            background:linear-gradient(180deg, #f15a38, #9c2118);
            color:white !important;
            font-weight:850;
            text-decoration:none !important;
            box-shadow:0 6px 14px rgba(0,0,0,.25), inset 0 1px 0 rgba(255,255,255,.22);
        }
        @media(max-width:700px){
            .block-container{padding-left:.25rem;padding-right:.25rem}.event-title{font-size:1rem}.event-subtitle{display:none}.status-pill{font-size:.72rem}.pot-grid{width:min(98vw,610px);gap:4px}.poster-title{font-size:1.75rem;-webkit-text-stroke:1.2px #2b120d}.poster-start{font-size:1.55rem}.poster-food{width:82%;bottom:-20px}.poster-side{width:62px;height:105px}.poster-herb{width:72px;height:92px}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(status: dict[str, Any]) -> None:
    st.markdown(
        f"""
        <div class="brand-mark"><div class="brand-seal">藝</div></div>
        <div class="event-title">{escape_html(status['activity_name'])}</div>
        <div class="event-subtitle">{escape_html(status['activity_subtitle'])}</div>
        <div class="status-pill">第 {status['customer_no']} 位客人・還可以抽 {status['remaining']} 次</div>
        """,
        unsafe_allow_html=True,
    )


def escape_html(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


@st.cache_data
def image_data_uri(relative_path: str) -> str:
    path = APP_DIR / relative_path
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/webp;base64,{encoded}"


def pot_card(theme: dict[str, str]) -> str:
    image_src = image_data_uri(theme["image"])
    return f"""
    <div class="pot-card" style="--accent:{theme['accent']};--glow:{theme['glow']};--poster-a:{theme['poster_a']};--poster-b:{theme['poster_b']};--poster-c:{theme['poster_c']}">
        <img src="{image_src}" alt="{escape_html(theme['name'])} 火鍋抽抽樂 START">
    </div>
    """


def opening_animation(pot_name: str, theme: dict[str, str]) -> str:
    return f"""
    <div class="opening-stage" style="--accent:{theme['accent']}">
        <div class="opening-pot">
            <div class="flare"></div>
            <div class="lid"></div>
            <div class="body"></div>
        </div>
        <div class="opening-text">{escape_html(pot_name)} 開鍋中…</div>
    </div>
    """


def render_result(result: dict[str, Any]) -> None:
    kicker = "恭喜中獎" if result["is_win"] else "謝謝參加"
    ticket_no = f"{int(result.get('customer_no', 0)):04d}-{int(result.get('draw_no', 0)):02d}-{int(result.get('id', 0)):06d}"
    redeem_hint = "請將此畫面交給店員核銷" if result["is_win"] else "感謝參加，請交還平板"
    st.markdown(
        f"""
        <div class="result-card">
            <div class="result-emoji">{escape_html(result['emoji'])}</div>
            <div class="result-kicker">{kicker}・{escape_html(result['pot_name'])}</div>
            <div class="result-name">{escape_html(result['name'])}</div>
            <div class="result-copy">{escape_html(result['result_text'])}</div>
            <div class="ticket-meta">券號 {escape_html(ticket_no)}・第 {escape_html(result['customer_no'])} 位客人</div>
            <div class="redeem-hint">{escape_html(redeem_hint)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if result["is_win"] and not st.session_state.get("balloons_shown", False):
        st.balloons()
        st.session_state.balloons_shown = True


def render_customer_summary(customer_no: int) -> None:
    draws = customer_draws(customer_no)
    if not draws:
        return
    rows = "".join(
        f"<div class='summary-row'><span class='emoji'>{escape_html(row['prize_emoji'])}</span><span class='label'>第 {row['draw_no']} 抽・{escape_html(row['prize_name'])}</span></div>"
        for row in draws
    )
    st.markdown(
        f"<div class='summary-card'><h3>本次抽獎結果</h3>{rows}</div>",
        unsafe_allow_html=True,
    )


def set_pending_draw(index: int) -> None:
    st.session_state.pending_draw_index = index


def render_pot_grid(selected_index: int | None = None) -> None:
    for row_start in range(0, 4, 2):
        columns = st.columns(2, gap="small")
        for offset, column in enumerate(columns):
            index = row_start + offset
            theme = POT_THEMES[index]
            with column:
                st.markdown("<div class='pot-tile'>", unsafe_allow_html=True)
                if selected_index == index:
                    st.markdown(opening_animation(theme["name"], theme), unsafe_allow_html=True)
                else:
                    st.markdown(pot_card(theme), unsafe_allow_html=True)
                    st.button("START", key=f"draw_pot_{index}", width="stretch", on_click=set_pending_draw, args=(index,))
                st.markdown("</div>", unsafe_allow_html=True)


def render_lottery_page() -> None:
    status = current_status()
    render_header(status)

    if not status["enabled"]:
        st.warning("活動目前暫停，請將平板交還櫃檯。")
        render_admin_link()
        return

    result = st.session_state.get("last_result")
    if result and int(result.get("customer_no", -1)) == status["customer_no"]:
        render_result(result)
        done_label = "完成，下一位客人" if int(result.get("remaining", 0)) <= 0 else "完成，繼續抽"
        if st.button(done_label, width="stretch", type="primary"):
            st.session_state.pop("last_result", None)
            st.session_state.pop("balloons_shown", None)
            if int(result.get("remaining", 0)) <= 0:
                next_customer()
            st.rerun()
        return

    if status["remaining"] <= 0:
        next_customer()
        st.rerun()

    st.markdown(
        "<h3 class='lottery-prompt'>請憑直覺選一鍋</h3>",
        unsafe_allow_html=True,
    )

    pending_draw = st.session_state.get("pending_draw_index")
    if pending_draw is not None:
        try:
            draw_index = int(pending_draw)
            theme = POT_THEMES[draw_index]
            render_pot_grid(selected_index=draw_index)
            time.sleep(0.9)
            draw_result = perform_draw(theme["name"])
            st.session_state.last_result = draw_result
            st.session_state.balloons_shown = False
            st.session_state.pop("pending_draw_index", None)
            st.rerun()
        except Exception as exc:
            st.session_state.pop("pending_draw_index", None)
            st.error(str(exc))
            render_pot_grid()
    else:
        render_pot_grid()

    render_admin_link()


def render_admin_link() -> None:
    st.markdown(
        "<div class='tiny-admin'><a href='?admin=1' target='_self'>⚙ 管理後台</a></div>",
        unsafe_allow_html=True,
    )


def render_admin_page() -> None:
    top_left, top_right = st.columns([4, 1])
    with top_left:
        st.markdown("# 藝鍋物抽獎管理後台")
    with top_right:
        st.markdown(
            "<a class='admin-return-link' href='?page=lottery&admin=0' target='_self'>返回抽獎頁面</a>",
            unsafe_allow_html=True,
        )

    status = current_status()
    metric_cols = st.columns(4)
    metric_cols[0].metric("目前客人", f"第 {status['customer_no']} 位")
    metric_cols[1].metric("本位已抽", f"{status['used']} / {status['total']}")
    metric_cols[2].metric("剩餘次數", status["remaining"])
    total_draws = len(all_draws(limit=100000))
    metric_cols[3].metric("累積抽獎", total_draws)

    st.markdown("### 櫃檯操作")
    action_cols = st.columns(3)
    with action_cols[0]:
        if st.button("✅ 下一位客人", width="stretch", type="primary"):
            next_customer()
            st.session_state.pop("last_result", None)
            st.session_state.pop("balloons_shown", None)
            st.success("已切換到下一位客人。")
            st.rerun()
    with action_cols[1]:
        if st.button("↩ 撤銷最後一抽", width="stretch"):
            try:
                prize_name = undo_last_draw()
                st.session_state.pop("last_result", None)
                st.success(f"已撤銷：{prize_name}")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    with action_cols[2]:
        enabled = status["enabled"]
        label = "⏸ 暫停活動" if enabled else "▶ 開放活動"
        if st.button(label, width="stretch"):
            set_settings({"activity_enabled": "0" if enabled else "1"})
            st.rerun()

    st.divider()
    settings = get_settings()
    st.markdown("### 活動設定")
    with st.form("activity_settings"):
        activity_name = st.text_input("活動名稱", value=settings.get("activity_name", "藝起開鍋抽好禮"))
        activity_subtitle = st.text_input("副標題", value=settings.get("activity_subtitle", "選一鍋，讓今天的好運滾起來"))
        draws_per_customer = st.number_input(
            "每位客人可抽次數",
            min_value=1,
            max_value=20,
            value=max(1, int(settings.get("draws_per_customer", "1"))),
            step=1,
        )
        save_activity = st.form_submit_button("儲存活動設定", width="stretch")
    if save_activity:
        set_settings(
            {
                "activity_name": activity_name.strip() or "藝起開鍋抽好禮",
                "activity_subtitle": activity_subtitle.strip() or "選一鍋，讓今天的好運滾起來",
                "pot_count": 4,
                "draws_per_customer": int(draws_per_customer),
            }
        )
        st.success("活動設定已儲存。")
        st.rerun()

    st.divider()
    st.markdown("### 獎項設定")
    st.caption("手機填寫版：先填總數量，再填每個獎項名稱與機率。各獎項數量會自動換算。")
    prizes = load_prizes(include_disabled=True)
    prize_df = pd.DataFrame(prizes)
    if prize_df.empty:
        prize_df = pd.DataFrame(
            columns=["id", "enabled", "emoji", "name", "probability", "quantity", "issued", "is_win", "result_text"]
        )
    editor_columns = ["id", "enabled", "emoji", "name", "probability", "quantity", "issued", "is_win", "result_text"]
    prize_df = prize_df.reindex(columns=editor_columns)

    active_prizes = prize_df[prize_df["enabled"].fillna(1).astype(int) == 1]
    default_total_quantity = int(
        active_prizes.loc[
            active_prizes["name"].fillna("").map(lambda value: not is_no_win_prize_name(str(value))),
            "quantity",
        ].fillna(0).astype(int).sum()
    )
    default_prize_count = max(1, len(active_prizes) if not active_prizes.empty else len(prize_df))

    st.info("範例：總數量 100，折價券 10%、小菜一份 20%、未中獎 70%。系統會自動算出折價券 10 張、小菜 20 份；未中獎不限量。")
    total_quantity = st.number_input(
        "獎項總數量",
        min_value=0,
        step=1,
        value=max(0, default_total_quantity),
        help="用來自動換算各獎項份數。填 0 表示全部不限量。",
    )
    prize_count = st.number_input(
        "要設定幾個獎項",
        min_value=1,
        max_value=30,
        step=1,
        value=int(default_prize_count),
        help="包含未中獎項目。例如有 2 個獎品加 1 個未中獎，就填 3。",
    )

    edited_rows: list[dict[str, Any]] = []
    with st.form("prize_settings_form"):
        for index in range(int(prize_count)):
            raw = prize_df.iloc[index].to_dict() if index < len(prize_df) else {}
            row_id = raw.get("id")
            current_name = str(raw.get("name", "") if not pd.isna(raw.get("name", "")) else "")
            current_probability = float(raw.get("probability", 0.0) or 0.0)
            st.markdown(f"#### 獎項 {index + 1}")
            enabled = st.checkbox(
                "使用這個獎項",
                value=bool(raw.get("enabled", True)),
                key=f"prize_enabled_{index}",
            )
            name = st.text_input(
                "獎項名稱",
                value=current_name,
                placeholder="例如：折價券、牛肉盤、未中獎",
                key=f"prize_name_{index}",
            )
            probability = st.number_input(
                "抽中機率（%）",
                min_value=0.0,
                max_value=100.0,
                step=0.1,
                value=max(0.0, min(100.0, current_probability)),
                format="%.1f",
                key=f"prize_probability_{index}",
            )
            calculated_quantity = calculate_prize_quantity(int(total_quantity), float(probability), name.strip())
            if is_no_win_prize_name(name.strip()):
                st.caption("自動數量：不限量")
            else:
                st.caption(f"自動數量：約 {calculated_quantity} 份")
            edited_rows.append(
                {
                    "id": None if pd.isna(row_id) else int(row_id),
                    "enabled": enabled,
                    "emoji": raw.get("emoji", "🎁"),
                    "name": name,
                    "probability": probability,
                    "quantity": calculated_quantity,
                    "issued": int(raw.get("issued", 0) or 0),
                    "is_win": None,
                    "result_text": "",
                }
            )
        save_prize_settings = st.form_submit_button("儲存獎項設定", width="stretch", type="primary")

    edited = pd.DataFrame(edited_rows, columns=editor_columns)
    enabled_probability = float(edited.loc[edited["enabled"] == True, "probability"].fillna(0).sum()) if not edited.empty else 0.0  # noqa: E712
    if abs(enabled_probability - 100.0) <= 0.001:
        st.success(f"目前使用中的機率合計：{enabled_probability:.1f}%")
    else:
        st.error(f"目前使用中的機率合計：{enabled_probability:.1f}%，請調整到 100%。")
    if save_prize_settings:
        try:
            save_prizes(edited)
            st.success("獎品設定已儲存。")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    st.divider()
    st.markdown("### 抽獎紀錄")
    records = all_draws(limit=500)
    if records.empty:
        st.info("尚無抽獎紀錄。")
    else:
        display_records = records.rename(
            columns={
                "created_at": "時間",
                "customer_no": "客人編號",
                "draw_no": "第幾抽",
                "pot_name": "選擇的鍋",
                "prize_emoji": "圖示",
                "prize_name": "抽獎結果",
                "is_win": "中獎",
                "redeem_status": "核銷狀態",
                "redeemed_at": "核銷時間",
            }
        ).drop(columns=["id", "redeemed"])
        st.dataframe(display_records, hide_index=True, width="stretch")
        st.markdown("#### 中獎核銷")
        redeem_rows = records[(records["is_win"] == "是")].head(20)
        if redeem_rows.empty:
            st.info("目前沒有可核銷的中獎紀錄。")
        else:
            for _, row in redeem_rows.iterrows():
                cols = st.columns([3, 2, 2, 2])
                cols[0].markdown(
                    f"第 {int(row['customer_no'])} 位 / {row['prize_emoji']} {row['prize_name']}"
                )
                cols[1].markdown(str(row["redeem_status"]))
                if int(row["redeemed"]) == 1:
                    if cols[2].button("撤銷核銷", key=f"unredeem_{int(row['id'])}"):
                        set_draw_redeemed(int(row["id"]), False)
                        st.rerun()
                else:
                    if cols[2].button("核銷", key=f"redeem_{int(row['id'])}", type="primary"):
                        set_draw_redeemed(int(row["id"]), True)
                        st.rerun()
                cols[3].caption(str(row["created_at"]))
        csv_buffer = io.StringIO()
        display_records.to_csv(csv_buffer, index=False, quoting=csv.QUOTE_MINIMAL)
        st.download_button(
            "下載 CSV",
            data="\ufeff" + csv_buffer.getvalue(),
            file_name=f"yihop_lottery_{datetime.now():%Y%m%d_%H%M%S}.csv",
            mime="text/csv",
            width="stretch",
        )

    st.divider()
    with st.expander("重設資料"):
        confirm_reset = st.checkbox("我了解這會清除所有抽獎紀錄，並將所有已抽出數量歸零。")
        if st.button("清空並重新開始", disabled=not confirm_reset):
            reset_activity()
            st.session_state.pop("last_result", None)
            st.success("活動資料已重設。")
            st.rerun()


init_database()
apply_global_styles()

try:
    page_mode = str(st.query_params.get("page", ""))
    if page_mode == "lottery":
        st.session_state.pop("last_result", None)
        st.session_state.pop("balloons_shown", None)
    if st.session_state.pop("force_lottery_page", False):
        admin_mode = False
    else:
        admin_mode = str(st.query_params.get("admin", "0")) == "1" and page_mode != "lottery"
except Exception:
    admin_mode = False

if admin_mode:
    render_admin_page()
else:
    render_lottery_page()
