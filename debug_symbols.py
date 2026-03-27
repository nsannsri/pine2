import requests

data = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo").json()

sol = [s for s in data["symbols"] if "SOL" in s["symbol"]]
print(f"Found {len(sol)} SOL symbols:\n")
for s in sol:
    print(f"  {s['symbol']:30s}  contractType={s['contractType']}")
