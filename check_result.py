import json

all_rank_path = "/home/pi/.hermes/gvw_screener/all_rank.json"
with open(all_rank_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

print("=" * 50)
print("Execution Result Report")
print("=" * 50)
print(f"✅ all_rank.json generated: YES")
print(f"last_update_time: {data.get('last_update_time', 'NOT FOUND')}")
print(f"Top-level keys: {list(data.keys())}")
print(f"Total items in data: {len(data)}")

# Count high conviction stocks
high_conv = 0
if 'data' in data:
    stocks = data['data']
    total = len(stocks)
    if isinstance(stocks, list):
        for s in stocks:
            if s.get('final_score', 0) > 0.8:
                high_conv += 1
else:
    print("\n⚠️ Warning: Could not extract stocks from data structure")

print(f"\n⭐ High conviction stocks (>0.8): {high_conv}")
print("=" * 50)
