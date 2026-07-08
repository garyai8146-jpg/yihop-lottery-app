from __future__ import annotations

import csv
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


APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("LOTTERY_DB_PATH", APP_DIR / "lottery.db"))
DEFAULT_ADMIN_PIN = os.getenv("LOTTERY_ADMIN_PIN", "1688")
TAIPEI_TZ_LABEL = "Asia/Taipei"

POT_THEMES = [
    {"name": "好運紅鍋", "icon": "福", "accent": "#d94432", "glow": "rgba(217,68,50,.46)"},
    {"name": "黃金旺鍋", "icon": "旺", "accent": "#e6aa3c", "glow": "rgba(230,170,60,.45)"},
    {"name": "招財辣鍋", "icon": "辣", "accent": "#ef5b38", "glow": "rgba(239,91,56,.48)"},
    {"name": "幸福暖鍋", "icon": "暖", "accent": "#d88b51", "glow": "rgba(216,139,81,.46)"},
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
def db_connection() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        yield conn
    finally:
        conn.close()


def pin_hash(pin: str) -> str:
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def init_database() -> None:
    with db_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

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
            );

            CREATE TABLE IF NOT EXISTS draws (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_no INTEGER NOT NULL,
                draw_no INTEGER NOT NULL,
                pot_name TEXT NOT NULL,
                prize_id INTEGER,
                prize_name TEXT NOT NULL,
                prize_emoji TEXT NOT NULL,
                is_win INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

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


def get_setting(key: str, default: str = "") -> str:
    with db_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def get_settings() -> dict[str, str]:
    with db_connection() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
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
        rows = conn.execute(query).fetchall()
    return [dict(row) for row in rows]


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
            settings_rows = conn.execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?, ?, ?)",
                ("activity_enabled", "current_customer_no", "current_draws_used", "draws_per_customer"),
            ).fetchall()
            settings = {row["key"]: row["value"] for row in settings_rows}

            if settings.get("activity_enabled", "1") != "1":
                raise ValueError("活動目前未開放。")

            total = max(1, int(settings.get("draws_per_customer", "1")))
            used = max(0, int(settings.get("current_draws_used", "0")))
            customer_no = max(1, int(settings.get("current_customer_no", "1")))
            if used >= total:
                raise ValueError("本位客人的抽獎次數已用完。")

            candidates = conn.execute(
                """
                SELECT * FROM prizes
                WHERE enabled = 1 AND probability > 0
                ORDER BY id
                """
            ).fetchall()
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
                prize = conn.execute(
                    """
                    SELECT * FROM prizes
                    WHERE enabled = 1
                      AND is_win = 0
                      AND (quantity = 0 OR issued < quantity)
                    ORDER BY CASE WHEN quantity = 0 THEN 0 ELSE 1 END, id
                    LIMIT 1
                    """
                ).fetchone()
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
            conn.execute("COMMIT")

            return {
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
            current = int(
                conn.execute(
                    "SELECT value FROM settings WHERE key = 'current_customer_no'"
                ).fetchone()[0]
            )
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
            row = conn.execute("SELECT * FROM draws ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                raise ValueError("目前沒有可撤銷的抽獎紀錄。")

            prize = conn.execute("SELECT quantity, issued FROM prizes WHERE id = ?", (row["prize_id"],)).fetchone()
            if prize and int(prize["quantity"]) > 0 and int(prize["issued"]) > 0:
                conn.execute("UPDATE prizes SET issued = issued - 1 WHERE id = ?", (row["prize_id"],))

            current_customer = int(
                conn.execute(
                    "SELECT value FROM settings WHERE key = 'current_customer_no'"
                ).fetchone()[0]
            )
            if int(row["customer_no"]) == current_customer:
                used = int(
                    conn.execute(
                        "SELECT value FROM settings WHERE key = 'current_draws_used'"
                    ).fetchone()[0]
                )
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
        rows = conn.execute(
            "SELECT * FROM draws WHERE customer_no = ? ORDER BY draw_no",
            (customer_no,),
        ).fetchall()
    return [dict(row) for row in rows]


def all_draws(limit: int = 500) -> pd.DataFrame:
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, customer_no, draw_no, pot_name, prize_emoji,
                   prize_name, CASE WHEN is_win = 1 THEN '是' ELSE '否' END AS is_win
            FROM draws ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return pd.DataFrame([dict(row) for row in rows])


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
        row_id = raw.get("id")
        clean_rows.append(
            {
                "id": None if pd.isna(row_id) else int(row_id),
                "name": name,
                "emoji": emoji,
                "probability": round(float(raw.get("probability", 0.0)), 4),
                "quantity": int(raw.get("quantity", 0)),
                "enabled": 1 if bool(raw.get("enabled", True)) else 0,
                "is_win": 1 if bool(raw.get("is_win", True)) else 0,
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
                int(row[0]) for row in conn.execute("SELECT id FROM prizes").fetchall()
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
            max-width: 1080px;
            padding-top: 1.1rem;
            padding-bottom: 2rem;
        }
        h1, h2, h3, p, label, .stMarkdown { color: var(--cream); }
        .brand-mark {
            text-align:center;
            margin: .2rem auto .25rem;
        }
        .brand-seal {
            width: 94px;
            height: 94px;
            display:inline-flex;
            align-items:center;
            justify-content:center;
            border: 3px solid #dc4a38;
            border-radius: 50%;
            box-shadow: 0 0 0 7px rgba(220,74,56,.10), 0 0 34px rgba(220,74,56,.35);
            color:#f7d59a;
            font-size: 3.35rem;
            font-family: "DFKai-SB", "KaiTi", serif;
            font-weight: 800;
            line-height:1;
            transform: rotate(-4deg);
        }
        .event-title {
            text-align:center;
            margin:.55rem 0 .1rem;
            font-size: clamp(2rem, 5vw, 3.55rem);
            letter-spacing:.12em;
            font-weight:900;
            color:#ffe6b5;
            text-shadow: 0 2px 0 #6c1d17, 0 0 26px rgba(225,75,54,.42);
        }
        .event-subtitle {
            text-align:center;
            color:#d8c5ac;
            font-size: clamp(1rem, 2.2vw, 1.25rem);
            letter-spacing:.08em;
            margin-bottom:.8rem;
        }
        .status-pill {
            width:fit-content;
            margin:.25rem auto 1rem;
            padding:.55rem 1rem;
            border:1px solid rgba(227,174,86,.35);
            border-radius:999px;
            background:rgba(20,14,12,.7);
            color:#f7d59a;
            font-weight:700;
            box-shadow: inset 0 0 20px rgba(227,174,86,.06);
        }
        .pot-card {
            position:relative;
            min-height:235px;
            padding:28px 10px 14px;
            margin:.35rem .15rem .1rem;
            overflow:hidden;
            border-radius:28px;
            border:1px solid rgba(255,238,210,.13);
            background:linear-gradient(165deg, rgba(44,31,25,.9), rgba(12,10,9,.96));
            box-shadow:0 18px 38px rgba(0,0,0,.32), inset 0 1px 0 rgba(255,255,255,.06);
            text-align:center;
        }
        .pot-card:before {
            content:"";
            position:absolute;
            width:150px;
            height:150px;
            border-radius:50%;
            left:50%; top:46%;
            transform:translate(-50%,-50%);
            background:var(--glow);
            filter:blur(38px);
            opacity:.72;
        }
        .steam { position:absolute; left:50%; top:11px; width:130px; height:65px; transform:translateX(-50%); }
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
        div[data-testid="stButton"] > button {
            min-height:3.25rem;
            border-radius:16px;
            border:1px solid rgba(255,225,180,.42);
            background:linear-gradient(180deg, #d94c37, #8f281f);
            color:white;
            font-weight:850;
            font-size:1.05rem;
            letter-spacing:.05em;
            box-shadow:0 8px 22px rgba(0,0,0,.28), inset 0 1px 0 rgba(255,255,255,.22);
            transition:.18s ease;
        }
        div[data-testid="stButton"] > button:hover {
            border-color:#ffd89a;
            color:#fff8e9;
            transform:translateY(-2px);
            box-shadow:0 12px 28px rgba(213,70,49,.3);
        }
        .opening-stage {
            min-height:440px;
            display:flex;
            flex-direction:column;
            justify-content:center;
            align-items:center;
            text-align:center;
            border-radius:32px;
            border:1px solid rgba(255,226,181,.18);
            background:radial-gradient(circle, rgba(222,71,48,.25), rgba(15,10,8,.92) 58%);
            overflow:hidden;
        }
        .opening-pot { position:relative; width:270px; height:220px; animation:shake .2s linear 5, pulse 1.4s ease-in-out infinite; }
        .opening-pot .lid { position:absolute; left:43px; top:52px; width:185px; height:48px; border-radius:95px 95px 18px 18px; background:#211b18; border:5px solid #df4b36; animation:lid-open 2.1s ease-in-out forwards; z-index:3; }
        .opening-pot .lid:before{content:"";position:absolute;width:55px;height:25px;border:5px solid #df4b36;border-bottom:0;border-radius:28px 28px 0 0;left:60px;top:-28px}
        .opening-pot .body { position:absolute; left:24px; top:96px; width:222px; height:105px; border-radius:17px 17px 90px 90px; background:linear-gradient(120deg,#483b35,#0c0a09); border:6px solid #df4b36; box-shadow:0 24px 36px rgba(0,0,0,.5),0 0 50px rgba(223,75,54,.25); }
        .opening-pot .flare { position:absolute; left:50%; top:74px; transform:translateX(-50%); width:165px; height:90px; border-radius:50%; background:radial-gradient(circle,#ffe29c 0%,#e95034 32%,transparent 68%); filter:blur(5px); animation:flare 1.7s ease-in-out infinite; }
        @keyframes shake { 25%{transform:translateX(-5px) rotate(-1deg)} 75%{transform:translateX(5px) rotate(1deg)} }
        @keyframes pulse { 50%{filter:drop-shadow(0 0 22px rgba(231,78,55,.8))} }
        @keyframes lid-open { 0%,35%{transform:translate(0,0) rotate(0)} 70%,100%{transform:translate(36px,-72px) rotate(24deg)} }
        @keyframes flare { 50%{transform:translateX(-50%) scale(1.25);opacity:.65} }
        .opening-text { color:#ffe5af; font-size:1.8rem; font-weight:900; letter-spacing:.12em; animation:blink .75s ease-in-out infinite alternate; }
        @keyframes blink{to{opacity:.58}}
        .result-card {
            max-width:720px;
            margin:1rem auto;
            padding:2rem 1.4rem;
            text-align:center;
            border-radius:30px;
            border:2px solid rgba(229,174,82,.56);
            background:linear-gradient(155deg, rgba(67,29,22,.94), rgba(17,12,10,.96));
            box-shadow:0 26px 58px rgba(0,0,0,.42),0 0 38px rgba(220,72,50,.18),inset 0 1px 0 rgba(255,255,255,.08);
        }
        .result-emoji { font-size:5rem; line-height:1.05; filter:drop-shadow(0 8px 12px rgba(0,0,0,.35)); }
        .result-kicker { color:#d3b98e; font-weight:700; letter-spacing:.13em; margin-top:.6rem; }
        .result-name { color:#ffe2a8; font-size:clamp(2rem,6vw,3.6rem); font-weight:950; margin:.25rem 0 .65rem; text-shadow:0 2px 0 #77261c,0 0 24px rgba(231,78,55,.4); }
        .result-copy { color:#fff2dc; font-size:1.18rem; }
        .summary-card { max-width:720px; margin:1rem auto; padding:1.2rem 1.3rem; border-radius:22px; background:rgba(27,19,16,.88); border:1px solid rgba(230,174,86,.25); }
        .summary-row { display:flex; align-items:center; gap:.8rem; padding:.65rem .3rem; border-bottom:1px dashed rgba(255,255,255,.12); }
        .summary-row:last-child{border-bottom:0}.summary-row .emoji{font-size:1.8rem}.summary-row .label{font-weight:750;color:#ffe5b6}
        .finish-note { text-align:center; color:#dbc6a8; margin:1.2rem 0 .3rem; font-size:1.08rem; }
        .admin-panel { padding:1.1rem 1.2rem; border-radius:20px; background:rgba(23,17,14,.86); border:1px solid rgba(255,255,255,.1); margin-bottom:1rem; }
        [data-testid="stDataFrame"], [data-testid="stDataEditor"] { border-radius:16px; overflow:hidden; }
        .tiny-admin { text-align:center; margin-top:1.5rem; opacity:.58; }
        .tiny-admin a { color:#cdbb9d; text-decoration:none; font-size:.9rem; }
        @media(max-width:700px){
            .block-container{padding-left:.75rem;padding-right:.75rem}.pot-card{min-height:205px}.pot-lid{top:65px;width:132px}.pot-body{top:99px;width:157px;height:72px}.pot-icon{top:114px}.pot-name{font-size:.96rem}.steam{top:3px}.brand-seal{width:76px;height:76px;font-size:2.7rem}
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


def pot_card(theme: dict[str, str]) -> str:
    return f"""
    <div class="pot-card" style="--accent:{theme['accent']};--glow:{theme['glow']}">
        <div class="steam"><i></i><i></i><i></i></div>
        <div class="pot-lid"></div>
        <div class="pot-body"></div>
        <div class="pot-icon">{escape_html(theme['icon'])}</div>
        <div class="pot-name">{escape_html(theme['name'])}</div>
    </div>
    """


def opening_animation(pot_name: str) -> str:
    return f"""
    <div class="opening-stage">
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
    st.markdown(
        f"""
        <div class="result-card">
            <div class="result-emoji">{escape_html(result['emoji'])}</div>
            <div class="result-kicker">{kicker}・{escape_html(result['pot_name'])}</div>
            <div class="result-name">{escape_html(result['name'])}</div>
            <div class="result-copy">{escape_html(result['result_text'])}</div>
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
        if status["remaining"] > 0:
            if st.button("🍲 再選一鍋", width="stretch", type="primary"):
                st.session_state.pop("last_result", None)
                st.session_state.pop("balloons_shown", None)
                st.rerun()
        else:
            render_customer_summary(status["customer_no"])
            st.markdown(
                "<div class='finish-note'>抽獎完成，請將平板交還櫃檯人員。</div>",
                unsafe_allow_html=True,
            )
        render_admin_link()
        return

    if status["remaining"] <= 0:
        render_customer_summary(status["customer_no"])
        st.markdown(
            "<div class='finish-note'>抽獎完成，請將平板交還櫃檯人員。</div>",
            unsafe_allow_html=True,
        )
        render_admin_link()
        return

    st.markdown(
        "<h3 style='text-align:center;color:#f6d9a6;letter-spacing:.12em;margin:.15rem 0 .55rem'>請憑直覺選一鍋</h3>",
        unsafe_allow_html=True,
    )

    themes = POT_THEMES[: status["pot_count"]]
    for row_start in range(0, len(themes), 2):
        columns = st.columns(2, gap="medium")
        for column_index, theme in enumerate(themes[row_start : row_start + 2]):
            index = row_start + column_index
            with columns[column_index]:
                st.markdown(pot_card(theme), unsafe_allow_html=True)
                if st.button(
                    f"選擇 {theme['name']}",
                    key=f"pot_{status['customer_no']}_{status['used']}_{index}",
                    width="stretch",
                ):
                    try:
                        draw_result = perform_draw(theme["name"])
                        animation = st.empty()
                        animation.markdown(opening_animation(theme["name"]), unsafe_allow_html=True)
                        time.sleep(2.15)
                        st.session_state.last_result = draw_result
                        st.session_state.balloons_shown = False
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    render_admin_link()


def render_admin_link() -> None:
    st.markdown(
        "<div class='tiny-admin'><a href='?admin=1' target='_self'>⚙ 管理後台</a></div>",
        unsafe_allow_html=True,
    )


def admin_authenticated() -> bool:
    return bool(st.session_state.get("admin_authenticated", False))


def render_admin_login() -> None:
    st.markdown("## 管理後台")
    st.caption("初始 PIN：1688。首次使用後請立即修改。")
    with st.form("admin_login", clear_on_submit=True):
        pin = st.text_input("管理 PIN", type="password", max_chars=20)
        submitted = st.form_submit_button("登入", width="stretch")
    if submitted:
        if pin_hash(pin) == get_setting("admin_pin_hash"):
            st.session_state.admin_authenticated = True
            st.rerun()
        else:
            st.error("PIN 錯誤。")
    if st.button("← 返回抽獎頁"):
        st.query_params.clear()
        st.rerun()


def render_admin_page() -> None:
    if not admin_authenticated():
        render_admin_login()
        return

    top_left, top_right = st.columns([4, 1])
    with top_left:
        st.markdown("# 藝鍋物抽獎管理後台")
    with top_right:
        if st.button("返回抽獎頁", width="stretch"):
            st.query_params.clear()
            st.session_state.pop("last_result", None)
            st.rerun()

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
        setting_cols = st.columns(2)
        with setting_cols[0]:
            pot_count = st.radio(
                "顯示幾個火鍋",
                options=[2, 4],
                index=0 if settings.get("pot_count") == "2" else 1,
                horizontal=True,
            )
        with setting_cols[1]:
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
                "pot_count": int(pot_count),
                "draws_per_customer": int(draws_per_customer),
            }
        )
        st.success("活動設定已儲存。")
        st.rerun()

    st.divider()
    st.markdown("### 獎品、機率與數量")
    st.caption("數量填 0 代表不限量。啟用獎項的機率合計必須為 100%。庫存用完後，該獎項會自動停止抽出。")
    prizes = load_prizes(include_disabled=True)
    prize_df = pd.DataFrame(prizes)
    if prize_df.empty:
        prize_df = pd.DataFrame(
            columns=["id", "enabled", "emoji", "name", "probability", "quantity", "issued", "is_win", "result_text"]
        )
    editor_columns = ["id", "enabled", "emoji", "name", "probability", "quantity", "issued", "is_win", "result_text"]
    prize_df = prize_df.reindex(columns=editor_columns)
    edited = st.data_editor(
        prize_df,
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
        disabled=["id", "issued"],
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "enabled": st.column_config.CheckboxColumn("啟用", default=True, width="small"),
            "emoji": st.column_config.TextColumn("圖示", width="small", max_chars=8),
            "name": st.column_config.TextColumn("獎品名稱", required=True, width="medium"),
            "probability": st.column_config.NumberColumn("機率 %", min_value=0.0, max_value=100.0, step=0.1, format="%.2f"),
            "quantity": st.column_config.NumberColumn("總數量", min_value=0, step=1, help="0 代表不限量"),
            "issued": st.column_config.NumberColumn("已抽出", width="small"),
            "is_win": st.column_config.CheckboxColumn("算中獎", default=True, width="small"),
            "result_text": st.column_config.TextColumn("結果顯示文字", width="large"),
        },
        key="prize_editor",
    )
    enabled_probability = float(edited.loc[edited["enabled"] == True, "probability"].fillna(0).sum()) if not edited.empty else 0.0  # noqa: E712
    st.info(f"目前啟用機率合計：{enabled_probability:.2f}%")
    if st.button("儲存獎品設定", width="stretch", type="primary"):
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
            }
        ).drop(columns=["id"])
        st.dataframe(display_records, hide_index=True, width="stretch")
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
    with st.expander("安全與重設"):
        st.markdown("#### 修改管理 PIN")
        with st.form("change_pin"):
            new_pin = st.text_input("新 PIN", type="password", max_chars=20)
            confirm_pin = st.text_input("再次輸入新 PIN", type="password", max_chars=20)
            pin_submit = st.form_submit_button("修改 PIN")
        if pin_submit:
            if len(new_pin) < 4:
                st.error("PIN 至少需要 4 碼。")
            elif new_pin != confirm_pin:
                st.error("兩次輸入的 PIN 不一致。")
            else:
                set_settings({"admin_pin_hash": pin_hash(new_pin)})
                st.success("管理 PIN 已修改。")

        st.markdown("#### 清空活動資料")
        confirm_reset = st.checkbox("我了解這會清除所有抽獎紀錄，並將所有已抽出數量歸零。")
        if st.button("清空並重新開始", disabled=not confirm_reset):
            reset_activity()
            st.session_state.pop("last_result", None)
            st.success("活動資料已重設。")
            st.rerun()

        if st.button("登出後台"):
            st.session_state.admin_authenticated = False
            st.query_params.clear()
            st.rerun()


init_database()
apply_global_styles()

try:
    admin_mode = str(st.query_params.get("admin", "0")) == "1"
except Exception:
    admin_mode = False

if admin_mode:
    render_admin_page()
else:
    render_lottery_page()
