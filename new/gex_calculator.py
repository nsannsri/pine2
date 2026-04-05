"""
GEX (Gamma Exposure) Calculator for NIFTY & BANKNIFTY
Uses Dhan API v2 option chain data
"""

import os
import json
import requests
import math
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN")

BASE_URL = "https://api.dhan.co/v2"
HEADERS = {
    "access-token": ACCESS_TOKEN,
    "client-id": CLIENT_ID,
    "Content-Type": "application/json",
}

# Underlying security IDs
UNDERLYINGS = {
    "NIFTY": {"scrip": 13, "seg": "IDX_I", "lot": 25, "multiplier": 1},
    "BANKNIFTY": {"scrip": 25, "seg": "IDX_I", "lot": 15, "multiplier": 1},
    "FINNIFTY": {"scrip": 27, "seg": "IDX_I", "lot": 25, "multiplier": 1},
}


def get_expiry_list(scrip, seg="IDX_I"):
    """Fetch available expiry dates for an underlying."""
    url = f"{BASE_URL}/optionchain/expirylist"
    payload = {"UnderlyingScrip": scrip, "UnderlyingSeg": seg}
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()


def get_option_chain(scrip, seg="IDX_I", expiry=None):
    """Fetch full option chain with Greeks for an underlying + expiry."""
    url = f"{BASE_URL}/optionchain"
    payload = {"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry}
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()


def calculate_gex(option_chain_data, spot_price, contract_multiplier=1):
    """
    Calculate GEX per strike and aggregate.

    GEX per strike = Gamma × OI × Contract_Size × Spot² × 0.01
    - For CALLS: positive GEX (dealers long gamma)
    - For PUTS: negative GEX (dealers short gamma)
    """
    results = []
    total_call_gex = 0
    total_put_gex = 0

    for strike_str, data in option_chain_data.items():
        try:
            strike = float(strike_str)
        except ValueError:
            continue

        ce = data.get("ce", {})
        pe = data.get("pe", {})

        # Greeks are nested: ce.greeks.gamma
        ce_greeks = ce.get("greeks", {})
        pe_greeks = pe.get("greeks", {})

        # Call GEX
        ce_gamma = ce_greeks.get("gamma", 0) or 0
        ce_oi = ce.get("oi", 0) or 0
        ce_gex = ce_gamma * ce_oi * contract_multiplier * (spot_price ** 2) * 0.01

        # Put GEX (negative - dealers short gamma on puts)
        pe_gamma = pe_greeks.get("gamma", 0) or 0
        pe_oi = pe.get("oi", 0) or 0
        pe_gex = -1 * pe_gamma * pe_oi * contract_multiplier * (spot_price ** 2) * 0.01

        net_gex = ce_gex + pe_gex

        # Skip strikes with zero OI on both sides
        if ce_oi == 0 and pe_oi == 0:
            continue

        results.append({
            "strike": strike,
            "ce_gamma": ce_gamma,
            "ce_oi": ce_oi,
            "ce_gex": ce_gex,
            "pe_gamma": pe_gamma,
            "pe_oi": pe_oi,
            "pe_gex": pe_gex,
            "net_gex": net_gex,
            "ce_iv": ce.get("implied_volatility", 0) or 0,
            "pe_iv": pe.get("implied_volatility", 0) or 0,
            "ce_volume": ce.get("volume", 0) or 0,
            "pe_volume": pe.get("volume", 0) or 0,
            "ce_ltp": ce.get("ltp", 0) or 0,
            "pe_ltp": pe.get("ltp", 0) or 0,
        })

        total_call_gex += ce_gex
        total_put_gex += pe_gex

    results.sort(key=lambda x: x["strike"])
    return results, total_call_gex, total_put_gex


def find_key_levels(gex_results, spot_price):
    """Find HVL, Call Wall, Put Wall, Flip Levels from GEX data."""
    if not gex_results:
        return {}

    # Call Wall = strike with highest positive (call) GEX
    call_wall = max(gex_results, key=lambda x: x["ce_gex"])

    # Put Wall = strike with most negative (put) GEX
    put_wall = min(gex_results, key=lambda x: x["pe_gex"])

    # Top 5 GEX strikes by absolute net GEX
    top_strikes = sorted(gex_results, key=lambda x: abs(x["net_gex"]), reverse=True)[:5]

    # ========== METHOD A: Cumulative GEX HVL (SpotGamma style) ==========
    # Sum net GEX from highest strike downward. Where cumulative crosses
    # zero = the HVL. Above HVL = positive gamma regime, below = negative.
    sorted_desc = sorted(gex_results, key=lambda x: x["strike"], reverse=True)
    cumulative = 0
    hvl = None
    for i, row in enumerate(sorted_desc):
        prev_cumulative = cumulative
        cumulative += row["net_gex"]
        # Detect zero crossing
        if i > 0 and prev_cumulative * cumulative < 0:
            # Interpolate between this strike and previous
            prev_strike = sorted_desc[i - 1]["strike"]
            curr_strike = row["strike"]
            # Linear interpolation for more precise HVL
            if abs(prev_cumulative) + abs(cumulative) > 0:
                ratio = abs(prev_cumulative) / (abs(prev_cumulative) + abs(cumulative))
                hvl = prev_strike - ratio * (prev_strike - curr_strike)
            else:
                hvl = (prev_strike + curr_strike) / 2
            break

    # Fallback: if no zero crossing found, use strike nearest to spot with lowest absolute cumulative
    if hvl is None:
        cumulative = 0
        min_abs_cum = float("inf")
        for row in sorted_desc:
            cumulative += row["net_gex"]
            if abs(cumulative) < min_abs_cum:
                min_abs_cum = abs(cumulative)
                hvl = row["strike"]

    # ========== METHOD B: Local Flip Levels (per-strike sign changes) ==========
    # Find ALL strikes where per-strike net GEX flips sign
    sorted_asc = sorted(gex_results, key=lambda x: x["strike"])
    flip_levels = []
    for i in range(len(sorted_asc) - 1):
        curr_gex = sorted_asc[i]["net_gex"]
        next_gex = sorted_asc[i + 1]["net_gex"]
        if curr_gex * next_gex < 0:  # Sign change
            # Interpolate flip point
            s1 = sorted_asc[i]["strike"]
            s2 = sorted_asc[i + 1]["strike"]
            g1 = abs(curr_gex)
            g2 = abs(next_gex)
            flip_strike = s1 + (g1 / (g1 + g2)) * (s2 - s1) if (g1 + g2) > 0 else (s1 + s2) / 2
            direction = "NEG->POS" if curr_gex < 0 else "POS->NEG"
            flip_levels.append({"strike": round(flip_strike, 2), "direction": direction})

    # Nearest flip to spot (local flip)
    local_flip = None
    if flip_levels:
        local_flip = min(flip_levels, key=lambda x: abs(x["strike"] - spot_price))

    # ========== Gamma Condition at Spot ==========
    # Based on cumulative GEX at spot level (Method A)
    cumulative = 0
    gamma_condition = "UNKNOWN"
    for row in sorted_desc:
        cumulative += row["net_gex"]
        if row["strike"] <= spot_price:
            gamma_condition = "POSITIVE" if cumulative > 0 else "NEGATIVE"
            break

    # ========== Gamma Tilt (near-spot bias) ==========
    # Sum positive vs negative GEX within 2% of spot
    near_range = spot_price * 0.02
    pos_gex_near = sum(r["net_gex"] for r in gex_results
                       if abs(r["strike"] - spot_price) < near_range and r["net_gex"] > 0)
    neg_gex_near = sum(r["net_gex"] for r in gex_results
                       if abs(r["strike"] - spot_price) < near_range and r["net_gex"] < 0)
    total_near = pos_gex_near + neg_gex_near
    if pos_gex_near > 0 and neg_gex_near < 0:
        gamma_tilt = pos_gex_near / (pos_gex_near + abs(neg_gex_near)) * 100
    else:
        gamma_tilt = 100 if pos_gex_near > 0 else 0

    # ========== IV Skew ==========
    # Compare average put IV vs call IV near spot (within 2.5%)
    near_range_iv = spot_price * 0.025
    near_iv_strikes = [r for r in gex_results
                       if abs(r["strike"] - spot_price) < near_range_iv
                       and r["ce_iv"] > 0 and r["pe_iv"] > 0]
    if near_iv_strikes:
        avg_ce_iv = sum(r["ce_iv"] for r in near_iv_strikes) / len(near_iv_strikes)
        avg_pe_iv = sum(r["pe_iv"] for r in near_iv_strikes) / len(near_iv_strikes)
        iv_skew = round(avg_pe_iv - avg_ce_iv, 2)  # positive = puts expensive = bearish
    else:
        avg_ce_iv = 0
        avg_pe_iv = 0
        iv_skew = 0

    # Thresholds calibrated for Indian indices where +2 to +5 put skew is normal
    if iv_skew > 8:
        skew_signal = "BEARISH"
    elif iv_skew > 5:
        skew_signal = "MILD BEAR"
    elif iv_skew < -3:
        skew_signal = "BULLISH"
    elif iv_skew < -1:
        skew_signal = "MILD BULL"
    else:
        skew_signal = "NEUTRAL"

    # ========== Peak Gamma ==========
    # Strike with highest total gamma exposure (|CE_GEX| + |PE_GEX|)
    # This is the "magnet" level where dealer hedging is most intense
    peak_gamma_row = max(gex_results, key=lambda x: abs(x["ce_gex"]) + abs(x["pe_gex"]))
    peak_gamma = peak_gamma_row["strike"]

    # ========== Max Pain ==========
    # Strike where total intrinsic value payout to option buyers is minimized
    # For each candidate strike K, sum:
    #   CE pain = sum of max(0, K - strike) * ce_oi for all strikes (ITM calls at K)
    #   PE pain = sum of max(0, strike - K) * pe_oi for all strikes (ITM puts at K)
    # Max Pain = K with lowest total pain
    all_strikes = sorted(set(r["strike"] for r in gex_results))
    min_pain = float("inf")
    max_pain = spot_price  # fallback
    for k in all_strikes:
        total_pain = 0
        for r in gex_results:
            # Call holders gain if spot > strike => at settlement price K
            if k > r["strike"]:
                total_pain += (k - r["strike"]) * r["ce_oi"]
            # Put holders gain if spot < strike => at settlement price K
            if k < r["strike"]:
                total_pain += (r["strike"] - k) * r["pe_oi"]
        if total_pain < min_pain:
            min_pain = total_pain
            max_pain = k

    # ========== BIAS Calculation ==========
    # Combine multiple signals into a single directional bias score
    # Each signal contributes +1 (bull) or -1 (bear), then map to label
    bias_score = 0

    # 1. Gamma condition: positive = bullish dampening
    if gamma_condition == "POSITIVE":
        bias_score += 1
    else:
        bias_score -= 1

    # 2. Gamma tilt
    if gamma_tilt > 65:
        bias_score += 2
    elif gamma_tilt > 50:
        bias_score += 1
    elif gamma_tilt < 35:
        bias_score -= 2
    else:
        bias_score -= 1

    # 3. Spot vs local flip (above flip = positive gamma zone)
    local_flip_strike = local_flip["strike"] if local_flip else 0
    if local_flip_strike > 0:
        if spot_price > local_flip_strike:
            bias_score += 1
        else:
            bias_score -= 1

    # 4. Spot vs max pain (below max pain = bullish pull into expiry)
    if spot_price < max_pain:
        bias_score += 1
    elif spot_price > max_pain:
        bias_score -= 1

    # 5. Spot vs peak gamma (below peak gamma = bullish magnet pull)
    if spot_price < peak_gamma:
        bias_score += 1
    elif spot_price > peak_gamma:
        bias_score -= 1

    # 6. Net GEX sign
    net_gex_total = sum(r["net_gex"] for r in gex_results)
    if net_gex_total > 0:
        bias_score += 1
    else:
        bias_score -= 1

    # Map score to bias label (max possible: +7, min: -7)
    if bias_score >= 5:
        bias = "STRONG BULL"
    elif bias_score >= 2:
        bias = "BULL"
    elif bias_score >= -1:
        bias = "NEUTRAL"
    elif bias_score >= -4:
        bias = "BEAR"
    else:
        bias = "STRONG BEAR"

    return {
        "hvl": round(hvl, 2) if hvl else None,
        "call_wall": call_wall["strike"],
        "put_wall": put_wall["strike"],
        "local_flip": local_flip,
        "flip_levels": flip_levels,
        "gamma_condition": gamma_condition,
        "gamma_tilt": round(gamma_tilt, 1),
        "pos_gex_near": pos_gex_near,
        "neg_gex_near": neg_gex_near,
        "top_strikes": [s["strike"] for s in top_strikes],
        "peak_gamma": peak_gamma,
        "max_pain": max_pain,
        "bias": bias,
        "bias_score": bias_score,
        "iv_skew": iv_skew,
        "avg_ce_iv": round(avg_ce_iv, 2),
        "avg_pe_iv": round(avg_pe_iv, 2),
        "skew_signal": skew_signal,
    }


OI_HISTORY_DIR = "C:/tv/oi_history"


def calculate_expected_move(gex_results, spot_price, expiry_str):
    """Calculate Expected Move bands using IV-based 1SD for daily and weekly windows."""
    # Find ATM strike
    atm = min(gex_results, key=lambda x: abs(x["strike"] - spot_price))
    atm_strike = atm["strike"]

    # DTE (for reference only)
    expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    dte = max((expiry_date - date.today()).days, 1)

    # ATM IV (average of CE and PE)
    atm_iv = (atm["ce_iv"] + atm["pe_iv"]) / 2

    # Daily EM: always 1-day window regardless of expiry DTE
    daily_1sd = spot_price * (atm_iv / 100) * math.sqrt(1 / 365) if atm_iv > 0 else 0
    # Weekly EM: 5-day window
    weekly_1sd = spot_price * (atm_iv / 100) * math.sqrt(5 / 365) if atm_iv > 0 else 0

    if daily_1sd > 0:
        em_upper = round(spot_price + daily_1sd, 2)
        em_lower = round(spot_price - daily_1sd, 2)
        em_wk_upper = round(spot_price + weekly_1sd, 2)
        em_wk_lower = round(spot_price - weekly_1sd, 2)
    else:
        em_upper = None
        em_lower = None
        em_wk_upper = None
        em_wk_lower = None

    return {
        "em_upper": em_upper,
        "em_lower": em_lower,
        "em_wk_upper": em_wk_upper,
        "em_wk_lower": em_wk_lower,
        "atm_strike": atm_strike,
        "atm_iv": round(atm_iv, 1),
        "daily_1sd": round(daily_1sd, 2) if daily_1sd else 0,
        "weekly_1sd": round(weekly_1sd, 2) if weekly_1sd else 0,
        "dte": dte,
    }


def track_oi_changes(symbol, expiry, gex_results):
    """Store OI snapshot and compute changes from previous snapshot."""
    os.makedirs(OI_HISTORY_DIR, exist_ok=True)
    today_str = date.today().isoformat()
    filepath = f"{OI_HISTORY_DIR}/{symbol}_{today_str}.json"

    # Current OI snapshot
    current_oi = {}
    for r in gex_results:
        current_oi[str(r["strike"])] = {
            "ce_oi": r["ce_oi"],
            "pe_oi": r["pe_oi"],
        }

    # Load previous snapshot (look back up to 3 days)
    prev_oi = None
    for days_back in range(1, 4):
        check_date = (date.today() - timedelta(days=days_back)).isoformat()
        check_path = f"{OI_HISTORY_DIR}/{symbol}_{check_date}.json"
        if os.path.exists(check_path):
            with open(check_path, "r") as f:
                prev_data = json.load(f)
                prev_oi = prev_data.get("oi_snapshot", {})
            break

    # Save current snapshot
    save_data = {"oi_snapshot": current_oi, "timestamp": datetime.now().isoformat(), "expiry": expiry}
    with open(filepath, "w") as f:
        json.dump(save_data, f)

    # Compute changes
    oi_changes = []
    if prev_oi:
        for r in gex_results:
            sk = str(r["strike"])
            if sk in prev_oi:
                ce_chg = r["ce_oi"] - prev_oi[sk]["ce_oi"]
                pe_chg = r["pe_oi"] - prev_oi[sk]["pe_oi"]
                net_chg = ce_chg + pe_chg
                oi_changes.append({
                    "strike": r["strike"],
                    "ce_oi_chg": ce_chg,
                    "pe_oi_chg": pe_chg,
                    "net_oi_chg": net_chg,
                })

    # Top 3 buildup (new positions = reinforced S/R)
    buildups = sorted([c for c in oi_changes if c["net_oi_chg"] > 0],
                      key=lambda x: x["net_oi_chg"], reverse=True)[:3]

    # Top 3 unwinding
    unwinds = sorted([c for c in oi_changes if c["net_oi_chg"] < 0],
                     key=lambda x: x["net_oi_chg"])[:3]

    total_ce_chg = sum(c["ce_oi_chg"] for c in oi_changes) if oi_changes else 0
    total_pe_chg = sum(c["pe_oi_chg"] for c in oi_changes) if oi_changes else 0

    if prev_oi is None:
        direction = "N/A"
    elif (total_ce_chg + total_pe_chg) > 0:
        direction = "BUILDUP"
    else:
        direction = "UNWINDING"

    return {
        "oi_buildups": buildups,
        "oi_unwinds": unwinds,
        "total_ce_oi_chg": total_ce_chg,
        "total_pe_oi_chg": total_pe_chg,
        "net_oi_chg_direction": direction,
        "has_prev_data": prev_oi is not None,
    }


def format_number(n):
    """Format large numbers with K/M/B suffixes."""
    abs_n = abs(n)
    if abs_n >= 1e9:
        return f"{n/1e9:.2f}B"
    elif abs_n >= 1e6:
        return f"{n/1e6:.2f}M"
    elif abs_n >= 1e3:
        return f"{n/1e3:.2f}K"
    else:
        return f"{n:.2f}"


def print_gex_report(symbol, spot, gex_results, call_gex, put_gex, levels, expiry):
    """Print formatted GEX report."""
    net_gex = call_gex + put_gex

    print("\n" + "=" * 70)
    print(f"  GEX REPORT - {symbol} | Spot: {spot:.2f} | Expiry: {expiry}")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    print(f"\n  Total Call GEX:  {format_number(call_gex):>12}")
    print(f"  Total Put GEX:   {format_number(put_gex):>12}")
    print(f"  NET GEX:         {format_number(net_gex):>12}")
    print(f"  Put/Call GEX:    {abs(put_gex / call_gex):.2f}" if call_gex != 0 else "  Put/Call GEX:    N/A")

    print(f"\n  Gamma Condition: {levels.get('gamma_condition', 'N/A')}")
    if levels["gamma_condition"] == "POSITIVE":
        print("  >> Market makers DAMPEN moves (sell rallies, buy dips)")
        print("  >> Expect: Mean reversion, range-bound, lower volatility")
    else:
        print("  >> Market makers AMPLIFY moves (buy rallies, sell dips)")
        print("  >> Expect: Trending, breakouts, higher volatility")

    print(f"\n  Gamma Tilt (near spot):  {levels.get('gamma_tilt', 0):.1f}% bullish")
    print(f"    Positive GEX (2%):     {format_number(levels.get('pos_gex_near', 0)):>12}")
    print(f"    Negative GEX (2%):     {format_number(levels.get('neg_gex_near', 0)):>12}")

    print(f"\n  KEY LEVELS:")
    print(f"  {'-' * 50}")
    print(f"  Call Wall (Resistance):  {levels['call_wall']:>10.0f}")
    print(f"  Put Wall (Support):      {levels['put_wall']:>10.0f}")
    hvl = levels.get("hvl")
    if hvl:
        print(f"  HVL (Cumulative Flip):   {hvl:>10.2f}  [Method A]")
        dist_hvl = ((spot - hvl) / hvl) * 100
        print(f"  Distance to HVL:         {dist_hvl:>9.2f}%")
        if spot > hvl:
            print(f"  Spot is ABOVE HVL        >> Positive gamma zone")
        else:
            print(f"  Spot is BELOW HVL        >> Negative gamma zone")
    local_flip = levels.get("local_flip")
    if local_flip:
        print(f"  Local Flip (Nearest):    {local_flip['strike']:>10.2f}  [{local_flip['direction']}]")
    peak_gamma = levels.get("peak_gamma")
    if peak_gamma:
        print(f"  Peak Gamma (Magnet):     {peak_gamma:>10.0f}")
    max_pain = levels.get("max_pain")
    if max_pain:
        print(f"  Max Pain (Expiry Pin):   {max_pain:>10.0f}")
    print(f"  Spot Price:              {spot:>10.2f}")
    bias = levels.get("bias", "N/A")
    bias_score = levels.get("bias_score", 0)
    print(f"\n  BIAS: {bias} (score: {bias_score:+d}/7)")

    # IV Skew
    iv_skew = levels.get("iv_skew", 0)
    skew_signal = levels.get("skew_signal", "N/A")
    print(f"  IV Skew: {iv_skew:+.2f} ({skew_signal})")
    print(f"    Avg CE IV: {levels.get('avg_ce_iv', 0):.2f}  |  Avg PE IV: {levels.get('avg_pe_iv', 0):.2f}")

    # Expected Move
    em_upper = levels.get("em_upper")
    em_lower = levels.get("em_lower")
    if em_upper and em_lower:
        print(f"\n  EXPECTED MOVE (1-day, 1SD):")
        print(f"    Daily:  {em_lower:.0f} - {em_upper:.0f}  (±{levels.get('daily_1sd', 0):.0f})")
        em_wk_upper = levels.get("em_wk_upper")
        em_wk_lower = levels.get("em_wk_lower")
        if em_wk_upper and em_wk_lower:
            print(f"    Weekly: {em_wk_lower:.0f} - {em_wk_upper:.0f}  (±{levels.get('weekly_1sd', 0):.0f})")
        print(f"    ATM IV: {levels.get('atm_iv', 0):.1f}%  |  DTE: {levels.get('dte', 0)}")

    # OI Change
    if levels.get("has_prev_data"):
        print(f"\n  OI CHANGE: {levels.get('net_oi_chg_direction', 'N/A')}")
        print(f"    CE OI Chg: {format_number(levels.get('total_ce_oi_chg', 0))}")
        print(f"    PE OI Chg: {format_number(levels.get('total_pe_oi_chg', 0))}")
        buildups = levels.get("oi_buildups", [])
        if buildups:
            print(f"    Top Buildups:")
            for b in buildups:
                print(f"      {b['strike']:.0f}  +{format_number(b['net_oi_chg'])}")
    else:
        print(f"\n  OI CHANGE: No previous data (first run)")

    # All flip levels
    flip_levels = levels.get("flip_levels", [])
    if flip_levels:
        # Show flips near spot (within 5%)
        near_flips = [f for f in flip_levels if abs(f["strike"] - spot) < spot * 0.05]
        if near_flips:
            print(f"\n  GAMMA FLIP LEVELS (within 5% of spot):")
            print(f"  {'-' * 50}")
            for f in sorted(near_flips, key=lambda x: x["strike"]):
                dist = ((f["strike"] - spot) / spot) * 100
                marker = " << NEAREST" if local_flip and f["strike"] == local_flip["strike"] else ""
                print(f"  {f['strike']:>10.2f}  {f['direction']:<10}  ({dist:+.2f}%){marker}")

    print(f"\n  TOP 5 GEX STRIKES:")
    print(f"  {'-' * 40}")
    for s in levels["top_strikes"]:
        row = next((r for r in gex_results if r["strike"] == s), None)
        if row:
            marker = " < SPOT" if abs(s - spot) < spot * 0.003 else ""
            print(f"  {s:>10.0f}  Net GEX: {format_number(row['net_gex']):>10}{marker}")

    # Print strikes around spot with GEX
    print(f"\n  GEX BY STRIKE (near spot):")
    print(f"  {'-' * 66}")
    print(f"  {'Strike':>10} {'CE_OI':>10} {'CE_GEX':>10} {'PE_OI':>10} {'PE_GEX':>10} {'NET_GEX':>10}")
    print(f"  {'-' * 66}")

    nearby = [r for r in gex_results if abs(r["strike"] - spot) < spot * 0.03]
    for r in nearby:
        marker = " <" if abs(r["strike"] - spot) < spot * 0.003 else ""
        print(f"  {r['strike']:>10.0f} {r['ce_oi']:>10} {format_number(r['ce_gex']):>10} "
              f"{r['pe_oi']:>10} {format_number(r['pe_gex']):>10} {format_number(r['net_gex']):>10}{marker}")

    print("=" * 70)


def get_spot_price_from_chain(option_chain_data):
    """Estimate spot price from ATM options (midpoint of highest OI CE/PE)."""
    max_oi_strike = None
    max_oi = 0
    for strike_str, data in option_chain_data.items():
        try:
            strike = float(strike_str)
        except ValueError:
            continue
        ce_oi = (data.get("ce", {}).get("oi", 0) or 0)
        pe_oi = (data.get("pe", {}).get("oi", 0) or 0)
        total = ce_oi + pe_oi
        if total > max_oi:
            max_oi = total
            max_oi_strike = strike

    return max_oi_strike


def run_gex(symbol="NIFTY", expiry=None):
    """Main function to run GEX analysis."""
    if symbol not in UNDERLYINGS:
        print(f"Unknown symbol: {symbol}. Available: {list(UNDERLYINGS.keys())}")
        return

    config = UNDERLYINGS[symbol]
    print(f"\nFetching data for {symbol}...")

    # Get expiry list if not provided
    if not expiry:
        expiry_data = get_expiry_list(config["scrip"], config["seg"])
        # Handle nested response: {"data": [...], "status": "success"}
        if isinstance(expiry_data, dict) and "data" in expiry_data:
            expiries = sorted(expiry_data["data"])
        elif isinstance(expiry_data, list):
            expiries = sorted(expiry_data)
        else:
            expiries = []
        if not expiries:
            print(f"No expiries found. Raw response: {expiry_data}")
            return
        # Pick nearest expiry
        today = date.today().isoformat()
        future_expiries = [e for e in expiries if e >= today]
        expiry = future_expiries[0] if future_expiries else expiries[-1]
        print(f"Using nearest expiry: {expiry}")
        print(f"All expiries: {', '.join(future_expiries[:5])}")

    # Fetch option chain
    print(f"Fetching option chain...")
    chain_data = get_option_chain(config["scrip"], config["seg"], expiry)

    if not chain_data or isinstance(chain_data, dict) and "error" in chain_data:
        print(f"Error fetching chain: {chain_data}")
        return

    # Unwrap Dhan response: {"data": {"last_price": ..., "oc": {...}}, "status": "success"}
    if isinstance(chain_data, dict) and "data" in chain_data:
        chain_data = chain_data["data"]

    spot = chain_data.get("last_price", 0)
    oc_data = chain_data.get("oc", {})

    if not spot or not oc_data:
        print(f"Missing data. spot={spot}, oc_keys={len(oc_data)}")
        return

    print(f"Spot price: {spot}")
    print(f"Total strikes: {len(oc_data)}")

    # Calculate GEX
    lot_size = config["lot"]
    gex_results, call_gex, put_gex = calculate_gex(oc_data, spot, lot_size)

    if not gex_results:
        print("No GEX data calculated. Check option chain response.")
        return

    # Find key levels
    levels = find_key_levels(gex_results, spot)

    # Expected Move
    em_data = calculate_expected_move(gex_results, spot, expiry)
    levels.update(em_data)

    # OI Change tracking
    oi_data = track_oi_changes(symbol, expiry, gex_results)
    levels.update(oi_data)

    # Print report
    print_gex_report(symbol, spot, gex_results, call_gex, put_gex, levels, expiry)

    return {
        "symbol": symbol,
        "spot": spot,
        "expiry": expiry,
        "net_gex": call_gex + put_gex,
        "call_gex": call_gex,
        "put_gex": put_gex,
        "levels": levels,
        "strikes": gex_results,
    }


if __name__ == "__main__":
    import sys

    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "NIFTY"
    expiry = sys.argv[2] if len(sys.argv) > 2 else None

    print("=" * 70)
    print("  DHAN GEX CALCULATOR - Nifty / BankNifty / FinNifty")
    print("=" * 70)

    run_gex(symbol, expiry)

    # Run both if no specific symbol
    if len(sys.argv) <= 1:
        import time
        time.sleep(3)  # Rate limit
        run_gex("BANKNIFTY")
