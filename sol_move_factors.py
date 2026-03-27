import ccxt
import pandas as pd
import numpy as np
from datetime import datetime

# ─── CONFIG ───────────────────────────────────────────────────
SYMBOL           = 'SOLUSDT'
TIMEFRAME        = '15m'
LIMIT            = 1500
POINTS           = 2.0
LOOKAHEAD_CANDLES = 5

exchange = ccxt.binanceusdm({'enableRateLimit': True})

# ─── FETCH DATA ───────────────────────────────────────────────
print(f"Fetching {LIMIT} candles for {SYMBOL}...")
ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=LIMIT)
df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
df['time'] = pd.to_datetime(df['timestamp'], unit='ms')

# ─── INDICATORS ───────────────────────────────────────────────

def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

def calc_macd_hist(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd     = ema_fast - ema_slow
    sig      = macd.ewm(span=signal, adjust=False).mean()
    return macd - sig

df['rsi']       = calc_rsi(df['close'], 14)
df['macd_hist'] = calc_macd_hist(df['close'])
df['vol_ma20']  = df['volume'].rolling(20).mean()
df['vol_ratio'] = df['volume'] / df['vol_ma20']    # >1 = above avg volume
df['ema21']     = df['close'].ewm(span=21, adjust=False).mean()
df['ema55']     = df['close'].ewm(span=55, adjust=False).mean()
df['above_ema21'] = df['close'] > df['ema21']
df['above_ema55'] = df['close'] > df['ema55']
df['candle_bull'] = df['close'] > df['open']
df['hour']      = df['time'].dt.hour

# ─── FIND CROSS-CANDLE LONG MOVES ─────────────────────────────
closes = df['close'].values
highs  = df['high'].values
lows   = df['low'].values
times  = df['time'].values

records_long  = []
records_short = []

for i in range(20, len(df) - LOOKAHEAD_CANDLES):  # start at 20 to have indicator warmup
    row = df.iloc[i]
    start_price = closes[i]

    # Check long move
    for j in range(i + 1, min(i + LOOKAHEAD_CANDLES + 1, len(df))):
        if highs[j] - start_price >= POINTS:
            records_long.append({
                'trigger_time' : row['time'].strftime('%Y-%m-%d %H:%M'),
                'start_price'  : round(start_price, 4),
                'end_price'    : round(highs[j], 4),
                'move'         : round(highs[j] - start_price, 4),
                'rsi'          : round(row['rsi'], 1),
                'macd_hist'    : round(row['macd_hist'], 4),
                'vol_ratio'    : round(row['vol_ratio'], 2),
                'above_ema21'  : row['above_ema21'],
                'above_ema55'  : row['above_ema55'],
                'candle_bull'  : row['candle_bull'],
                'hour'         : row['hour'],
            })
            break

    # Check short move
    for j in range(i + 1, min(i + LOOKAHEAD_CANDLES + 1, len(df))):
        if start_price - lows[j] >= POINTS:
            records_short.append({
                'trigger_time' : row['time'].strftime('%Y-%m-%d %H:%M'),
                'start_price'  : round(start_price, 4),
                'end_price'    : round(lows[j], 4),
                'move'         : round(start_price - lows[j], 4),
                'rsi'          : round(row['rsi'], 1),
                'macd_hist'    : round(row['macd_hist'], 4),
                'vol_ratio'    : round(row['vol_ratio'], 2),
                'above_ema21'  : row['above_ema21'],
                'above_ema55'  : row['above_ema55'],
                'candle_bull'  : row['candle_bull'],
                'hour'         : row['hour'],
            })
            break

# ─── PRINT RESULTS ────────────────────────────────────────────

def print_table(records, label):
    if not records:
        print(f"  No {label} moves found.\n")
        return

    df_r = pd.DataFrame(records)
    print(f"\n{'='*90}")
    print(f"{label} MOVES — {len(df_r)} occurrences")
    print(f"{'='*90}")
    print(f"{'Time':<18} {'Start':>8} {'End':>8} {'Move':>6} {'RSI':>6} {'MACD_H':>8} {'VolRatio':>9} {'EMA21':>6} {'EMA55':>6} {'Bull':>5} {'Hr':>4}")
    print("-"*90)
    for r in records:
        print(f"  {r['trigger_time']:<16} {r['start_price']:>8} {r['end_price']:>8} "
              f"{r['move']:>+6.2f} {r['rsi']:>6.1f} {r['macd_hist']:>8.4f} "
              f"{r['vol_ratio']:>9.2f}x "
              f"{'Y' if r['above_ema21'] else 'N':>6} "
              f"{'Y' if r['above_ema55'] else 'N':>6} "
              f"{'Y' if r['candle_bull'] else 'N':>5} "
              f"{r['hour']:>4}")

    # ─── AVERAGES (what conditions are typical before a move) ─
    print(f"\n  --- AVERAGE CONDITIONS BEFORE {label} MOVE ---")
    print(f"  RSI avg          : {df_r['rsi'].mean():.1f}  (min={df_r['rsi'].min():.1f}, max={df_r['rsi'].max():.1f})")
    print(f"  MACD hist avg    : {df_r['macd_hist'].mean():.4f}")
    print(f"  Vol ratio avg    : {df_r['vol_ratio'].mean():.2f}x  (above avg vol = >1.0x)")
    print(f"  Above EMA21      : {df_r['above_ema21'].mean()*100:.1f}% of moves")
    print(f"  Above EMA55      : {df_r['above_ema55'].mean()*100:.1f}% of moves")
    print(f"  Bullish candle   : {df_r['candle_bull'].mean()*100:.1f}% of trigger candles")

    # RSI distribution
    print(f"\n  --- RSI DISTRIBUTION ---")
    bins = [(0,30,'Oversold'), (30,50,'Neutral-Bear'), (50,70,'Neutral-Bull'), (70,100,'Overbought')]
    for lo, hi, name in bins:
        count = ((df_r['rsi'] >= lo) & (df_r['rsi'] < hi)).sum()
        pct   = count / len(df_r) * 100
        bar   = '█' * int(pct / 2)
        print(f"  {name:<15} ({lo}-{hi}): {count:>4} ({pct:>5.1f}%)  {bar}")

    # Volume distribution
    print(f"\n  --- VOLUME RATIO DISTRIBUTION ---")
    vbins = [(0,0.5,'Very Low'), (0.5,1.0,'Below Avg'), (1.0,1.5,'Above Avg'), (1.5,99,'Spike')]
    for lo, hi, name in vbins:
        count = ((df_r['vol_ratio'] >= lo) & (df_r['vol_ratio'] < hi)).sum()
        pct   = count / len(df_r) * 100
        bar   = '█' * int(pct / 2)
        print(f"  {name:<12} ({lo:.1f}-{hi if hi<99 else '∞'}x): {count:>4} ({pct:>5.1f}%)  {bar}")

    # Best hours
    print(f"\n  --- TOP HOURS (UTC) ---")
    hour_counts = df_r['hour'].value_counts().head(5)
    for hr, cnt in hour_counts.items():
        print(f"  {hr:02d}:00 UTC : {cnt} moves")

print_table(records_long,  "LONG  (+2pt)")
print_table(records_short, "SHORT (-2pt)")
