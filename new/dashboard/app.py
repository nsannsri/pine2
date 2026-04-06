"""
GEX Dashboard — Flask + SocketIO web app.
Real-time GEX levels, price charts, historical trails.
"""

import os
import sys
from datetime import datetime, date, timedelta

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

# IST offset: Lightweight Charts displays UTC, so we add 5:30 to show IST times
IST_OFFSET = 5 * 3600 + 30 * 60  # 19800 seconds

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import get_session, GexSnapshot, StrikeGex, Price, init_db
from gex_service import GexWorker, snapshot_to_dict, fetch_and_store

app = Flask(__name__)
app.config["SECRET_KEY"] = "gex-dashboard-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

worker = None


# ── Pages ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── REST API ───────────────────────────────────────────────────────────────

@app.route("/api/symbols")
def api_symbols():
    """Available symbols."""
    return jsonify(["NIFTY", "BANKNIFTY", "GOLD"])


@app.route("/api/snapshot/<symbol>")
def api_latest_snapshot(symbol):
    """Latest GEX snapshot for a symbol."""
    session = get_session()
    try:
        snap = (
            session.query(GexSnapshot)
            .filter_by(symbol=symbol.upper())
            .order_by(GexSnapshot.timestamp.desc())
            .first()
        )
        if not snap:
            return jsonify({"error": "No data"}), 404
        data = snapshot_to_dict(snap)
        # Include strikes
        data["strikes"] = [
            {
                "strike": s.strike,
                "ce_oi": s.ce_oi,
                "pe_oi": s.pe_oi,
                "ce_gex": s.ce_gex,
                "pe_gex": s.pe_gex,
                "net_gex": s.net_gex,
                "ce_iv": s.ce_iv,
                "pe_iv": s.pe_iv,
            }
            for s in snap.strikes
        ]
        return jsonify(data)
    finally:
        session.close()


@app.route("/api/snapshots/<symbol>")
def api_snapshots(symbol):
    """Historical GEX snapshots for a symbol. Query params: date (YYYY-MM-DD), limit."""
    session = get_session()
    try:
        query_date = request.args.get("date", date.today().strftime("%Y-%m-%d"))
        limit = int(request.args.get("limit", 500))

        day_start = datetime.strptime(query_date, "%Y-%m-%d")
        day_end = day_start + timedelta(days=1)

        snaps = (
            session.query(GexSnapshot)
            .filter(
                GexSnapshot.symbol == symbol.upper(),
                GexSnapshot.timestamp >= day_start,
                GexSnapshot.timestamp < day_end,
            )
            .order_by(GexSnapshot.timestamp.asc())
            .limit(limit)
            .all()
        )
        return jsonify([snapshot_to_dict(s) for s in snaps])
    finally:
        session.close()


def aggregate_candles(candles_1m, tf_minutes):
    """Aggregate 1-minute candles into higher timeframe."""
    if tf_minutes <= 1:
        return candles_1m

    aggregated = []
    bucket = None
    bucket_time = None

    for c in candles_1m:
        # Floor timestamp to timeframe boundary
        ts = c["time"]
        floored = ts - (ts % (tf_minutes * 60))

        if floored != bucket_time:
            if bucket:
                aggregated.append(bucket)
            bucket_time = floored
            bucket = {
                "time": floored,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"] or 0,
            }
        else:
            bucket["high"] = max(bucket["high"], c["high"])
            bucket["low"] = min(bucket["low"], c["low"])
            bucket["close"] = c["close"]
            bucket["volume"] = (bucket["volume"] or 0) + (c["volume"] or 0)

    if bucket:
        aggregated.append(bucket)

    return aggregated


@app.route("/api/prices/<symbol>")
def api_prices(symbol):
    """Price candles. Query params: date, days, limit, tf (1, 5, 15)."""
    session = get_session()
    try:
        query_date = request.args.get("date")
        days = int(request.args.get("days", 1))
        limit = int(request.args.get("limit", 2000))
        tf = int(request.args.get("tf", 1))

        if query_date:
            day_start = datetime.strptime(query_date, "%Y-%m-%d")
        else:
            day_start = datetime.combine(date.today() - timedelta(days=days - 1), datetime.min.time())
        day_end = day_start + timedelta(days=days)

        prices = (
            session.query(Price)
            .filter(
                Price.symbol == symbol.upper(),
                Price.timestamp >= day_start,
                Price.timestamp < day_end,
            )
            .order_by(Price.timestamp.asc())
            .limit(limit)
            .all()
        )
        candles_1m = [
            {
                "time": int(p.timestamp.timestamp()) + IST_OFFSET,
                "open": p.open,
                "high": p.high,
                "low": p.low,
                "close": p.close,
                "volume": p.volume,
            }
            for p in prices
        ]
        return jsonify(aggregate_candles(candles_1m, tf))
    finally:
        session.close()


