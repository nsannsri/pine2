"""
GEX Data Service - Background worker that fetches GEX + price data every 1 minute.
Stores in SQLite, emits updates via SocketIO.
"""

import os
import sys
import time
import json
import requests
from datetime import datetime, date, timedelta, timezone
from threading import Thread, Event

# Indian Standard Time (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# Add parent dir to path so we can import gex_calculator
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from gex_calculator import run_gex, UNDERLYINGS, ACCESS_TOKEN, CLIENT_ID, BASE_URL, HEADERS
from models import get_session, GexSnapshot, StrikeGex, Price, cleanup_old_data

# Dhan chart API for historical/intraday candles
CHART_URL = "https://api.dhan.co/v2/charts/intraday"
HISTORY_URL = "https://api.dhan.co/v2/charts/historical"

# Market hours (IST)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MIN = 30

# MCX market hours (IST) — 9:00 AM to 11:30 PM
MCX_OPEN_HOUR = 9
MCX_OPEN_MIN = 0
MCX_CLOSE_HOUR = 23
MCX_CLOSE_MIN = 30

# Update interval in seconds
UPDATE_INTERVAL = 60


def now_ist():
    """Get current time in IST."""
    return datetime.now(IST)


def is_trading_day():
    """Check if today is a weekday (Mon-Fri) in IST."""
    return now_ist().weekday() < 5


def is_market_hours(symbol=None):
    """Check if current time is within market hours (IST)."""
    now = now_ist()
    if now.weekday() >= 5:
        return False
    if symbol == "GOLD":
        # MCX hours: 9:00 AM - 11:30 PM IST (Mon-Fri)
        market_open = now.replace(hour=MCX_OPEN_HOUR, minute=MCX_OPEN_MIN, second=0, microsecond=0)
        market_close = now.replace(hour=MCX_CLOSE_HOUR, minute=MCX_CLOSE_MIN, second=0, microsecond=0)
    else:
        # NSE hours: 9:15 AM - 3:30 PM IST (Mon-Fri)
        market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0)
        market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0)
    return market_open <= now <= market_close


def fetch_intraday_candles(symbol, from_date=None, to_date=None):
    """Fetch 1-minute candles from Dhan API.

    Args:
        symbol: "NIFTY" or "BANKNIFTY"
        from_date: start date string "YYYY-MM-DD" (default: today)
        to_date: end date string "YYYY-MM-DD" (default: today)

    Returns:
        list of {timestamp, open, high, low, close, volume} dicts
    """
    scrip = UNDERLYINGS[symbol]["scrip"]
    seg = UNDERLYINGS[symbol]["seg"]

    today = date.today().strftime("%Y-%m-%d")
    from_date = from_date or today
    to_date = to_date or today

    # MCX GOLD uses FUTCOM instrument type
    instrument = "FUTCOM" if seg == "MCX_COMM" else "INDEX"

    payload = {
        "securityId": str(scrip),
        "exchangeSegment": seg,
        "instrument": instrument,
        "interval": "1",
        "fromDate": from_date,
        "toDate": to_date,
    }

    try:
        resp = requests.post(CHART_URL, json=payload, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            print(f"[PRICE] Dhan chart API error {resp.status_code}: {resp.text[:200]}")
            return []

        data = resp.json()
        candles = []

        # Dhan returns: {open: [...], high: [...], low: [...], close: [...], volume: [...], timestamp: [...]}
        timestamps = data.get("timestamp", [])
        opens = data.get("open", [])
        highs = data.get("high", [])
        lows = data.get("low", [])
        closes = data.get("close", [])
        volumes = data.get("volume", [])

        for i in range(len(timestamps)):
            ts = datetime.fromtimestamp(timestamps[i])
            candles.append({
                "timestamp": ts,
                "open": opens[i] if i < len(opens) else 0,
                "high": highs[i] if i < len(highs) else 0,
                "low": lows[i] if i < len(lows) else 0,
                "close": closes[i] if i < len(closes) else 0,
                "volume": volumes[i] if i < len(volumes) else 0,
            })

        return candles
    except Exception as e:
        print(f"[PRICE] Error fetching candles for {symbol}: {e}")
        return []


def store_candles(symbol, candles):
    """Store price candles in DB, skip duplicates."""
    if not candles:
        return 0

    session = get_session()
    stored = 0
    try:
        for c in candles:
            existing = session.query(Price).filter_by(
                symbol=symbol, timestamp=c["timestamp"]
            ).first()
            if not existing:
                session.add(Price(
                    symbol=symbol,
                    timestamp=c["timestamp"],
                    open=c["open"],
                    high=c["high"],
                    low=c["low"],
                    close=c["close"],
                    volume=c["volume"],
                ))
                stored += 1
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"[DB] Error storing candles: {e}")
    finally:
        session.close()
    return stored


