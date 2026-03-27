"""
SOL Basis Arbitrage Monitor + Trader
Binance USD-M Futures: Perpetual vs Quarterly

Usage:
    pip install requests python-binance

Set your API keys in environment variables:
    set BINANCE_API_KEY=your_key
    set BINANCE_API_SECRET=your_secret
"""

import os
import time
import requests
from datetime import datetime
from binance.client import Client
from binance.enums import *

# --- Config ---
API_KEY    = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")

PERP_SYMBOL    = "SOLUSDT"          # Perpetual
# Find the active quarterly symbol below (auto-detected)
BASIS_THRESHOLD = 0.5               # % basis to trigger alert/trade
TRADE_QTY       = 1.0               # SOL quantity per trade

FAPI_BASE = "https://fapi.binance.com"

# ---------------------------------------------------------------
# 1. FIND ACTIVE QUARTERLY FUTURES SYMBOL
# ---------------------------------------------------------------
def get_quarterly_symbol():
    """Returns the nearest quarterly futures symbol, e.g. SOLUSDT_250328"""
    url = f"{FAPI_BASE}/fapi/v1/exchangeInfo"
    data = requests.get(url).json()

    sol_syms = [s for s in data["symbols"] if s["baseAsset"] == "SOL" and s["quoteAsset"] == "USDT"]
    print("Available SOL/USDT symbols on Binance Futures:")
    for s in sol_syms:
        print(f"  {s['symbol']:30s}  contractType={s['contractType']}")

    candidates = [
        s for s in sol_syms
        if s["contractType"] in ("CURRENT_QUARTER", "NEXT_QUARTER")
    ]
    candidates.sort(key=lambda x: x["deliveryDate"])
    if candidates:
        sym = candidates[0]["symbol"]
        expiry_ms = candidates[0]["deliveryDate"]
        expiry_dt = datetime.utcfromtimestamp(expiry_ms / 1000)
        days_left = max((expiry_dt - datetime.utcnow()).days, 1)
        return sym, days_left
    raise ValueError("No quarterly SOL futures found — Binance may not list them currently")


# ---------------------------------------------------------------
# 2. FETCH PRICES
# ---------------------------------------------------------------
def get_price(symbol: str) -> float:
    url = f"{FAPI_BASE}/fapi/v1/ticker/price?symbol={symbol}"
    return float(requests.get(url).json()["price"])


def get_funding_rate() -> float:
    """Current predicted funding rate for the perp"""
    url = f"{FAPI_BASE}/fapi/v1/premiumIndex?symbol={PERP_SYMBOL}"
    data = requests.get(url).json()
    return float(data["lastFundingRate"]) * 100  # as %


# ---------------------------------------------------------------
# 3. CALCULATE BASIS
# ---------------------------------------------------------------
def calc_basis(perp_price: float, fut_price: float, days_left: int):
    basis_abs = fut_price - perp_price
    basis_pct = (basis_abs / perp_price) * 100
    annualized = basis_pct * (365 / days_left) if days_left > 0 else 0
    return basis_abs, basis_pct, annualized


# ---------------------------------------------------------------
# 4. PLACE ORDER ON PERP
# ---------------------------------------------------------------
def place_perp_order(client: Client, side: str, qty: float):
    """
    side: 'BUY' or 'SELL'
    Uses MARKET order on perpetual
    """
    order = client.futures_create_order(
        symbol=PERP_SYMBOL,
        side=side,
        type=ORDER_TYPE_MARKET,
        quantity=qty,
    )
    print(f"  ORDER PLACED: {side} {qty} {PERP_SYMBOL}")
    print(f"  Order ID: {order['orderId']} | Status: {order['status']}")
    return order


# ---------------------------------------------------------------
# 5. MONITOR LOOP
# ---------------------------------------------------------------
def monitor(trade=False):
    client = Client(API_KEY, API_SECRET) if trade else None

    print("Fetching quarterly symbol...")
    fut_symbol, days_left = get_quarterly_symbol()
    print(f"Quarterly: {fut_symbol} | Days to expiry: {days_left}\n")
    print(f"{'Time':<20} {'Perp':>10} {'Futures':>10} {'Basis$':>10} {'Basis%':>8} {'Ann%':>8} {'Fund%':>8}")
    print("-" * 80)

    while True:
        try:
            perp_price = get_price(PERP_SYMBOL)
            fut_price  = get_price(fut_symbol)
            funding    = get_funding_rate()

            basis_abs, basis_pct, ann_pct = calc_basis(perp_price, fut_price, days_left)
            now = datetime.utcnow().strftime("%H:%M:%S")

            print(f"{now:<20} {perp_price:>10.4f} {fut_price:>10.4f} "
                  f"{basis_abs:>+10.4f} {basis_pct:>+8.3f}% {ann_pct:>+8.2f}% {funding:>+8.4f}%")

            # --- Trade Logic ---
            if trade and abs(basis_pct) >= BASIS_THRESHOLD:
                if basis_pct > 0:
                    # Futures premium: SHORT perp, LONG futures (manual)
                    print(f"  >> BASIS HIGH ({basis_pct:+.3f}%): Consider SHORT perp + LONG futures")
                    # place_perp_order(client, "SELL", TRADE_QTY)  # uncomment to auto-trade
                else:
                    # Futures discount: LONG perp, SHORT futures (manual)
                    print(f"  >> BASIS LOW ({basis_pct:+.3f}%): Consider LONG perp + SHORT futures")
                    # place_perp_order(client, "BUY", TRADE_QTY)   # uncomment to auto-trade

        except Exception as e:
            print(f"Error: {e}")

        time.sleep(5)  # poll every 5 seconds


# ---------------------------------------------------------------
if __name__ == "__main__":
    # Set trade=True to enable order placement (uncomment auto-trade lines too)
    monitor(trade=False)
