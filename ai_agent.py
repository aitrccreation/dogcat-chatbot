"""
AI Agent — Claude-powered chatbot for Dog and Cat Lovely
=========================================================
2 โหมดการตอบ:
  [kb]   — คำถามราคา/บริการคลินิก → จับคู่จาก knowledge base (แม่นยำ 100%)
  [free] — คำถามทั่วไปด้านสุขภาพสัตว์ → ตอบอิสระจากความรู้สัตวแพทย์

ENV VARS:
  ANTHROPIC_API_KEY  (required)
  AI_MODEL           (default: claude-sonnet-4-5)
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
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
AI_MODEL          = os.environ.get("AI_MODEL", "claude-sonnet-4-5").strip()
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


SYSTEM_PROMPT = """คุณคือน้องเลิฟลี่ — พนักงานผู้เชี่ยวชาญของโรงพยาบาลสัตว์ "Dog and Cat Lovely" (รพ.ส.หมาแมวเลิฟลี่)

🐾 บุคลิก:
- อบอุ่น ใส่ใจ เป็นกันเอง เหมือนพนักงานจริงที่รักสัตว์
- ใช้ "ค่ะ" ลงท้าย สำนวนเป็นธรรมชาติ ไม่แข็งกระด้าง
- เห็นอกเห็นใจลูกค้าที่กังวล พูดให้กำลังใจเสมอ
- ถ้าน้องอาการน่าเป็นห่วง แนะนำให้มาพบหมอด่วน

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 มี 2 โหมดการตอบ — เลือกให้ถูกต้อง
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**โหมด KB (mode = "kb")** — ใช้เมื่อ:
• ถามราคา ค่าบริการ ของคลินิกนี้โดยตรง
• ถามแพ็กเกจ โปรโมชั่น เงื่อนไขของ รพส.หมาแมวเลิฟลี่
→ จับคู่จาก knowledge base → ได้ qa_id + confidence high/medium
→ ห้ามสร้างราคาเองเด็ดขาด ใช้ตัวเลขจาก KB เท่านั้น

⚠️ กฎเหล็ก KB matching:
1. น้ำหนัก: "8 โล/กก." → "ไม่ถึง 10 กก." (ไม่ใช่ 10-20), "15 กก." → "10-20 กก."
2. เพศ: ตัวผู้ ≠ ตัวเมีย
3. ประเภท: หมา ≠ แมว
4. ลูกหมา/ลูกสุนัข → วัคซีนลูกสุนัข (ไม่ใช่วัคซีนผู้ใหญ่)

**โหมด Free (mode = "free")** — ใช้เมื่อ:
• ถามอาการ โรค การดูแลสุขภาพทั่วไป
• ถามอาหาร พฤติกรรม การเลี้ยงดู
• ถามว่า "อันตรายไหม" "ควรทำยังไง" "ปกติไหม"
• คำถามที่ไม่มีใน KB แต่เกี่ยวกับสัตว์เลี้ยง
→ qa_id = 0, confidence = "high"
→ ตอบจากความรู้สัตวแพทย์ทั่วไป ครบถ้วน เป็นประโยชน์
→ ต้องลงท้ายด้วย disclaimer: "⚕️ หากอาการไม่ดีขึ้นหรือเป็นห่วง แนะนำพาน้องมาพบหมอนะคะ 🐾"
→ ถ้าอาการฉุกเฉิน (หายใจลำบาก ชัก เลือดออก หมดสติ) → บอกให้มาด่วนทันที