def store_gex_snapshot(data):
    """Store GEX snapshot + per-strike data in DB."""
    session = get_session()
    try:
        levels = data["levels"]
        local_flip = levels.get("local_flip", {})
        now = datetime.now().replace(second=0, microsecond=0)

        # Check for duplicate (same symbol + minute)
        existing = session.query(GexSnapshot).filter_by(
            symbol=data["symbol"], timestamp=now
        ).first()
        if existing:
            session.delete(existing)
            session.flush()

        snapshot = GexSnapshot(
            symbol=data["symbol"],
            timestamp=now,
            expiry=data["expiry"],
            spot=data["spot"],
            call_wall=levels.get("call_wall", 0),
            put_wall=levels.get("put_wall", 0),
            hvl=levels.get("hvl", 0),
            local_flip=local_flip.get("strike", 0) if isinstance(local_flip, dict) else 0,
            peak_gamma=levels.get("peak_gamma", 0),
            max_pain=levels.get("max_pain", 0),
            bias=levels.get("bias", "NEUTRAL"),
            bias_score=levels.get("bias_score", 0),
            gamma_condition=levels.get("gamma_condition", "UNKNOWN"),
            gamma_tilt=levels.get("gamma_tilt", 0),
            regime=levels.get("regime", "MIXED"),
            regime_score=levels.get("regime_score", 0),
            net_gex=data["net_gex"],
            call_gex=data["call_gex"],
            put_gex=data["put_gex"],
            em_upper=levels.get("em_upper", 0) or 0,
            em_lower=levels.get("em_lower", 0) or 0,
            em_wk_upper=levels.get("em_wk_upper", 0) or 0,
            em_wk_lower=levels.get("em_wk_lower", 0) or 0,
            atm_iv=levels.get("atm_iv", 0),
            dte=levels.get("dte", 0),
            iv_skew=levels.get("iv_skew", 0),
            skew_signal=levels.get("skew_signal", "NEUTRAL"),
            total_ce_oi_chg=levels.get("total_ce_oi_chg", 0),
            total_pe_oi_chg=levels.get("total_pe_oi_chg", 0),
            net_oi_chg_direction=levels.get("net_oi_chg_direction", "N/A"),
        )

        # XAUUSD fields for GOLD
        xau = data.get("xauusd", {})
        if xau:
            snapshot.xau_spot = xau.get("spot")
            snapshot.usdinr = xau.get("usdinr")
            xau_lvl = xau.get("levels", {})
            snapshot.xau_call_wall = xau_lvl.get("call_wall")
            snapshot.xau_put_wall = xau_lvl.get("put_wall")
            snapshot.xau_peak_gamma = xau_lvl.get("peak_gamma")
            snapshot.xau_max_pain = xau_lvl.get("max_pain")
            snapshot.xau_local_flip = xau_lvl.get("local_flip")
            snapshot.xau_em_upper = xau_lvl.get("em_upper")
            snapshot.xau_em_lower = xau_lvl.get("em_lower")

        # Store top nearby strikes (within 3% of spot)
        spot = data["spot"]
        for s in data.get("strikes", []):
            if abs(s["strike"] - spot) < spot * 0.03:
                snapshot.strikes.append(StrikeGex(
                    strike=s["strike"],
                    ce_oi=s.get("ce_oi", 0),
                    pe_oi=s.get("pe_oi", 0),
                    ce_gex=s.get("ce_gex", 0),
                    pe_gex=s.get("pe_gex", 0),
                    net_gex=s.get("net_gex", 0),
                    ce_iv=s.get("ce_iv", 0),
                    pe_iv=s.get("pe_iv", 0),
                ))

        session.add(snapshot)
        session.commit()
        return snapshot.id
    except Exception as e:
        session.rollback()
        print(f"[DB] Error storing GEX snapshot: {e}")
        return None
    finally:
        session.close()


