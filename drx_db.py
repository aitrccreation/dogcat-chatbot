"""
DRX Local DB — read-only connector ไปยัง MySQL ของระบบ DRX บนเครื่องนี้ (localhost:3306)
================================================================================
นี่คือฐานข้อมูลจริงที่ระบบ DoctorDogs ใช้งานอยู่ (drxvet_db_5_0_dogandcat)
ต่างจาก drx_bridge.py ที่ scrape หน้าเว็บ — โมดูลนี้อ่านตรงจาก DB จึงได้ข้อมูล
"ทุก OPD" ครบทุกประวัติ ไม่ถูกจำกัดจำนวนแถวแบบหน้า dashboard

ใช้เพื่อ "สำรวจ/วิเคราะห์" ข้อมูลเท่านั้น — ห้ามใช้ query เขียน (INSERT/UPDATE/DELETE)
เด็ดขาด เพราะเป็นฐานข้อมูล production ที่ระบบคลินิกใช้งานจริงพร้อมกัน

ใช้งาน:
    from drx_db import fetch_all, fetch_one

    rows = fetch_all("SELECT * FROM opd WHERE opd_datetime >= %s", (since,))
"""
import os
from pathlib import Path
from contextlib import contextmanager

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import pymysql
import pymysql.cursors

DB_HOST = os.environ.get("DRX_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DRX_DB_PORT", "3306"))
DB_NAME = os.environ.get("DRX_DB_NAME", "drxvet_db_5_0_dogandcat")
DB_USER = os.environ.get("DRX_DB_USER", "root")
DB_PASSWORD = os.environ.get("DRX_DB_PASSWORD", "")


class ReadOnlyQueryError(Exception):
    pass


def _assert_select_only(sql: str):
    """กันเผลอรัน query เขียนใส่ฐาน production — ยอมให้เฉพาะ SELECT/SHOW/DESCRIBE/EXPLAIN"""
    head = sql.strip().split(None, 1)[0].lower() if sql.strip() else ""
    if head not in ("select", "show", "describe", "desc", "explain"):
        raise ReadOnlyQueryError(f"อนุญาตเฉพาะ read-only query เท่านั้น ('{head}' ไม่อนุญาต)")


@contextmanager
def get_connection():
    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        read_timeout=30,
        connect_timeout=10,
    )
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(sql: str, params=None) -> list[dict]:
    _assert_select_only(sql)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()


def fetch_one(sql: str, params=None) -> dict | None:
    _assert_select_only(sql)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()


def test_connection() -> bool:
    try:
        row = fetch_one("SELECT 1 AS ok")
        return bool(row and row.get("ok") == 1)
    except Exception as e:
        print(f"[drx_db] เชื่อมต่อ DB ไม่สำเร็จ: {e}")
        return False


if __name__ == "__main__":
    print(f"กำลังเชื่อมต่อ {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME} ...")
    if test_connection():
        print("เชื่อมต่อสำเร็จ (read-only)")
    else:
        print("เชื่อมต่อไม่สำเร็จ — ตรวจ .env (DRX_DB_*) และ MySQL56 service")