@app.route("/api/levels/<symbol>")
def api_levels(symbol):
    """Historical level trails for charting (call_wall, put_wall, etc over time)."""
    session = get_session()
    try:
        query_date = request.args.get("date", date.today().strftime("%Y-%m-%d"))
        day_start = datetime.strptime(query_date, "%Y-%m-%d")
        day_end = day_start + timedelta(days=1)

        snaps = (
            session.query(GexSnapshot)
            .filter(
                GexSnapshot.symbol == symbol.upper(),
                GexSnapshot.timestamp >= day_start,
                GexSnapshot.timestamp < day_end,
            )
            .order_by(GexSnapshot.timestamp.asc())
            .all()
        )

        levels = {
            "call_wall": [],
            "put_wall": [],
            "hvl": [],
            "local_flip": [],
            "peak_gamma": [],
            "max_pain": [],
            "em_upper": [],
            "em_lower": [],
        }

        # XAUUSD level trails for GOLD
        xau_levels = {}
        if symbol.upper() == "GOLD":
            for key in ["call_wall", "put_wall", "peak_gamma", "max_pain", "local_flip", "em_upper", "em_lower"]:
                xau_levels["xau_" + key] = []

        for s in snaps:
            t = int(s.timestamp.timestamp()) + IST_OFFSET
            for key in levels:
                val = getattr(s, key, 0)
                if val and val > 0:
                    levels[key].append({"time": t, "value": val})
            # XAUUSD trails
            for key in xau_levels:
                val = getattr(s, key, 0)
                if val and val > 0:
                    xau_levels[key].append({"time": t, "value": val})

        result = levels
        if xau_levels:
            result.update(xau_levels)
        return jsonify(result)
    finally:
        session.close()


@app.route("/api/strikes/<symbol>")
def api_strikes(symbol):
    """Latest per-strike GEX data for bar chart."""
    session = get_session()
    try:
        snap = (
            session.query(GexSnapshot)
            .filter_by(symbol=symbol.upper())
            .order_by(GexSnapshot.timestamp.desc())
            .first()
        )
        if not snap:
            return jsonify([])
        return jsonify([
            {
                "strike": s.strike,
                "ce_gex": s.ce_gex,
                "pe_gex": s.pe_gex,
                "net_gex": s.net_gex,
                "ce_oi": s.ce_oi,
                "pe_oi": s.pe_oi,
            }
            for s in sorted(snap.strikes, key=lambda x: x.strike)
        ])
    finally:
        session.close()


@app.route("/api/dates/<symbol>")
def api_dates(symbol):
    """Available dates with data."""
    session = get_session()
    try:
        from sqlalchemy import func
        dates = (
            session.query(func.date(GexSnapshot.timestamp))
            .filter(GexSnapshot.symbol == symbol.upper())
            .distinct()
            .order_by(func.date(GexSnapshot.timestamp).desc())
            .all()
        )
        return jsonify([d[0] for d in dates])
    finally:
        session.close()


# ── SocketIO Events ───────────────────────────────────────────────────────

@socketio.on("connect")
def handle_connect():
    print(f"[WS] Client connected")


@socketio.on("disconnect")
def handle_disconnect():
    print(f"[WS] Client disconnected")


@socketio.on("request_refresh")
def handle_refresh(data):
    """Manual refresh request from client."""
    symbol = data.get("symbol", "NIFTY").upper()
    try:
        result = fetch_and_store(symbol, socketio)
        if result:
            socketio.emit("refresh_complete", {"symbol": symbol, "success": True})
        else:
            socketio.emit("refresh_complete", {"symbol": symbol, "success": False})
    except Exception as e:
        socketio.emit("refresh_complete", {"symbol": symbol, "success": False, "error": str(e)})


# ── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("=" * 60)
    print("  GEX Dashboard Starting...")
    print("  http://localhost:5000")
    print("=" * 60)

    # Start background worker
    worker = GexWorker(socketio=socketio, symbols=["NIFTY", "BANKNIFTY", "GOLD"], interval=60)
    worker.start()

    try:
        socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        pass
    finally:
        if worker:
            worker.stop()
