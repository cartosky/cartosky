from herbie import Herbie
import pandas as pd
from datetime import datetime, timedelta

# Try today's date instead of future date 2026
run_time = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
fccs = [0, 3, 6, 9, 12, 18, 24]
model = 'aifs'
product = 'oper'

results = []

print(f"Testing with run_time: {run_time}")

for fxx in fccs:
    try:
        H = Herbie(run_time, model=model, product=product, fxx=fxx, priority=['azure', 'paws', 'google', 'aws'])
        idx = H.inventory()
        if idx is not None and not idx.empty:
            results.append((fxx, "SUCCESS", f"Found {len(idx)} rows"))
        else:
            results.append((fxx, "FAIL", "Inventory empty"))
    except Exception as e:
        results.append((fxx, "FAIL", "Not found or error"))

print("\nSummary of Herbie Probe for AIFS Oper (Recent Date):")
for fxx, status, msg in results:
    print(f"FXX {fxx:03d}: {status} - {msg}")
