"""
Admin Commands — คำสั่งที่ Wirote (แอดมิน) พิมพ์คุยกับ Bot Lovely เพื่อดึงข้อมูล OPD/สรุปยอด
=================================================================================
ทำงานเฉพาะ user_id == ADMIN_LINE_ID เท่านั้น (เช็คใน chatbot_server.py ก่อนเรียกไฟล์นี้)
แยกเป็นโมดูลต่างหากจาก Q&A flow ของลูกค้าโดยสิ้นเชิง — ไม่มีทางกระทบลูกค้า เพราะ
handle_admin_command() ถูกเรียกเฉพาะตอน user_id ตรงกับแอดมิน และคืน None ถ้าไม่ตรง
คำสั่งใดๆ (fall-through ไป flow ปกติ)

คำสั่งที่รองรับ:
  ประวัติ <HN หรือชื่อสัตว์/เจ้าของ>   → ประวัติ OPD ของสัตว์ตัวนั้น (5 ครั้งล่าสุด)
  ค้นหา <คำค้น>                        → ค้นหา OPD ทั้งหมด (5 รายการล่าสุดที่ตรง)
  สรุปวันนี้                            → ยอด OPD + รายรับวันนี้ (ตรงรายงานเงินสด DRX)
  คำสั่ง / help                         → รายการคำสั่งทั้งหมด

ใช้ opd_db.py (ชั้น query กลางเดียวกับ dashboard) — ข้อมูลจาก local SQLite (drx_opd.db)
อ่านอย่างเดียว ไม่มีการเขียน/แก้ไขข้อมูลใดๆ
"""
import hashlib
import hmac
import os
import re
import time
from pathlib import Path

_DB_FILE = Path(__file__).parent / "drx_opd.db"


def _sign_picture_token(opd_picture_id: int, expires: int) -> str:
    key = os.environ.get("INTERNAL_API_KEY", "dogcatlovely_internal_2026")
    msg = f"{opd_picture_id}:{expires}".encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()[:16]


def _make_picture_url(opd_picture_id: int) -> str | None:
    """สร้างลิงก์รูปแบบ public ให้ LINE ดึงไปแสดงได้ (ผ่าน ngrok tunnel ของเครื่อง local เสมอ
    ไม่ว่าจะรันจากเครื่องนี้เองหรือถูก proxy มาจาก Railway) เซ็น HMAC+เวลาหมดอายุ (24 ชม.)
    กันคนอื่นเดา opd_picture_id (เลขเรียงต่อกัน) ไล่ดูรูปคนไข้รายอื่น"""
    base = os.environ.get("LOCAL_API_URL", "").rstrip("/")
    if not base:
        return None
    expires = int(time.time()) + 86400
    sig = _sign_picture_token(opd_picture_id, expires)
    return f"{base}/opd_image/{opd_picture_id}?t={expires}&sig={sig}"


def _has_local_data() -> bool:
    """มีฐาน SQLite อยู่เครื่องนี้ไหม — บน Railway ไม่มี (drx_opd.db ถูก gitignore ไว้
    เพราะเป็นข้อมูลผู้ป่วยจริง ไม่ควรขึ้น cloud)"""
    return _DB_FILE.exists()


def _remote_call(text: str) -> dict | None:
    """บน Railway: ยิงกลับมาที่เครื่อง local ผ่าน ngrok เพื่อ query ข้อมูล
    (รูปแบบเดียวกับ _update_local_queue ใน chatbot_server.py ที่ใช้อยู่แล้ว)
    ข้อมูลผู้ป่วยจึงไม่ต้องขึ้น cloud — อยู่ที่เครื่องคลินิกที่เดียว"""
    local_url = os.environ.get("LOCAL_API_URL", "").rstrip("/")
    api_key = os.environ.get("INTERNAL_API_KEY", "dogcatlovely_internal_2026")
    if not local_url:
        return {"text": "⚠️ ยังไม่ได้ตั้งค่า LOCAL_API_URL — ดึงข้อมูลจากเครื่องคลินิกไม่ได้ค่ะ"}
    try:
        import requests
        r = requests.post(
            f"{local_url}/api/admin_cmd",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={"msg": text},
            timeout=20,
        )
        if r.status_code != 200:
            return {"text": f"⚠️ เครื่องคลินิกตอบกลับ {r.status_code} — ลองใหม่อีกครั้งค่ะ"}
        return (r.json() or {}).get("reply")
    except Exception as e:
        return {"text": f"⚠️ ต่อเครื่องคลินิกไม่ได้ (เปิดเครื่อง/ngrok อยู่ไหม?)\n{e}"}


