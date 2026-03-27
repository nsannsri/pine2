import ccxt
import pandas as pd
from datetime import datetime

# Connect to Binance Futures
exchange = ccxt.binanceusdm({
    'enableRateLimit': True,
})

SYMBOL = 'SOLUSDT'
TIMEFRAME = '15m'  # Change to '5m', '15m', etc. if needed
LIMIT = 1500
POINTS = 2.0           # USD move size to detect
LOOKAHEAD_CANDLES = 5  # how many candles ahead to check for cross-candle move

print(f"Fetching last {LIMIT} candles for {SYMBOL} (Perpetual Futures)...")

# Fetch OHLCV data
ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=LIMIT)

df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
df['time'] = pd.to_datetime(df['timestamp'], unit='ms')

print(f"Fetched {len(df)} candles from {df['time'].iloc[0]} to {df['time'].iloc[-1]}\n")

closes = df['close'].values
highs  = df['high'].values
lows   = df['low'].values
times  = df['time'].values

# ─── SINGLE CANDLE ────────────────────────────────────────────────────────────
long_single  = []
short_single = []

for _, row in df.iterrows():
    move = row['high'] - row['low']
    if move >= POINTS:
        t = row['time'].strftime('%Y-%m-%d %H:%M:%S')
        long_single.append({
            'time': t, 'start_price': round(row['low'], 4),
            'end_price': round(row['high'], 4), 'move': round(move, 4),
        })
        short_single.append({
            'time': t, 'start_price': round(row['high'], 4),
            'end_price': round(row['low'], 4), 'move': round(move, 4),
        })

# ─── CROSS-CANDLE LONGS (close → future high) ─────────────────────────────────
long_cross = []
for i in range(len(df)):
    start_price = closes[i]
    for j in range(i + 1, min(i + LOOKAHEAD_CANDLES + 1, len(df))):
        if highs[j] - start_price >= POINTS:
            long_cross.append({
                'start_time':  pd.Timestamp(times[i]).strftime('%Y-%m-%d %H:%M:%S'),
                'start_price': round(start_price, 4),
                'peak_time':   pd.Timestamp(times[j]).strftime('%Y-%m-%d %H:%M:%S'),
                'end_price':   round(highs[j], 4),
                'move':        round(highs[j] - start_price, 4),
            })
            break

# ─── CROSS-CANDLE SHORTS (close → future low) ─────────────────────────────────
short_cross = []
for i in range(len(df)):
    start_price = closes[i]
    for j in range(i + 1, min(i + LOOKAHEAD_CANDLES + 1, len(df))):
        if start_price - lows[j] >= POINTS:
            short_cross.append({
                'start_time':  pd.Timestamp(times[i]).strftime('%Y-%m-%d %H:%M:%S'),
                'start_price': round(start_price, 4),
                'peak_time':   pd.Timestamp(times[j]).strftime('%Y-%m-%d %H:%M:%S'),
                'end_price':   round(lows[j], 4),
                'move':        round(start_price - lows[j], 4),
            })
            break

# ─── PRINT RESULTS ────────────────────────────────────────────────────────────

print("=" * 75)
print(f"LONG  — SINGLE CANDLE  (high - low >= +{POINTS})")
print("=" * 75)
for r in long_single:
    print(f"  {r['time']}  |  {r['start_price']} -> {r['end_price']}  |  +{r['move']}")
print(f"  Total: {len(long_single)}" if long_single else "  None found.")

print()
print("=" * 75)
print(f"SHORT — SINGLE CANDLE  (high - low >= -{POINTS})")
print("=" * 75)
for r in short_single:
    print(f"  {r['time']}  |  {r['start_price']} -> {r['end_price']}  |  -{r['move']}")
print(f"  Total: {len(short_single)}" if short_single else "  None found.")

print()
print("=" * 75)
print(f"LONG  — CROSS-CANDLE   close -> future high >= +{POINTS}  (within {LOOKAHEAD_CANDLES} candles)")
print("=" * 75)
for r in long_cross:
    print(f"  Start: {r['start_time']} @ {r['start_price']}  ->  Peak: {r['peak_time']} @ {r['end_price']}  |  +{r['move']}")
print(f"  Total: {len(long_cross)}" if long_cross else "  None found.")

print()
print("=" * 75)
print(f"SHORT — CROSS-CANDLE   close -> future low <= -{POINTS}  (within {LOOKAHEAD_CANDLES} candles)")
print("=" * 75)
for r in short_cross:
    print(f"  Start: {r['start_time']} @ {r['start_price']}  ->  Low:  {r['peak_time']} @ {r['end_price']}  |  -{r['move']}")
print(f"  Total: {len(short_cross)}" if short_cross else "  None found.")
