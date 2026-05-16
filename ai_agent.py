"""
AI Agent — Claude-powered Q&A matcher
======================================
ใช้ Claude Haiku 3.5 เพื่อจับคู่คำถามผู้ใช้กับ Q&A ใน Excel

- รับ user_msg + qa_list
- ส่งไปให้ Claude ดู (เป็น context)
- Claude เลือก qa_id ที่ตรง + confidence + ปรับคำตอบให้เข้ากับคำถาม
- ถ้าไม่ตรง → ส่งต่อพนักงาน

ENV VARS:
  ANTHROPIC_API_KEY  (required)
  AI_MODEL           (default: claude-haiku-4-5)
  AI_ENABLED         (default: true)
"""
import os
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# โหลด .env (local dev)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
AI_MODEL          = os.environ.get("AI_MODEL", "claude-haiku-4-5").strip()
AI_ENABLED        = os.environ.get("AI_ENABLED", "true").lower() != "false"

# Singleton client
_client = None
def _get_client():
    global _client
    if _client is None:
        try:
            import anthropic
            _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        except ImportError:
            log.error("anthropic package not installed")
            return None
    return _client


SYSTEM_PROMPT = """คุณคือ AI ผู้ช่วยของโรงพยาบาลสัตว์ "Dog and Cat Lovely" — รพ.ส.หมาแมวเลิฟลี่

หน้าที่: จับคู่คำถามลูกค้ากับ Q&A ใน knowledge base ด้านล่าง

หลักการตอบ:
1. ใช้ข้อมูลจาก knowledge base เป็นหลัก (อย่าสร้างราคา/บริการที่ไม่มีในฐานข้อมูล)
2. คำตอบสามารถปรับสำนวนให้เข้ากับคำถาม ให้เป็นกันเอง สุภาพ (ใช้ "ค่ะ")
3. รักษาราคา ตัวเลข เงื่อนไข ให้ตรงตามฐานข้อมูล 100%
4. ถ้าคำถามไม่ตรงกับ Q&A ใดเลย หรือไม่แน่ใจ → confidence = "low" และไม่ต้องสร้างคำตอบ

Output: ตอบเป็น JSON เท่านั้น รูปแบบ:
{
  "qa_id": <id ของ Q&A ที่ตรงที่สุด หรือ 0 ถ้าไม่มี>,
  "confidence": "high" | "medium" | "low",
  "answer": "<คำตอบที่ปรับสำนวนแล้ว หรือ empty string ถ้า confidence ต่ำ>"
}

ห้ามเพิ่มข้อความใดๆ นอกเหนือจาก JSON"""


def build_kb_text(qa_list: list) -> str:
    """สร้าง knowledge base text จาก qa_list"""
    lines = []
    for qa in qa_list:
        # ตัด answer ให้สั้นลงเล็กน้อย เพื่อประหยัด token
        q = qa["question"][:120]
        a = qa["answer"][:300]
        lines.append(f"[{qa['id']}] Q: {q}\n    A: {a}")
    return "\n".join(lines)


def match_qa(user_msg: str, qa_list: list) -> dict | None:
    """
    เรียก Claude เพื่อจับคู่ user_msg กับ qa_list
    Return: {"qa_id": int, "confidence": str, "answer": str} หรือ None ถ้า AI ใช้ไม่ได้
    """
    if not AI_ENABLED:
        log.info("[AI] disabled by env var")
        return None
    if not ANTHROPIC_API_KEY:
        log.warning("[AI] ANTHROPIC_API_KEY not set")
        return None

    client = _get_client()
    if client is None:
        return None

    kb = build_kb_text(qa_list)
    user_prompt = f"Knowledge base:\n{kb}\n\nคำถามจากลูกค้า: {user_msg!r}"

    try:
        response = client.messages.create(
            model=AI_MODEL,
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        log.info(f"[AI] raw: {text[:200]}")

        # parse JSON (ตัด markdown code fences ถ้ามี)
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        data = json.loads(text)
        return {
            "qa_id":      int(data.get("qa_id", 0)),
            "confidence": data.get("confidence", "low"),
            "answer":     str(data.get("answer", "")).strip(),
        }
    except Exception as e:
        log.exception(f"[AI] match_qa error: {e}")
        return None


if __name__ == "__main__":
    # ทดสอบ
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    logging.basicConfig(level=logging.INFO)
    import qa_database as qa

    tests = [
        "หมาตัวเมียน้ำหนัก 8 โล อยากทำหมัน ราคาประมาณเท่าไหร่",
        "วัคซีนลูกหมา ต้องฉีดอะไรบ้าง",
        "ผ่าคลอดแมวกี่บาท",
        "เปิดกี่โมง",
        "อาหารแมวยี่ห้อไหนดี",  # นอก kb
    ]
    for t in tests:
        print(f"\n=== Query: {t!r} ===")
        result = match_qa(t, qa.QA_LIST)
        print(json.dumps(result, ensure_ascii=False, indent=2) if result else "None")
