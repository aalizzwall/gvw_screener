#!/usr/bin/env python3
import json
import sys

# Read the all_rank.json file
with open('/home/pi/.hermes/gvw_screener/all_rank.json', 'r', encoding='utf-8') as f:
    content = f.read()

# Parse as JSON
data = json.loads(content)

# Check structure
print(f"Top-level type: {type(data)}")
print(f"Length of 'data' array: {len(data['data'])}")
print(f"\nFirst entry (decoded):")
print(json.dumps(data['data'][0], indent=2, ensure_ascii=False))

# Find last_update_time
last_update = data.get('last_update_time', 'NOT FOUND')
print(f"\n=== Metadata ===")
print(f"last_update_time: {last_update}")
print(f"total_stocks_saved: {data.get('total_stocks_saved')}")
print(f"earliest_data_date: {data.get('earliest_data_date')}")

# Check if there are stocks with high scores (>0.8)
high_score_stocks = [s for s in data['data'] if s['final_score'] > 0.8]
print(f"\n=== High Conviction Stocks (final_score > 0.8) ===")
print(f"Count: {len(high_score_stocks)}")
for s in high_score_stocks[:10]:
    print(f"  - {s['name']} (ticker: {s['ticker']}) | Score: {s['final_score']:.4f} | Signal: {s['entry_signal']}")