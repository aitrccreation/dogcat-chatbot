"""Check after Railway runs all 3 jobs"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from dotenv import load_dotenv
load_dotenv()
import gsheet_db
from collections import Counter

ws_q = gsheet_db._get_queue()
records = ws_q.get_all_records()
print("Total queue rows: %d" % len(records))
print()

# Group by appt_date
print("=== Queue by date ===")
dates = Counter(r.get("appt_date","") for r in records)
for d, n in sorted(dates.items()):
    print("  %s: %d rows" % (d, n))
print()

# Find HN 690001-1 in DRX
print("=== DRX rows for HN 690001-1 ===")
ws_drx = gsheet_db._get_drx()
drx = ws_drx.get_all_records()
for r in drx:
    if r.get("hn") == "690001-1":
        ad = r.get("appt_date")
        print("  date=%s service=%s" % (ad, r.get("service","")[:50]))
print()

# Check sent
print("=== Sent (sent_round_1_at) ===")
for r in records:
    if r.get("sent_round_1_at"):
        print("  qid=%s date=%s hn=%s status=%s" % (
            r.get("queue_id"), r.get("appt_date"), r.get("hn"), r.get("status")))

# Check log for recent send activity
print()
print("=== Log (last 10) ===")
ws_log = gsheet_db._get_log()
log_records = ws_log.get_all_records()
for r in log_records[-10:]:
    print("  %s | %s | %s" % (r.get("timestamp",""), r.get("event",""), r.get("detail","")[:60]))