HELP_TEXT = (
    "📋 คำสั่งแอดมิน (Bot Lovely)\n"
    "━━━━━━━━━━━━━━━━━\n"
    "ประวัติ <HN/ชื่อ> — ดูประวัติ OPD ของสัตว์\n"
    "ค้นหา <คำค้น> — ค้นหา OPD ทั้งหมด\n"
    "สรุปวันนี้ — ยอด OPD/รายรับวันนี้"
)


def _fmt_money(n) -> str:
    return f"฿{round(n or 0):,}"


def handle_admin_command(text: str, allow_remote: bool = True) -> dict | None:
    """คืน {'text': ...} ถ้าข้อความตรงกับคำสั่งแอดมิน, None ถ้าไม่ตรง (ให้ fall-through ปกติ)

    allow_remote=True (default): ถ้าเครื่องนี้ไม่มีฐานข้อมูล (= รันบน Railway) จะยิงกลับไป
    ถามเครื่อง local ผ่าน ngrok — ตั้ง False ตอนถูกเรียกจาก /api/admin_cmd เพื่อกัน loop
    """
    t = (text or "").strip()
    if not t:
        return None

    # help ตอบได้เองทุกที่ ไม่ต้องใช้ข้อมูล
    if t in ("คำสั่ง", "help", "Help", "HELP", "ช่วยเหลือ", "แอดมิน"):
        return {"text": HELP_TEXT}

    # คำสั่งที่ต้องใช้ข้อมูล — เช็คว่าตรงคำสั่งก่อน แล้วค่อยตัดสินใจว่า query เองหรือ proxy
    is_data_cmd = (
        t in ("สรุปวันนี้", "opd วันนี้", "OPD วันนี้", "สรุปopd", "สรุป OPD")
        or re.match(r"^ประวัติ\s+(.+)$", t)
        or re.match(r"^ค้นหา\s+(.+)$", t)
    )
    if not is_data_cmd:
        # "ประวัติ" เปล่าๆ ไม่มี HN ตามหลัง — บอกวิธีใช้ (กันงงว่าทำไมพิมพ์แล้วเงียบ)
        if t in ("ประวัติ", "ค้นหา"):
            return {"text": f"พิมพ์แบบนี้ค่ะ:\n{t} <HN หรือชื่อสัตว์>\nเช่น  {t} 690114-1"}
        return None

    if not _has_local_data():
        return _remote_call(t) if allow_remote else {"text": "⚠️ ไม่พบฐานข้อมูลบนเครื่องนี้ค่ะ"}

    if t in ("สรุปวันนี้", "opd วันนี้", "OPD วันนี้", "สรุปopd", "สรุป OPD"):
        return _summary_today()

    m = re.match(r"^ประวัติ\s+(.+)$", t)
    if m:
        return _pet_history(m.group(1).strip())

    m = re.match(r"^ค้นหา\s+(.+)$", t)
    if m:
        return _search_opd(m.group(1).strip())

    return None


def _summary_today() -> dict:
    try:
        import opd_db
        rows = opd_db.daily_series(1)
        if not rows:
            return {"text": "ยังไม่มีข้อมูลวันนี้ค่ะ"}
        r = rows[-1]
        text = (
            f"📊 สรุปวันนี้ ({r['date']})\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🐾 OPD: {r['visits']} visit\n"
            f"🧾 ใบเสร็จ: {r['receipt_count']} ใบ\n"
            f"💰 รายรับสุทธิ: {_fmt_money(r['total_revenue'])}\n"
            f"   • บริการ {_fmt_money(r['bill_service'])}\n"
            f"   • มัดจำ {_fmt_money(r['bill_deposit'])}\n"
            f"   • คืนเงิน -{_fmt_money(r['bill_refund'])}\n"
            f"   • เงินสด {_fmt_money(r['bill_cash'])} / โอน {_fmt_money(r['bill_transfer'])}"
        )
        return {"text": text}
    except Exception as e:
        return {"text": f"⚠️ ดึงข้อมูลไม่สำเร็จ: {e}"}


