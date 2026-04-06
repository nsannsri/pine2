"""
GEX TradingView Pusher
Calculates GEX from Dhan API and pushes Pine Script indicator to TradingView
via TradingView MCP. Run every 5 minutes for near-live levels.

Usage:
  python gex_tv_push.py           (both NIFTY + BANKNIFTY)
  python gex_tv_push.py NIFTY     (NIFTY only)
  python gex_tv_push.py --loop    (auto-refresh every 5 min)
"""

import sys
import os
import time
import json
import subprocess
import sqlite3
import shutil
import tempfile
import requests
from datetime import datetime, date
from gex_calculator import run_gex, UNDERLYINGS


# Maps push symbol → published script name on TradingView
PUBLISH_MAP = {
    "combined":    "GEX Levels",
    "gold_xauusd": None,   # not published, set name to enable
}

_tv_session = {}   # cache: {headers, scripts}


def _get_tv_headers():
    """Read sessionid from TradingView's local cookie store."""
    cookie_path = r'C:\Users\teju\AppData\Roaming\TradingView\Network\Cookies'
    tmp = tempfile.mktemp(suffix='.db')
    shutil.copy2(cookie_path, tmp)
    try:
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        cur.execute("SELECT name, value FROM cookies WHERE host_key LIKE '%tradingview%' AND name IN ('sessionid', 'sessionid_sign')")
        cookies = dict(cur.fetchall())
        conn.close()
    finally:
        os.unlink(tmp)
    cookie_str = '; '.join(f'{k}={v}' for k, v in cookies.items())
    return {
        'cookie': cookie_str,
        'origin': 'https://in.tradingview.com',
        'referer': 'https://in.tradingview.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }


def _get_published_scripts(headers):
    r = requests.get(
        'https://pine-facade.tradingview.com/pine-facade/list?filter=published',
        headers=headers, timeout=10
    )
    r.raise_for_status()
    return {s['scriptName']: s['scriptIdPart'] for s in r.json()}


def republish_pine(pine_code, symbol):
    """Republish pine_code to the TradingView published script matching symbol."""
    script_name = PUBLISH_MAP.get(symbol)
    if not script_name:
        return  # not configured for publishing

    try:
        if not _tv_session.get('headers'):
            _tv_session['headers'] = _get_tv_headers()
        headers = _tv_session['headers']

        if not _tv_session.get('scripts'):
            _tv_session['scripts'] = _get_published_scripts(headers)
        pub_id = _tv_session['scripts'].get(script_name)

        if not pub_id:
            print(f"[PUBLISH] Script '{script_name}' not found in published list")
            return

        url = f'https://pine-facade.tradingview.com/pine-facade/publish/next/{requests.utils.quote(pub_id, safe="")}'
        r = requests.post(url, data={'source': pine_code}, headers=headers, timeout=15)
        if r.status_code == 200:
            print(f"[PUBLISH] '{script_name}' republished OK")
        else:
            print(f"[PUBLISH] Failed HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[PUBLISH] Error: {e}")


def generate_combined_pine(nifty_data=None, bnf_data=None):
    """Generate Pine Script with NIFTY + BANKNIFTY + GOLD GEX levels, toggles, and text boxes."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    def fmt(n):
        abs_n = abs(n)
        if abs_n >= 1e9:
            return f"{n/1e9:.1f}B"
        elif abs_n >= 1e6:
            return f"{n/1e6:.1f}M"
        elif abs_n >= 1e3:
            return f"{n/1e3:.1f}K"
        return f"{n:.0f}"

    def extract(data):
        if not data:
            return None
        levels = data["levels"]
        local_flip = levels.get("local_flip", {})
        return {
            "symbol": data["symbol"],
            "spot": data["spot"],
            "expiry": data["expiry"],
            "call_wall": levels.get("call_wall", 0),
            "put_wall": levels.get("put_wall", 0),
            "hvl": levels.get("hvl", 0),
            "local_flip_strike": local_flip.get("strike", 0) if local_flip else 0,
            "gamma_cond": levels.get("gamma_condition", "UNKNOWN"),
            "gamma_tilt": levels.get("gamma_tilt", 0),
            "net_gex": data["net_gex"],
            "call_gex": data["call_gex"],
            "put_gex": data["put_gex"],
            "pc_ratio": f"{abs(data['put_gex']/data['call_gex']):.2f}" if data['call_gex'] != 0 else "N/A",
            "peak_gamma": levels.get("peak_gamma", 0),
            "max_pain": levels.get("max_pain", 0),
            "bias": levels.get("bias", "NEUTRAL"),
            "bias_score": levels.get("bias_score", 0),
            "top_nearby": get_top_nearby(data),
            # Expected Move (daily 1SD)
            "em_upper": levels.get("em_upper", 0) or 0,
            "em_lower": levels.get("em_lower", 0) or 0,
            "em_wk_upper": levels.get("em_wk_upper", 0) or 0,
            "em_wk_lower": levels.get("em_wk_lower", 0) or 0,
            "atm_iv": levels.get("atm_iv", 0),
            "dte": levels.get("dte", 0),
            # IV Skew
            "iv_skew": levels.get("iv_skew", 0),
            "skew_signal": levels.get("skew_signal", "NEUTRAL"),
            # OI Change
            "oi_buildups": levels.get("oi_buildups", []),
            "total_ce_oi_chg": levels.get("total_ce_oi_chg", 0),
            "total_pe_oi_chg": levels.get("total_pe_oi_chg", 0),
            "net_oi_chg_direction": levels.get("net_oi_chg_direction", "N/A"),
            "has_prev_data": levels.get("has_prev_data", False),
            # Regime
            "regime": levels.get("regime", "MIXED"),
            "regime_score": levels.get("regime_score", 0),
        }

    def get_top_nearby(data):
        spot = data["spot"]
        strikes_data = data.get("strikes", [])
        nearby = [r for r in strikes_data if abs(r["strike"] - spot) < spot * 0.03]
        return sorted(nearby, key=lambda x: abs(x["net_gex"]), reverse=True)[:5]

    def load_session_history(symbol):
        """Load today's session history for a symbol."""
        hist_dir = "C:/tv/gex_history"
        today = date.today().strftime("%Y%m%d")
        hist_file = os.path.join(hist_dir, f"{symbol}_{today}.json")
        if not os.path.exists(hist_file):
            return []
        try:
            with open(hist_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def generate_history_pine(history, prefix, levels_to_track):
        """Generate Pine Script step-line code for historical level trails.

        Args:
            history: list of dicts with time, call_wall, put_wall, etc.
            prefix: "N" for NIFTY, "B" for BANKNIFTY
            levels_to_track: list of (key, color_expr, label) tuples
        """
        if len(history) < 2:
            return ""  # Need at least 2 points for a trail

        # Use prefix-based variable names to avoid Pine redeclaration errors
        vp = prefix[0].lower()  # "n" or "b"
        code = f"\n// ===== {prefix} LEVEL HISTORY ({len(history)} snapshots) =====\n"
        code += "if showHist and barstate.islast\n"
        code += f"    int {vp}hBar = na(sessStart) ? bar_index - 50 : sessStart\n"
        code += f"    int {vp}hSpan = bar_index - {vp}hBar\n"

        for key, color_expr, label in levels_to_track:
            # Extract values from history
            values = [h.get(key, 0) for h in history]
            times = [h.get("time", "") for h in history]

            # Skip if all values are the same (no change to show)
            unique_vals = set(v for v in values if v and v > 0)
            if len(unique_vals) <= 1:
                continue

            # Generate step-line segments between each pair of points
            # Each segment is positioned proportionally across the session bars
            for i in range(len(values) - 1):
                v1 = values[i]
                v2 = values[i + 1]
                if not v1 or v1 <= 0:
                    continue

                # Position: proportional to snapshot index within session
                # frac1 = i / (hCount - 1), frac2 = (i+1) / (hCount - 1)
                frac1 = i / (len(history) - 1)
                frac2 = (i + 1) / (len(history) - 1)

                # Horizontal line at v1 from frac1 to frac2
                code += f"    line.new({vp}hBar + math.round({vp}hSpan * {frac1:.4f}), {v1:.2f}, {vp}hBar + math.round({vp}hSpan * {frac2:.4f}), {v1:.2f}, color=color.new({color_expr}, 60), width=1, style=line.style_dotted)\n"

                # Vertical connector from v1 to v2 at frac2 (if different)
                if v2 and v2 > 0 and v1 != v2:
                    code += f"    line.new({vp}hBar + math.round({vp}hSpan * {frac2:.4f}), {v1:.2f}, {vp}hBar + math.round({vp}hSpan * {frac2:.4f}), {v2:.2f}, color=color.new({color_expr}, 60), width=1, style=line.style_dotted)\n"

            # Label at start showing first value
            if values[0] and values[0] > 0:
                code += f"    label.new({vp}hBar, {values[0]:.2f}, \"{label} {times[0]}\", style=label.style_label_right, color=color.new(color.gray, 80), textcolor=color.white, size=size.tiny)\n"

        return code

    n = extract(nifty_data)
    b = extract(bnf_data)

    # Load session histories
    n_history = load_session_history("NIFTY") if nifty_data else []
    b_history = load_session_history("BANKNIFTY") if bnf_data else []

    # Define level configs: (key, default_color_hex, default_width, default_style, label_prefix)
    level_defs = [
        ("CW", "Call Wall", "#FF0000", 3, "Solid"),
        ("PW", "Put Wall", "#00FF00", 3, "Solid"),
        ("HVL", "HVL", "#FF9800", 3, "Dashed"),
        ("FLIP", "Local Flip", "#FFEB3B", 2, "Dotted"),
        ("PG", "Peak Gamma", "#FF00FF", 3, "Solid"),
        ("MP", "Max Pain", "#00FFFF", 3, "Dashed"),
    ]

    pine = f'''// This file is auto-generated by gex_tv_push.py
// Last updated: {timestamp}
//@version=6
indicator("GEX Levels", overlay=true, max_lines_count=500, max_labels_count=500, max_boxes_count=500)

// ===== DISPLAY TOGGLES =====
showNifty = input.bool(true, "Show NIFTY", group="Display")
showBNF = input.bool(true, "Show BANKNIFTY", group="Display")
showTable = input.bool(true, "Show Levels Table", group="Display")
showTopStrikes = input.bool(true, "Top GEX Strikes", group="Display")
showEM = input.bool(true, "Expected Move Zone", group="Display")
showOI = input.bool(true, "OI Buildup Lines", group="Display")
showHist = input.bool(true, "Level History Trail", group="Display")

// ===== LEVEL STYLE INPUTS =====
'''

    # Generate style inputs for each level type
    for key, name, def_color, def_width, def_style in level_defs:
        pine += f'show{key} = input.bool(true, "{name}", group="{name} Style")\n'
        pine += f'clr{key} = input.color({def_color}, "Color", group="{name} Style")\n'
        pine += f'wid{key} = input.int({def_width}, "Width", minval=1, maxval=5, group="{name} Style")\n'
        pine += f'sty{key}Str = input.string("{def_style}", "Style", options=["Solid", "Dashed", "Dotted"], group="{name} Style")\n'

    pine += '''clrEM = input.color(#2196F3, "Color", group="Expected Move Style")
widEM = input.int(2, "Width", minval=1, maxval=5, group="Expected Move Style")
styEMStr = input.string("Dashed", "Style", options=["Solid", "Dashed", "Dotted"], group="Expected Move Style")

// Style string to line.style converter
lineStyle(string s) =>
    s == "Dashed" ? line.style_dashed : s == "Dotted" ? line.style_dotted : line.style_solid

'''

    pine += f'var string UPDATED = "{timestamp}"\n'

    # NIFTY levels
    if n:
        pine += f'''
// ===== NIFTY LEVELS (editable) =====
N_CW = input.float({n["call_wall"]:.0f}, "Call Wall", group="NIFTY Levels")
N_PW = input.float({n["put_wall"]:.0f}, "Put Wall", group="NIFTY Levels")
N_HVL = input.float({n["hvl"]:.2f}, "HVL", group="NIFTY Levels")
N_FLIP = input.float({n["local_flip_strike"]:.2f}, "Local Flip", group="NIFTY Levels")
N_PG = input.float({n["peak_gamma"]:.0f}, "Peak Gamma", group="NIFTY Levels")
N_MP = input.float({n["max_pain"]:.0f}, "Max Pain", group="NIFTY Levels")
var string N_GAMMA_COND = "{n["gamma_cond"]}"
var float N_GAMMA_TILT = {n["gamma_tilt"]:.1f}
var float N_NET_GEX = {n["net_gex"]:.0f}
var string N_EXPIRY = "{n["expiry"]}"
var string N_BIAS = "{n["bias"]}"
var int N_BIAS_SCORE = {n["bias_score"]}
var float N_EM_UP = {n["em_upper"]:.2f}
var float N_EM_LO = {n["em_lower"]:.2f}
var float N_EM_WK_UP = {n["em_wk_upper"]:.2f}
var float N_EM_WK_LO = {n["em_wk_lower"]:.2f}
var float N_ATM_IV = {n["atm_iv"]:.1f}
var int N_DTE = {n["dte"]}
var float N_IV_SKEW = {n["iv_skew"]:.2f}
var string N_SKEW_SIG = "{n["skew_signal"]}"
var string N_OI_DIR = "{n["net_oi_chg_direction"]}"
var int N_OI_CE_CHG = {n["total_ce_oi_chg"]}
var int N_OI_PE_CHG = {n["total_pe_oi_chg"]}
var bool N_HAS_OI = {"true" if n["has_prev_data"] else "false"}
var string N_REGIME = "{n["regime"]}"
var int N_REGIME_SCORE = {n["regime_score"]}
'''

    if b:
        pine += f'''
// ===== BANKNIFTY LEVELS (editable) =====
B_CW = input.float({b["call_wall"]:.0f}, "Call Wall", group="BANKNIFTY Levels")
B_PW = input.float({b["put_wall"]:.0f}, "Put Wall", group="BANKNIFTY Levels")
B_HVL = input.float({b["hvl"]:.2f}, "HVL", group="BANKNIFTY Levels")
B_FLIP = input.float({b["local_flip_strike"]:.2f}, "Local Flip", group="BANKNIFTY Levels")
B_PG = input.float({b["peak_gamma"]:.0f}, "Peak Gamma", group="BANKNIFTY Levels")
B_MP = input.float({b["max_pain"]:.0f}, "Max Pain", group="BANKNIFTY Levels")
var string B_GAMMA_COND = "{b["gamma_cond"]}"
var float B_GAMMA_TILT = {b["gamma_tilt"]:.1f}
var float B_NET_GEX = {b["net_gex"]:.0f}
var string B_EXPIRY = "{b["expiry"]}"
var string B_BIAS = "{b["bias"]}"
var int B_BIAS_SCORE = {b["bias_score"]}
var float B_EM_UP = {b["em_upper"]:.2f}
var float B_EM_LO = {b["em_lower"]:.2f}
var float B_EM_WK_UP = {b["em_wk_upper"]:.2f}
var float B_EM_WK_LO = {b["em_wk_lower"]:.2f}
var float B_ATM_IV = {b["atm_iv"]:.1f}
var int B_DTE = {b["dte"]}
var float B_IV_SKEW = {b["iv_skew"]:.2f}
var string B_SKEW_SIG = "{b["skew_signal"]}"
var string B_OI_DIR = "{b["net_oi_chg_direction"]}"
var int B_OI_CE_CHG = {b["total_ce_oi_chg"]}
var int B_OI_PE_CHG = {b["total_pe_oi_chg"]}
var bool B_HAS_OI = {"true" if b["has_prev_data"] else "false"}
var string B_REGIME = "{b["regime"]}"
var int B_REGIME_SCORE = {b["regime_score"]}
'''

    # Session detection + line drawing using hline-like approach with session bars
    # We track session start bar and draw persistent lines across the session
    pine += '''
// ===== SESSION TRACKING =====
var int sessStart = na
newSess = session.isfirstbar
if newSess
    sessStart := bar_index

// Helper: draw a session-wide level line + label on last bar
drawLevel(float price, color clr, int wid, string styStr, string txt, bool show) =>
    if show and not na(price) and price > 0 and barstate.islast
        startBar = na(sessStart) ? bar_index - 50 : sessStart
        l = line.new(startBar, price, bar_index + 15, price, color=clr, width=wid, style=lineStyle(styStr), extend=extend.none)
        label.new(bar_index + 17, price, txt, style=label.style_label_left, color=clr, textcolor=color.white, size=size.small)

// ===== DRAW LEVELS =====
'''

    if n:
        pine += '''// --- NIFTY ---
if showNifty
    drawLevel(N_CW, clrCW, widCW, styCWStr, "Call Wall " + str.tostring(N_CW, "#"), showCW)
    drawLevel(N_PW, clrPW, widPW, styPWStr, "Put Wall " + str.tostring(N_PW, "#"), showPW)
    drawLevel(N_HVL, clrHVL, widHVL, styHVLStr, "HVL " + str.tostring(N_HVL, "#.##"), showHVL)
    drawLevel(N_FLIP, clrFLIP, widFLIP, styFLIPStr, "Local Flip " + str.tostring(N_FLIP, "#.##"), showFLIP)
    drawLevel(N_PG, clrPG, widPG, styPGStr, "Peak Gamma " + str.tostring(N_PG, "#"), showPG)
    drawLevel(N_MP, clrMP, widMP, styMPStr, "Max Pain " + str.tostring(N_MP, "#"), showMP)
'''

        # Top strikes for NIFTY
        pine += '    if showTopStrikes and barstate.islast\n'
        pine += '        int sBar = na(sessStart) ? bar_index - 50 : sessStart\n'
        for i, s in enumerate(n["top_nearby"]):
            gex_str = fmt(s["net_gex"])
            pine += f'        line.new(sBar, {s["strike"]:.2f}, bar_index + 10, {s["strike"]:.2f}, color=color.new(color.blue, 40), width=1, style=line.style_dotted)\n'
            pine += f'        label.new(sBar - 2, {s["strike"]:.2f}, "{s["strike"]:.0f} ({gex_str})", style=label.style_label_right, color=color.new(color.gray, 70), textcolor=color.white, size=size.small)\n'

        # EM band for NIFTY
        pine += '''    if showEM and barstate.islast and N_EM_UP > 0 and N_EM_LO > 0
        int emBar = na(sessStart) ? bar_index - 50 : sessStart
        // Weekly EM (outer, lighter zone)
        emWkUp = line.new(emBar, N_EM_WK_UP, bar_index + 15, N_EM_WK_UP, color=color.new(clrEM, 50), width=1, style=line.style_dotted)
        emWkLo = line.new(emBar, N_EM_WK_LO, bar_index + 15, N_EM_WK_LO, color=color.new(clrEM, 50), width=1, style=line.style_dotted)
        linefill.new(emWkUp, emWkLo, color=color.new(clrEM, 93))
        // Daily EM (inner, solid zone)
        emUpLine = line.new(emBar, N_EM_UP, bar_index + 15, N_EM_UP, color=clrEM, width=widEM, style=lineStyle(styEMStr))
        emLoLine = line.new(emBar, N_EM_LO, bar_index + 15, N_EM_LO, color=clrEM, width=widEM, style=lineStyle(styEMStr))
        linefill.new(emUpLine, emLoLine, color=color.new(clrEM, 85))
        label.new(bar_index + 17, N_EM_UP, "EM+ " + str.tostring(N_EM_UP, "#"), style=label.style_label_left, color=clrEM, textcolor=color.white, size=size.small)
        label.new(bar_index + 17, N_EM_LO, "EM- " + str.tostring(N_EM_LO, "#"), style=label.style_label_left, color=clrEM, textcolor=color.white, size=size.small)
'''

        # OI buildup lines for NIFTY
        if n["has_prev_data"] and n["oi_buildups"]:
            pine += '    if showOI and barstate.islast\n'
            pine += '        int oiBar = na(sessStart) ? bar_index - 50 : sessStart\n'
            for bu in n["oi_buildups"]:
                chg_str = fmt(bu["net_oi_chg"])
                pine += f'        line.new(oiBar, {bu["strike"]:.2f}, bar_index + 10, {bu["strike"]:.2f}, color=color.new(#4CAF50, 30), width=2, style=line.style_dashed)\n'
                pine += f'        label.new(oiBar - 2, {bu["strike"]:.2f}, "OI+ {bu["strike"]:.0f} (+{chg_str})", style=label.style_label_right, color=color.new(#4CAF50, 50), textcolor=color.white, size=size.small)\n'

    if b:
        pine += '''
// --- BANKNIFTY ---
if showBNF
    drawLevel(B_CW, color.new(clrCW, 30), widCW, styCWStr, "BN Call Wall " + str.tostring(B_CW, "#"), showCW)
    drawLevel(B_PW, color.new(clrPW, 30), widPW, styPWStr, "BN Put Wall " + str.tostring(B_PW, "#"), showPW)
    drawLevel(B_HVL, color.new(clrHVL, 30), widHVL, styHVLStr, "BN HVL " + str.tostring(B_HVL, "#.##"), showHVL)
    drawLevel(B_FLIP, color.new(clrFLIP, 30), widFLIP, styFLIPStr, "BN Local Flip " + str.tostring(B_FLIP, "#.##"), showFLIP)
    drawLevel(B_PG, color.new(clrPG, 30), widPG, styPGStr, "BN Peak Gamma " + str.tostring(B_PG, "#"), showPG)
    drawLevel(B_MP, color.new(clrMP, 30), widMP, styMPStr, "BN Max Pain " + str.tostring(B_MP, "#"), showMP)
'''

        pine += '    if showTopStrikes and barstate.islast\n'
        pine += '        int sBar2 = na(sessStart) ? bar_index - 50 : sessStart\n'
        for i, s in enumerate(b["top_nearby"]):
            gex_str = fmt(s["net_gex"])
            pine += f'        line.new(sBar2, {s["strike"]:.2f}, bar_index + 10, {s["strike"]:.2f}, color=color.new(color.purple, 40), width=1, style=line.style_dotted)\n'
            pine += f'        label.new(sBar2 - 2, {s["strike"]:.2f}, "BN {s["strike"]:.0f} ({gex_str})", style=label.style_label_right, color=color.new(color.gray, 70), textcolor=color.white, size=size.small)\n'

        # EM band for BANKNIFTY
        pine += '''    if showEM and barstate.islast and B_EM_UP > 0 and B_EM_LO > 0
        int emBar2 = na(sessStart) ? bar_index - 50 : sessStart
        // Weekly EM (outer, lighter zone)
        emWkUp2 = line.new(emBar2, B_EM_WK_UP, bar_index + 15, B_EM_WK_UP, color=color.new(clrEM, 60), width=1, style=line.style_dotted)
        emWkLo2 = line.new(emBar2, B_EM_WK_LO, bar_index + 15, B_EM_WK_LO, color=color.new(clrEM, 60), width=1, style=line.style_dotted)
        linefill.new(emWkUp2, emWkLo2, color=color.new(clrEM, 95))
        // Daily EM (inner, solid zone)
        emUpLine2 = line.new(emBar2, B_EM_UP, bar_index + 15, B_EM_UP, color=color.new(clrEM, 30), width=widEM, style=lineStyle(styEMStr))
        emLoLine2 = line.new(emBar2, B_EM_LO, bar_index + 15, B_EM_LO, color=color.new(clrEM, 30), width=widEM, style=lineStyle(styEMStr))
        linefill.new(emUpLine2, emLoLine2, color=color.new(clrEM, 90))
        label.new(bar_index + 17, B_EM_UP, "BN EM+ " + str.tostring(B_EM_UP, "#"), style=label.style_label_left, color=color.new(clrEM, 30), textcolor=color.white, size=size.small)
        label.new(bar_index + 17, B_EM_LO, "BN EM- " + str.tostring(B_EM_LO, "#"), style=label.style_label_left, color=color.new(clrEM, 30), textcolor=color.white, size=size.small)
'''

        # OI buildup lines for BANKNIFTY
        if b["has_prev_data"] and b["oi_buildups"]:
            pine += '    if showOI and barstate.islast\n'
            pine += '        int oiBar2 = na(sessStart) ? bar_index - 50 : sessStart\n'
            for bu in b["oi_buildups"]:
                chg_str = fmt(bu["net_oi_chg"])
                pine += f'        line.new(oiBar2, {bu["strike"]:.2f}, bar_index + 10, {bu["strike"]:.2f}, color=color.new(#4CAF50, 30), width=2, style=line.style_dashed)\n'
                pine += f'        label.new(oiBar2 - 2, {bu["strike"]:.2f}, "BN OI+ {bu["strike"]:.0f} (+{chg_str})", style=label.style_label_right, color=color.new(#4CAF50, 50), textcolor=color.white, size=size.small)\n'

    # Historical level trails
    nifty_levels_track = [
        ("call_wall", "clrCW", "CW"),
        ("put_wall", "clrPW", "PW"),
        ("peak_gamma", "clrPG", "PG"),
        ("max_pain", "clrMP", "MP"),
        ("hvl", "clrHVL", "HVL"),
        ("local_flip", "clrFLIP", "FLIP"),
    ]

    if n and len(n_history) >= 2:
        pine += generate_history_pine(n_history, "NIFTY", nifty_levels_track)
        print(f"  NIFTY history trail: {len(n_history)} snapshots")

    if b and len(b_history) >= 2:
        # Use same colors but with "BN" prefix labels
        bnf_levels_track = [
            ("call_wall", "color.new(clrCW, 30)", "BN CW"),
            ("put_wall", "color.new(clrPW, 30)", "BN PW"),
            ("peak_gamma", "color.new(clrPG, 30)", "BN PG"),
            ("max_pain", "color.new(clrMP, 30)", "BN MP"),
            ("hvl", "color.new(clrHVL, 30)", "BN HVL"),
            ("local_flip", "color.new(clrFLIP, 30)", "BN FLIP"),
        ]
        pine += generate_history_pine(b_history, "BANKNIFTY", bnf_levels_track)
        print(f"  BANKNIFTY history trail: {len(b_history)} snapshots")

    # Table
    n_rows = 2
    if n:
        n_rows += 16
    if b:
        n_rows += 16

    pine += f'''
// ===== LEVELS TEXT BOX =====
var table lvlBox = table.new(position.top_right, 2, {n_rows}, bgcolor=color.new(color.black, 10), border_width=1, border_color=color.new(color.gray, 50))

if barstate.islast and showTable
    int row = 0
    table.cell(lvlBox, 0, row, "GEX LEVELS", text_color=color.white, text_size=size.normal, bgcolor=color.new(color.blue, 30))
    table.cell(lvlBox, 1, row, UPDATED, text_color=color.gray, text_size=size.small, bgcolor=color.new(color.blue, 30))
    row += 1
'''

    if n:
        pine += f'''
    if showNifty
        table.cell(lvlBox, 0, row, "NIFTY", text_color=color.white, text_size=size.normal, bgcolor=color.new(color.teal, 40))
        nBClr = N_BIAS == "STRONG BULL" or N_BIAS == "BULL" ? color.green : N_BIAS == "NEUTRAL" ? color.orange : color.red
        table.cell(lvlBox, 1, row, N_BIAS + " (" + str.tostring(N_BIAS_SCORE) + "/7)", text_color=nBClr, text_size=size.normal, bgcolor=color.new(color.teal, 40))
        row += 1
        nGClr = N_GAMMA_COND == "POSITIVE" ? color.green : color.red
        table.cell(lvlBox, 0, row, "Gamma", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, N_GAMMA_COND + " | " + str.tostring(N_GAMMA_TILT, "#.#") + "% Bull", text_color=nGClr, text_size=size.small)
        row += 1
        table.cell(lvlBox, 0, row, "Call Wall", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(N_CW, "#"), text_color=clrCW, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Put Wall", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(N_PW, "#"), text_color=clrPW, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "HVL", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(N_HVL, "#.##"), text_color=clrHVL, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Local Flip", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(N_FLIP, "#.##"), text_color=clrFLIP, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Peak Gamma", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(N_PG, "#"), text_color=clrPG, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Max Pain", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(N_MP, "#"), text_color=clrMP, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Net GEX", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, "{fmt(n["net_gex"])}", text_color=N_NET_GEX > 0 ? color.green : color.red, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Expiry", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, N_EXPIRY, text_color=color.white, text_size=size.small)
        row += 1
        table.cell(lvlBox, 0, row, "P/C GEX", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, "{n["pc_ratio"]}", text_color=color.white, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Exp Move", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(N_EM_LO, "#") + " - " + str.tostring(N_EM_UP, "#"), text_color=clrEM, text_size=size.normal)
        row += 1
        nSkClr = N_IV_SKEW > 1 ? color.red : N_IV_SKEW < -1 ? color.green : color.orange
        table.cell(lvlBox, 0, row, "IV Skew", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(N_IV_SKEW, "#.##") + " " + N_SKEW_SIG, text_color=nSkClr, text_size=size.small)
        row += 1
        table.cell(lvlBox, 0, row, "OI Chg", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, N_HAS_OI ? N_OI_DIR : "No prev data", text_color=N_OI_DIR == "BUILDUP" ? color.green : color.red, text_size=size.small)
        row += 1
        nRClr = N_REGIME == "RANGE" ? color.blue : N_REGIME == "TREND" ? color.orange : color.gray
        nRText = N_REGIME == "RANGE" ? N_REGIME + " " + str.tostring(N_PW, "#") + "-" + str.tostring(N_CW, "#") : N_REGIME == "TREND" ? N_REGIME + " (follow momentum)" : N_REGIME
        table.cell(lvlBox, 0, row, "Regime", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, nRText, text_color=nRClr, text_size=size.normal)
        row += 1
'''

    if b:
        pine += f'''
    if showBNF
        table.cell(lvlBox, 0, row, "BANKNIFTY", text_color=color.white, text_size=size.normal, bgcolor=color.new(color.purple, 40))
        bBClr = B_BIAS == "STRONG BULL" or B_BIAS == "BULL" ? color.green : B_BIAS == "NEUTRAL" ? color.orange : color.red
        table.cell(lvlBox, 1, row, B_BIAS + " (" + str.tostring(B_BIAS_SCORE) + "/7)", text_color=bBClr, text_size=size.normal, bgcolor=color.new(color.purple, 40))
        row += 1
        bGClr = B_GAMMA_COND == "POSITIVE" ? color.green : color.red
        table.cell(lvlBox, 0, row, "Gamma", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, B_GAMMA_COND + " | " + str.tostring(B_GAMMA_TILT, "#.#") + "% Bull", text_color=bGClr, text_size=size.small)
        row += 1
        table.cell(lvlBox, 0, row, "Call Wall", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(B_CW, "#"), text_color=clrCW, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Put Wall", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(B_PW, "#"), text_color=clrPW, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "HVL", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(B_HVL, "#.##"), text_color=clrHVL, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Local Flip", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(B_FLIP, "#.##"), text_color=clrFLIP, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Peak Gamma", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(B_PG, "#"), text_color=clrPG, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Max Pain", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(B_MP, "#"), text_color=clrMP, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Net GEX", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, "{fmt(b["net_gex"])}", text_color=B_NET_GEX > 0 ? color.green : color.red, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Expiry", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, B_EXPIRY, text_color=color.white, text_size=size.small)
        row += 1
        table.cell(lvlBox, 0, row, "P/C GEX", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, "{b["pc_ratio"]}", text_color=color.white, text_size=size.normal)
        row += 1
        table.cell(lvlBox, 0, row, "Exp Move", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(B_EM_LO, "#") + " - " + str.tostring(B_EM_UP, "#"), text_color=clrEM, text_size=size.normal)
        row += 1
        bSkClr = B_IV_SKEW > 1 ? color.red : B_IV_SKEW < -1 ? color.green : color.orange
        table.cell(lvlBox, 0, row, "IV Skew", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, str.tostring(B_IV_SKEW, "#.##") + " " + B_SKEW_SIG, text_color=bSkClr, text_size=size.small)
        row += 1
        table.cell(lvlBox, 0, row, "OI Chg", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, B_HAS_OI ? B_OI_DIR : "No prev data", text_color=B_OI_DIR == "BUILDUP" ? color.green : color.red, text_size=size.small)
        row += 1
        bRClr = B_REGIME == "RANGE" ? color.blue : B_REGIME == "TREND" ? color.orange : color.gray
        bRText = B_REGIME == "RANGE" ? B_REGIME + " " + str.tostring(B_PW, "#") + "-" + str.tostring(B_CW, "#") : B_REGIME == "TREND" ? B_REGIME + " (follow momentum)" : B_REGIME
        table.cell(lvlBox, 0, row, "Regime", text_color=color.gray, text_size=size.small)
        table.cell(lvlBox, 1, row, bRText, text_color=bRClr, text_size=size.normal)
        row += 1
'''

    return pine


def generate_gold_pine(gold_data):
    """Generate Pine Script for XAUUSD chart with MCX GOLD GEX levels converted to USD."""
    if not gold_data:
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    xau = gold_data.get("xauusd", {})
    xau_lvl = xau.get("levels", {})
    levels = gold_data["levels"]
    local_flip = levels.get("local_flip", {})

    def fmt(n):
        abs_n = abs(n)
        if abs_n >= 1e9:
            return f"{n/1e9:.1f}B"
        elif abs_n >= 1e6:
            return f"{n/1e6:.1f}M"
        elif abs_n >= 1e3:
            return f"{n/1e3:.1f}K"
        return f"{n:.0f}"

    def get_top_nearby(data):
        spot = data["spot"]
        strikes_data = data.get("strikes", [])
        nearby = [r for r in strikes_data if abs(r["strike"] - spot) < spot * 0.03]
        return sorted(nearby, key=lambda x: abs(x["net_gex"]), reverse=True)[:5]

    usdinr = xau.get("usdinr", 85.0)
    xau_spot = xau.get("spot", 0)
    conv = 31.1035 / (10 * usdinr)  # MCX to XAUUSD conversion factor

    # Extract XAUUSD levels
    cw = xau_lvl.get("call_wall", 0) or 0
    pw = xau_lvl.get("put_wall", 0) or 0
    hvl = xau_lvl.get("hvl", 0) or 0
    flip = xau_lvl.get("local_flip", 0) or 0
    pg = xau_lvl.get("peak_gamma", 0) or 0
    mp = xau_lvl.get("max_pain", 0) or 0
    em_up = xau_lvl.get("em_upper", 0) or 0
    em_lo = xau_lvl.get("em_lower", 0) or 0

    net_gex = gold_data["net_gex"]
    call_gex = gold_data["call_gex"]
    put_gex = gold_data["put_gex"]
    pc_ratio = f"{abs(put_gex/call_gex):.2f}" if call_gex != 0 else "N/A"

    bias = levels.get("bias", "NEUTRAL")
    bias_score = levels.get("bias_score", 0)
    gamma_cond = levels.get("gamma_condition", "UNKNOWN")
    gamma_tilt = levels.get("gamma_tilt", 0)
    regime = levels.get("regime", "MIXED")
    regime_score = levels.get("regime_score", 0)
    iv_skew = levels.get("iv_skew", 0)
    skew_signal = levels.get("skew_signal", "NEUTRAL")
    oi_dir = levels.get("net_oi_chg_direction", "N/A")
    has_prev = levels.get("has_prev_data", False)
    atm_iv = levels.get("atm_iv", 0)
    dte = levels.get("dte", 0)
    total_ce_oi = levels.get("total_ce_oi_chg", 0)
    total_pe_oi = levels.get("total_pe_oi_chg", 0)

    # Top nearby strikes converted to XAUUSD
    top_nearby = get_top_nearby(gold_data)

    # OI buildups converted to XAUUSD
    oi_buildups = levels.get("oi_buildups", [])

    # Load session history for historical trails
    hist_dir = "C:/tv/gex_history"
    today = date.today().strftime("%Y%m%d")
    hist_file = os.path.join(hist_dir, f"GOLD_{today}.json")
    gold_history = []
    if os.path.exists(hist_file):
        try:
            with open(hist_file, "r") as f:
                gold_history = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    pine = f'''// GOLD GEX Levels for XAUUSD — Auto-generated
// MCX GOLD options GEX converted to XAUUSD via USDINR
// Last updated: {timestamp}
//@version=6
indicator("GOLD GEX Levels (XAUUSD)", overlay=true, max_lines_count=500, max_labels_count=500, max_boxes_count=500)

// ===== DISPLAY TOGGLES =====
showLevels = input.bool(true, "Show GEX Levels", group="Display")
showTable = input.bool(true, "Show Levels Table", group="Display")
showEM = input.bool(true, "Expected Move Zone", group="Display")
showOI = input.bool(true, "OI Buildup Lines", group="Display")
showHist = input.bool(true, "Level History Trail", group="Display")

// ===== LEVEL STYLE INPUTS =====
showCW = input.bool(true, "Call Wall", group="Call Wall Style")
clrCW = input.color(#FF0000, "Color", group="Call Wall Style")
widCW = input.int(3, "Width", minval=1, maxval=5, group="Call Wall Style")
styCWStr = input.string("Solid", "Style", options=["Solid", "Dashed", "Dotted"], group="Call Wall Style")

showPW = input.bool(true, "Put Wall", group="Put Wall Style")
clrPW = input.color(#00FF00, "Color", group="Put Wall Style")
widPW = input.int(3, "Width", minval=1, maxval=5, group="Put Wall Style")
styPWStr = input.string("Solid", "Style", options=["Solid", "Dashed", "Dotted"], group="Put Wall Style")

showHVL = input.bool(true, "HVL", group="HVL Style")
clrHVL = input.color(#FF9800, "Color", group="HVL Style")
widHVL = input.int(3, "Width", minval=1, maxval=5, group="HVL Style")
styHVLStr = input.string("Dashed", "Style", options=["Solid", "Dashed", "Dotted"], group="HVL Style")

showFLIP = input.bool(true, "Local Flip", group="Local Flip Style")
clrFLIP = input.color(#FFEB3B, "Color", group="Local Flip Style")
widFLIP = input.int(2, "Width", minval=1, maxval=5, group="Local Flip Style")
styFLIPStr = input.string("Dotted", "Style", options=["Solid", "Dashed", "Dotted"], group="Local Flip Style")

showPG = input.bool(true, "Peak Gamma", group="Peak Gamma Style")
clrPG = input.color(#FF00FF, "Color", group="Peak Gamma Style")
widPG = input.int(3, "Width", minval=1, maxval=5, group="Peak Gamma Style")
styPGStr = input.string("Solid", "Style", options=["Solid", "Dashed", "Dotted"], group="Peak Gamma Style")

showMP = input.bool(true, "Max Pain", group="Max Pain Style")
clrMP = input.color(#00FFFF, "Color", group="Max Pain Style")
widMP = input.int(3, "Width", minval=1, maxval=5, group="Max Pain Style")
styMPStr = input.string("Dashed", "Style", options=["Solid", "Dashed", "Dotted"], group="Max Pain Style")

clrEM = input.color(#2196F3, "Color", group="Expected Move Style")
widEM = input.int(2, "Width", minval=1, maxval=5, group="Expected Move Style")
styEMStr = input.string("Dashed", "Style", options=["Solid", "Dashed", "Dotted"], group="Expected Move Style")

// Style converter
lineStyle(string s) =>
    s == "Dashed" ? line.style_dashed : s == "Dotted" ? line.style_dotted : line.style_solid

var string UPDATED = "{timestamp}"

// ===== GOLD GEX LEVELS (XAUUSD) =====
G_CW = input.float({cw:.2f}, "Call Wall", group="GOLD XAUUSD Levels")
G_PW = input.float({pw:.2f}, "Put Wall", group="GOLD XAUUSD Levels")
G_HVL = input.float({hvl:.2f}, "HVL", group="GOLD XAUUSD Levels")
G_FLIP = input.float({flip:.2f}, "Local Flip", group="GOLD XAUUSD Levels")
G_PG = input.float({pg:.2f}, "Peak Gamma", group="GOLD XAUUSD Levels")
G_MP = input.float({mp:.2f}, "Max Pain", group="GOLD XAUUSD Levels")
var string G_GAMMA_COND = "{gamma_cond}"
var float G_GAMMA_TILT = {gamma_tilt:.1f}
var float G_NET_GEX = {net_gex:.0f}
var string G_EXPIRY = "{gold_data["expiry"]}"
var string G_BIAS = "{bias}"
var int G_BIAS_SCORE = {bias_score}
var float G_EM_UP = {em_up:.2f}
var float G_EM_LO = {em_lo:.2f}
var float G_ATM_IV = {atm_iv:.1f}
var int G_DTE = {dte}
var float G_IV_SKEW = {iv_skew:.2f}
var string G_SKEW_SIG = "{skew_signal}"
var string G_OI_DIR = "{oi_dir}"
var int G_OI_CE_CHG = {total_ce_oi}
var int G_OI_PE_CHG = {total_pe_oi}
var bool G_HAS_OI = {"true" if has_prev else "false"}
var string G_REGIME = "{regime}"
var int G_REGIME_SCORE = {regime_score}
var float G_USDINR = {usdinr:.2f}
var float G_MCX_SPOT = {gold_data["spot"]:.0f}

// ===== SESSION TRACKING =====
var int sessStart = na
newSess = session.isfirstbar
if newSess
    sessStart := bar_index

// Helper: draw level line + label
drawLevel(float price, color clr, int wid, string styStr, string txt, bool show) =>
    if show and not na(price) and price > 0 and barstate.islast
        startBar = na(sessStart) ? bar_index - 50 : sessStart
        l = line.new(startBar, price, bar_index + 15, price, color=clr, width=wid, style=lineStyle(styStr), extend=extend.none)
        label.new(bar_index + 17, price, txt, style=label.style_label_left, color=clr, textcolor=color.white, size=size.small)

// ===== DRAW LEVELS =====
if showLevels
    drawLevel(G_CW, clrCW, widCW, styCWStr, "Call Wall $" + str.tostring(G_CW, "#.##"), showCW)
    drawLevel(G_PW, clrPW, widPW, styPWStr, "Put Wall $" + str.tostring(G_PW, "#.##"), showPW)
    drawLevel(G_HVL, clrHVL, widHVL, styHVLStr, "HVL $" + str.tostring(G_HVL, "#.##"), showHVL)
    drawLevel(G_FLIP, clrFLIP, widFLIP, styFLIPStr, "Flip $" + str.tostring(G_FLIP, "#.##"), showFLIP)
    drawLevel(G_PG, clrPG, widPG, styPGStr, "Peak Gamma $" + str.tostring(G_PG, "#.##"), showPG)
    drawLevel(G_MP, clrMP, widMP, styMPStr, "Max Pain $" + str.tostring(G_MP, "#.##"), showMP)
'''

    # Top nearby strikes as XAUUSD
    if top_nearby:
        pine += '    if barstate.islast\n'
        pine += '        int sBar = na(sessStart) ? bar_index - 50 : sessStart\n'
        for s in top_nearby:
            xau_strike = s["strike"] * conv
            gex_str = fmt(s["net_gex"])
            pine += f'        line.new(sBar, {xau_strike:.2f}, bar_index + 10, {xau_strike:.2f}, color=color.new(#FFD700, 40), width=1, style=line.style_dotted)\n'
            pine += f'        label.new(sBar - 2, {xau_strike:.2f}, "${xau_strike:.0f} ({gex_str})", style=label.style_label_right, color=color.new(color.gray, 70), textcolor=color.white, size=size.small)\n'

    # EM band
    pine += '''
    if showEM and barstate.islast and G_EM_UP > 0 and G_EM_LO > 0
        int emBar = na(sessStart) ? bar_index - 50 : sessStart
        emUpLine = line.new(emBar, G_EM_UP, bar_index + 15, G_EM_UP, color=clrEM, width=widEM, style=lineStyle(styEMStr))
        emLoLine = line.new(emBar, G_EM_LO, bar_index + 15, G_EM_LO, color=clrEM, width=widEM, style=lineStyle(styEMStr))
        linefill.new(emUpLine, emLoLine, color=color.new(clrEM, 85))
        label.new(bar_index + 17, G_EM_UP, "EM+ $" + str.tostring(G_EM_UP, "#.##"), style=label.style_label_left, color=clrEM, textcolor=color.white, size=size.small)
        label.new(bar_index + 17, G_EM_LO, "EM- $" + str.tostring(G_EM_LO, "#.##"), style=label.style_label_left, color=clrEM, textcolor=color.white, size=size.small)
'''

    # OI buildup lines converted to XAUUSD
    if has_prev and oi_buildups:
        pine += '    if showOI and barstate.islast\n'
        pine += '        int oiBar = na(sessStart) ? bar_index - 50 : sessStart\n'
        for bu in oi_buildups:
            xau_strike = bu["strike"] * conv
            chg_str = fmt(bu["net_oi_chg"])
            pine += f'        line.new(oiBar, {xau_strike:.2f}, bar_index + 10, {xau_strike:.2f}, color=color.new(#4CAF50, 30), width=2, style=line.style_dashed)\n'
            pine += f'        label.new(oiBar - 2, {xau_strike:.2f}, "OI+ ${xau_strike:.0f} (+{chg_str})", style=label.style_label_right, color=color.new(#4CAF50, 50), textcolor=color.white, size=size.small)\n'

    # Historical level trails (converted to XAUUSD)
    if len(gold_history) >= 2:
        vp = "g"
        pine += f"\n// ===== GOLD LEVEL HISTORY ({len(gold_history)} snapshots) =====\n"
        pine += "if showHist and barstate.islast\n"
        pine += f"    int {vp}hBar = na(sessStart) ? bar_index - 50 : sessStart\n"
        pine += f"    int {vp}hSpan = bar_index - {vp}hBar\n"

        history_levels = [
            ("call_wall", "clrCW", "CW"),
            ("put_wall", "clrPW", "PW"),
            ("peak_gamma", "clrPG", "PG"),
            ("max_pain", "clrMP", "MP"),
        ]

        for key, color_expr, label in history_levels:
            values = [h.get(key, 0) for h in gold_history]
            xau_values = [v * conv if v and v > 0 else 0 for v in values]
            unique_vals = set(v for v in xau_values if v > 0)
            if len(unique_vals) <= 1:
                continue

            for i in range(len(xau_values) - 1):
                v1 = xau_values[i]
                v2 = xau_values[i + 1]
                if v1 <= 0:
                    continue
                frac1 = i / (len(gold_history) - 1)
                frac2 = (i + 1) / (len(gold_history) - 1)
                pine += f"    line.new({vp}hBar + math.round({vp}hSpan * {frac1:.4f}), {v1:.2f}, {vp}hBar + math.round({vp}hSpan * {frac2:.4f}), {v1:.2f}, color=color.new({color_expr}, 60), width=1, style=line.style_dotted)\n"
                if v2 > 0 and v1 != v2:
                    pine += f"    line.new({vp}hBar + math.round({vp}hSpan * {frac2:.4f}), {v1:.2f}, {vp}hBar + math.round({vp}hSpan * {frac2:.4f}), {v2:.2f}, color=color.new({color_expr}, 60), width=1, style=line.style_dotted)\n"

        print(f"  GOLD history trail: {len(gold_history)} snapshots")

    # Table
    pine += f'''
// ===== LEVELS TABLE =====
var table lvlBox = table.new(position.top_right, 2, 18, bgcolor=color.new(color.black, 10), border_width=1, border_color=color.new(color.gray, 50))

if barstate.islast and showTable
    int row = 0
    table.cell(lvlBox, 0, row, "GOLD GEX (XAUUSD)", text_color=#FFD700, text_size=size.normal, bgcolor=color.new(#FFD700, 80))
    table.cell(lvlBox, 1, row, UPDATED, text_color=color.gray, text_size=size.small, bgcolor=color.new(#FFD700, 80))
    row += 1

    gBClr = G_BIAS == "STRONG BULL" or G_BIAS == "BULL" ? color.green : G_BIAS == "NEUTRAL" ? color.orange : color.red
    table.cell(lvlBox, 0, row, "BIAS", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, G_BIAS + " (" + str.tostring(G_BIAS_SCORE) + "/7)", text_color=gBClr, text_size=size.normal)
    row += 1

    gGClr = G_GAMMA_COND == "POSITIVE" ? color.green : color.red
    table.cell(lvlBox, 0, row, "Gamma", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, G_GAMMA_COND + " | " + str.tostring(G_GAMMA_TILT, "#.#") + "% Bull", text_color=gGClr, text_size=size.small)
    row += 1

    table.cell(lvlBox, 0, row, "Call Wall", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, "$" + str.tostring(G_CW, "#.##"), text_color=clrCW, text_size=size.normal)
    row += 1

    table.cell(lvlBox, 0, row, "Put Wall", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, "$" + str.tostring(G_PW, "#.##"), text_color=clrPW, text_size=size.normal)
    row += 1

    table.cell(lvlBox, 0, row, "HVL", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, "$" + str.tostring(G_HVL, "#.##"), text_color=clrHVL, text_size=size.normal)
    row += 1

    table.cell(lvlBox, 0, row, "Local Flip", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, "$" + str.tostring(G_FLIP, "#.##"), text_color=clrFLIP, text_size=size.normal)
    row += 1

    table.cell(lvlBox, 0, row, "Peak Gamma", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, "$" + str.tostring(G_PG, "#.##"), text_color=clrPG, text_size=size.normal)
    row += 1

    table.cell(lvlBox, 0, row, "Max Pain", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, "$" + str.tostring(G_MP, "#.##"), text_color=clrMP, text_size=size.normal)
    row += 1

    table.cell(lvlBox, 0, row, "Net GEX", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, "{fmt(net_gex)}", text_color=G_NET_GEX > 0 ? color.green : color.red, text_size=size.normal)
    row += 1

    table.cell(lvlBox, 0, row, "Exp Move", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, "$" + str.tostring(G_EM_LO, "#.##") + " - $" + str.tostring(G_EM_UP, "#.##"), text_color=clrEM, text_size=size.normal)
    row += 1

    table.cell(lvlBox, 0, row, "ATM IV / DTE", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, str.tostring(G_ATM_IV, "#.#") + "% / " + str.tostring(G_DTE) + "d", text_color=color.white, text_size=size.small)
    row += 1

    gSkClr = G_IV_SKEW > 1 ? color.red : G_IV_SKEW < -1 ? color.green : color.orange
    table.cell(lvlBox, 0, row, "IV Skew", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, str.tostring(G_IV_SKEW, "#.##") + " " + G_SKEW_SIG, text_color=gSkClr, text_size=size.small)
    row += 1

    table.cell(lvlBox, 0, row, "OI Chg", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, G_HAS_OI ? G_OI_DIR : "No prev data", text_color=G_OI_DIR == "BUILDUP" ? color.green : color.red, text_size=size.small)
    row += 1

    table.cell(lvlBox, 0, row, "USDINR", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, str.tostring(G_USDINR, "#.##"), text_color=#FFD700, text_size=size.small)
    row += 1

    table.cell(lvlBox, 0, row, "MCX Spot", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, str.tostring(G_MCX_SPOT, "#"), text_color=#FFD700, text_size=size.small)
    row += 1

    gRClr = G_REGIME == "RANGE" ? color.blue : G_REGIME == "TREND" ? color.orange : color.gray
    table.cell(lvlBox, 0, row, "Regime", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, G_REGIME, text_color=gRClr, text_size=size.normal)
    row += 1

    table.cell(lvlBox, 0, row, "Expiry", text_color=color.gray, text_size=size.small)
    table.cell(lvlBox, 1, row, G_EXPIRY, text_color=color.white, text_size=size.small)
'''

    return pine


def push_to_tradingview(pine_code, symbol="combined"):
    """Save Pine Script to file and republish to TradingView."""
    filename = f"C:/tv/gex_{symbol.lower()}_indicator.pine"
    with open(filename, "w") as f:
        f.write(pine_code)
    print(f"Pine Script saved to {filename}")
    republish_pine(pine_code, symbol)
    return pine_code


def main():
    loop_mode = "--loop" in sys.argv
    # If specific symbol given, only fetch that one
    args = [a.upper() for a in sys.argv[1:] if not a.startswith("--")]

    while True:
        print(f"\n{'='*50}")
        print(f"  GEX Update - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*50}")

        nifty_data = None
        bnf_data = None
        gold_data = None

        if not args or "NIFTY" in args:
            nifty_data = run_gex("NIFTY")

        if not args or "BANKNIFTY" in args:
            import time as t
            if nifty_data:
                t.sleep(2)  # Rate limit between API calls
            bnf_data = run_gex("BANKNIFTY")

        if not args or "GOLD" in args:
            import time as t
            t.sleep(2)
            gold_data = run_gex("GOLD")

        if nifty_data or bnf_data:
            pine_code = generate_combined_pine(nifty_data, bnf_data)
            if pine_code:
                push_to_tradingview(pine_code, "combined")
                # Also save individual files for reference
                if nifty_data:
                    push_to_tradingview(
                        generate_combined_pine(nifty_data, None), "nifty"
                    )
                if bnf_data:
                    push_to_tradingview(
                        generate_combined_pine(None, bnf_data), "banknifty"
                    )
                print(f"\n[OK] Combined Pine Script ready (NIFTY/BANKNIFTY)")
            else:
                print("[ERROR] Failed to generate Pine Script")

        if gold_data:
            gold_pine = generate_gold_pine(gold_data)
            if gold_pine:
                push_to_tradingview(gold_pine, "gold_xauusd")
                print(f"[OK] GOLD XAUUSD Pine Script ready")
            else:
                print("[ERROR] Failed to generate GOLD Pine Script")

        if not nifty_data and not bnf_data and not gold_data:
            print("[ERROR] Failed to calculate GEX")

        if not loop_mode:
            break

        print(f"\nNext update in 5 minutes...")
        time.sleep(300)


if __name__ == "__main__":
    main()