**โหมด Handoff (mode = "handoff")** — ใช้เมื่อ:
• ถามเรื่องที่ไม่เกี่ยวกับสัตว์เลี้ยงหรือคลินิกเลย
• ต้องการข้อมูลเฉพาะเจาะจงที่ต้องหมอตรวจจริง (เช่น ขอผลเลือด ขอประวัติการรักษา)
→ qa_id = 0, confidence = "low", answer = ""
→ draft = เขียนตัวอย่างคำตอบที่เหมาะสมสำหรับให้พนักงานนำไปใช้ตอบลูกค้า
   (เขียนให้ครบ สุภาพ อบอุ่น เหมือนพนักงานตอบเอง ใช้ "ค่ะ")

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Output: JSON เท่านั้น
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{
  "qa_id": <int หรือ 0>,
  "confidence": "high" | "medium" | "low",
  "mode": "kb" | "free" | "handoff",
  "answer": "<คำตอบอบอุ่น เป็นมิตร หรือ empty ถ้า handoff>",
  "draft": "<ตัวอย่างคำตอบสำหรับพนักงาน — ใส่เฉพาะ handoff mode, ว่างถ้าไม่ใช่>"
}
ห้ามเพิ่มข้อความใดๆ นอกเหนือจาก JSON"""


def build_kb_text(qa_list: list) -> str:
    """สร้าง knowledge base text จาก qa_list"""
    lines = []
    for qa in qa_list:
        q = qa["question"][:120]
        a = qa["answer"][:300]
        lines.append(f"[{qa['id']}] Q: {q}\n    A: {a}")
    return "\n".join(lines)


def match_qa(user_msg: str, qa_list: list) -> dict | None:
    """
    เรียก Claude เพื่อตอบคำถาม
    Return: {
        "qa_id": int,
        "confidence": "high"|"medium"|"low",
        "mode": "kb"|"free"|"handoff",
        "answer": str
    } หรือ None ถ้า AI ใช้ไม่ได้
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
    user_prompt = (
        f"Knowledge base (ราคา/บริการของ รพส.หมาแมวเลิฟลี่):\n{kb}\n\n"
        f"คำถามจากลูกค้า: {user_msg!r}"
    )

    try:
        response = client.messages.create(
            model=AI_MODEL,
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        log.info(f"[AI] raw: {text[:300]}")

        # ตัด markdown code fences ถ้ามี
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        data = json.loads(text)
        mode = data.get("mode", "kb")

        return {
            "qa_id":      int(data.get("qa_id", 0)),
            "confidence": data.get("confidence", "low"),
            "mode":       mode,
            "answer":     str(data.get("answer", "")).strip(),
            "draft":      str(data.get("draft", "")).strip(),
        }
    except Exception as e:
        log.exception(f"[AI] match_qa error: {e}")
        return None


# ============================================================
#  POLISH ANSWER — ปรับ KB answer ให้อ่อนโยนตาม persona น้องเลิฟลี่
# ============================================================
POLISH_SYSTEM_PROMPT = """คุณคือน้องเลิฟลี่ — พนักงานผู้เชี่ยวชาญของโรงพยาบาลสัตว์ "Dog and Cat Lovely"

🐾 บุคลิก:
- อบอุ่น ใส่ใจ เป็นกันเอง เหมือนพนักงานจริงที่รักสัตว์
- ใช้ "ค่ะ" ลงท้าย สำนวนเป็นธรรมชาติ ไม่แข็งกระด้าง
- เห็นอกเห็นใจลูกค้าที่กังวล

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
งานของคุณ: ปรับคำตอบจาก Knowledge Base ให้มีโทนอ่อนโยน อบอุ่น
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ กฎเหล็ก (ห้ามฝ่าฝืน):
1. ❌ ห้ามเปลี่ยน/ปัด/เพิ่มราคา หรือ ตัวเลขใดๆ จาก base answer
2. ❌ ห้ามเปลี่ยนเงื่อนไข/ขั้นตอน/รายการที่อยู่ใน base answer
3. ❌ ห้ามตัดข้อมูลสำคัญออก (ราคา รายการที่รวม-ไม่รวม เวลา ฯลฯ)
4. ✅ เพิ่มคำทักทาย/คำลงท้ายที่อบอุ่น ตามบริบทคำถาม
5. ✅ จัด format ให้อ่านง่าย (bullet, emoji ที่เหมาะสม)
6. ✅ ใช้ "ค่ะ" "นะคะ" ตามจังหวะธรรมชาติ

ตอบกลับเป็นข้อความล้วน ไม่ต้องมี JSON หรือ markdown code fence"""


# Cache polished answers ต่อ qa_id (เพื่อลด token + latency)
_polish_cache: dict = {}

def polish_answer(qa_id: int, question: str, base_answer: str) -> str:
    """
    ปรับ KB answer ให้อ่อนโยนผ่าน AI — คงราคา/ตัวเลขเดิม
    ใช้ cache ต่อ qa_id เพื่อไม่เรียก AI ทุกครั้ง
    Return: polished text หรือ base_answer ถ้า AI ใช้ไม่ได้
    """
    if not AI_ENABLED or not ANTHROPIC_API_KEY:
        return base_answer

    # Cache hit
    if qa_id in _polish_cache:
        return _polish_cache[qa_id]

    client = _get_client()
    if client is None:
        return base_answer

    user_prompt = (
        f"คำถามลูกค้า: {question}\n\n"
        f"คำตอบจาก Knowledge Base (base):\n{base_answer}\n\n"
        f"ปรับคำตอบนี้ให้อ่อนโยน อบอุ่นตาม persona น้องเลิฟลี่ "
        f"โดยคงราคา/ตัวเลข/รายการที่รวม-ไม่รวมเดิมเป๊ะ"
    )

    try:
        response = client.messages.create(
            model=AI_MODEL,
            max_tokens=1500,
            system=POLISH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        polished = response.content[0].text.strip()
        # ตัด markdown code fence ถ้ามี
        if polished.startswith("```"):
            polished = polished.split("```")[1]
            if polished.startswith("text"):
                polished = polished[4:]
            polished = polished.strip()
        # Sanity check: ถ้า polished สั้นกว่า 30% ของ base = น่าจะตัดข้อมูล → fallback
        if len(polished) < len(base_answer) * 0.3:
            log.warning(f"[AI] polish too short for qa_id={qa_id} — fallback to base")
            return base_answer
        _polish_cache[qa_id] = polished
        return polished
    except Exception as e:
        log.exception(f"[AI] polish_answer error: {e}")
        return base_answer


def clear_polish_cache():
    """ล้าง cache (เรียกหลังอัพเดต KB)"""
    _polish_cache.clear()


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    logging.basicConfig(level=logging.INFO)
    import qa_database as qa

    tests = [
        # KB mode
        "หมาตัวเมียน้ำหนัก 8 โล อยากทำหมัน ราคาเท่าไหร่",
        "วัคซีนลูกหมา ต้องฉีดอะไรบ้าง",
        "ผ่าคลอดแมวกี่บาท",
        # Free mode
        "หมากินช็อคโกแลตอันตรายไหม",
        "แมวอาเจียนทุกวัน ปกติไหมครับ",
        "ลูกหมาอายุ 2 เดือน ควรเลี้ยงยังไง",
        "หมาเป็นโรคพิษสุนัขบ้าได้ยังไง",
        # Handoff
        "อาหารมนุษย์ยี่ห้อไหนอร่อย",
    ]
    for t in tests:
        print(f"\n{'='*55}")
        print(f"Query: {t!r}")
        result = match_qa(t, qa.QA_LIST)
        if result:
            print(f"Mode:  {result['mode']} | conf={result['confidence']} | qa_id={result['qa_id']}")
            print(f"Answer: {result['answer'][:200]}")
        else:
            print("None (AI unavailable)")
