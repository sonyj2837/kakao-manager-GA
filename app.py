import base64
import hashlib
import math
import os
import re
import sqlite3
import json
import sys
import time as py_time
import urllib.request
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST).replace(tzinfo=None)


def today_kst() -> date:
    return now_kst().date()


def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


# ==========================================
# 1. 페이지 설정 & 모바일 좌측 트리 메뉴 CSS
# ==========================================
st.set_page_config(
    page_title="부방 통합 관리 시스템",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stButton > button {
        min-height: 44px;
        border-radius: 10px;
        font-weight: 600;
    }

    section[data-testid="stSidebar"] {
        width: 270px !important;
    }
    section[data-testid="stSidebar"] .block-container {
        padding-top: 1.2rem !important;
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
    }

    .nav-group-title {
        font-size: 0.96rem;
        font-weight: 700;
        color: #1f2937;
        margin-top: 12px;
        margin-bottom: 4px;
        padding: 4px 6px;
        border-left: 3px solid #3b82f6;
        background-color: rgba(59, 130, 246, 0.06);
        border-radius: 0 6px 6px 0;
    }
    @media (prefers-color-scheme: dark) {
        .nav-group-title {
            color: #f3f4f6;
            background-color: rgba(59, 130, 246, 0.15);
        }
    }

    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] > div {
        gap: 0.25rem !important;
    }
    section[data-testid="stSidebar"] .stButton > button {
        min-height: 38px !important;
        padding: 4px 10px !important;
        justify-content: flex-start !important;
        text-align: left !important;
        font-size: 0.88rem !important;
        font-weight: 500 !important;
        border-radius: 8px !important;
    }

    .chat-bubble {
        padding: 10px 12px;
        border-radius: 10px;
        margin-bottom: 8px;
        border: 1px solid rgba(128, 128, 128, 0.25);
        background-color: rgba(128, 128, 128, 0.06);
    }
    .chat-meta {
        font-size: 0.78rem;
        opacity: 0.75;
        margin-bottom: 4px;
    }

    @media (max-width: 768px) {
        .block-container {
            padding-top: 2.2rem !important;
            padding-left: 0.85rem !important;
            padding-right: 0.85rem !important;
            padding-bottom: 2rem !important;
        }
        h1 {
            font-size: 1.4rem !important;
        }
        h2, h3 {
            font-size: 1.12rem !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ==========================================
# 2. 데이터베이스 연결 (Supabase Postgres / SQLite)
# ==========================================
DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manager.db")
MANAGER_GRADES = ("MS", "MP")
WEEKDAY_NAMES = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

DEFAULT_VACATION_RULES = [
    ("중간/기말고사 (7일)", 7, None, 0, "📌 [안내] 중간/기말고사 일정표 또는 가정통신문 사진(캡처)을 인증란에 첨부해 주세요."),
    ("모의고사 (5일)", 5, None, 0, "📌 [안내] 모의고사 응시 확인서 또는 학년/성적 관련 인증 자료를 첨부해 주세요."),
    ("연휴 - 추석/설날 (3일)", 3, None, 0, "📌 [안내] 명절 연휴 기간 정기 휴가입니다. 별도의 증빙 사진 없이 신청 가능합니다."),
    ("공휴일 (2일 - 월 1회)", 2, 1, 0, "📌 [안내] 국가지정공휴일에 한해 인정되며, 월 최대 1회만 신청할 수 있습니다."),
    ("교내/사내행사 1박 이상 (2일 - 월 1회)", 2, 1, 0, "📌 [안내] 수학여행 등 1박 이상 머무는 행사의 가정통신문 또는 예약 증빙 자료를 첨부해 주세요."),
    ("생일 (1일)", 1, None, 0, "📌 [안내] 생일자 본인을 위한 1일 휴가입니다. 별도의 인증 절차가 요구되지 않습니다."),
    ("가족행사 (1일 - 월 1회)", 1, 1, 0, "📌 [안내] 가족 행사(결혼식, 제사 등) 관련 안내장이나 확인 가능한 메모를 남겨주세요."),
    ("교내/사내행사 당일치기 (1일 - 월 2회)", 1, 2, 0, "📌 [안내] 현장체험학습 등 당일치기 행사의 가정통신문을 첨부해 주세요."),
    ("관리자재량 - 부상 (월 2회)", 0, 2, 1, "📌 [안내] 진단서 또는 자택 재활 증명 사진을 첨부하고 총괄부방/방장과 협의해 주세요."),
    ("관리자재량 - 가족사 (제한없음)", 0, None, 1, "📌 [안내] 관련 내용을 기재해 주시면 관리자 협의 후 지급됩니다."),
]


def get_database_url() -> str:
    try:
        if "DATABASE_URL" in st.secrets and str(st.secrets["DATABASE_URL"]).strip():
            return str(st.secrets["DATABASE_URL"]).strip()
    except Exception:
        pass
    return os.environ.get("DATABASE_URL", "").strip()


def is_postgres_configured() -> bool:
    return bool(get_database_url())


class PgCursorWrapper:
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql: str, params: tuple | list = ()):
        self._cur.execute(_convert_sql_to_pg(sql), params)
        return self

    def executemany(self, sql: str, seq_of_params):
        pg_sql = _convert_sql_to_pg(sql)
        for p in seq_of_params:
            self._cur.execute(pg_sql, p)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount


class PgConnWrapper:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        return PgCursorWrapper(self._conn.cursor(cursor_factory=RealDictCursor))

    def execute(self, sql: str, params: tuple | list = ()):
        cur = self.cursor()
        return cur.execute(sql, params)

    def executemany(self, sql: str, seq_of_params):
        cur = self.cursor()
        return cur.executemany(sql, seq_of_params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _convert_sql_to_pg(sql: str) -> str:
    s = sql.replace("?", "%s")
    s = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", s, flags=re.IGNORECASE)
    if re.search(r"\bINSERT\s+INTO\b", s, flags=re.IGNORECASE) and "ON CONFLICT" not in s.upper():
        s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return s


def get_conn():
    url = get_database_url()
    if url:
        if psycopg2 is None:
            st.session_state["db_status_msg"] = "psycopg2-binary 미설치"
            raise RuntimeError("psycopg2-binary가 설치되어 있지 않습니다. requirements.txt에 추가해 주세요.")
        try:
            raw = psycopg2.connect(url, sslmode="require")
            st.session_state["db_status_msg"] = "Supabase DB 연결됨"
            return PgConnWrapper(raw)
        except Exception as e:
            st.session_state["db_status_msg"] = f"Supabase 연결 실패: {e}"
            raise
    st.session_state["db_status_msg"] = "로컬 SQLite 모드 (재시작 시 초기화 주의)"
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def read_sql_df(sql: str, params: tuple | list = ()) -> pd.DataFrame:
    conn = get_conn()
    try:
        if is_postgres_configured():
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    use_pg = is_postgres_configured()

    if use_pg:
        cur.execute("CREATE TABLE IF NOT EXISTS admins (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL)")
        cur.execute("SELECT COUNT(*) AS cnt FROM admins")
        if cur.fetchone()["cnt"] == 0:
            cur.execute("INSERT INTO admins (username, password_hash) VALUES (%s, %s)", ("admin", hash_pw("1234")))

        cur.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id SERIAL PRIMARY KEY,
            nickname TEXT UNIQUE NOT NULL,
            role TEXT DEFAULT '부방',
            grade TEXT DEFAULT 'M1',
            points INTEGER DEFAULT 0,
            vacation_days INTEGER DEFAULT 0,
            status TEXT DEFAULT '활동중',
            joined_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nickname TEXT UNIQUE NOT NULL,
            approval_status TEXT DEFAULT '대기중',
            created_at TEXT NOT NULL,
            approved_at TEXT DEFAULT ''
        )
        """)

        now_str = now_kst().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "INSERT INTO users (username, password_hash, nickname, approval_status, created_at, approved_at) VALUES (%s, %s, %s, '승인완료', %s, %s) ON CONFLICT (username) DO NOTHING",
            ("admin", hash_pw("1234"), "총괄관리자", now_str, now_str),
        )
        cur.execute(
            "INSERT INTO members (nickname, role, grade, points, vacation_days, status, joined_at) VALUES ('총괄관리자', '총괄', 'MP', 0, 0, '활동중', %s) ON CONFLICT (nickname) DO NOTHING",
            (now_str,),
        )

        cur.execute("CREATE TABLE IF NOT EXISTS system_options (opt_key TEXT PRIMARY KEY, opt_value TEXT NOT NULL)")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS vacation_rule_items (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            days INTEGER NOT NULL DEFAULT 1,
            monthly_limit INTEGER,
            is_discretionary INTEGER DEFAULT 0,
            guide TEXT DEFAULT ''
        )
        """)
        cur.execute("SELECT COUNT(*) AS cnt FROM vacation_rule_items")
        if cur.fetchone()["cnt"] == 0:
            for item in DEFAULT_VACATION_RULES:
                cur.execute(
                    "INSERT INTO vacation_rule_items (name, days, monthly_limit, is_discretionary, guide) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (name) DO NOTHING",
                    item,
                )

        cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id SERIAL PRIMARY KEY,
            app_name TEXT NOT NULL,
            username TEXT NOT NULL,
            nickname TEXT NOT NULL,
            grade TEXT DEFAULT '-',
            action TEXT NOT NULL,
            detail TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            member_id INTEGER REFERENCES members(id),
            season TEXT,
            week TEXT,
            log_type TEXT,
            category TEXT,
            minutes INTEGER DEFAULT 0,
            points_delta INTEGER NOT NULL,
            vacation_delta INTEGER DEFAULT 0,
            memo TEXT,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id SERIAL PRIMARY KEY,
            item_name TEXT NOT NULL,
            price INTEGER NOT NULL,
            description TEXT,
            stock INTEGER DEFAULT 99
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS shop_orders (
            id SERIAL PRIMARY KEY,
            member_id INTEGER REFERENCES members(id),
            item_name TEXT,
            price INTEGER,
            status TEXT DEFAULT '대기중',
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS vacation_requests (
            id SERIAL PRIMARY KEY,
            member_id INTEGER REFERENCES members(id),
            category TEXT NOT NULL,
            requested_days INTEGER NOT NULL,
            proof_memo TEXT,
            proof_image TEXT,
            status TEXT DEFAULT '대기중',
            request_month TEXT,
            created_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS mod_records (
            id SERIAL PRIMARY KEY,
            target_name TEXT NOT NULL,
            action_date TEXT NOT NULL,
            action_time TEXT NOT NULL,
            action_dt TEXT NOT NULL,
            cutoff_date TEXT NOT NULL,
            room TEXT NOT NULL,
            action_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            staff_name TEXT NOT NULL,
            is_valid_score INTEGER DEFAULT 1,
            review_status TEXT DEFAULT '정상유지',
            note TEXT DEFAULT '',
            warning_count INTEGER DEFAULT 0,
            action_detail TEXT DEFAULT '',
            evidence_image TEXT DEFAULT '',
            timer_end_dt TEXT DEFAULT '',
            timer_notified INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """)
        cur.execute("CREATE TABLE IF NOT EXISTS mod_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS mod_score_sync_log (
            id SERIAL PRIMARY KEY,
            week_cutoff TEXT NOT NULL,
            staff_name TEXT NOT NULL,
            member_id INTEGER,
            valid_count INTEGER NOT NULL,
            score_delta INTEGER NOT NULL,
            status TEXT NOT NULL,
            message TEXT DEFAULT '',
            synced_at TEXT NOT NULL,
            UNIQUE(week_cutoff, staff_name)
        )
        """)
        for k, v in {"cutoff_weekday": "2", "cutoff_hour": "22", "cutoff_minute": "0"}.items():
            cur.execute("INSERT INTO mod_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", (k, v))

        cur.execute("""
        CREATE TABLE IF NOT EXISTS voice_records (
            id SERIAL PRIMARY KEY,
            staff_name TEXT NOT NULL,
            room TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            end_time TEXT NOT NULL,
            end_dt TEXT NOT NULL,
            cutoff_date TEXT NOT NULL,
            duration_str TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL,
            is_valid_score INTEGER DEFAULT 1,
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """)
        cur.execute("CREATE TABLE IF NOT EXISTS voice_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS voice_score_sync_log (
            id SERIAL PRIMARY KEY,
            week_cutoff TEXT NOT NULL,
            staff_name TEXT NOT NULL,
            member_id INTEGER,
            total_seconds INTEGER NOT NULL,
            score_delta INTEGER NOT NULL,
            status TEXT NOT NULL,
            message TEXT DEFAULT '',
            synced_at TEXT NOT NULL,
            UNIQUE(week_cutoff, staff_name)
        )
        """)
        for k, v in {"cutoff_weekday": "2", "cutoff_hour": "22", "cutoff_minute": "0"}.items():
            cur.execute("INSERT INTO voice_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", (k, v))

        cur.execute("""
        CREATE TABLE IF NOT EXISTS suggestion_box (
            id SERIAL PRIMARY KEY,
            author_username TEXT NOT NULL,
            author_nickname TEXT NOT NULL,
            author_grade TEXT DEFAULT '-',
            category TEXT DEFAULT '일반건의',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT DEFAULT '접수됨',
            admin_reply TEXT DEFAULT '',
            replied_by TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT DEFAULT ''
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS site_chat_messages (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            nickname TEXT NOT NULL,
            grade TEXT DEFAULT '-',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS alba_jobs (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            reward_points INTEGER NOT NULL DEFAULT 10,
            status TEXT DEFAULT '모집중',
            booked_by TEXT DEFAULT '',
            booked_at TEXT DEFAULT '',
            proof_url TEXT DEFAULT '',
            proof_image TEXT DEFAULT '',
            proof_memo TEXT DEFAULT '',
            submitted_at TEXT DEFAULT '',
            reviewed_by TEXT DEFAULT '',
            reviewed_at TEXT DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

    else:
        cur.execute("CREATE TABLE IF NOT EXISTS admins (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL)")
        cur.execute("SELECT COUNT(*) FROM admins")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO admins (username, password_hash) VALUES (?, ?)", ("admin", hash_pw("1234")))

        cur.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname TEXT UNIQUE NOT NULL,
            role TEXT DEFAULT '부방',
            grade TEXT DEFAULT 'M1',
            points INTEGER DEFAULT 0,
            vacation_days INTEGER DEFAULT 0,
            status TEXT DEFAULT '활동중',
            joined_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nickname TEXT UNIQUE NOT NULL,
            approval_status TEXT DEFAULT '대기중',
            created_at TEXT NOT NULL,
            approved_at TEXT DEFAULT ''
        )
        """)

        now_str = now_kst().strftime("%Y-%m-%d %H:%M:%S")
        if not cur.execute("SELECT id FROM users WHERE username = 'admin'").fetchone():
            cur.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, nickname, approval_status, created_at, approved_at) VALUES (?, ?, ?, '승인완료', ?, ?)",
                ("admin", hash_pw("1234"), "총괄관리자", now_str, now_str),
            )
        if not cur.execute("SELECT id FROM members WHERE nickname = '총괄관리자'").fetchone():
            cur.execute(
                "INSERT OR IGNORE INTO members (nickname, role, grade, points, vacation_days, status, joined_at) VALUES ('총괄관리자', '총괄', 'MP', 0, 0, '활동중', ?)",
                (now_str,),
            )

        cur.execute("CREATE TABLE IF NOT EXISTS system_options (opt_key TEXT PRIMARY KEY, opt_value TEXT NOT NULL)")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS vacation_rule_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            days INTEGER NOT NULL DEFAULT 1,
            monthly_limit INTEGER,
            is_discretionary INTEGER DEFAULT 0,
            guide TEXT DEFAULT ''
        )
        """)
        if cur.execute("SELECT COUNT(*) FROM vacation_rule_items").fetchone()[0] == 0:
            cur.executemany(
                "INSERT INTO vacation_rule_items (name, days, monthly_limit, is_discretionary, guide) VALUES (?,?,?,?,?)",
                DEFAULT_VACATION_RULES,
            )

        cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT NOT NULL,
            username TEXT NOT NULL,
            nickname TEXT NOT NULL,
            grade TEXT DEFAULT '-',
            action TEXT NOT NULL,
            detail TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER,
            season TEXT,
            week TEXT,
            log_type TEXT,
            category TEXT,
            minutes INTEGER DEFAULT 0,
            points_delta INTEGER NOT NULL,
            vacation_delta INTEGER DEFAULT 0,
            memo TEXT,
            created_at TEXT,
            FOREIGN KEY(member_id) REFERENCES members(id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL,
            price INTEGER NOT NULL,
            description TEXT,
            stock INTEGER DEFAULT 99
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS shop_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER,
            item_name TEXT,
            price INTEGER,
            status TEXT DEFAULT '대기중',
            created_at TEXT,
            FOREIGN KEY(member_id) REFERENCES members(id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS vacation_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER,
            category TEXT NOT NULL,
            requested_days INTEGER NOT NULL,
            proof_memo TEXT,
            proof_image TEXT,
            status TEXT DEFAULT '대기중',
            request_month TEXT,
            created_at TEXT,
            FOREIGN KEY(member_id) REFERENCES members(id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS mod_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_name TEXT NOT NULL,
            action_date TEXT NOT NULL,
            action_time TEXT NOT NULL,
            action_dt TEXT NOT NULL,
            cutoff_date TEXT NOT NULL,
            room TEXT NOT NULL,
            action_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            staff_name TEXT NOT NULL,
            is_valid_score INTEGER DEFAULT 1,
            review_status TEXT DEFAULT '정상유지',
            note TEXT DEFAULT '',
            warning_count INTEGER DEFAULT 0,
            action_detail TEXT DEFAULT '',
            evidence_image TEXT DEFAULT '',
            timer_end_dt TEXT DEFAULT '',
            timer_notified INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """)
        cur.execute("PRAGMA table_info(mod_records)")
        mod_cols = [c["name"] for c in cur.fetchall()]
        if "evidence_image" not in mod_cols:
            cur.execute("ALTER TABLE mod_records ADD COLUMN evidence_image TEXT DEFAULT ''")
        if "warning_count" not in mod_cols:
            cur.execute("ALTER TABLE mod_records ADD COLUMN warning_count INTEGER DEFAULT 0")
        if "action_detail" not in mod_cols:
            cur.execute("ALTER TABLE mod_records ADD COLUMN action_detail TEXT DEFAULT ''")
        if "timer_end_dt" not in mod_cols:
            cur.execute("ALTER TABLE mod_records ADD COLUMN timer_end_dt TEXT DEFAULT ''")
        if "timer_notified" not in mod_cols:
            cur.execute("ALTER TABLE mod_records ADD COLUMN timer_notified INTEGER DEFAULT 0")
        cur.execute("CREATE TABLE IF NOT EXISTS mod_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS mod_score_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_cutoff TEXT NOT NULL,
            staff_name TEXT NOT NULL,
            member_id INTEGER,
            valid_count INTEGER NOT NULL,
            score_delta INTEGER NOT NULL,
            status TEXT NOT NULL,
            message TEXT DEFAULT '',
            synced_at TEXT NOT NULL,
            UNIQUE(week_cutoff, staff_name)
        )
        """)
        for k, v in {"cutoff_weekday": "2", "cutoff_hour": "22", "cutoff_minute": "0"}.items():
            cur.execute("INSERT OR IGNORE INTO mod_settings (key, value) VALUES (?, ?)", (k, v))

        cur.execute("""
        CREATE TABLE IF NOT EXISTS voice_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_name TEXT NOT NULL,
            room TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            end_time TEXT NOT NULL,
            end_dt TEXT NOT NULL,
            cutoff_date TEXT NOT NULL,
            duration_str TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL,
            is_valid_score INTEGER DEFAULT 1,
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """)
        cur.execute("CREATE TABLE IF NOT EXISTS voice_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS voice_score_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_cutoff TEXT NOT NULL,
            staff_name TEXT NOT NULL,
            member_id INTEGER,
            total_seconds INTEGER NOT NULL,
            score_delta INTEGER NOT NULL,
            status TEXT NOT NULL,
            message TEXT DEFAULT '',
            synced_at TEXT NOT NULL,
            UNIQUE(week_cutoff, staff_name)
        )
        """)
        for k, v in {"cutoff_weekday": "2", "cutoff_hour": "22", "cutoff_minute": "0"}.items():
            cur.execute("INSERT OR IGNORE INTO voice_settings (key, value) VALUES (?, ?)", (k, v))

        cur.execute("""
        CREATE TABLE IF NOT EXISTS suggestion_box (
            id SERIAL PRIMARY KEY,
            author_username TEXT NOT NULL,
            author_nickname TEXT NOT NULL,
            author_grade TEXT DEFAULT '-',
            category TEXT DEFAULT '일반건의',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT DEFAULT '접수됨',
            admin_reply TEXT DEFAULT '',
            replied_by TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT DEFAULT ''
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS site_chat_messages (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            nickname TEXT NOT NULL,
            grade TEXT DEFAULT '-',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS alba_jobs (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            reward_points INTEGER NOT NULL DEFAULT 10,
            status TEXT DEFAULT '모집중',
            booked_by TEXT DEFAULT '',
            booked_at TEXT DEFAULT '',
            proof_url TEXT DEFAULT '',
            proof_image TEXT DEFAULT '',
            proof_memo TEXT DEFAULT '',
            submitted_at TEXT DEFAULT '',
            reviewed_by TEXT DEFAULT '',
            reviewed_at TEXT DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

    conn.commit()
    conn.close()


try:
    init_db()
except Exception as _init_err:
    st.session_state["db_status_msg"] = f"DB 초기화 경고: {_init_err}"


# ==========================================
# 3. 동적 옵션 & 기준표 조회/저장 함수
# ==========================================
def get_opt_str(key: str, default: str) -> str:
    conn = get_conn()
    row = conn.execute("SELECT opt_value FROM system_options WHERE opt_key = ?", (key,)).fetchone()
    conn.close()
    return row["opt_value"] if (row and row["opt_value"].strip()) else default


def set_opt_str(key: str, val: str):
    conn = get_conn()
    if is_postgres_configured():
        conn.execute(
            "INSERT INTO system_options (opt_key, opt_value) VALUES (%s, %s) ON CONFLICT(opt_key) DO UPDATE SET opt_value=EXCLUDED.opt_value",
            (key, str(val)),
        )
    else:
        conn.execute(
            "INSERT INTO system_options (opt_key, opt_value) VALUES (?, ?) ON CONFLICT(opt_key) DO UPDATE SET opt_value=excluded.opt_value",
            (key, str(val)),
        )
    conn.commit()
    conn.close()


def get_opt_int(key: str, default: int) -> int:
    try:
        return int(get_opt_str(key, str(default)))
    except Exception:
        return default


def get_option_list(opt_key: str, default_list: list[str]) -> list[str]:
    conn = get_conn()
    row = conn.execute("SELECT opt_value FROM system_options WHERE opt_key = ?", (opt_key,)).fetchone()
    conn.close()
    if not row or not row["opt_value"].strip():
        return default_list
    items = [x.strip() for x in row["opt_value"].splitlines() if x.strip()]
    return items if items else default_list


def save_option_list(opt_key: str, items: list[str]):
    cleaned = "\n".join([x.strip() for x in items if x.strip()])
    set_opt_str(opt_key, cleaned)


def get_grade_thresholds() -> list[tuple[int, str]]:
    m4 = get_opt_int("grade_m4", 300)
    m3 = get_opt_int("grade_m3", 200)
    m2 = get_opt_int("grade_m2", 100)
    return [(m4, "M4"), (m3, "M3"), (m2, "M2"), (0, "M1")]


def calculate_grade(points: int, current_grade: str) -> str:
    if current_grade in ["MP", "MS"]:
        return current_grade
    for threshold, grade in reversed(get_grade_thresholds()):
        if points >= threshold:
            return grade
    return "M1"


def is_manager_grade(user: dict | None) -> bool:
    if not user:
        return False
    return str(user.get("grade", "")).upper() in MANAGER_GRADES or str(user.get("username", "")) == "admin"


def get_vacation_rules() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM vacation_rule_items ORDER BY id ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ==========================================
# 4. 인증, 회원, 활동로그, 휴가, 상점 CRUD
# ==========================================
def log_activity(app_name: str, username: str, nickname: str, grade: str, action: str, detail: str = ""):
    conn = get_conn()
    now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO activity_logs (app_name, username, nickname, grade, action, detail, created_at) VALUES (?,?,?,?,?,?,?)",
        (app_name, username, nickname, grade, action, detail, now),
    )
    conn.commit()
    conn.close()


def register_user(username: str, password: str, nickname: str) -> tuple[bool, str]:
    uname, nick = username.strip(), nickname.strip()
    if not uname or not password or not nick:
        return False, "아이디, 비밀번호, 닉네임을 모두 입력해 주세요."
    if len(password) < 4:
        return False, "비밀번호는 4자 이상으로 설정해 주세요."
    conn = get_conn()
    now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, nickname, approval_status, created_at) VALUES (?,?,?,'대기중',?)",
            (uname, hash_pw(password), nick, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        return False, "이미 사용 중인 아이디 또는 닉네임입니다."
    conn.close()
    log_activity("auth", uname, nick, "-", "회원가입신청", "관리자 승인 대기")
    return True, "회원가입 신청이 완료되었습니다! 관리자(MS/MP) 승인 후 로그인할 수 있습니다."


def authenticate_user(username: str, password: str) -> tuple[bool, str, dict | None]:
    uname = username.strip()
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (uname,)).fetchone()
    if not row or row["password_hash"] != hash_pw(password):
        conn.close()
        return False, "아이디 또는 비밀번호가 올바르지 않습니다.", None
    if row["approval_status"] == "대기중":
        conn.close()
        return False, "⏳ 관리자 승인 대기 중입니다.", None
    if row["approval_status"] == "거절됨":
        conn.close()
        return False, "❌ 가입이 거절된 계정입니다.", None
    m = conn.execute("SELECT * FROM members WHERE nickname = ?", (row["nickname"],)).fetchone()
    conn.close()
    if m and m["status"] == "해임":
        return False, "해임 처리된 계정입니다.", None
    return True, "로그인 성공", {
        "id": row["id"],
        "username": row["username"],
        "nickname": row["nickname"],
        "grade": m["grade"] if m else "M1",
        "role": m["role"] if m else "부방",
        "status": m["status"] if m else "활동중",
    }


def refresh_user_info(username: str) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not row or row["approval_status"] != "승인완료":
        conn.close()
        return None
    m = conn.execute("SELECT * FROM members WHERE nickname = ?", (row["nickname"],)).fetchone()
    conn.close()
    if m and m["status"] == "해임":
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "nickname": row["nickname"],
        "grade": m["grade"] if m else "M1",
        "role": m["role"] if m else "부방",
        "status": m["status"] if m else "활동중",
    }


def render_auth_gate(site_title: str) -> dict:
    if "auth_user" not in st.session_state:
        st.session_state.auth_user = None
    if st.session_state.auth_user:
        latest = refresh_user_info(st.session_state.auth_user["username"])
        st.session_state.auth_user = latest

    if not st.session_state.auth_user:
        st.title(f"🔒 {site_title}")
        t_in, t_up = st.tabs(["🔑 로그인", "📝 회원가입"])
        with t_in:
            with st.form("login_form"):
                un = st.text_input("아이디")
                pw = st.text_input("비밀번호", type="password")
                if st.form_submit_button("로그인", type="primary", use_container_width=True):
                    ok, msg, uinfo = authenticate_user(un, pw)
                    if ok and uinfo:
                        st.session_state.auth_user = uinfo
                        st.session_state["visited_unified"] = True
                        log_activity("unified_app", uinfo["username"], uinfo["nickname"], uinfo["grade"], "로그인", "사이트 접속")
                        st.rerun()
                    else:
                        st.error(msg)
        with t_up:
            st.caption("가입 신청 후 관리자(MS/MP)가 승인해야 로그인할 수 있습니다.")
            with st.form("signup_form"):
                nu = st.text_input("사용할 아이디 *")
                nn = st.text_input("활동 닉네임 * (기존 부방 닉네임과 동일하게 입력)")
                np1 = st.text_input("비밀번호 (4자 이상) *", type="password")
                np2 = st.text_input("비밀번호 확인 *", type="password")
                if st.form_submit_button("가입 신청하기", use_container_width=True):
                    if np1 != np2:
                        st.error("비밀번호가 서로 일치하지 않습니다.")
                    else:
                        ok, msg = register_user(nu, np1, nn)
                        st.success(msg) if ok else st.error(msg)
        st.stop()

    user = st.session_state.auth_user
    if not st.session_state.get("visited_unified"):
        st.session_state["visited_unified"] = True
        log_activity("unified_app", user["username"], user["nickname"], user["grade"], "앱접속", "사이트 열람")

    with st.sidebar:
        st.success(f"👤 **[{user['grade']}] {user['nickname']}**")
        if st.button("🚪 로그아웃", use_container_width=True):
            log_activity("unified_app", user["username"], user["nickname"], user["grade"], "로그아웃", "")
            st.session_state.auth_user = None
            st.session_state["visited_unified"] = False
            st.rerun()
    return user


def get_members(only_active=False):
    conn = get_conn()
    q = "SELECT * FROM members" + (" WHERE status = '활동중'" if only_active else "") + " ORDER BY points DESC, nickname ASC"
    rows = conn.execute(q).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def record_performance(member_id: int, season: str, week: str, category: str, points: int, vacation_days: int, memo: str = ""):
    conn = get_conn()
    now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    m = conn.execute("SELECT points, grade FROM members WHERE id = ?", (member_id,)).fetchone()
    new_points = max(0, m["points"] + points)
    old_grade = m["grade"] or "M1"
    new_grade = old_grade if old_grade in ["MP", "MS"] else calculate_grade(new_points, old_grade)
    conn.execute("UPDATE members SET points = ?, grade = ?, vacation_days = vacation_days + ? WHERE id = ?", (new_points, new_grade, vacation_days, member_id))
    conn.execute(
        "INSERT INTO logs (member_id, season, week, log_type, category, minutes, points_delta, vacation_delta, memo, created_at) VALUES (?,?,?,'실적지급',?,0,?,?,?,?)",
        (member_id, season, week, category, points, vacation_days, memo, now),
    )
    conn.commit()
    conn.close()
    return f" 🎉 등급 변동: [{old_grade}] ➔ [{new_grade}]!" if new_grade != old_grade else ""


def get_logs(limit=100):
    conn = get_conn()
    rows = conn.execute("SELECT l.*, m.nickname, m.grade FROM logs l JOIN members m ON l.member_id = m.id ORDER BY l.id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tbl_settings(table_name: str) -> dict:
    conn = get_conn()
    rows = conn.execute(f"SELECT key, value FROM {table_name}").fetchall()
    conn.close()
    s = {r["key"]: r["value"] for r in rows}
    return {"cutoff_weekday": int(s.get("cutoff_weekday", 2)), "cutoff_hour": int(s.get("cutoff_hour", 22)), "cutoff_minute": int(s.get("cutoff_minute", 0))}


def save_tbl_setting(table_name: str, key: str, value: str):
    conn = get_conn()
    if is_postgres_configured():
        conn.execute(f"INSERT INTO {table_name} (key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", (key, str(value)))
    else:
        conn.execute(f"INSERT INTO {table_name} (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()
    conn.close()


def get_cutoff_date_str(dt: datetime, cw: int, ch: int, cm: int) -> str:
    wd = dt.weekday()
    ct = time(ch, cm, 0)
    if wd == cw:
        td = dt.date() if dt.time() <= ct else dt.date() + timedelta(days=7)
    else:
        td = dt.date() + timedelta(days=(cw - wd) % 7)
    return td.strftime("%Y-%m-%d")


def recalculate_cutoffs(record_table: str, dt_col: str, cw: int, ch: int, cm: int):
    conn = get_conn()
    for r in conn.execute(f"SELECT id, {dt_col} FROM {record_table}").fetchall():
        nc = get_cutoff_date_str(datetime.strptime(r[dt_col], "%Y-%m-%d %H:%M"), cw, ch, cm)
        conn.execute(f"UPDATE {record_table} SET cutoff_date = ? WHERE id = ?", (nc, r["id"]))
    conn.commit()
    conn.close()


def parse_date_str(raw_date: str) -> date:
    digits = re.sub(r"[^0-9]", "", raw_date.strip())
    if len(digits) == 6:
        return date(2000 + int(digits[:2]), int(digits[2:4]), int(digits[4:6]))
    if len(digits) == 8:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    raise ValueError(f"날짜 형식 오류: {raw_date}")


def parse_time_str(raw_time: str) -> time:
    m = re.match(r"^\s*(\d{1,2})\s*:\s*(\d{2})\s*$", raw_time)
    if not m:
        raise ValueError(f"시간 형식 오류: {raw_time}")
    return time(int(m.group(1)), int(m.group(2)))


def encode_uploaded_image(uploaded_file) -> str:
    if uploaded_file is None:
        return ""
    return "b64:" + base64.b64encode(uploaded_file.getvalue()).decode("utf-8")


def render_saved_image(val: str, caption: str = ""):
    if not val or not str(val).strip():
        return
    s = str(val).strip()
    if s.startswith("b64:"):
        st.image(base64.b64decode(s[4:]), caption=caption, width=260)
    else:
        st.image(s, caption=caption, width=260)


# ==========================================
# 5. ntfy 발송/수신 & 결산 시간 알림 헬퍼
# ==========================================
def send_phone_push(title: str, message: str) -> tuple[bool, str]:
    ntfy_topic = get_opt_str("naepul_ntfy_topic", "").strip()
    webhook_url = get_opt_str("naepul_webhook_url", "").strip()
    sent_any = False
    errs = []

    if ntfy_topic:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{ntfy_topic}",
                data=message.encode("utf-8"),
                headers={"Title": title.encode("utf-8"), "Priority": "high", "Tags": "bell"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                sent_any = True
        except Exception as e:
            errs.append(f"ntfy 오류: {e}")

    if webhook_url:
        try:
            payload = json.dumps({"content": f"**[{title}]**\n{message}"}).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                sent_any = True
        except Exception as e:
            errs.append(f"Webhook 오류: {e}")

    if not ntfy_topic and not webhook_url:
        return False, "ntfy 토픽 또는 Webhook URL이 설정되지 않았습니다."
    if sent_any:
        return True, "알림 전송 성공"
    return False, " / ".join(errs)


def fetch_ntfy_messages(limit: int = 15) -> tuple[list[dict], str]:
    ntfy_topic = get_opt_str("naepul_ntfy_topic", "").strip()
    if not ntfy_topic:
        return [], "ntfy 토픽이 설정되어 있지 않습니다."
    url = f"https://ntfy.sh/{ntfy_topic}/json?poll=1&since=24h"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StreamlitManager/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw_lines = resp.read().decode("utf-8", errors="replace").splitlines()
        msgs = []
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("event") == "message":
                    ts = obj.get("time", 0)
                    dt_str = datetime.fromtimestamp(ts, tz=KST).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
                    msgs.append({
                        "id": obj.get("id", ""),
                        "time": dt_str,
                        "title": obj.get("title") or "ntfy 메시지",
                        "message": obj.get("message") or "",
                    })
            except Exception:
                continue
        msgs.reverse()
        return msgs[:limit], "수신 완료"
    except Exception as e:
        return [], f"ntfy 수신 실패: {e}"


def check_and_send_naepul_timers():
    if get_opt_str("naepul_timer_enabled", "0") != "1":
        return []
    now_str = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    due_rows = conn.execute(
        "SELECT id, target_name, room, action_type, action_detail, reason, timer_end_dt FROM mod_records WHERE timer_end_dt != '' AND timer_notified = 0 AND timer_end_dt <= ?",
        (now_str,),
    ).fetchall()
    conn.close()

    notified_list = []
    for r in due_rows:
        msg = f"대상: {r['target_name']} | 방: {r['room']}\n조치: {r['action_type']} ({r['action_detail']}) | 사유: {r['reason']}\n만료시각: {r['timer_end_dt']}"
        ok, _ = send_phone_push("⏰ 내풀 타이머 만료 알림", msg)
        if ok:
            conn = get_conn()
            conn.execute("UPDATE mod_records SET timer_notified = 1 WHERE id = ?", (r["id"],))
            conn.commit()
            conn.close()
            notified_list.append(r["target_name"])
    return notified_list


def check_and_send_settlement_alert():
    if get_opt_str("settlement_alert_enabled", "0") != "1":
        return None
    s_wd = get_opt_int("settlement_weekday", 4)
    s_h = get_opt_int("settlement_hour", 21)
    s_m = get_opt_int("settlement_minute", 0)
    lead_min = max(0, get_opt_int("settlement_lead_minutes", 0))

    now_dt = now_kst()
    target_dt = datetime.combine(now_dt.date(), time(s_h, s_m, 0)) - timedelta(minutes=lead_min)
    alert_key = f"{now_dt.strftime('%Y-%m-%d')}_{s_wd}_{s_h:02d}:{s_m:02d}_lead{lead_min}"
    last_key = get_opt_str("settlement_last_fired_key", "")

    if now_dt.weekday() == s_wd and now_dt >= target_dt and last_key != alert_key:
        wd_txt = WEEKDAY_NAMES[s_wd]
        msg = (
            f"설정된 결산 시각: 매주 {wd_txt} {s_h:02d}:{s_m:02d}\n"
            f"현재 시각(KST): {now_dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"사전 알림: {lead_min}분 전 기준"
        )
        ok, _ = send_phone_push("📊 결산 시간 알림", msg)
        if ok:
            set_opt_str("settlement_last_fired_key", alert_key)
            return f"{wd_txt} {s_h:02d}:{s_m:02d} 결산 알림"
    return None


# ==========================================
# 6. 메인 실행부 & 좌측 트리(ㄴ) 사이드바 메뉴
# ==========================================
if "--watch-timers" in sys.argv:
    print("⏰ 내풀 타이머 & 결산 알림 백그라운드 감시 시작 (30초 간격)...")
    while True:
        fired = check_and_send_naepul_timers()
        if fired:
            print(f"[{now_kst().strftime('%H:%M:%S')}] 내풀 알림 발송 완료: {fired}")
        s_fired = check_and_send_settlement_alert()
        if s_fired:
            print(f"[{now_kst().strftime('%H:%M:%S')}] 결산 알림 발송 완료: {s_fired}")
        py_time.sleep(30)

_auto_fired = check_and_send_naepul_timers()
if _auto_fired:
    st.toast(f"⏰ 내풀 타이머 만료 알림 발송: {', '.join(_auto_fired)}")

_settle_fired = check_and_send_settlement_alert()
if _settle_fired:
    st.toast(f"📊 {_settle_fired} 발송 완료")

SITE_TITLE = get_opt_str("site_title", "오픈채팅 부방 통합 관리")
current_user = render_auth_gate(SITE_TITLE)
is_mgr = is_manager_grade(current_user)

MENU_TREE = [
    ("🏆 순위 · 휴가 · 상점 · 알바", [
        ("rank", "순위 / 등급"),
        ("my_logs", "내 지급내역"),
        ("vacation", "정기휴가 신청"),
        ("shop", "포인트 상점"),
        ("alba", "알바 (예약 · 인증 · 승인)"),
    ]),
    ("💬 소통 · 건의 · ntfy", [
        ("site_chat", "사이트 채팅"),
        ("suggestions", "건의함"),
        ("ntfy_box", "ntfy 알림 수발신 & 설정"),
    ]),
    ("🛡️ 경고/강퇴 · 내풀 관리", [
        ("mod_input", "기록 입력 (개별/카톡)"),
        ("mod_score", "주간 점수 & 반영"),
        ("mod_search", "검색 / 재심 / 증거"),
        ("mod_timer", "내풀 타이머 현황"),
        ("mod_audit", "중복 검수 / CSV백업"),
    ]),
    ("🎙️ 보이스룸 관리", [
        ("vr_input", "보룸 기록 입력"),
        ("vr_score", "주간 시간 & 반영"),
        ("vr_history", "전체 이력 & CSV"),
    ]),
]

if is_mgr:
    MENU_TREE.append(
        ("📊 관리자 (MS/MP)", [
            ("adm_users", "가입 승인 & 부방 관리"),
            ("adm_opts", "전체 항목 · 점수 · 사이트 설정"),
            ("adm_vac", "정기휴가 편집 & 승인"),
            ("adm_shop", "상점 편집 & 주문 관리"),
            ("adm_logs", "전체 앱 사용·접속 기록"),
            ("adm_perf", "실적 수동지급 & 취소"),
            ("adm_settle", "월말 결산 (리셋/이월)"),
            ("adm_db", "DB 백업 및 복원"),
        ])
    )

if "nav_key" not in st.session_state:
    st.session_state.nav_key = "rank"

with st.sidebar:
    db_msg = st.session_state.get("db_status_msg", "DB 상태 확인 중")
    if is_postgres_configured() and "실패" not in db_msg and "경고" not in db_msg:
        st.success(f"🟢 {db_msg}")
    elif is_postgres_configured():
        st.error(f"🔴 {db_msg}")
    else:
        st.warning(f"🟡 {db_msg}")

    for group_title, sub_items in MENU_TREE:
        st.markdown(f'<div class="nav-group-title">{group_title}</div>', unsafe_allow_html=True)
        for key, label in sub_items:
            is_selected = st.session_state.nav_key == key
            if st.button(
                f"ㄴ {label}",
                key=f"nav_btn_{key}",
                type="primary" if is_selected else "secondary",
                use_container_width=True,
            ):
                st.session_state.nav_key = key
                st.rerun()

nav = st.session_state.nav_key


# ==========================================
# SECTION 1: 순위 · 휴가 · 상점 · 알바
# ==========================================
if nav in ("rank", "my_logs", "vacation", "shop", "alba"):
    st.title(f"🏆 {SITE_TITLE}")

    if nav == "rank":
        st.subheader("📊 순위 / 등급")
        grades = get_grade_thresholds()
        carry_pct = get_opt_int("monthly_carryover_pct", 60)
        grade_info = " | ".join([f"**{g}**: {t}P↑" for t, g in grades])
        st.info(f"🎖️ **승급 기준:** {grade_info} *(MP/MS 임명 전용)*\n\n💡 **월말 결산:** 포인트 리셋 & **{carry_pct}%** 휴가 이월")
        members = get_members()
        if members:
            st.dataframe(
                [{
                    "순위": f"{idx}위",
                    "등급 & 닉네임": f"[{m.get('grade','M1')}] {m['nickname']}",
                    "직책": m["role"],
                    "포인트": f"{m['points']}P",
                    "잔여휴가": f"{m['vacation_days']}일",
                    "상태": m["status"],
                } for idx, m in enumerate(members, 1)],
                use_container_width=True,
                hide_index=True,
            )

    elif nav == "my_logs":
        st.subheader("📋 내 지급내역")
        members = get_members()
        if members:
            nicks = [m["nickname"] for m in members]
            def_i = nicks.index(current_user["nickname"]) if current_user["nickname"] in nicks else 0
            sn = st.selectbox("조회할 부방", nicks, index=def_i)
            tgt = next((m for m in members if m["nickname"] == sn), None)
            if tgt:
                c1, c2 = st.columns(2)
                c1.metric("등급 / 직책", f"[{tgt.get('grade','M1')}] {tgt['role']}")
                c2.metric("포인트 / 휴가", f"{tgt['points']}P / {tgt['vacation_days']}일")
                ulogs = [l for l in get_logs(100) if l["nickname"] == sn]
                if ulogs:
                    st.dataframe(
                        [{"시간": l["created_at"], "시즌": l.get("season", "-"), "주차": l.get("week", "-"), "항목": l["category"], "포인트": f"{l['points_delta']:+d}P", "휴가": f"{l['vacation_delta']:+d}일", "메모": l["memo"]} for l in ulogs],
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.info("기록된 지급 내역이 없습니다.")

    elif nav == "vacation":
        st.subheader("🏖️ 정기휴가 신청")
        rules = get_vacation_rules()
        with st.expander("📋 정기휴가 기준표 보기"):
            for r in rules:
                lim_txt = f"월 {r['monthly_limit']}회" if r["monthly_limit"] else "제한없음"
                day_txt = "관리자 협의" if r["is_discretionary"] else f"{r['days']}일"
                st.write(f"• **{r['name']}** — `{day_txt}` ({lim_txt})")

        members = get_members(only_active=True)
        if members and rules:
            m_opts = {f"[{m.get('grade','M1')}] {m['nickname']}": m["id"] for m in members}
            def_l = next((k for k in m_opts if current_user["nickname"] in k), list(m_opts.keys())[0])
            sel_m_id = m_opts[st.selectbox("신청자", list(m_opts.keys()), index=list(m_opts.keys()).index(def_l))]

            rule_map = {r["name"]: r for r in rules}
            sel_cat = st.selectbox("휴가 항목", list(rule_map.keys()))
            r_obj = rule_map[sel_cat]
            if r_obj["guide"]:
                st.info(r_obj["guide"])

            req_days = st.number_input("협의 요청 일수", 1, 30, max(1, r_obj["days"])) if r_obj["is_discretionary"] else r_obj["days"]
            if not r_obj["is_discretionary"]:
                st.write(f"📌 **적용 일수:** `{req_days}일`")

            pm = st.text_input("사유 및 메모")
            vac_file = st.file_uploader("📸 인증 사진 업로드 (선택)", type=["png", "jpg", "jpeg", "webp"], key="vac_img_up")
            if vac_file is not None:
                st.image(vac_file, caption="업로드 미리보기", width=220)
            pi = encode_uploaded_image(vac_file)
            if st.button("🏖️ 정기휴가 신청하기", type="primary", use_container_width=True):
                conn = get_conn()
                now_dt_kst = now_kst()
                cm = now_dt_kst.strftime("%Y-%m")
                now = now_dt_kst.strftime("%Y-%m-%d %H:%M:%S")
                lim = r_obj["monthly_limit"]
                if lim is not None:
                    cnt_row = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM vacation_requests WHERE member_id=? AND category=? AND request_month=? AND status!='거절됨'",
                        (sel_m_id, sel_cat, cm),
                    ).fetchone()
                    cnt = cnt_row["cnt"] if isinstance(cnt_row, dict) else cnt_row[0]
                    if cnt >= lim:
                        conn.close()
                        st.error(f"이번 달 최대 신청 횟수({lim}회)를 초과했습니다!")
                        st.stop()
                conn.execute(
                    "INSERT INTO vacation_requests (member_id, category, requested_days, proof_memo, proof_image, status, request_month, created_at) VALUES (?,?,?,?,?,'대기중',?,?)",
                    (sel_m_id, sel_cat, req_days, pm, pi, cm, now),
                )
                conn.commit()
                conn.close()
                log_activity("kakao_user_app", current_user["username"], current_user["nickname"], current_user["grade"], "정기휴가신청", f"{sel_cat} ({req_days}일)")
                st.success(f"'{sel_cat}' ({req_days}일) 신청 완료!")

    elif nav == "shop":
        st.subheader("🎁 포인트 상점")
        members = get_members(only_active=True)
        conn = get_conn()
        items = [dict(r) for r in conn.execute("SELECT * FROM shop_items ORDER BY price ASC").fetchall()]
        conn.close()
        if members and items:
            b_opts = {f"[{m.get('grade','M1')}] {m['nickname']} ({m['points']}P)": m["id"] for m in members}
            def_b = next((k for k in b_opts if current_user["nickname"] in k), list(b_opts.keys())[0])
            bid = b_opts[st.selectbox("구매자 선택", list(b_opts.keys()), index=list(b_opts.keys()).index(def_b), key="shop_u")]
            for item in items:
                with st.container(border=True):
                    st.markdown(f"**{item['item_name']}** — `{item['price']}P` (재고 {item['stock']}개)")
                    if item["description"]:
                        st.caption(item["description"])
                    if st.button("🎁 교환 신청", key=f"buy_{item['id']}", use_container_width=True):
                        conn = get_conn()
                        m = conn.execute("SELECT * FROM members WHERE id=?", (bid,)).fetchone()
                        it = conn.execute("SELECT * FROM shop_items WHERE id=?", (item["id"],)).fetchone()
                        if m["points"] < it["price"]:
                            conn.close()
                            st.error("포인트가 부족합니다!")
                        elif it["stock"] <= 0:
                            conn.close()
                            st.error("재고가 소진되었습니다.")
                        else:
                            now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                            npts = m["points"] - it["price"]
                            ng = m["grade"] if m["grade"] in ["MP", "MS"] else calculate_grade(npts, m["grade"])
                            conn.execute("UPDATE members SET points=?, grade=? WHERE id=?", (npts, ng, bid))
                            conn.execute("UPDATE shop_items SET stock=stock-1 WHERE id=?", (it["id"],))
                            conn.execute("INSERT INTO shop_orders (member_id, item_name, price, status, created_at) VALUES (?,?,?,'대기중',?)", (bid, it["item_name"], it["price"], now))
                            conn.execute("INSERT INTO logs (member_id, season, week, log_type, category, minutes, points_delta, vacation_delta, memo, created_at) VALUES (?,'시즌통합','상점주차','상점구매','포인트상점',0,?,0,?,?)", (bid, -it["price"], f"상품 교환: {it['item_name']}", now))
                            conn.commit()
                            conn.close()
                            log_activity("kakao_user_app", current_user["username"], current_user["nickname"], current_user["grade"], "상점교환", f"{it['item_name']} ({it['price']}P)")
                            st.success(f"'{it['item_name']}' 교환 완료!")
                            st.rerun()
        else:
            st.info("등록된 상점 상품이 없습니다.")

    elif nav == "alba":
        st.subheader("💼 알바 게시판 (예약 · 사진/링크 인증 · 승인 지급)")
        st.caption("관리자가 걸어둔 포인트를 확인하고 예약한 뒤, 사진 또는 링크를 올려 인증하면 관리자 승인 후 포인트가 지급됩니다.")

        if is_mgr:
            with st.expander("➕ 새 알바 공고 등록 (MS/MP)", expanded=False):
                with st.form("create_alba_form", clear_on_submit=True):
                    a_title = st.text_input("알바 제목 *", placeholder="예: 오픈채팅 공지 정리 / 주간 기록 검수")
                    a_pts = st.number_input("걸 포인트 (P)", min_value=1, max_value=10000, value=20, step=1)
                    a_desc = st.text_area("상세 설명 및 인증 기준", placeholder="예: 완료 후 결과 캡처 사진 또는 링크를 첨부해 주세요.")
                    if st.form_submit_button("📌 알바 등록", type="primary", use_container_width=True):
                        if not a_title.strip():
                            st.error("알바 제목을 입력해 주세요.")
                        else:
                            now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                            conn = get_conn()
                            conn.execute(
                                "INSERT INTO alba_jobs (title, description, reward_points, status, created_by, created_at) VALUES (?,?,?,'모집중',?,?)",
                                (a_title.strip(), a_desc.strip(), int(a_pts), current_user["nickname"], now),
                            )
                            conn.commit()
                            conn.close()
                            log_activity("alba", current_user["username"], current_user["nickname"], current_user["grade"], "알바등록", f"{a_title.strip()} ({int(a_pts)}P)")
                            send_phone_push("💼 새 알바 등록", f"[{a_title.strip()}] 보상: {int(a_pts)}P\n등록자: {current_user['nickname']}")
                            st.success("알바 공고가 등록되었습니다!")
                            st.rerun()

        conn = get_conn()
        jobs = [dict(r) for r in conn.execute("SELECT * FROM alba_jobs ORDER BY id DESC").fetchall()]
        conn.close()

        if not jobs:
            st.info("현재 등록된 알바가 없습니다.")
        else:
            for job in jobs:
                jid = job["id"]
                status = job["status"]
                booked_by = job.get("booked_by") or ""
                with st.container(border=True):
                    st.markdown(f"### [{status}] {job['title']} — **`{job['reward_points']}P`**")
                    if job.get("description"):
                        st.write(job["description"])
                    meta_parts = [f"등록자: {job['created_by']}", f"등록일: {job['created_at']}"]
                    if booked_by:
                        meta_parts.append(f"예약자: **{booked_by}** ({job.get('booked_at', '')})")
                    if job.get("reviewed_by"):
                        meta_parts.append(f"검토: {job['reviewed_by']} ({job.get('reviewed_at', '')})")
                    st.caption(" | ".join(meta_parts))

                    if job.get("proof_url"):
                        st.markdown(f"🔗 **인증 링크:** [{job['proof_url']}]({job['proof_url']})")
                    if job.get("proof_memo"):
                        st.write(f"📝 **인증 메모:** {job['proof_memo']}")
                    if job.get("proof_image"):
                        render_saved_image(job["proof_image"], caption=f"알바 #{jid} 인증 사진")

                    if status == "모집중":
                        if st.button("🙋 이 알바 예약하기", key=f"alba_book_{jid}", type="primary", use_container_width=True):
                            now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                            conn = get_conn()
                            conn.execute(
                                "UPDATE alba_jobs SET status='예약중', booked_by=?, booked_at=? WHERE id=? AND status='모집중'",
                                (current_user["nickname"], now, jid),
                            )
                            conn.commit()
                            conn.close()
                            log_activity("alba", current_user["username"], current_user["nickname"], current_user["grade"], "알바예약", f"#{jid} {job['title']}")
                            send_phone_push("🙋 알바 예약 완료", f"알바: {job['title']} ({job['reward_points']}P)\n예약자: {current_user['nickname']}")
                            st.success("예약되었습니다! 완료 후 사진이나 링크를 올려 인증해 주세요.")
                            st.rerun()

                    elif status == "예약중":
                        can_submit = (booked_by == current_user["nickname"]) or is_mgr
                        if can_submit:
                            with st.form(f"alba_proof_form_{jid}"):
                                p_url = st.text_input("🔗 인증 링크 (선택)", value=job.get("proof_url") or "", placeholder="https://...")
                                p_memo = st.text_input("📝 작업 메모 (선택)", value=job.get("proof_memo") or "")
                                p_file = st.file_uploader("📸 인증 사진 업로드 (선택)", type=["png", "jpg", "jpeg", "webp"], key=f"alba_file_{jid}")
                                if st.form_submit_button("📤 인증 제출하기", type="primary", use_container_width=True):
                                    img_val = encode_uploaded_image(p_file) if p_file is not None else (job.get("proof_image") or "")
                                    if not p_url.strip() and not img_val and not p_memo.strip():
                                        st.error("인증 사진, 링크, 메모 중 하나 이상을 입력해 주세요.")
                                    else:
                                        now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                                        conn = get_conn()
                                        conn.execute(
                                            "UPDATE alba_jobs SET status='인증제출', proof_url=?, proof_image=?, proof_memo=?, submitted_at=? WHERE id=?",
                                            (p_url.strip(), img_val, p_memo.strip(), now, jid),
                                        )
                                        conn.commit()
                                        conn.close()
                                        log_activity("alba", current_user["username"], current_user["nickname"], current_user["grade"], "알바인증제출", f"#{jid} {job['title']}")
                                        send_phone_push("📤 알바 인증 제출", f"알바: {job['title']} ({job['reward_points']}P)\n제출자: {booked_by or current_user['nickname']}")
                                        st.success("인증이 제출되었습니다! 관리자 승인을 기다려 주세요.")
                                        st.rerun()
                        if booked_by == current_user["nickname"] and not is_mgr:
                            if st.button("❌ 예약 취소 (다시 모집중으로)", key=f"alba_cancel_{jid}", use_container_width=True):
                                conn = get_conn()
                                conn.execute("UPDATE alba_jobs SET status='모집중', booked_by='', booked_at='' WHERE id=?", (jid,))
                                conn.commit()
                                conn.close()
                                st.rerun()

                    elif status == "인증제출" and is_mgr:
                        c1, c2 = st.columns(2)
                        if c1.button(f"✅ 승인 & {job['reward_points']}P 지급", key=f"alba_ok_{jid}", type="primary", use_container_width=True):
                            conn = get_conn()
                            now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                            mem = conn.execute("SELECT id FROM members WHERE nickname=?", (booked_by,)).fetchone()
                            conn.execute(
                                "UPDATE alba_jobs SET status='승인완료', reviewed_by=?, reviewed_at=? WHERE id=?",
                                (current_user["nickname"], now, jid),
                            )
                            conn.commit()
                            conn.close()
                            if mem:
                                td = today_kst()
                                record_performance(
                                    mem["id"],
                                    f"{td.year}-Q{(td.month-1)//3+1}",
                                    "알바지급",
                                    f"알바완료: {job['title']}",
                                    int(job["reward_points"]),
                                    0,
                                    f"알바 #{jid} 인증 승인 ({booked_by})",
                                )
                            log_activity("alba", current_user["username"], current_user["nickname"], current_user["grade"], "알바승인", f"#{jid} {booked_by} +{job['reward_points']}P")
                            send_phone_push("✅ 알바 승인 및 포인트 지급", f"대상: {booked_by}\n알바: {job['title']}\n지급: +{job['reward_points']}P")
                            st.success(f"{booked_by}님에게 {job['reward_points']}P 지급 완료!")
                            st.rerun()

                        if c2.button("↩️ 반려 (예약중으로 되돌리기)", key=f"alba_rej_{jid}", use_container_width=True):
                            now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                            conn = get_conn()
                            conn.execute(
                                "UPDATE alba_jobs SET status='예약중', reviewed_by=?, reviewed_at=? WHERE id=?",
                                (current_user["nickname"], now, jid),
                            )
                            conn.commit()
                            conn.close()
                            st.rerun()

                    if is_mgr:
                        if st.button("🗑️ 공고 삭제", key=f"alba_del_{jid}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("DELETE FROM alba_jobs WHERE id=?", (jid,))
                            conn.commit()
                            conn.close()
                            st.rerun()


# ==========================================
# SECTION 2: 소통 · 건의 · ntfy
# ==========================================
elif nav in ("site_chat", "suggestions", "ntfy_box"):
    st.title(f"💬 {SITE_TITLE}")

    if nav == "site_chat":
        st.subheader("💬 사이트 내 채팅")
        col_ref, _ = st.columns([1, 3])
        if col_ref.button("🔄 채팅 새로고침", use_container_width=True):
            st.rerun()

        conn = get_conn()
        msgs = [dict(r) for r in conn.execute("SELECT * FROM site_chat_messages ORDER BY id DESC LIMIT 80").fetchall()]
        conn.close()

        msgs.reverse()
        with st.container(border=True):
            if not msgs:
                st.info("아직 채팅 메시지가 없습니다. 첫 메시지를 남겨보세요!")
            else:
                for m in msgs:
                    st.markdown(
                        f'<div class="chat-bubble"><div class="chat-meta"><b>[{m.get("grade","-")}] {m["nickname"]}</b> · {m["created_at"]}</div><div>{m["message"]}</div></div>',
                        unsafe_allow_html=True,
                    )

        with st.form("site_chat_form", clear_on_submit=True):
            chat_txt = st.text_input("메시지 입력", placeholder="사이트 채팅방에 보낼 내용을 입력하세요...")
            if st.form_submit_button("📨 전송", type="primary", use_container_width=True):
                if chat_txt.strip():
                    now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                    conn = get_conn()
                    conn.execute(
                        "INSERT INTO site_chat_messages (username, nickname, grade, message, created_at) VALUES (?,?,?,?,?)",
                        (current_user["username"], current_user["nickname"], current_user["grade"], chat_txt.strip(), now),
                    )
                    conn.commit()
                    conn.close()
                    st.rerun()

        if is_mgr and msgs:
            with st.expander("🧹 관리자 채팅 관리"):
                if st.button("🗑️ 전체 채팅 내역 비우기", use_container_width=True):
                    conn = get_conn()
                    conn.execute("DELETE FROM site_chat_messages")
                    conn.commit()
                    conn.close()
                    st.rerun()

    elif nav == "suggestions":
        st.subheader("📬 건의함")
        with st.form("suggestion_form", clear_on_submit=True):
            s_cat = st.selectbox("건의 구분", ["기능건의", "규칙/점수건의", "불편/오류신고", "기타"])
            s_title = st.text_input("제목 *", placeholder="건의 제목을 입력하세요")
            s_content = st.text_area("상세 내용 *", height=130, placeholder="자세한 내용이나 개선 아이디어를 적어주세요")
            send_push_chk = st.checkbox("ntfy로 알림 보내기", value=True)
            if st.form_submit_button("📮 건의 제출하기", type="primary", use_container_width=True):
                if not s_title.strip() or not s_content.strip():
                    st.error("제목과 내용을 모두 입력해 주세요.")
                else:
                    now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                    conn = get_conn()
                    conn.execute(
                        "INSERT INTO suggestion_box (author_username, author_nickname, author_grade, category, title, content, status, created_at, updated_at) VALUES (?,?,?,?,?,?,'접수됨',?,?)",
                        (current_user["username"], current_user["nickname"], current_user["grade"], s_cat, s_title.strip(), s_content.strip(), now, now),
                    )
                    conn.commit()
                    conn.close()
                    log_activity("suggestions", current_user["username"], current_user["nickname"], current_user["grade"], "건의등록", f"[{s_cat}] {s_title.strip()}")
                    if send_push_chk:
                        send_phone_push("📬 새 건의 접수", f"[{s_cat}] {s_title.strip()}\n작성자: {current_user['nickname']}")
                    st.success("건의가 정상적으로 접수되었습니다!")
                    st.rerun()

        st.divider()
        st.markdown("### 📋 접수된 건의 목록")
        conn = get_conn()
        s_rows = [dict(r) for r in conn.execute("SELECT * FROM suggestion_box ORDER BY id DESC").fetchall()]
        conn.close()

        if not s_rows:
            st.info("등록된 건의가 없습니다.")
        else:
            for item in s_rows:
                with st.container(border=True):
                    st.markdown(f"**[{item['status']}] [{item['category']}] {item['title']}**")
                    st.caption(f"작성자: [{item.get('author_grade','-')}] {item['author_nickname']} | 작성일: {item['created_at']}")
                    st.write(item["content"])
                    if item.get("admin_reply"):
                        st.info(f"💬 **관리자 답변 ({item.get('replied_by') or '관리자'}):** {item['admin_reply']}")

                    if is_mgr:
                        st_list = ["접수됨", "검토중", "반영완료", "보류"]
                        cur_st = item["status"] if item["status"] in st_list else "접수됨"
                        new_st = st.selectbox("처리 상태", st_list, index=st_list.index(cur_st), key=f"sug_st_{item['id']}")
                        new_rep = st.text_input("관리자 답변", value=item.get("admin_reply") or "", key=f"sug_rep_{item['id']}")
                        b1, b2 = st.columns(2)
                        if b1.button("💾 상태/답변 저장", key=f"sug_sv_{item['id']}", use_container_width=True):
                            now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                            conn = get_conn()
                            conn.execute(
                                "UPDATE suggestion_box SET status=?, admin_reply=?, replied_by=?, updated_at=? WHERE id=?",
                                (new_st, new_rep.strip(), current_user["nickname"], now, item["id"]),
                            )
                            conn.commit()
                            conn.close()
                            st.rerun()
                        if b2.button("🗑️ 삭제", key=f"sug_del_{item['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("DELETE FROM suggestion_box WHERE id=?", (item["id"],))
                            conn.commit()
                            conn.close()
                            st.rerun()

    elif nav == "ntfy_box":
        st.subheader("🔔 ntfy 알림 보내기 · 받기 · 설정")
        topic_now = get_opt_str("naepul_ntfy_topic", "").strip()
        s_on = get_opt_str("settlement_alert_enabled", "0") == "1"
        s_wd = WEEKDAY_NAMES[get_opt_int("settlement_weekday", 4)]
        s_h = get_opt_int("settlement_hour", 21)
        s_m = get_opt_int("settlement_minute", 0)
        s_lead = get_opt_int("settlement_lead_minutes", 0)

        st.info(
            f"• **현재 ntfy 토픽:** `{topic_now or '미설정'}`\n"
            f"• **결산 시간 알림:** `{'사용 중' if s_on else '꺼짐'}` — 매주 **{s_wd} {s_h:02d}:{s_m:02d}** (사전 알림: `{s_lead}분 전`)"
        )

        t_send, t_recv, t_cfg = st.tabs(["📤 ntfy 알림 발송", "📥 ntfy 수신 메시지 확인", "⚙️ 알림 & 결산 시간 설정"])

        with t_send:
            with st.form("ntfy_send_form", clear_on_submit=True):
                p_title = st.text_input("알림 제목", value="📢 부방 공지/결산 알림")
                p_body = st.text_area("알림 내용 *", height=110, placeholder="보낼 알림 메시지를 입력하세요.")
                if st.form_submit_button("🚀 ntfy로 보내기", type="primary", use_container_width=True):
                    if not p_body.strip():
                        st.error("알림 내용을 입력해 주세요.")
                    else:
                        ok, msg = send_phone_push(p_title.strip() or "부방 알림", p_body.strip())
                        if ok:
                            log_activity("ntfy_box", current_user["username"], current_user["nickname"], current_user["grade"], "ntfy알림발송", p_title.strip())
                        st.success(msg) if ok else st.error(msg)

        with t_recv:
            if st.button("🔄 최신 ntfy 메시지 불러오기", type="primary", use_container_width=True):
                st.session_state["ntfy_poll_trigger"] = True
            msgs, status_msg = fetch_ntfy_messages(limit=20)
            st.caption(f"상태: {status_msg}")
            if not msgs:
                st.info("표시할 수신 메시지가 없습니다. (ntfy 앱에서 같은 토픽으로 메시지를 보내거나 토픽 설정을 확인해 주세요.)")
            else:
                for m in msgs:
                    with st.container(border=True):
                        st.markdown(f"**{m['title']}**")
                        st.caption(f"수신시각(KST): {m['time']}")
                        st.write(m["message"])

        with t_cfg:
            st.caption("일반 사용자도 ntfy 토픽과 결산 시간 알림을 직접 설정하고 테스트할 수 있습니다.")
            with st.form("user_ntfy_settlement_form"):
                ntfy_in = st.text_input("📱 ntfy.sh 토픽 이름 (발송·수신 공용 토픽)", value=get_opt_str("naepul_ntfy_topic", ""), placeholder="예: kakao-naepul-1234")
                wh_in = st.text_input("🔗 Discord / Webhook URL (선택)", value=get_opt_str("naepul_webhook_url", ""), placeholder="https://discord.com/api/webhooks/...")
                naepul_on = st.checkbox("내풀 타이머 알림 기능 사용하기", value=(get_opt_str("naepul_timer_enabled", "0") == "1"))

                st.markdown("#### 📊 결산 시간 알림 설정")
                settle_on = st.checkbox("결산 시간 알림 사용하기", value=(get_opt_str("settlement_alert_enabled", "0") == "1"))
                s1, s2, s3, s4 = st.columns(4)
                s_wd_idx = s1.selectbox("결산 요일", list(range(7)), format_func=lambda x: WEEKDAY_NAMES[x], index=get_opt_int("settlement_weekday", 4))
                s_hr = s2.number_input("결산 시(0~23)", 0, 23, get_opt_int("settlement_hour", 21))
                s_mn = s3.number_input("결산 분(0~59)", 0, 59, get_opt_int("settlement_minute", 0))
                s_ld = s4.number_input("사전 알림(분 전)", 0, 1440, get_opt_int("settlement_lead_minutes", 0))

                if st.form_submit_button("💾 알림/결산 설정 저장", type="primary", use_container_width=True):
                    set_opt_str("naepul_ntfy_topic", ntfy_in.strip())
                    set_opt_str("naepul_webhook_url", wh_in.strip())
                    set_opt_str("naepul_timer_enabled", "1" if naepul_on else "0")
                    set_opt_str("settlement_alert_enabled", "1" if settle_on else "0")
                    set_opt_str("settlement_weekday", str(int(s_wd_idx)))
                    set_opt_str("settlement_hour", str(int(s_hr)))
                    set_opt_str("settlement_minute", str(int(s_mn)))
                    set_opt_str("settlement_lead_minutes", str(int(s_ld)))
                    log_activity("ntfy_box", current_user["username"], current_user["nickname"], current_user["grade"], "알림설정변경", f"토픽={ntfy_in.strip()}, 결산={WEEKDAY_NAMES[int(s_wd_idx)]} {int(s_hr):02d}:{int(s_mn):02d}")
                    st.success("알림 및 결산 시간 설정이 저장되었습니다!")
                    st.rerun()

            b_t1, b_t2 = st.columns(2)
            if b_t1.button("🔔 테스트 알림 보내기", use_container_width=True):
                ok, msg = send_phone_push("🔔 알림 테스트", f"{current_user['nickname']}님이 테스트 알림을 보냈습니다.")
                st.success(msg) if ok else st.error(msg)
            if b_t2.button("📊 결산 알림 지금 1회 테스트 발송", use_container_width=True):
                wd_txt = WEEKDAY_NAMES[get_opt_int("settlement_weekday", 4)]
                sh = get_opt_int("settlement_hour", 21)
                sm = get_opt_int("settlement_minute", 0)
                ok, msg = send_phone_push("📊 결산 시간 테스트 알림", f"설정된 결산 시각: 매주 {wd_txt} {sh:02d}:{sm:02d}")
                st.success(msg) if ok else st.error(msg)


# ==========================================
# SECTION 3: 경고/강퇴 · 내풀 관리
# ==========================================
elif nav in ("mod_input", "mod_score", "mod_search", "mod_timer", "mod_audit"):
    cfg = get_tbl_settings("mod_settings")
    wd_name = WEEKDAY_NAMES[cfg["cutoff_weekday"]]
    t_str = f"{cfg['cutoff_hour']:02d}:{cfg['cutoff_minute']:02d}"

    c_rooms = get_option_list("control_rooms", ["거래방", "거방1", "소통방", "2호점", "기타"])
    c_types = get_option_list("control_action_types", ["경고", "경고1회", "경고2회", "강퇴", "내풀", "재심", "기타"])
    c_reasons = get_option_list("control_reasons", ["내풀", "경고누적", "분탕", "미옾프", "거쿨", "가격미기재", "관리자재량", "기타"])
    rev_statuses = get_option_list("review_statuses", ["정상유지", "재심요청", "조치해제(감경)", "내풀대상", "무효(점수제외)"])

    mod_safe = get_opt_int("mod_safe_cnt", 25)
    mod_fail_pt = get_opt_int("mod_fail_pt", -5)
    mod_bonus_start = get_opt_int("mod_bonus_start", 35)
    mod_bonus_step = get_opt_int("mod_bonus_step", 10)
    mod_bonus_pt = get_opt_int("mod_bonus_pt", 5)

    st.title("🛡️ 경고/강퇴·내풀 관리")
    st.caption(f"마감: 매주 {wd_name} {t_str} | 세이프 {mod_safe}명 ({mod_fail_pt}점) | {mod_bonus_start}명↑ {mod_bonus_step}명당 +{mod_bonus_pt}점")

    active_members = get_members(only_active=True)
    member_nicknames = [m["nickname"] for m in active_members]
    now_dt = now_kst()
    cur_cut = get_cutoff_date_str(now_dt, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"])
    rem = datetime.strptime(f"{cur_cut} {t_str}:00", "%Y-%m-%d %H:%M:%S") - now_dt

    with st.sidebar:
        st.divider()
        if is_mgr and member_nicknames:
            def_i = member_nicknames.index(current_user["nickname"]) if current_user["nickname"] in member_nicknames else 0
            rec_staff = st.selectbox("기록 담당자 (MS/MP 대리)", member_nicknames, index=def_i)
        else:
            rec_staff = current_user["nickname"]
        st.info(f"**마감일:** {cur_cut} ({wd_name[0]}) {t_str}\n\n**남은 시간:** {max(rem.days,0)}일 {max(rem.seconds//3600,0)}시간")
        if is_mgr:
            with st.expander("⚙️ 마감 시간 변경 (MS/MP)"):
                nw = st.selectbox("마감 요일", WEEKDAY_NAMES, index=cfg["cutoff_weekday"], key="mod_wd")
                ch1, ch2 = st.columns(2)
                nh = ch1.number_input("시", 0, 23, cfg["cutoff_hour"], key="mod_h")
                nm = ch2.number_input("분", 0, 59, cfg["cutoff_minute"], key="mod_m")
                if st.button("💾 마감 적용", key="mod_sv", use_container_width=True):
                    w_idx = WEEKDAY_NAMES.index(nw)
                    save_tbl_setting("mod_settings", "cutoff_weekday", str(w_idx))
                    save_tbl_setting("mod_settings", "cutoff_hour", str(int(nh)))
                    save_tbl_setting("mod_settings", "cutoff_minute", str(int(nm)))
                    recalculate_cutoffs("mod_records", "action_dt", w_idx, int(nh), int(nm))
                    st.rerun()

    df_all = read_sql_df("SELECT * FROM mod_records ORDER BY action_dt DESC, id DESC")

    def calc_mod_score(cnt: int) -> dict:
        if cnt < mod_safe:
            return {"status": "😭 미달(감점)", "score": mod_fail_pt, "next": f"세이프({mod_safe}명)까지 {mod_safe-cnt}건"}
        if cnt < mod_bonus_start:
            return {"status": "✅ 세이프", "score": 0, "next": f"가점({mod_bonus_start}명)까지 {mod_bonus_start-cnt}건"}
        bonus = mod_bonus_pt + ((cnt - mod_bonus_start) // max(1, mod_bonus_step)) * mod_bonus_pt
        nxt = mod_bonus_start + (((cnt - mod_bonus_start) // max(1, mod_bonus_step)) + 1) * mod_bonus_step
        return {"status": "✴️ 가산점", "score": bonus, "next": f"다음({nxt}명)까지 {nxt-cnt}건"}

    if nav == "mod_input":
        st.subheader("📝 경고/강퇴·내풀 기록 입력")
        with st.expander("1️⃣ 간편 개별 입력", expanded=True):
            a_type = st.selectbox("조치 유형 선택", c_types, key="mod_atype_sel")
            with st.form("mod_single_form", clear_on_submit=True):
                t_name = st.text_input("대상자 닉네임 *")
                a1, a2 = st.columns(2)
                a_date = a1.date_input("조치일", value=today_kst())
                a_time = a2.time_input("조치시간", value=now_kst().time().replace(second=0, microsecond=0))
                a3, a4 = st.columns(2)
                room = a3.selectbox("방 선택", c_rooms)
                if "경고" in a_type:
                    warn_cnt = a4.number_input("경고 횟수 (1~10회)", min_value=1, max_value=10, value=1, step=1)
                    period_sel, period_cust = "", ""
                else:
                    warn_cnt = 0
                    period_sel = a4.selectbox("조치 기간", ["영구", "1일", "3일", "7일", "14일", "30일", "60일", "90일", "직접 입력"])
                    period_cust = st.text_input("기간 직접 입력 (선택)", placeholder="예: 26.11.20~11.27 또는 15일")
                r_sel = st.selectbox("사유 선택", c_reasons)
                r_cust = st.text_input("사유 직접 입력 (선택)")
                note_in = st.text_input("비고 / 메모")
                ev_file = st.file_uploader("📸 증거 사진 업로드 (선택)", type=["png", "jpg", "jpeg", "webp"], key="mod_ev_up")
                use_timer_cfg = get_opt_str("naepul_timer_enabled", "0") == "1"
                if use_timer_cfg:
                    st.markdown("**⏰ 내풀 타이머 알림 설정 (선택)**")
                    tm_use = st.checkbox("이 기록에 내풀 타이머 걸기", value=("내풀" in a_type))
                    tc1, tc2, tc3 = st.columns(3)
                    tm_d = tc1.number_input("일", 0, 365, 0, key="tm_d")
                    tm_h = tc2.number_input("시간", 0, 23, 0, key="tm_h")
                    tm_m = tc3.number_input("분", 0, 59, 10, key="tm_m")
                else:
                    tm_use, tm_d, tm_h, tm_m = False, 0, 0, 0
                if st.form_submit_button("💾 기록 저장", type="primary", use_container_width=True):
                    if not t_name.strip():
                        st.error("대상자 닉네임을 입력해 주세요.")
                    else:
                        fr = r_cust.strip() if r_cust.strip() else r_sel
                        dt_obj = datetime.combine(a_date, a_time)
                        c_str = get_cutoff_date_str(dt_obj, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"])
                        ev_b64 = encode_uploaded_image(ev_file)
                        a_detail = f"{int(warn_cnt)}회" if "경고" in a_type else (period_cust.strip() if (period_sel == "직접 입력" and period_cust.strip()) else (period_cust.strip() or period_sel))
                        conn = get_conn()
                        cur_kst = now_kst()
                        timer_end_str = ""
                        if tm_use and (tm_d > 0 or tm_h > 0 or tm_m > 0):
                            timer_end_str = (cur_kst + timedelta(days=int(tm_d), hours=int(tm_h), minutes=int(tm_m))).strftime("%Y-%m-%d %H:%M:%S")
                        conn.execute(
                            "INSERT INTO mod_records (target_name, action_date, action_time, action_dt, cutoff_date, room, action_type, warning_count, action_detail, reason, staff_name, review_status, note, evidence_image, timer_end_dt, timer_notified, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (t_name.lstrip("@").strip(), a_date.strftime("%Y-%m-%d"), a_time.strftime("%H:%M"), dt_obj.strftime("%Y-%m-%d %H:%M"), c_str, room, a_type, int(warn_cnt), a_detail, fr, rec_staff, rev_statuses[0], note_in.strip(), ev_b64, timer_end_str, 0, cur_kst.strftime("%Y-%m-%d %H:%M:%S")),
                        )
                        conn.commit()
                        conn.close()
                        log_activity("kakao_control_manager", current_user["username"], current_user["nickname"], current_user["grade"], "경고강퇴등록", f"{t_name} ({a_type} {a_detail} / {room} / {fr})")
                        st.success(f"저장 완료! ({a_type} {a_detail} | {c_str})")
                        st.rerun()

        with st.expander("2️⃣ 카톡 양식 붙여넣기 (일괄 등록)"):
            b_txt = st.text_area("카톡 내용 붙여넣기", height=180, placeholder="FC야구찐팬/26.09.28/23:49/거래방/내풀")
            if st.button("⚡ 자동 분석 후 일괄 저장", use_container_width=True):
                lines = [l.strip() for l in b_txt.splitlines() if l.strip()]
                items, errs, i = [], [], 0
                now_str_batch = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                while i < len(lines):
                    line = lines[i]
                    if line.count("/") >= 4:
                        nm, ds, ts, rm, rsn = [p.strip() for p in line.split("/", 4)]
                        try:
                            dt_obj = datetime.combine(parse_date_str(ds), parse_time_str(ts))
                            atype = "내풀" if "내풀" in rsn else ("강퇴" if "강퇴" in rsn else "경고")
                            items.append((nm.lstrip("@").strip(), dt_obj.strftime("%Y-%m-%d"), dt_obj.strftime("%H:%M"), dt_obj.strftime("%Y-%m-%d %H:%M"), get_cutoff_date_str(dt_obj, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"]), rm, atype, rsn, rec_staff, rev_statuses[0], "", now_str_batch))
                        except Exception as e:
                            errs.append(f"'{line}': {e}")
                        i += 1
                        continue
                    if i + 2 < len(lines) and lines[i + 2].count("/") == 2:
                        try:
                            ch = lines[i].lstrip("@").strip()
                            m_h = re.match(r"^(\S+)\s*(.*)$", ch)
                            nm = m_h.group(1) if m_h else ch
                            atype = m_h.group(2).strip() if (m_h and m_h.group(2).strip()) else "경고"
                            rsn = re.sub(r"^사유\s*[:：]?\s*", "", lines[i + 1]).strip()
                            ds, ts, rm = [p.strip() for p in lines[i + 2].split("/", 2)]
                            dt_obj = datetime.combine(parse_date_str(ds), parse_time_str(ts))
                            items.append((nm, dt_obj.strftime("%Y-%m-%d"), dt_obj.strftime("%H:%M"), dt_obj.strftime("%Y-%m-%d %H:%M"), get_cutoff_date_str(dt_obj, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"]), rm, atype, rsn, rec_staff, rev_statuses[0], "", now_str_batch))
                            i += 3
                            continue
                        except Exception as e:
                            errs.append(f"'{lines[i]}': {e}")
                            i += 1
                            continue
                    errs.append(f"건너뜀: '{line}'")
                    i += 1
                if items:
                    conn = get_conn()
                    conn.executemany("INSERT INTO mod_records (target_name, action_date, action_time, action_dt, cutoff_date, room, action_type, reason, staff_name, review_status, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", items)
                    conn.commit()
                    conn.close()
                    log_activity("kakao_control_manager", current_user["username"], current_user["nickname"], current_user["grade"], "경고강퇴일괄등록", f"{len(items)}건")
                    st.success(f"총 {len(items)}건 저장 완료!")
                    st.rerun()

    elif nav == "mod_score":
        st.subheader("📊 주간 점수 & 반영")
        if df_all.empty:
            st.info("기록이 없습니다.")
        else:
            weeks = sorted(df_all["cutoff_date"].unique().tolist(), reverse=True)
            if cur_cut not in weeks:
                weeks.insert(0, cur_cut)
            sel_w = st.selectbox("마감 주차", weeks, key="mod_w_sel")
            td = today_kst()
            s_in = st.text_input("시즌명", value=f"{td.year}-Q{(td.month-1)//3+1}", key="mod_season")
            df_w = df_all[df_all["cutoff_date"] == sel_w]
            s_list = sorted(list(set(member_nicknames + df_w["staff_name"].unique().tolist())))
            rows = []
            for stf in s_list:
                df_s = df_w[df_w["staff_name"] == stf]
                vc = int((df_s["is_valid_score"] == 1).sum())
                si = calc_mod_score(vc)
                rows.append({"부방": stf, "건수": vc, "상태": si["status"], "점수": si["score"], "목표": si["next"]})
            df_sc = pd.DataFrame(rows).sort_values(by=["점수", "건수"], ascending=[False, False])
            st.dataframe(df_sc, use_container_width=True, hide_index=True)

            if is_mgr:
                if st.button("✅ 선택한 주차 점수 자동 반영 (MS/MP)", type="primary", use_container_width=True):
                    ok_c = 0
                    for _, r in df_sc.iterrows():
                        conn = get_conn()
                        ex = conn.execute("SELECT status FROM mod_score_sync_log WHERE week_cutoff=? AND staff_name=?", (sel_w, r["부방"])).fetchone()
                        mem = conn.execute("SELECT id FROM members WHERE nickname=?", (r["부방"],)).fetchone()
                        conn.close()
                        if (ex and ex["status"] == "SUCCESS") or not mem:
                            continue
                        record_performance(mem["id"], s_in.strip() or "시즌통합", f"{sel_w} 마감", "경고/강퇴 주간실적", int(r["점수"]), 0, f"유효 조치 {int(r['건수'])}건 ({r['상태']})")
                        conn = get_conn()
                        if is_postgres_configured():
                            conn.execute(
                                "INSERT INTO mod_score_sync_log (week_cutoff, staff_name, member_id, valid_count, score_delta, status, message, synced_at) VALUES (%s,%s,%s,%s,%s,'SUCCESS','반영완료',%s) ON CONFLICT(week_cutoff, staff_name) DO UPDATE SET status='SUCCESS', synced_at=EXCLUDED.synced_at",
                                (sel_w, r["부방"], mem["id"], int(r["건수"]), int(r["점수"]), now_kst().strftime("%Y-%m-%d %H:%M:%S")),
                            )
                        else:
                            conn.execute(
                                "INSERT INTO mod_score_sync_log (week_cutoff, staff_name, member_id, valid_count, score_delta, status, message, synced_at) VALUES (?,?,?,?,?,'SUCCESS','반영완료',?) ON CONFLICT(week_cutoff, staff_name) DO UPDATE SET status='SUCCESS', synced_at=excluded.synced_at",
                                (sel_w, r["부방"], mem["id"], int(r["건수"]), int(r["점수"]), now_kst().strftime("%Y-%m-%d %H:%M:%S")),
                            )
                        conn.commit()
                        conn.close()
                        ok_c += 1
                    st.success(f"{ok_c}명 반영 완료!")

    elif nav == "mod_search":
        st.subheader("🔍 검색 / 재심 / 증거")
        if not df_all.empty:
            sq = st.text_input("대상자 닉네임 또는 사유 검색")
            rf = st.multiselect("방 필터", options=sorted(df_all["room"].unique().tolist()))
            df_f = df_all.copy()
            if sq.strip():
                df_f = df_f[df_f["target_name"].str.contains(sq.strip(), case=False, na=False) | df_f["reason"].str.contains(sq.strip(), case=False, na=False)]
            if rf:
                df_f = df_f[df_f["room"].isin(rf)]
            show_cols = [c for c in ["id", "target_name", "action_dt", "room", "action_type", "action_detail", "reason", "staff_name", "review_status"] if c in df_f.columns]
            st.dataframe(df_f[show_cols], use_container_width=True, hide_index=True)
            if not df_f.empty:
                opts = {f"[#{r['id']}] {r['target_name']} | {r['action_type']} ({r.get('action_detail') or '-'}) | {r['reason']}": r["id"] for _, r in df_f.head(50).iterrows()}
                sid = opts[st.selectbox("상세 보기 / 증거사진 확인", list(opts.keys()))]
                tr = df_all[df_all["id"] == sid].iloc[0]
                if "evidence_image" in tr and tr["evidence_image"]:
                    render_saved_image(tr["evidence_image"], caption=f"#{sid} 증거 사진")
            if is_mgr and not df_f.empty:
                if "경고" in str(tr["action_type"]):
                    cur_wc = int(tr.get("warning_count") or 1)
                    new_wc = st.number_input("경고 횟수 조정 (1~10회)", min_value=1, max_value=10, value=max(1, min(10, cur_wc)), step=1)
                    new_ad = f"{int(new_wc)}회"
                else:
                    new_wc = 0
                    new_ad = st.text_input("조치 기간 수정", value=str(tr.get("action_detail") or ""))
                ns = st.selectbox("재심 상태", rev_statuses, index=rev_statuses.index(tr["review_status"]) if tr["review_status"] in rev_statuses else 0)
                nv = st.checkbox("점수 인정", value=bool(tr["is_valid_score"]))
                nn = st.text_input("메모", value=tr["note"] or "")
                c1, c2 = st.columns(2)
                if c1.button("✅ 저장", type="primary", use_container_width=True):
                    conn = get_conn()
                    conn.execute("UPDATE mod_records SET warning_count=?, action_detail=?, is_valid_score=?, review_status=?, note=? WHERE id=?", (int(new_wc), new_ad, int(nv), ns, nn, int(sid)))
                    conn.commit()
                    conn.close()
                    st.rerun()
                if c2.button("🗑️ 삭제", use_container_width=True):
                    conn = get_conn()
                    conn.execute("DELETE FROM mod_records WHERE id=?", (int(sid),))
                    conn.commit()
                    conn.close()
                    st.rerun()
        else:
            st.info("기록이 없습니다.")

    elif nav == "mod_timer":
        st.subheader("⏰ 내풀 타이머 현황")
        if get_opt_str("naepul_timer_enabled", "0") != "1":
            st.info("**💬 소통 · 건의 · ntfy → ㄴ ntfy 알림 수발신 & 설정**에서 내풀 타이머 알림을 켤 수 있습니다.")
        if not df_all.empty and "timer_end_dt" in df_all.columns:
            df_tm = df_all[df_all["timer_end_dt"].fillna("") != ""].copy()
            if df_tm.empty:
                st.info("설정된 내풀 타이머가 없습니다.")
            else:
                now_t = now_kst()
                for _, r in df_tm.head(30).iterrows():
                    end_t = datetime.strptime(r["timer_end_dt"], "%Y-%m-%d %H:%M:%S")
                    diff = end_t - now_t
                    if r["timer_notified"] == 1:
                        st_lbl = "✅ 알림 발송 완료"
                    elif diff.total_seconds() <= 0:
                        st_lbl = "🔔 만료됨 (발송 대기)"
                    else:
                        d_left = diff.days
                        h_left = diff.seconds // 3600
                        m_left = (diff.seconds % 3600) // 60
                        st_lbl = f"⏳ 남은 시간: {d_left}일 {h_left}시간 {m_left}분"
                    with st.container(border=True):
                        st.markdown(f"**{r['target_name']}** ({r['room']}) — {st_lbl}")
                        st.caption(f"만료 예정: {r['timer_end_dt']} | 사유: {r['reason']}")
                if st.button("🔄 타이머 새로고침 & 만료 체크", use_container_width=True):
                    check_and_send_naepul_timers()
                    st.rerun()

    elif nav == "mod_audit":
        st.subheader("🛡️ 중복 검수 / CSV백업")
        if not df_all.empty:
            dup = df_all.groupby(["target_name", "action_date"]).agg(당일건수=("id", "count"), 담당부방=("staff_name", lambda x: ", ".join(x))).reset_index()
            susp = dup[dup["당일건수"] >= 2]
            if susp.empty:
                st.success("동일 날짜 중복 조치 의심 건이 없습니다.")
            else:
                st.warning(f"동일 날짜 중복 기록 {len(susp)}건")
                st.dataframe(susp, use_container_width=True, hide_index=True)
            st.download_button("📊 CSV 다운로드", df_all.to_csv(index=False).encode("utf-8-sig"), f"경고강퇴_{today_kst().strftime('%Y%m%d')}.csv", "text/csv", use_container_width=True)
        else:
            st.info("기록이 없습니다.")


# ==========================================
# SECTION 4: 보이스룸(보룸) 관리
# ==========================================
elif nav in ("vr_input", "vr_score", "vr_history"):
    cfg = get_tbl_settings("voice_settings")
    wd_name = WEEKDAY_NAMES[cfg["cutoff_weekday"]]
    t_str = f"{cfg['cutoff_hour']:02d}:{cfg['cutoff_minute']:02d}"
    v_rooms = get_option_list("voiceroom_rooms", ["7ㅓ방", "1호점", "2호점", "소통방", "피모700방", "피모1000방", "기타"])

    vr_safe_h = get_opt_int("vr_safe_h", 40)
    vr_fail_pt = get_opt_int("vr_fail_pt", -5)
    vr_bonus_h = get_opt_int("vr_bonus_start_h", 50)
    vr_bonus_step_h = get_opt_int("vr_bonus_step_h", 10)
    vr_bonus_pt = get_opt_int("vr_bonus_pt", 5)

    st.title("🎙️ 보이스룸(보룸) 관리")
    st.caption(f"마감: 매주 {wd_name} {t_str} | 세이프 {vr_safe_h}시간 ({vr_fail_pt}점) | {vr_bonus_h}시간↑ {vr_bonus_step_h}시간당 +{vr_bonus_pt}점")

    active_members = get_members(only_active=True)
    member_nicknames = [m["nickname"] for m in active_members]
    now_dt = now_kst()
    cur_cut = get_cutoff_date_str(now_dt, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"])
    rem = datetime.strptime(f"{cur_cut} {t_str}:00", "%Y-%m-%d %H:%M:%S") - now_dt

    with st.sidebar:
        st.divider()
        if is_mgr and member_nicknames:
            def_i = member_nicknames.index(current_user["nickname"]) if current_user["nickname"] in member_nicknames else 0
            rec_staff = st.selectbox("기록 담당자 (MS/MP 대리)", member_nicknames, index=def_i, key="vr_stf")
        else:
            rec_staff = current_user["nickname"]
        st.info(f"**마감일:** {cur_cut} ({wd_name[0]}) {t_str}\n\n**남은 시간:** {max(rem.days,0)}일 {max(rem.seconds//3600,0)}시간")
        if is_mgr:
            with st.expander("⚙️ 마감 시간 변경 (MS/MP)"):
                nw = st.selectbox("마감 요일", WEEKDAY_NAMES, index=cfg["cutoff_weekday"], key="vr_wd")
                ch1, ch2 = st.columns(2)
                nh = ch1.number_input("시", 0, 23, cfg["cutoff_hour"], key="vr_h")
                nm = ch2.number_input("분", 0, 59, cfg["cutoff_minute"], key="vr_m")
                if st.button("💾 마감 적용", key="vr_sv", use_container_width=True):
                    w_idx = WEEKDAY_NAMES.index(nw)
                    save_tbl_setting("voice_settings", "cutoff_weekday", str(w_idx))
                    save_tbl_setting("voice_settings", "cutoff_hour", str(int(nh)))
                    save_tbl_setting("voice_settings", "cutoff_minute", str(int(nm)))
                    recalculate_cutoffs("voice_records", "end_dt", w_idx, int(nh), int(nm))
                    st.rerun()

    def sec_to_hms(sec: int) -> str:
        return f"{sec//3600}시간 {(sec%3600)//60:02d}분 {sec%60:02d}초"

    def parse_dur(ds: str) -> tuple[int, str]:
        p = [x.strip() for x in ds.strip().split(":")]
        h, m, s = (int(p[0]), int(p[1]), int(p[2])) if len(p) == 3 else (int(p[0]), int(p[1]), 0)
        if m >= 60 or s >= 60 or h < 0 or m < 0 or s < 0:
            raise ValueError("올바른 시:분:초 범위가 아닙니다.")
        return h * 3600 + m * 60 + s, f"{h}:{m:02d}:{s:02d}"

    def calc_vr_score(sec: int) -> dict:
        s_safe, s_bon = vr_safe_h * 3600, vr_bonus_h * 3600
        if sec < s_safe:
            return {"status": "😭 미달", "score": vr_fail_pt, "next": f"세이프({vr_safe_h}h)까지 {sec_to_hms(s_safe-sec)}"}
        if sec < s_bon:
            return {"status": "✅ 세이프", "score": 0, "next": f"가점({vr_bonus_h}h)까지 {sec_to_hms(s_bon-sec)}"}
        eh = (sec - s_bon) // 3600
        step = max(1, vr_bonus_step_h)
        bonus = vr_bonus_pt + (eh // step) * vr_bonus_pt
        nt = vr_bonus_h + ((eh // step) + 1) * step
        return {"status": "✴️ 가산점", "score": bonus, "next": f"다음({nt}h)까지 {sec_to_hms(nt*3600-sec)}"}

    df_all = read_sql_df("SELECT * FROM voice_records ORDER BY end_dt DESC, id DESC")

    if nav == "vr_input":
        st.subheader("📝 보룸 기록 입력")
        with st.expander("1️⃣ 간편 개별 입력", expanded=True):
            with st.form("vr_single_form", clear_on_submit=True):
                r_sel = st.selectbox("방 선택", v_rooms)
                r_cus = st.text_input("방 직접 입력 (선택)")
                d1, d2 = st.columns(2)
                sd = d1.date_input("시작일", value=today_kst())
                ed = d2.date_input("종료일", value=today_kst())
                d3, d4 = st.columns(2)
                et = d3.time_input("종료시간", value=now_kst().time().replace(second=0, microsecond=0))
                dur_in = d4.text_input("진행 시간 (시:분:초) *", placeholder="10:08:32")
                note_in = st.text_input("메모 (선택)")
                if st.form_submit_button("💾 보룸 기록 저장", type="primary", use_container_width=True):
                    try:
                        ds, nd = parse_dur(dur_in)
                        frm = r_cus.strip() if r_cus.strip() else r_sel
                        edt = datetime.combine(ed, et)
                        c_str = get_cutoff_date_str(edt, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"])
                        conn = get_conn()
                        conn.execute("INSERT INTO voice_records (staff_name, room, start_date, end_date, end_time, end_dt, cutoff_date, duration_str, duration_seconds, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (rec_staff, frm, sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d"), et.strftime("%H:%M"), edt.strftime("%Y-%m-%d %H:%M"), c_str, nd, ds, note_in.strip(), now_kst().strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()
                        conn.close()
                        log_activity("kakao_voiceroom_manager", current_user["username"], current_user["nickname"], current_user["grade"], "보룸기록등록", f"{frm} ({nd})")
                        st.success(f"저장 완료! ({nd})")
                        st.rerun()
                    except Exception as e:
                        st.error(f"오류: {e}")

        with st.expander("2️⃣ 카톡 양식 붙여넣기 (일괄 등록)"):
            b_txt = st.text_area("카톡 보룸 내용 붙여넣기", height=180, placeholder="FC야구찐팬/7ㅓ방/26.09.30~10.01/08:32/10:08:32")
            if st.button("⚡ 자동 분석 후 일괄 저장", use_container_width=True):
                raw_lines = [l.strip() for l in b_txt.splitlines() if l.strip()]
                merged, idx = [], 0
                while idx < len(raw_lines):
                    cur = raw_lines[idx]
                    while cur.count("/") < 4 and cur.endswith("/") and idx + 1 < len(raw_lines):
                        idx += 1
                        cur += raw_lines[idx]
                    merged.append(cur)
                    idx += 1
                items, errs = [], []
                kst_year = today_kst().year
                now_str_vr = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                for line in merged:
                    if line.count("/") < 4:
                        errs.append(f"형식 불일치: '{line}'")
                        continue
                    try:
                        sn, rm, dr, ets, durs = [p.strip() for p in line.split("/", 4)]
                        def p_sd(r, ry=None, rm_n=None):
                            ns = [int(x) for x in re.findall(r"\d+", r)]
                            cy = ry if ry else kst_year
                            if len(ns) == 3:
                                return date(2000 + ns[0] if ns[0] < 100 else ns[0], ns[1], ns[2])
                            if len(ns) == 2:
                                return date(cy, ns[0], ns[1])
                            if len(ns) == 1 and rm_n:
                                return date(cy, rm_n, ns[0])
                            raise ValueError(f"날짜 오류: {r}")
                        if "~" in dr or "-" in dr:
                            sep = "~" if "~" in dr else "-"
                            l_p, r_p = [x.strip() for x in dr.split(sep, 1)]
                            s_d = p_sd(l_p)
                            e_d = p_sd(r_p, s_d.year, s_d.month)
                        else:
                            s_d = e_d = p_sd(dr)
                        e_t = parse_time_str(ets)
                        edt = datetime.combine(e_d, e_t)
                        ds, nd = parse_dur(durs)
                        items.append((sn.lstrip("@").strip(), rm, s_d.strftime("%Y-%m-%d"), e_d.strftime("%Y-%m-%d"), e_t.strftime("%H:%M"), edt.strftime("%Y-%m-%d %H:%M"), get_cutoff_date_str(edt, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"]), nd, ds, "", now_str_vr))
                    except Exception as e:
                        errs.append(f"'{line}': {e}")
                if items:
                    conn = get_conn()
                    conn.executemany("INSERT INTO voice_records (staff_name, room, start_date, end_date, end_time, end_dt, cutoff_date, duration_str, duration_seconds, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", items)
                    conn.commit()
                    conn.close()
                    log_activity("kakao_voiceroom_manager", current_user["username"], current_user["nickname"], current_user["grade"], "보룸일괄등록", f"{len(items)}건")
                    st.success(f"총 {len(items)}건 저장 완료!")
                    st.rerun()

    elif nav == "vr_score":
        st.subheader("📊 주간 시간 & 반영")
        if df_all.empty:
            st.info("기록이 없습니다.")
        else:
            weeks = sorted(df_all["cutoff_date"].unique().tolist(), reverse=True)
            if cur_cut not in weeks:
                weeks.insert(0, cur_cut)
            sel_w = st.selectbox("마감 주차", weeks, key="vr_w_sel")
            td = today_kst()
            s_in = st.text_input("시즌명", value=f"{td.year}-Q{(td.month-1)//3+1}", key="vr_season")
            df_w = df_all[df_all["cutoff_date"] == sel_w]
            s_list = sorted(list(set(member_nicknames + df_w["staff_name"].unique().tolist())))
            rows = []
            for stf in s_list:
                df_s = df_w[(df_w["staff_name"] == stf) & (df_w["is_valid_score"] == 1)]
                t_sec = int(df_s["duration_seconds"].sum()) if not df_s.empty else 0
                si = calc_vr_score(t_sec)
                rows.append({"부방": stf, "누적 시간": sec_to_hms(t_sec), "상태": si["status"], "점수": si["score"], "다음 목표": si["next"], "_sec": t_sec})
            df_sc = pd.DataFrame(rows).sort_values(by=["점수", "_sec"], ascending=[False, False])
            st.dataframe(df_sc.drop(columns=["_sec"]), use_container_width=True, hide_index=True)

            for _, r in df_sc.iterrows():
                st.write(f"**{r['부방']}** — {r['누적 시간']} ({r['상태']})")
                st.progress(min(r["_sec"] / max(1, vr_bonus_h * 3600), 1.0))

            if is_mgr:
                if st.button("✅ 선택한 주차 보룸 점수 자동 반영 (MS/MP)", type="primary", use_container_width=True):
                    ok_c = 0
                    for _, r in df_sc.iterrows():
                        conn = get_conn()
                        ex = conn.execute("SELECT status FROM voice_score_sync_log WHERE week_cutoff=? AND staff_name=?", (sel_w, r["부방"])).fetchone()
                        mem = conn.execute("SELECT id FROM members WHERE nickname=?", (r["부방"],)).fetchone()
                        conn.close()
                        if (ex and ex["status"] == "SUCCESS") or not mem:
                            continue
                        record_performance(mem["id"], s_in.strip() or "시즌통합", f"{sel_w} 마감", "보이스룸 주간실적", int(r["점수"]), 0, f"보룸 누적 {r['누적 시간']} ({r['상태']})")
                        conn = get_conn()
                        if is_postgres_configured():
                            conn.execute(
                                "INSERT INTO voice_score_sync_log (week_cutoff, staff_name, member_id, total_seconds, score_delta, status, message, synced_at) VALUES (%s,%s,%s,%s,%s,'SUCCESS','반영완료',%s) ON CONFLICT(week_cutoff, staff_name) DO UPDATE SET status='SUCCESS', synced_at=EXCLUDED.synced_at",
                                (sel_w, r["부방"], mem["id"], int(r["_sec"]), int(r["점수"]), now_kst().strftime("%Y-%m-%d %H:%M:%S")),
                            )
                        else:
                            conn.execute(
                                "INSERT INTO voice_score_sync_log (week_cutoff, staff_name, member_id, total_seconds, score_delta, status, message, synced_at) VALUES (?,?,?,?,?,'SUCCESS','반영완료',?) ON CONFLICT(week_cutoff, staff_name) DO UPDATE SET status='SUCCESS', synced_at=excluded.synced_at",
                                (sel_w, r["부방"], mem["id"], int(r["_sec"]), int(r["점수"]), now_kst().strftime("%Y-%m-%d %H:%M:%S")),
                            )
                        conn.commit()
                        conn.close()
                        ok_c += 1
                    st.success(f"{ok_c}명 반영 완료!")

    elif nav == "vr_history":
        st.subheader("📋 전체 이력 & CSV")
        if not df_all.empty:
            qs = st.text_input("부방 또는 방 검색")
            qw = st.multiselect("주차 필터", sorted(df_all["cutoff_date"].unique().tolist(), reverse=True))
            df_f = df_all.copy()
            if qs.strip():
                df_f = df_f[df_f["staff_name"].str.contains(qs.strip(), case=False, na=False) | df_f["room"].str.contains(qs.strip(), case=False, na=False)]
            if qw:
                df_f = df_f[df_f["cutoff_date"].isin(qw)]
            st.dataframe(df_f[["id", "staff_name", "room", "start_date", "end_date", "end_time", "duration_str", "is_valid_score"]], use_container_width=True, hide_index=True)
            if is_mgr and not df_f.empty:
                opts = {f"[#{r['id']}] {r['staff_name']} | {r['room']} | {r['duration_str']}": r["id"] for _, r in df_f.head(50).iterrows()}
                sid = opts[st.selectbox("수정/삭제 선택 (MS/MP)", list(opts.keys()))]
                tr = df_all[df_all["id"] == sid].iloc[0]
                vc = st.checkbox("시간 인정", value=bool(tr["is_valid_score"]))
                ne = st.text_input("메모", value=tr["note"] or "")
                c1, c2 = st.columns(2)
                if c1.button("✅ 저장", type="primary", use_container_width=True):
                    conn = get_conn()
                    conn.execute("UPDATE voice_records SET is_valid_score=?, note=? WHERE id=?", (int(vc), ne, int(sid)))
                    conn.commit()
                    conn.close()
                    st.rerun()
                if c2.button("🗑️ 삭제", use_container_width=True):
                    conn = get_conn()
                    conn.execute("DELETE FROM voice_records WHERE id=?", (int(sid),))
                    conn.commit()
                    conn.close()
                    st.rerun()
            st.download_button("📊 CSV 다운로드", df_all.to_csv(index=False).encode("utf-8-sig"), f"보이스룸_{today_kst().strftime('%Y%m%d')}.csv", "text/csv", use_container_width=True)
        else:
            st.info("기록이 없습니다.")


# ==========================================
# SECTION 5: 관리자 통합 대시보드 (MS/MP 전용)
# ==========================================
elif nav.startswith("adm_") and is_mgr:
    st.title("📊 관리자 통합 대시보드 (MS/MP 전용)")

    if nav == "adm_users":
        st.subheader("👥 회원가입 승인 & 부방 관리")
        tu, tm = st.tabs(["📝 가입 승인/거절", "🎖️ 부방 명단 · 등급 · 삭제"])
        with tu:
            sf = st.selectbox("상태 필터", ["대기중", "전체", "승인완료", "거절됨"])
            conn = get_conn()
            q = "SELECT u.*, COALESCE(m.grade,'M1') AS grade FROM users u LEFT JOIN members m ON u.nickname=m.nickname"
            if sf != "전체":
                q += f" WHERE u.approval_status='{sf}'"
            ul = [dict(r) for r in conn.execute(q + " ORDER BY u.id DESC").fetchall()]
            conn.close()
            for u in ul:
                with st.container(border=True):
                    st.markdown(f"**{u['nickname']}** (`@{u['username']}`) — 상태: **{u['approval_status']}**")
                    st.caption(f"신청일: {u['created_at']}")
                    if u["username"] != "admin":
                        b1, b2 = st.columns(2)
                        if b1.button("✅ 승인", key=f"ua_{u['id']}", disabled=(u["approval_status"] == "승인완료"), use_container_width=True):
                            conn = get_conn()
                            now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                            conn.execute("UPDATE users SET approval_status='승인완료', approved_at=? WHERE id=?", (now, u["id"]))
                            if not conn.execute("SELECT id FROM members WHERE nickname=?", (u["nickname"],)).fetchone():
                                conn.execute("INSERT INTO members (nickname, role, grade, points, vacation_days, status, joined_at) VALUES (?,'부방','M1',0,0,'활동중',?)", (u["nickname"], now))
                            conn.commit()
                            conn.close()
                            st.rerun()
                        if b2.button("❌ 거절", key=f"ur_{u['id']}", disabled=(u["approval_status"] == "거절됨"), use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE users SET approval_status='거절됨' WHERE id=?", (u["id"],))
                            conn.commit()
                            conn.close()
                            st.rerun()
        with tm:
            with st.form("adm_add_m"):
                nn = st.text_input("신규 닉네임")
                rr = st.text_input("직책", value="부방")
                gg = st.selectbox("초기 등급", ["M1", "MS", "MP"])
                if st.form_submit_button("등록", use_container_width=True) and nn.strip():
                    try:
                        conn = get_conn()
                        conn.execute("INSERT INTO members (nickname, role, grade, points, vacation_days, status, joined_at) VALUES (?,?,?,0,0,'활동중',?)", (nn.strip(), rr, gg, now_kst().strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()
                        conn.close()
                        st.success("등록 완료")
                        st.rerun()
                    except Exception:
                        st.error("이미 존재하는 닉네임입니다.")
            for m in get_members():
                with st.container(border=True):
                    st.markdown(f"**[{m.get('grade','M1')}] {m['nickname']}** ({m['role']}) — {m['points']}P / 휴가 {m['vacation_days']}일")
                    c1, c2 = st.columns(2)
                    ng = c1.selectbox("등급", ["MP", "MS", "M1", "M2", "M3", "M4"], index=["MP", "MS", "M1", "M2", "M3", "M4"].index(m["grade"]), key=f"mg_{m['id']}")
                    ns = c2.selectbox("상태", ["활동중", "휴식", "해임"], index=["활동중", "휴식", "해임"].index(m["status"]), key=f"ms_{m['id']}")
                    if ng != m["grade"]:
                        conn = get_conn()
                        conn.execute("UPDATE members SET grade=? WHERE id=?", (ng, m["id"]))
                        conn.commit()
                        conn.close()
                        st.rerun()
                    if ns != m["status"]:
                        conn = get_conn()
                        conn.execute("UPDATE members SET status=? WHERE id=?", (ns, m["id"]))
                        conn.commit()
                        conn.close()
                        st.rerun()
                    if st.button("🗑️ 완전 삭제", key=f"dm_{m['id']}", use_container_width=True):
                        conn = get_conn()
                        conn.execute("DELETE FROM logs WHERE member_id=?", (m["id"],))
                        conn.execute("DELETE FROM vacation_requests WHERE member_id=?", (m["id"],))
                        conn.execute("DELETE FROM shop_orders WHERE member_id=?", (m["id"],))
                        conn.execute("DELETE FROM users WHERE nickname=?", (m["nickname"],))
                        conn.execute("DELETE FROM members WHERE id=?", (m["id"],))
                        conn.commit()
                        conn.close()
                        st.rerun()

    elif nav == "adm_opts":
        st.subheader("⚙️ 사이트 이름 · 방 · 사유 · 점수 · 승급 기준 일괄 설정")
        st.caption("ntfy 알림 발송 및 결산 시간 알림 설정은 일반 사용자도 **💬 소통 · 건의 · ntfy → ㄴ ntfy 알림 수발신 & 설정**에서 직접 관리할 수 있습니다.")
        with st.form("all_sys_opts"):
            new_title = st.text_input("🌐 사이트 제목", value=SITE_TITLE)
            c1, c2 = st.columns(2)
            t_cr = c1.text_area("🛡️ 경고/강퇴 — 방 목록 (줄바꿈 구분)", value="\n".join(get_option_list("control_rooms", ["거래방", "거방1", "소통방", "2호점", "기타"])), height=130)
            t_ct = c2.text_area("🛡️ 경고/강퇴 — 조치 유형", value="\n".join(get_option_list("control_action_types", ["경고", "경고1회", "경고2회", "강퇴", "내풀", "재심", "기타"])), height=130)
            t_crs = c1.text_area("🛡️ 경고/강퇴 — 조치 사유", value="\n".join(get_option_list("control_reasons", ["내풀", "경고누적", "분탕", "미옾프", "거쿨", "가격미기재", "관리자재량", "기타"])), height=150)
            t_rs = c2.text_area("🛡️ 경고/강퇴 — 재심 상태 목록", value="\n".join(get_option_list("review_statuses", ["정상유지", "재심요청", "조치해제(감경)", "내풀대상", "무효(점수제외)"])), height=150)
            t_vr = c1.text_area("🎙️ 보이스룸 — 방 목록", value="\n".join(get_option_list("voiceroom_rooms", ["7ㅓ방", "1호점", "2호점", "소통방", "피모700방", "피모1000방", "기타"])), height=120)

            st.markdown("#### 🎖️ 등급 승급 · 월말 이월 기준")
            g1, g2, g3, g4 = st.columns(4)
            gm2 = g1.number_input("M2 승급(P)", 1, 5000, get_opt_int("grade_m2", 100))
            gm3 = g2.number_input("M3 승급(P)", 1, 5000, get_opt_int("grade_m3", 200))
            gm4 = g3.number_input("M4 승급(P)", 1, 5000, get_opt_int("grade_m4", 300))
            cpct = g4.number_input("월말 이월(%)", 0, 100, get_opt_int("monthly_carryover_pct", 60))

            st.markdown("#### 🛡️ 경고/강퇴 & 🎙️ 보이스룸 점수 기준")
            m1, m2, m3, m4 = st.columns(4)
            msafe = m1.number_input("경고 세이프(명)", 1, 500, get_opt_int("mod_safe_cnt", 25))
            mfail = m2.number_input("경고 미달감점", -100, 0, get_opt_int("mod_fail_pt", -5))
            mbon = m3.number_input("경고 가점시작(명)", 1, 500, get_opt_int("mod_bonus_start", 35))
            mstep = m4.number_input("경고 가점간격(명)", 1, 100, get_opt_int("mod_bonus_step", 10))

            v1, v2, v3, v4 = st.columns(4)
            vsafe = v1.number_input("보룸 세이프(시간)", 1, 200, get_opt_int("vr_safe_h", 40))
            vfail = v2.number_input("보룸 미달감점", -100, 0, get_opt_int("vr_fail_pt", -5))
            vbon = v3.number_input("보룸 가점시작(시간)", 1, 200, get_opt_int("vr_bonus_start_h", 50))
            vstep = v4.number_input("보룸 가점간격(시간)", 1, 100, get_opt_int("vr_bonus_step_h", 10))

            if st.form_submit_button("💾 전체 설정 저장", type="primary", use_container_width=True):
                set_opt_str("site_title", new_title.strip() or "오픈채팅 부방 통합 관리")
                save_option_list("control_rooms", t_cr.splitlines())
                save_option_list("control_action_types", t_ct.splitlines())
                save_option_list("control_reasons", t_crs.splitlines())
                save_option_list("review_statuses", t_rs.splitlines())
                save_option_list("voiceroom_rooms", t_vr.splitlines())
                for k, v in [("grade_m2", gm2), ("grade_m3", gm3), ("grade_m4", gm4), ("monthly_carryover_pct", cpct), ("mod_safe_cnt", msafe), ("mod_fail_pt", mfail), ("mod_bonus_start", mbon), ("mod_bonus_step", mstep), ("vr_safe_h", vsafe), ("vr_fail_pt", vfail), ("vr_bonus_start_h", vbon), ("vr_bonus_step_h", vstep)]:
                    set_opt_str(k, str(int(v)))
                st.success("모든 설정이 저장되었습니다!")
                st.rerun()

    elif nav == "adm_vac":
        st.subheader("🏖️ 정기휴가 항목 편집 & 신청 승인")
        tv1, tv2 = st.tabs(["✅ 휴가 신청 승인", "✏️ 정기휴가 항목 추가/수정/삭제"])
        with tv1:
            conn = get_conn()
            reqs = [dict(r) for r in conn.execute("SELECT v.*, m.nickname, m.grade FROM vacation_requests v JOIN members m ON v.member_id=m.id ORDER BY v.id DESC").fetchall()]
            conn.close()
            for r in reqs:
                with st.container(border=True):
                    st.markdown(f"**[{r.get('grade','M1')}] {r['nickname']}** — `{r['category']}` ({r['requested_days']}일) | 상태: **{r['status']}**")
                    st.caption(f"사유: {r['proof_memo']}")
                    if r.get("proof_image"):
                        render_saved_image(r["proof_image"], caption="인증 사진")
                    if r["status"] == "대기중":
                        cd = st.number_input("지급 일수", 1, 30, r["requested_days"], key=f"ad_{r['id']}")
                        b1, b2 = st.columns(2)
                        if b1.button("✅ 승인", key=f"aa_{r['id']}", use_container_width=True):
                            conn = get_conn()
                            now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                            conn.execute("UPDATE vacation_requests SET status='승인완료', requested_days=? WHERE id=?", (cd, r["id"]))
                            conn.execute("UPDATE members SET vacation_days=vacation_days+? WHERE id=?", (cd, r["member_id"]))
                            conn.execute("INSERT INTO logs (member_id, season, week, log_type, category, minutes, points_delta, vacation_delta, memo, created_at) VALUES (?,'시즌통합','휴가승인주차','정기휴가승인',?,0,0,?,?,?)", (r["member_id"], r["category"], cd, f"정기휴가 승인 ({r['proof_memo']})", now))
                            conn.commit()
                            conn.close()
                            st.rerun()
                        if b2.button("❌ 거절", key=f"ar_{r['id']}", use_container_width=True):
                            conn = get_conn()
                            conn.execute("UPDATE vacation_requests SET status='거절됨' WHERE id=?", (r["id"],))
                            conn.commit()
                            conn.close()
                            st.rerun()
        with tv2:
            with st.form("add_vac_rule"):
                rn = st.text_input("새 휴가 항목명 *", placeholder="예: 특별포상휴가 (2일)")
                c1, c2, c3 = st.columns(3)
                rd = c1.number_input("기본 지급 일수", 0, 30, 1)
                rl = c2.number_input("월 제한 횟수 (0=무제한)", 0, 10, 0)
                rdisc = c3.checkbox("관리자 협의(일수 직접입력)")
                rg = st.text_input("안내 문구", value="📌 [안내] 증빙 자료를 첨부해 주세요.")
                if st.form_submit_button("➕ 휴가 항목 추가", type="primary", use_container_width=True) and rn.strip():
                    try:
                        conn = get_conn()
                        conn.execute("INSERT INTO vacation_rule_items (name, days, monthly_limit, is_discretionary, guide) VALUES (?,?,?,?,?)", (rn.strip(), rd, None if rl == 0 else rl, int(rdisc), rg))
                        conn.commit()
                        conn.close()
                        st.success("추가 완료!")
                        st.rerun()
                    except Exception:
                        st.error("이미 같은 이름의 항목이 있습니다.")
            for vr in get_vacation_rules():
                with st.container(border=True):
                    st.markdown(f"**{vr['name']}**")
                    c1, c2, c3 = st.columns(3)
                    ed_d = c1.number_input("일수", 0, 30, vr["days"], key=f"vrd_{vr['id']}")
                    ed_l = c2.number_input("월제한(0=무제한)", 0, 10, vr["monthly_limit"] or 0, key=f"vrl_{vr['id']}")
                    ed_disc = c3.checkbox("협의형", value=bool(vr["is_discretionary"]), key=f"vrx_{vr['id']}")
                    ed_g = st.text_input("안내 문구", value=vr["guide"], key=f"vrg_{vr['id']}")
                    b1, b2 = st.columns(2)
                    if b1.button("💾 수정 저장", key=f"vrs_{vr['id']}", use_container_width=True):
                        conn = get_conn()
                        conn.execute("UPDATE vacation_rule_items SET days=?, monthly_limit=?, is_discretionary=?, guide=? WHERE id=?", (ed_d, None if ed_l == 0 else ed_l, int(ed_disc), ed_g, vr["id"]))
                        conn.commit()
                        conn.close()
                        st.success("수정됨")
                        st.rerun()
                    if b2.button("🗑️ 삭제", key=f"vrdel_{vr['id']}", use_container_width=True):
                        conn = get_conn()
                        conn.execute("DELETE FROM vacation_rule_items WHERE id=?", (vr["id"],))
                        conn.commit()
                        conn.close()
                        st.rerun()

    elif nav == "adm_shop":
        st.subheader("🎁 포인트 상점 항목 편집 & 주문 관리")
        ts1, ts2 = st.tabs(["📦 상품 추가/수정/삭제", "🛒 교환 주문 처리"])
        with ts1:
            with st.form("adm_shop"):
                sn = st.text_input("새 상품명 *")
                c1, c2 = st.columns(2)
                sp = c1.number_input("필요 포인트", 1, 10000, 10)
                ss = c2.number_input("재고", 0, 9999, 10)
                sd = st.text_input("상품 설명")
                if st.form_submit_button("➕ 상품 등록", type="primary", use_container_width=True) and sn.strip():
                    conn = get_conn()
                    conn.execute("INSERT INTO shop_items (item_name, price, description, stock) VALUES (?,?,?,?)", (sn.strip(), sp, sd, ss))
                    conn.commit()
                    conn.close()
                    st.rerun()
            conn = get_conn()
            s_items = [dict(r) for r in conn.execute("SELECT * FROM shop_items ORDER BY price ASC").fetchall()]
            conn.close()
            for it in s_items:
                with st.container(border=True):
                    en = st.text_input("상품명", value=it["item_name"], key=f"sin_{it['id']}")
                    c1, c2 = st.columns(2)
                    ep = c1.number_input("가격(P)", 1, 10000, it["price"], key=f"sip_{it['id']}")
                    es = c2.number_input("재고", 0, 9999, it["stock"], key=f"sis_{it['id']}")
                    ed = st.text_input("설명", value=it["description"] or "", key=f"sid_{it['id']}")
                    b1, b2 = st.columns(2)
                    if b1.button("💾 수정", key=f"siu_{it['id']}", use_container_width=True):
                        conn = get_conn()
                        conn.execute("UPDATE shop_items SET item_name=?, price=?, stock=?, description=? WHERE id=?", (en, ep, es, ed, it["id"]))
                        conn.commit()
                        conn.close()
                        st.rerun()
                    if b2.button("🗑️ 삭제", key=f"di_{it['id']}", use_container_width=True):
                        conn = get_conn()
                        conn.execute("DELETE FROM shop_items WHERE id=?", (it["id"],))
                        conn.commit()
                        conn.close()
                        st.rerun()
        with ts2:
            conn = get_conn()
            orders = [dict(r) for r in conn.execute("SELECT o.*, m.nickname FROM shop_orders o JOIN members m ON o.member_id=m.id ORDER BY o.id DESC").fetchall()]
            conn.close()
            for o in orders:
                with st.container(border=True):
                    st.markdown(f"**{o['nickname']}** ➔ `{o['item_name']}` ({o['price']}P)")
                    ns = st.selectbox("상태", ["대기중", "처리완료", "취소됨"], index=["대기중", "처리완료", "취소됨"].index(o["status"]), key=f"ao_{o['id']}")
                    if ns != o["status"]:
                        conn = get_conn()
                        conn.execute("UPDATE shop_orders SET status=? WHERE id=?", (ns, o["id"]))
                        conn.commit()
                        conn.close()
                        st.rerun()

    elif nav == "adm_logs":
        st.subheader("🕵️ 전체 앱 사용·접속 기록 (한국시간 KST)")
        af = st.selectbox("앱 필터", ["전체", "unified_app", "kakao_user_app", "kakao_control_manager", "kakao_voiceroom_manager", "alba", "suggestions", "ntfy_box", "auth"])
        ql = st.text_input("아이디 · 닉네임 · 활동 검색")
        conn = get_conn()
        alogs = [dict(r) for r in conn.execute("SELECT * FROM activity_logs ORDER BY id DESC LIMIT 500").fetchall()]
        conn.close()
        if alogs:
            df_a = pd.DataFrame(alogs)
            if af != "전체":
                df_a = df_a[df_a["app_name"] == af]
            if ql.strip():
                q = ql.strip()
                df_a = df_a[df_a["username"].str.contains(q, case=False, na=False) | df_a["nickname"].str.contains(q, case=False, na=False) | df_a["action"].str.contains(q, case=False, na=False) | df_a["detail"].str.contains(q, case=False, na=False)]
            df_a = df_a[["created_at", "app_name", "username", "nickname", "grade", "action", "detail"]]
            df_a.columns = ["시간(KST)", "앱", "아이디", "닉네임", "등급", "활동", "상세 내용"]
            st.dataframe(df_a, use_container_width=True, hide_index=True)

    elif nav == "adm_perf":
        st.subheader("⭐ 실적 수동 지급 & 히스토리 취소")
        am = get_members(only_active=True)
        if am:
            mo = {f"[{m.get('grade','M1')}] {m['nickname']} ({m['points']}P)": m["id"] for m in am}
            sl = st.selectbox("대상 부방", list(mo.keys()))
            with st.form("adm_perf"):
                td = today_kst()
                c1, c2 = st.columns(2)
                ss = c1.text_input("시즌", value=f"{td.year}-Q{(td.month-1)//3+1}")
                wk = c2.text_input("주차", value="1주차")
                ct = st.text_input("활동 항목", value="일반관리")
                p1, p2 = st.columns(2)
                pts = p1.number_input("지급 포인트 (P)", -500, 500, 10)
                vac = p2.number_input("지급 휴가 (일)", -30, 30, 0)
                mm = st.text_area("메모")
                if st.form_submit_button("반영하기", type="primary", use_container_width=True):
                    gm = record_performance(mo[sl], ss, wk, ct, pts, vac, mm)
                    st.success(f"반영 완료!{gm}")
        st.divider()
        logs = get_logs(100)
        if logs:
            st.dataframe([{"번호": l["id"], "시간": l["created_at"], "부방": l["nickname"], "항목": l["category"], "포인트": f"{l['points_delta']:+d}P", "휴가": f"{l['vacation_delta']:+d}일", "메모": l["memo"]} for l in logs], use_container_width=True, hide_index=True)
            lo = {f"[#{l['id']}] {l['created_at']} | {l['nickname']} | {l['category']} ({l['points_delta']:+d}P)": l for l in logs}
            tl = lo[st.selectbox("취소할 지급 내역 선택", list(lo.keys()))]
            if st.button("↩️ 선택한 지급 내역 취소하기", type="primary", use_container_width=True):
                conn = get_conn()
                mr = conn.execute("SELECT id, points, vacation_days, grade FROM members WHERE id=?", (tl["member_id"],)).fetchone()
                if mr:
                    np = max(0, mr["points"] - tl["points_delta"])
                    nv = max(0, mr["vacation_days"] - tl["vacation_delta"])
                    og = mr["grade"] or "M1"
                    ng = og if og in ["MP", "MS"] else calculate_grade(np, og)
                    conn.execute("UPDATE members SET points=?, vacation_days=?, grade=? WHERE id=?", (np, nv, ng, mr["id"]))
                ws = str(tl.get("week") or "").replace(" 마감", "").strip()
                if ws:
                    for tbl in ("mod_score_sync_log", "voice_score_sync_log"):
                        try:
                            conn.execute(f"DELETE FROM {tbl} WHERE week_cutoff=? AND staff_name=?", (ws, tl["nickname"]))
                        except Exception:
                            pass
                conn.execute("DELETE FROM logs WHERE id=?", (tl["id"],))
                conn.commit()
                conn.close()
                st.success("취소 및 원상복구 완료!")
                st.rerun()

    elif nav == "adm_settle":
        st.subheader("🔄 월말 결산 (리셋 & 이월)")
        cpct = get_opt_int("monthly_carryover_pct", 60)
        st.warning(f"⚠️ 실행 시 활동중 부방 포인트가 0P로 리셋되고 기존 포인트의 **{cpct}%(내림)**가 휴가로 이월됩니다.")
        if st.button("🚀 월말 결산 실행", type="primary", use_container_width=True):
            conn = get_conn()
            now = now_kst().strftime("%Y-%m-%d %H:%M:%S")
            for m in conn.execute("SELECT * FROM members WHERE status='활동중'").fetchall():
                if m["points"] > 0:
                    cv = math.floor(m["points"] * (cpct / 100.0))
                    rg = m["grade"] if m["grade"] in ["MP", "MS"] else "M1"
                    conn.execute("UPDATE members SET points=0, grade=?, vacation_days=vacation_days+? WHERE id=?", (rg, cv, m["id"]))
                    conn.execute("INSERT INTO logs (member_id, season, week, log_type, category, minutes, points_delta, vacation_delta, memo, created_at) VALUES (?,'시즌결산','결산주차','월간결산','포인트리셋/휴가이월',0,?,?,?,?)", (m["id"], -m["points"], cv, f"월말 결산: {m['points']}P ➔ {cpct}%({cv}일) 이월", now))
                    st.write(f"- [{m['grade']}] {m['nickname']}: {m['points']}P ➔ 휴가 +{cv}일")
            conn.commit()
            conn.close()
            send_phone_push("📊 월말 결산 완료", f"포인트 리셋 및 {cpct}% 휴가 이월이 완료되었습니다.")
            st.success("월말 결산 완료!")

    elif nav == "adm_db":
        st.subheader("💾 DB 백업 및 복원 (회원·기록 보존)")
        if is_postgres_configured():
            st.success("🟢 현재 **Supabase(PostgreSQL)** 외부 DB 연결이 설정되어 있어, Streamlit 앱이 재시작되거나 자다 깨도 데이터가 삭제되지 않습니다.")
        else:
            st.info("Streamlit Cloud에서 GitHub 코드를 새로 덮어쓰기 전에 아래 버튼으로 `manager.db`를 내려받아 두시면, 업데이트 후 업로드 한 번으로 모든 회원가입 아이디·점수·기록을 그대로 복원할 수 있습니다.")
            if os.path.exists(DB_NAME):
                with open(DB_NAME, "rb") as f:
                    st.download_button(
                        label="📥 현재 manager.db 전체 백업 다운로드",
                        data=f.read(),
                        file_name=f"manager_backup_{today_kst().strftime('%Y%m%d')}.db",
                        mime="application/octet-stream",
                        type="primary",
                        use_container_width=True,
                    )
            st.divider()
            up_db = st.file_uploader("📤 백업해 둔 manager.db 파일 업로드(복원)", type=["db"])
            if up_db is not None:
                if st.button("♻️ 업로드한 DB로 즉시 복원하기", use_container_width=True):
                    with open(DB_NAME, "wb") as f:
                        f.write(up_db.getvalue())
                    st.success("DB 복원 완료! 새로고침하면 기존 회원과 기록이 모두 복구됩니다.")
                    st.rerun()