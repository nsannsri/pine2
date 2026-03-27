"""
SOL Funding Rate Arbitrage — Binance
Strategy: Long SOL spot + Short SOLUSDT perp → collect funding

Funding pays every 8 hours (00:00, 08:00, 16:00 UTC).
Enter when funding is high positive → you earn as the short side.
Exit when funding drops near zero or goes negative.

Setup:
    pip install requests python-binance
    set BINANCE_API_KEY=your_key
    set BINANCE_API_SECRET=your_secret
"""

import os
import time
import requests
from datetime import datetime, timezone
from binance.client import Client
from binance.enums import *

# --- Config ---
API_KEY    = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")

SPOT_SYMBOL  = "SOLUSDT"
PERP_SYMBOL  = "SOLUSDT"
TRADE_QTY    = 1.0          # SOL per trade

# Thresholds
ENTRY_FUNDING = 0.01        # % — enter if funding >= this (0.01% per 8h = ~10.95% APR)
EXIT_FUNDING  = 0.0         # % — exit if funding drops to 0 or negative

SPOT_BASE  = "https://api.binance.com"
FAPI_BASE  = "https://fapi.binance.com"

# ---------------------------------------------------------------
# FETCH DATA
# ---------------------------------------------------------------
def get_spot_price() -> float:
    url = f"{SPOT_BASE}/api/v3/ticker/price?symbol={SPOT_SYMBOL}"
    return float(requests.get(url).json()["price"])

def get_perp_price() -> float:
    url = f"{FAPI_BASE}/fapi/v1/ticker/price?symbol={PERP_SYMBOL}"
    return float(requests.get(url).json()["price"])

def get_funding_info() -> dict:
    """Returns current funding rate and next funding time"""
    url = f"{FAPI_BASE}/fapi/v1/premiumIndex?symbol={PERP_SYMBOL}"
    d = requests.get(url).json()
    funding_rate     = float(d["lastFundingRate"]) * 100       # as %
    next_funding_ms  = int(d["nextFundingTime"])
    next_funding_dt  = datetime.fromtimestamp(next_funding_ms / 1000, tz=timezone.utc)
    mins_until       = int((next_funding_dt - datetime.now(timezone.utc)).total_seconds() / 60)
    return {
        "rate": funding_rate,
        "next_funding_dt": next_funding_dt,
        "mins_until": mins_until,
    }

def get_funding_history(limit=10) -> list:
    """Last N funding rate payments"""
    url = f"{FAPI_BASE}/fapi/v1/fundingRate?symbol={PERP_SYMBOL}&limit={limit}"
    rows = requests.get(url).json()
    return [
        {
            "time": datetime.utcfromtimestamp(r["fundingTime"] / 1000).strftime("%m-%d %H:%M"),
            "rate": float(r["fundingRate"]) * 100,
        }
        for r in rows
    ]

def annualized(rate_pct: float) -> float:
    """Funding is paid 3x per day"""
    return rate_pct * 3 * 365

# ---------------------------------------------------------------
# ORDERS
# ---------------------------------------------------------------
def buy_spot(client: Client, qty: float):
    """Market buy SOL on spot"""
    order = client.order_market_buy(symbol=SPOT_SYMBOL, quantity=qty)
    print(f"  SPOT BUY  {qty} SOL | OrderId={order['orderId']} status={order['status']}")
    return order

def short_perp(client: Client, qty: float):
    """Market short SOLUSDT perp"""
    order = client.futures_create_order(
        symbol=PERP_SYMBOL,
        side=SIDE_SELL,
        type=ORDER_TYPE_MARKET,
        quantity=qty,
    )
    print(f"  PERP SHORT {qty} SOL | OrderId={order['orderId']} status={order['status']}")
    return order

def sell_spot(client: Client, qty: float):
    """Market sell SOL on spot (exit)"""
    order = client.order_market_sell(symbol=SPOT_SYMBOL, quantity=qty)
    print(f"  SPOT SELL {qty} SOL | OrderId={order['orderId']} status={order['status']}")
    return order

def close_perp_short(client: Client, qty: float):
    """Buy back perp short (exit)"""
    order = client.futures_create_order(
        symbol=PERP_SYMBOL,
        side=SIDE_BUY,
        type=ORDER_TYPE_MARKET,
        quantity=qty,
        reduceOnly=True,
    )
    print(f"  PERP CLOSE {qty} SOL | OrderId={order['orderId']} status={order['status']}")
    return order

# ---------------------------------------------------------------
# MONITOR LOOP
# ---------------------------------------------------------------
def monitor(trade=False):
    client = Client(API_KEY, API_SECRET) if trade else None

    print("\n=== SOL Funding Rate Arb Monitor ===")
    print(f"Entry threshold : funding >= {ENTRY_FUNDING}%")
    print(f"Exit threshold  : funding <= {EXIT_FUNDING}%")
    print(f"Trade qty       : {TRADE_QTY} SOL\n")

    print("Last 10 funding rates:")
    for h in get_funding_history():
        ann = annualized(h["rate"])
        flag = " <--" if h["rate"] > ENTRY_FUNDING else ""
        print(f"  {h['time']}  rate={h['rate']:+.4f}%  annualized={ann:+.2f}%{flag}")
    print()

    in_trade = False

    print(f"{'Time (UTC)':<12} {'Spot':>10} {'Perp':>10} {'Spread':>9} {'Funding%':>10} {'Ann%':>9} {'Next(min)':>10} {'State':>10}")
    print("-" * 90)

    while True:
        try:
            spot   = get_spot_price()
            perp   = get_perp_price()
            spread = perp - spot
            fi     = get_funding_info()
            rate   = fi["rate"]
            ann    = annualized(rate)
            now    = datetime.utcnow().strftime("%H:%M:%S")
            state  = "IN TRADE" if in_trade else "watching"

            print(f"{now:<12} {spot:>10.4f} {perp:>10.4f} {spread:>+9.4f} "
                  f"{rate:>+10.4f}% {ann:>+9.2f}% {fi['mins_until']:>10} {state:>10}")

            if trade:
                if not in_trade and rate >= ENTRY_FUNDING:
                    print(f"\n  >> ENTRY: funding={rate:+.4f}% >= threshold={ENTRY_FUNDING}%")
                    # buy_spot(client, TRADE_QTY)      # uncomment to live trade
                    # short_perp(client, TRADE_QTY)    # uncomment to live trade
                    in_trade = True

                elif in_trade and rate <= EXIT_FUNDING:
                    print(f"\n  >> EXIT: funding={rate:+.4f}% <= threshold={EXIT_FUNDING}%")
                    # sell_spot(client, TRADE_QTY)       # uncomment to live trade
                    # close_perp_short(client, TRADE_QTY) # uncomment to live trade
                    in_trade = False

        except Exception as e:
            print(f"Error: {e}")

        time.sleep(10)


# ---------------------------------------------------------------
if __name__ == "__main__":
    monitor(trade=False)