def snapshot_to_dict(snapshot):
    """Convert GexSnapshot ORM object to JSON-serializable dict."""
    return {
        "symbol": snapshot.symbol,
        "timestamp": snapshot.timestamp.isoformat(),
        "spot": snapshot.spot,
        "expiry": snapshot.expiry,
        "call_wall": snapshot.call_wall,
        "put_wall": snapshot.put_wall,
        "hvl": snapshot.hvl,
        "local_flip": snapshot.local_flip,
        "peak_gamma": snapshot.peak_gamma,
        "max_pain": snapshot.max_pain,
        "bias": snapshot.bias,
        "bias_score": snapshot.bias_score,
        "gamma_condition": snapshot.gamma_condition,
        "gamma_tilt": snapshot.gamma_tilt,
        "regime": snapshot.regime,
        "regime_score": snapshot.regime_score,
        "net_gex": snapshot.net_gex,
        "call_gex": snapshot.call_gex,
        "put_gex": snapshot.put_gex,
        "em_upper": snapshot.em_upper,
        "em_lower": snapshot.em_lower,
        "atm_iv": snapshot.atm_iv,
        "dte": snapshot.dte,
        "iv_skew": snapshot.iv_skew,
        "skew_signal": snapshot.skew_signal,
        "total_ce_oi_chg": snapshot.total_ce_oi_chg,
        "total_pe_oi_chg": snapshot.total_pe_oi_chg,
        "net_oi_chg_direction": snapshot.net_oi_chg_direction,
        # XAUUSD (GOLD only, None for others)
        "xau_spot": snapshot.xau_spot,
        "xau_call_wall": snapshot.xau_call_wall,
        "xau_put_wall": snapshot.xau_put_wall,
        "xau_peak_gamma": snapshot.xau_peak_gamma,
        "xau_max_pain": snapshot.xau_max_pain,
        "xau_local_flip": snapshot.xau_local_flip,
        "xau_em_upper": snapshot.xau_em_upper,
        "xau_em_lower": snapshot.xau_em_lower,
        "usdinr": snapshot.usdinr,
    }


