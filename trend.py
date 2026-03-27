import sys
import requests

def ema(values, period):
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def get_trend(symbol, interval):
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol.upper(), "interval": interval, "limit": 100}
    r = requests.get(url, params=params)
    r.raise_for_status()
    data = r.json()

    closes = [float(c[4]) for c in data]
    highs  = [float(c[2]) for c in data]
    lows   = [float(c[3]) for c in data]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    e20, e50 = ema20[-1], ema50[-1]
    price = closes[-1]

    score = 0

    # EMA alignment
    score += 1 if e20 > e50 else -1

    # Price vs EMAs
    if price > e20 and price > e50:
        score += 1
    elif price < e20 and price < e50:
        score -= 1

    # Market structure
    recent_highs = highs[-6:]
    recent_lows  = lows[-6:]
    if recent_highs[-1] > recent_highs[0] and recent_lows[-1] > recent_lows[0]:
        score += 1
    elif recent_highs[-1] < recent_highs[0] and recent_lows[-1] < recent_lows[0]:
        score -= 1

    # EMA slope
    if ema20[-1] > ema20[-3] and ema50[-1] > ema50[-3]:
        score += 1
    elif ema20[-1] < ema20[-3] and ema50[-1] < ema50[-3]:
        score -= 1

    if score >= 2:
        trend = "UPTREND"
    elif score <= -2:
        trend = "DOWNTREND"
    else:
        trend = "SIDEWAYS"

    return trend, score, price, e20, e50

def main():
    if len(sys.argv) != 3:
        print("Usage: python trend.py <interval> <symbol>")
        print("Example: python trend.py 15m SOLUSDT")
        sys.exit(1)

    interval = sys.argv[1]
    symbol   = sys.argv[2]

    valid_intervals = ['1m','3m','5m','15m','30m','1h','2h','4h','6h','8h','12h','1d']
    if interval not in valid_intervals:
        print(f"Invalid interval: {interval}")
        print(f"Valid options: {', '.join(valid_intervals)}")
        sys.exit(1)

    trend, score, price, e20, e50 = get_trend(symbol, interval)

    print(f"\n{'='*40}")
    print(f"  {symbol.upper()}  |  {interval} Trend Analysis")
    print(f"{'='*40}")
    print(f"  Price     : {price:.4f}")
    print(f"  EMA 20    : {e20:.4f}")
    print(f"  EMA 50    : {e50:.4f}")
    print(f"  Score     : {score:+d} / 4")
    print(f"  Trend     : {trend}")
    print(f"{'='*40}\n")

if __name__ == "__main__":
    main()
