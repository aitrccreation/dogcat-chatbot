"""
Admin push — สลับไปบอทสำรองอัตโนมัติเมื่อบอทหลักโควตาเต็ม
==========================================================
บอทแจ้งเตือน admin (Lovely Bot) อยู่แผนฟรี 300 ข้อความ/เดือน ซึ่งใช้หมดก่อนสิ้นเดือน
มาแล้ว (ส.ค. 2569) ทำให้รายงานหยุดเงียบ ๆ โดยไม่มีใครรู้

วิธีสลับ: ส่งด้วยบอทหลักก่อนเสมอ ถ้า LINE ตอบ 429 "monthly limit" ค่อยส่งซ้ำ
ด้วยบอทสำรองทันที — ไม่ต้องเช็คโควตาล่วงหน้าทุกครั้ง (เปลืองเวลา/โควตา API)
และไม่เสียโควตาบอทหลักที่ยังเหลือไปเปล่า ๆ

ตั้งค่าใน .env (และบน Railway):
    LOVELY_BOT_TOKEN   = บอทหลัก
    LINE_TARGET_ID     = user ID ของ Wirote "สำหรับบอทหลัก"
    LOVELY_BOT2_TOKEN  = บอทสำรอง
    LINE_TARGET_ID2    = user ID ของ Wirote "สำหรับบอทสำรอง"

หมายเหตุสำคัญ: LINE user ID ผูกกับ channel — คนเดียวกันคนละบอทจะได้ ID ไม่เหมือนกัน
จึงต้องตั้ง LINE_TARGET_ID2 แยก (ดูได้จาก Basic settings → Your user ID ของบอทสำรอง)
"""
import os
from pathlib import Path

import requests

# โหลด .env (local dev) — บน Railway ใช้ env vars ของ platform อยู่แล้ว
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def _bots() -> list[tuple[str, str, str]]:
    """คืน [(ชื่อ, token, target_id), ...] เรียงตามลำดับที่จะลองใช้"""
    out = []
    primary = (os.environ.get("LOVELY_BOT_TOKEN") or "").strip()
    primary_id = (os.environ.get("LINE_TARGET_ID") or "").strip()
    if primary and primary_id:
        out.append(("Lovely Bot", primary, primary_id))

    backup = (os.environ.get("LOVELY_BOT2_TOKEN") or "").strip()
    # ถ้าไม่ได้ตั้ง target แยก ให้ใช้ตัวเดิม (กรณีบอทสำรองอยู่ channel เดียวกัน)
    backup_id = (os.environ.get("LINE_TARGET_ID2") or "").strip() or primary_id
    if backup and backup_id:
        out.append(("Lovely Bot 2", backup, backup_id))
    return out


def _is_quota_exhausted(resp) -> bool:
    return resp.status_code == 429 and "monthly limit" in resp.text.lower()


def send_admin_push(messages: list[dict], timeout: int = 15) -> bool:
    """ส่ง push หา admin — ลองบอทหลักก่อน โควตาเต็มค่อยสลับไปสำรอง
    messages: LINE messages array เช่น [{"type": "text", "text": "..."}]
    """
    bots = _bots()
    if not bots:
        print("  [admin-push] ไม่ได้ตั้ง LOVELY_BOT_TOKEN / LINE_TARGET_ID — ข้าม")
        return False

    last_err = ""
    for name, token, target_id in bots:
        try:
            r = requests.post(
                LINE_PUSH_URL,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json={"to": target_id, "messages": messages},
                timeout=timeout,
            )
        except Exception as e:
            last_err = f"{name}: {type(e).__name__}: {e}"
            print(f"  [admin-push] {last_err}")
            continue

        if r.status_code == 200:
            return True

        last_err = f"{name}: HTTP {r.status_code} {r.text[:150]}"
        if _is_quota_exhausted(r):
            print(f"  [admin-push] {name} โควตาเต็มเดือนนี้ — สลับไปบอทถัดไป")
            continue   # ลองบอทถัดไป
        print(f"  [admin-push] {last_err}")
        break          # error อื่น (token ผิด/ไม่ได้เป็นเพื่อน) — สลับบอทไม่ช่วย

    print(f"  [admin-push] ส่งไม่สำเร็จ — {last_err}")
    return False


def send_admin_text(text: str) -> bool:
    """ทางลัดสำหรับข้อความ text ธรรมดา (ตัดที่ 4900 ตัวตามลิมิต LINE)"""
    return send_admin_push([{"type": "text", "text": text[:4900]}])


def quota_status() -> list[dict]:
    """เช็คโควตาของทุกบอท — ใช้ตอนตรวจสอบ/รายงาน ไม่ได้ใช้ตอนส่ง"""
    out = []
    for name, token, _ in _bots():
        row = {"name": name}
        try:
            h = {"Authorization": f"Bearer {token}"}
            q = requests.get("https://api.line.me/v2/bot/message/quota",
                             headers=h, timeout=10).json()
            c = requests.get("https://api.line.me/v2/bot/message/quota/consumption",
                             headers=h, timeout=10).json()
            row["limit"] = q.get("value")
            row["used"] = c.get("totalUsage")
        except Exception as e:
            row["error"] = str(e)[:100]
        out.append(row)
    return out


if __name__ == "__main__":
    for b in quota_status():
        if "error" in b:
            print(f"{b['name']}: ERROR {b['error']}")
        else:
            used, lim = b["used"], b["limit"]
            pct = f"{used / lim * 100:.0f}%" if lim else "?"
            print(f"{b['name']}: {used}/{lim} ({pct})")