def _pet_history(query: str) -> dict:
    try:
        import opd_db
        with opd_db._connect() as conn:
            # 1) HN ตรงเป๊ะ  2) HN ขึ้นต้นด้วยคำค้น (เผื่อพิมพ์แค่ base ไม่ใส่ "-1")  3) ชื่อสัตว์/เจ้าของ
            rows = conn.execute(
                "SELECT DISTINCT hn, petname, owner_name FROM opd_full WHERE hn = ? ORDER BY hn", (query,)
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    "SELECT DISTINCT hn, petname, owner_name FROM opd_full WHERE hn LIKE ? ORDER BY hn LIMIT 6",
                    (query + "%",),
                ).fetchall()
            if not rows:
                like = f"%{query}%"
                rows = conn.execute(
                    "SELECT DISTINCT hn, petname, owner_name FROM opd_full "
                    "WHERE petname LIKE ? OR owner_name LIKE ? ORDER BY hn LIMIT 6",
                    (like, like),
                ).fetchall()

        if not rows:
            return {"text": f"ไม่พบข้อมูลสำหรับ \"{query}\" ค่ะ"}
        if len(rows) > 1:
            lines = [f"• {r['hn']} — {r['petname']} (เจ้าของ: {r['owner_name']})" for r in rows]
            return {"text": "พบหลายรายการ พิมพ์ HN ให้ชัดเจนค่ะ:\n" + "\n".join(lines)}

        hn = rows[0]["hn"]
        history = opd_db.get_pet_history(hn)
        if not history:
            return {"text": f"ไม่พบประวัติ OPD ของ HN {hn} ค่ะ"}

        latest = history[-1]
        lines = [
            f"📋 ประวัติ {latest['petname']} (HN {hn})",
            f"เจ้าของ: {latest['owner_name']}",
            "━━━━━━━━━━━━━━━━━",
        ]
        recent = history[-5:][::-1]
        for i, v in enumerate(recent):
            date = (v["opd_datetime"] or "")[:16].replace("T", " ")
            dx = v["dx"] or v["final_diagnosis"] or v["major_problem"] or "-"
            if i == 0:
                # visit ล่าสุด — ขยายรายละเอียดการวินิจฉัยให้ครบกว่ารายการเก่า
                cc = v["chief_complaint"] or "-"
                tx = v["treatment"] or "-"
                block = f"🗓️ {date} (ล่าสุด)\n   อาการที่มา: {cc}\n   วินิจฉัย: {dx}"
                if v["final_diagnosis"] and v["final_diagnosis"] != dx:
                    block += f"\n   วินิจฉัยสุดท้าย: {v['final_diagnosis']}"
                block += f"\n   การรักษา: {tx}\n   ยอด: {_fmt_money(v['total_amount'])}"
                lines.append(block)
            else:
                lines.append(f"🗓️ {date}\n   วินิจฉัย: {dx}\n   ยอด: {_fmt_money(v['total_amount'])}")
        lines.append(f"━━━━━━━━━━━━━━━━━\nรวม {len(history)} visit ทั้งหมด")

        result = {"text": "\n".join(lines)}
        # แนบรูปเฉพาะ visit ล่าสุดเท่านั้น (ไม่เอารูปทุก visit — ข้อความจะยาวเกินไป)
        try:
            pic_ids = opd_db.get_opd_picture_ids(latest["opd_id"])
            images = [u for pid in pic_ids[:4] if (u := _make_picture_url(pid))]
            if images:
                result["images"] = images
        except Exception:
            pass
        return result
    except Exception as e:
        return {"text": f"⚠️ ดึงข้อมูลไม่สำเร็จ: {e}"}


def _search_opd(keyword: str) -> dict:
    try:
        import opd_db
        rows = opd_db.search_opd(keyword=keyword, limit=5)
        if not rows:
            return {"text": f"ไม่พบ OPD ที่ตรงกับ \"{keyword}\" ค่ะ"}
        lines = [f"🔍 ค้นหา \"{keyword}\" — พบ {len(rows)} รายการล่าสุด", "━━━━━━━━━━━━━━━━━"]
        for r in rows:
            date = (r["opd_datetime"] or "")[:10]
            dx = r["dx"] or r["final_diagnosis"] or r["major_problem"] or "-"
            lines.append(f"🗓️ {date} — {r['petname']} (HN {r['hn']})\n   {dx} | {_fmt_money(r['total_amount'])}")
        return {"text": "\n".join(lines)}
    except Exception as e:
        return {"text": f"⚠️ ค้นหาไม่สำเร็จ: {e}"}


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "สรุปวันนี้"
    result = handle_admin_command(q)
    print(result["text"] if result else "(ไม่ตรงคำสั่งใดๆ — fall-through ปกติ)")
