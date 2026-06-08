import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

with open("drx_data.json", encoding="utf-8") as f:
    d = json.load(f)

raw_appts = d["_raw"]["appointments"]
print(f"Total raw appointments: {len(raw_appts)}")

# Find rows with non-empty cell[16]
print("\n=== cell[16] non-empty ===")
found = 0
for r in raw_appts:
    cells = r.get("cell", [])
    c16 = cells[16] if len(cells) > 16 else None
    if c16:
        print(f"  id={r['id']}  [{cells[0]}]  pet={str(cells[5])[:25]}  c14={cells[14]}  c16={repr(c16)}")
        found += 1
print(f"  total: {found}")

# Scan ALL cells for status keywords
print("\n=== Any cell with มาตาม/ยกเลิก/Confirmed ===")
found2 = 0
for r in raw_appts:
    cells = r.get("cell", [])
    for i, c in enumerate(cells):
        if c and any(kw in str(c) for kw in ["มาตาม", "ยกเลิก", "confirmed", "Confirm", "done", "complete"]):
            print(f"  id={r['id']}  cell[{i}]={repr(c)[:40]}  date={cells[0]}")
            found2 += 1
            break
print(f"  total: {found2}")

# Show unique values of cell[14] (possible status code)
print("\n=== Unique cell[14] values ===")
vals = {}
for r in raw_appts:
    cells = r.get("cell", [])
    v = cells[14] if len(cells) > 14 else None
    vals[v] = vals.get(v, 0) + 1
for v, cnt in sorted(vals.items(), key=lambda x: str(x[0])):
    print(f"  cell[14]={repr(v)}  count={cnt}")