def fetch_and_store(symbol, socketio=None):
    """Fetch GEX data + price candles, store in DB, emit via SocketIO."""
    print(f"\n[{now_ist().strftime('%H:%M:%S')} IST] Fetching {symbol}...")

    # 1. Fetch GEX data
    try:
        gex_data = run_gex(symbol)
        if not gex_data:
            print(f"[ERROR] run_gex returned None for {symbol}")
            return None
    except Exception as e:
        print(f"[ERROR] GEX calculation failed for {symbol}: {e}")
        return None

    # 2. Store GEX snapshot
    snap_id = store_gex_snapshot(gex_data)
    print(f"[DB] GEX snapshot stored (id={snap_id})")

    # 3. Fetch & store latest price candles
    candles = fetch_intraday_candles(symbol)
    stored = store_candles(symbol, candles)
    print(f"[DB] Stored {stored} new candles for {symbol} ({len(candles)} total today)")

    # 4. Emit via SocketIO if available
    if socketio and snap_id:
        session = get_session()
        try:
            snapshot = session.query(GexSnapshot).get(snap_id)
            if snapshot:
                payload = {
                    "symbol": symbol,
                    "snapshot": snapshot_to_dict(snapshot),
                    "latest_candle": None,
                }
                if candles:
                    c = candles[-1]
                    IST_OFFSET = 19800  # 5h30m in seconds
                    payload["latest_candle"] = {
                        "time": (int(c["timestamp"].timestamp()) if isinstance(c["timestamp"], datetime) else c["timestamp"]) + IST_OFFSET,
                        "open": c["open"],
                        "high": c["high"],
                        "low": c["low"],
                        "close": c["close"],
                    }
                # Verify serializable before emit
                json.dumps(payload, default=str)
                socketio.emit("gex_update", payload)
        except Exception as e:
            print(f"[WS] Emit error for {symbol}: {e}")
        finally:
            session.close()

    return gex_data


def backfill_prices(symbol, days=7):
    """Backfill historical price data from Dhan."""
    print(f"[BACKFILL] Fetching {days} days of 1-min candles for {symbol}...")

    today = date.today()
    total_stored = 0

    for d in range(days):
        day = today - timedelta(days=d)
        if day.weekday() >= 5:  # Skip weekends
            continue
        day_str = day.strftime("%Y-%m-%d")
        candles = fetch_intraday_candles(symbol, from_date=day_str, to_date=day_str)
        stored = store_candles(symbol, candles)
        total_stored += stored
        if candles:
            print(f"  {day_str}: {len(candles)} candles ({stored} new)")
        time.sleep(0.5)  # Rate limit

    print(f"[BACKFILL] Done: {total_stored} new candles stored for {symbol}")
    return total_stored


class GexWorker:
    """Background worker that fetches GEX data on a schedule."""

    def __init__(self, socketio=None, symbols=None, interval=UPDATE_INTERVAL):
        self.socketio = socketio
        self.symbols = symbols or ["NIFTY", "BANKNIFTY"]
        self.interval = interval
        self._stop_event = Event()
        self._thread = None

    def start(self):
        """Start the background worker thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[WORKER] Started (interval={self.interval}s, symbols={self.symbols})")

    def stop(self):
        """Stop the background worker."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        print("[WORKER] Stopped")

    def _run(self):
        """Main worker loop."""
        # Daily cleanup on start
        cleanup_old_data()

        # Backfill price history on first run (only on trading days)
        if is_trading_day():
            for symbol in self.symbols:
                backfill_prices(symbol, days=7)
        else:
            print("[WORKER] Weekend/holiday — skipping backfill")

        logged_outside = False
        while not self._stop_event.is_set():
            # Check each symbol against its own market hours
            any_fetched = False
            for symbol in self.symbols:
                if self._stop_event.is_set():
                    break
                if is_market_hours(symbol):
                    any_fetched = True
                    try:
                        fetch_and_store(symbol, self.socketio)
                    except Exception as e:
                        print(f"[WORKER] Error fetching {symbol}: {e}")
                    time.sleep(2)  # Rate limit between symbols

            if any_fetched:
                logged_outside = False
                self._stop_event.wait(self.interval)
            else:
                # No symbol has open market — log once, sleep longer
                if not logged_outside:
                    ist = now_ist()
                    reason = "Weekend" if ist.weekday() >= 5 else "All markets closed"
                    print(f"[WORKER] {reason} (IST: {ist.strftime('%A %H:%M')}). Sleeping until next check...")
                    logged_outside = True
                self._stop_event.wait(300)  # Check every 5 min outside market hours


if __name__ == "__main__":
    # Test standalone
    print("Testing GEX service...")
    for sym in ["NIFTY", "BANKNIFTY"]:
        fetch_and_store(sym)
    print("Done.")
