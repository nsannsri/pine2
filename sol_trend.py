import requests

SYMBOL = "SOLUSDT"
INTERVAL = "15m"
LIMIT = 100  # enough candles for EMA calculation

def fetch_klines(symbol, interval, limit):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params)
    r.raise_for_status()
    data = r.json()
    closes = [float(c[4]) for c in data]
    highs  = [float(c[2]) for c in data]
    lows   = [float(c[3]) for c in data]
    return closes, highs, lows

def ema(values, period):
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def detect_trend(closes, highs, lows):
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    e20 = ema20[-1]
    e50 = ema50[-1]
    price = closes[-1]

    # Higher highs / higher lows check over last 3 swing points
    recent_highs = highs[-6:]
    recent_lows  = lows[-6:]
    hh = recent_highs[-1] > recent_highs[0]
    hl = recent_lows[-1]  > recent_lows[0]
    lh = recent_highs[-1] < recent_highs[0]
    ll = recent_lows[-1]  < recent_lows[0]

    score = 0
    # EMA alignment
    if e20 > e50:
        score += 1
    else:
        score -= 1

    # Price above/below EMAs
    if price > e20 and price > e50:
        score += 1
    elif price < e20 and price < e50:
        score -= 1

    # Structure
    if hh and hl:
        score += 1
    elif lh and ll:
        score -= 1

    # EMA slope (last 3 values)
    if ema20[-1] > ema20[-3] and ema50[-1] > ema50[-3]:
        score += 1
    elif ema20[-1] < ema20[-3] and ema50[-1] < ema50[-3]:
        score -= 1

    return score

def main():
    closes, highs, lows = fetch_klines(SYMBOL, INTERVAL, LIMIT)
    score = detect_trend(closes, highs, lows)

    price = closes[-1]
    print(f"\nSOL/USDT  |  15m Trend Analysis")
    print(f"Current Price : {price:.4f}")
    print(f"Trend Score   : {score:+d} / 4")

    if score >= 2:
        print(f"Trend         : UPTREND")
    elif score <= -2:
        print(f"Trend         : DOWNTREND")
    else:
        print(f"Trend         : SIDEWAYS / NO CLEAR TREND")

    print()

if __name__ == "__main__":
    main()
