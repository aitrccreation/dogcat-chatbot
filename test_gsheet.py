"""Quick test for gsheet_db connection"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from dotenv import load_dotenv
load_dotenv()
import gsheet_db

print("=== Test Google Sheet Connection ===")
if not gsheet_db.is_enabled():
    print("FAIL - cannot connect")
    sys.exit(1)
print("OK - connected")
print()

print("=== Existing customers ===")
customers = gsheet_db.get_all_customers()
print("Total: %d" % len(customers))
for c in customers:
    uid = str(c.get("line_user_id", ""))[:25]
    print("  uid=%s... hn=%s owner=%s" % (uid, c.get("hn"), c.get("owner_name")))

print()
print("=== Test write (test row) ===")
result = gsheet_db.register_customer(
    line_user_id="test_gsheet_user",
    hn="999999-9",
    owner_name="GSheet Test",
    pet_name="TestPet",
)
print("Result:", result)
