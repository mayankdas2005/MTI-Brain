import json

with open("output/join_key_profile.json") as f:
    data = json.load(f)

profiles = data["profiles"]

def label(p):
    return f"{p['from_table']}.{p['from_col']}  <->  {p['to_table']}.{p['to_col']}"

for verdict in ["safe", "caution", "dangerous"]:
    print(f"\n=== {verdict.upper()} examples ===")
    items = [p for p in profiles if p["verdict"] == verdict]
    for p in items[:5]:
        cs = p.get("cross_stats", {})
        print(f"  {label(p)}")
        print(f"    score={p.get('relationship_score',0):.2f}  fanout={p.get('fanout_risk','?')}  reason={p.get('verdict_reason','?')}")

# Check specific known joins
print("\n=== KNOWN JOIN CHECKS ===")
for p in profiles:
    lb = label(p)
    if "company.code" in lb and "ach_return" in lb:
        print(f"  {lb}")
        print(f"    verdict={p['verdict']}  score={p.get('relationship_score',0):.2f}  fanout={p.get('fanout_risk','?')}")
        print(f"    reason={p.get('verdict_reason','?')}")
    if "forecast_cash_flow" in lb and "account_ref" in lb and "cash_balance" in lb:
        print(f"  {lb}")
        print(f"    verdict={p['verdict']}  score={p.get('relationship_score',0):.2f}  fanout={p.get('fanout_risk','?')}")
        print(f"    reason={p.get('verdict_reason','?')}")

# Fanout risk distribution
from collections import Counter
fanout_dist = Counter(p.get("fanout_risk", "?") for p in profiles)
print(f"\n=== FANOUT RISK DISTRIBUTION ===")
for k, v in sorted(fanout_dist.items()):
    print(f"  {k}: {v}")

# Source distribution - how many from each source
src_counter = Counter()
for p in profiles:
    for s in p.get("sources", []):
        src_counter[s] += 1
print(f"\n=== SOURCE DISTRIBUTION ===")
for k, v in sorted(src_counter.items()):
    print(f"  {k}: {v}")

# Verdict by source
print(f"\n=== VERDICT BY SOURCE ===")
for src in ["yaml", "joins_to", "join_path"]:
    verdicts = Counter(p["verdict"] for p in profiles if src in p.get("sources", []))
    print(f"  {src}: {dict(verdicts)}")
