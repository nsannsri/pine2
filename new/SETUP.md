# GEX Levels - Setup Guide

Auto-fetch Gamma Exposure (GEX) levels for NIFTY & BANKNIFTY from Dhan API, generate a Pine Script indicator, and push it live to TradingView Desktop.

---

## Architecture

```
Dhan API  -->  gex_calculator.py  -->  gex_tv_push.py  -->  .pine files
                                                                 |
                                                            gex_push.py
                                                                 |
                                                   +-------------+-------------+
                                                   |                           |
                                          TradingView Desktop         pine-facade API
                                           (Monaco editor,            (republish public
                                            via Chrome DevTools)       "GEX Levels" script)
```

| File | Purpose |
|------|---------|
| `gex_calculator.py` | Fetches option chain from Dhan API, calculates GEX, Expected Move, IV Skew, OI changes |
| `gex_tv_push.py` | Generates Pine Script indicator code from calculator output |
| `gex_push.py` | Pushes Pine Script via CDP + republishes the public TradingView script |
| `.env` | Dhan API credentials |

---

## Prerequisites

- **Python 3.10+**
- **TradingView Desktop** (Windows/Mac/Linux) - the desktop app, not the website
- **Dhan trading account** with API access

---

## Step 1: Clone / Copy Files

Create a working directory and copy these files into it:

```
C:\tv\
  gex_calculator.py
  gex_tv_push.py
  gex_push.py
  .env
```

Or wherever you prefer. Just keep all 3 `.py` files in the same folder.

---

## Step 2: Install Python Packages

```bash
pip install requests python-dotenv websocket-client
```

| Package | Purpose |
|---------|---------|
| `requests` | HTTP calls to Dhan API + TradingView republish API |
| `python-dotenv` | Loads `.env` file for API credentials |
| `websocket-client` | CDP WebSocket connection to TradingView |

---

## Step 3: Dhan API Credentials

Create a `.env` file in the project directory:

```env
DHAN_CLIENT_ID=your_client_id_here
DHAN_ACCESS_TOKEN=your_access_token_here
```

**How to get these:**

1. Log in to [Dhan](https://dhan.co)
2. Go to **Profile > API Access** (or https://knowledge-center.dhan.co/tradingapis)
3. Generate an API token
4. Copy your Client ID and Access Token into `.env`

> **Note:** Dhan tokens expire periodically. If you get 401 errors, regenerate your token.

---

## Step 4: Launch TradingView with Remote Debugging

TradingView Desktop must be started with a special flag to allow the script to talk to it.

### Windows

Create a shortcut or run from Command Prompt:

```cmd
"C:\Users\<username>\AppData\Local\TradingView\TradingView.exe" --remote-debugging-port=9222
```

Or use the provided batch file (if you have the MCP repo):

```cmd
C:\Users\<username>\tradingview-mcp-jackson\scripts\launch_tv_debug.bat
```

### Mac

```bash
/Applications/TradingView.app/Contents/MacOS/TradingView --remote-debugging-port=9222
```

### Linux

```bash
tradingview --remote-debugging-port=9222
```

### Verify it's working

Open a browser and go to:

```
http://localhost:9222/json/version
```

You should see a JSON response with TradingView version info. If you get a connection error, TradingView isn't running with the debug flag.

---

## Step 5: First Run (One-Time Setup)

1. Open TradingView Desktop (with debug flag from Step 4)
2. Open a chart (NIFTY or BANKNIFTY)
3. **Open the Pine Editor** at least once (bottom panel > Pine Editor)
4. Run the script:

```bash
cd C:\tv
python gex_push.py
```

The first run will:
- Fetch NIFTY & BANKNIFTY option chain data from Dhan
- Calculate GEX levels, Expected Move, IV Skew, OI changes
- Generate the Pine Script indicator
- Push it to TradingView and compile it via CDP
- **Republish the public "GEX Levels" script** on TradingView automatically

**Expected output:**

```
[INFO] GEX Push - 2026-04-06 08:15:37
[INFO] Running gex_tv_push.py to fetch data and generate Pine...
[INFO]   Spot price: 22638.85
[INFO]   GEX REPORT - NIFTY | Spot: 22638.85 | Expiry: 2026-04-07
[INFO]   Call Wall (Resistance):       23000
[INFO]   Put Wall (Support):           22600
[INFO]   BIAS: BULL (score: +4/7)
[INFO]   ...
[INFO] Pine file loaded: 298 lines, 21791 chars
[INFO] CDP WebSocket connected
[INFO] Opening Pine Editor panel...
[INFO] Injecting Pine Script (298 lines)...
[INFO] Compiled clean - 0 errors
[INFO] [PUBLISH] 'GEX Levels' republished OK
[INFO] DONE - pushed in 15.2s
```

---

## Usage

### One-shot update (fetch + push)

```bash
python gex_push.py
```

Fetches latest data from Dhan API, generates Pine Script, pushes to TradingView.

### Push only (skip API fetch)

```bash
python gex_push.py --push-only
```

Pushes the existing `gex_combined_indicator.pine` file. Useful if you just want to re-push without re-fetching data.

### Auto-refresh loop

```bash
python gex_push.py --loop
```

Runs continuously, refreshing every 5 minutes. Good for live market hours.

---

## Running in Background

### Windows - Background (hidden window)

```cmd
start /B pythonw gex_push.py --loop
```

Or create a `.vbs` file (`run_gex.vbs`):

```vbs
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "python C:\tv\gex_push.py --loop", 0, False
```

Double-click the `.vbs` file to run silently.

### Windows - Task Scheduler (auto-start)

1. Open **Task Scheduler** (search in Start Menu)
2. Click **Create Basic Task**
3. Name: `GEX Push`
4. Trigger: **When I log on** (or **Daily** at market open 9:00 AM)
5. Action: **Start a program**
   - Program: `pythonw`
   - Arguments: `C:\tv\gex_push.py --loop`
   - Start in: `C:\tv`
6. Finish

### Linux / Mac - Background

```bash
nohup python gex_push.py --loop > /dev/null 2>&1 &
```

### Linux / Mac - Systemd Service

Create `/etc/systemd/system/gex-push.service`:

```ini
[Unit]
Description=GEX Levels Auto-Push
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/tv
ExecStart=/usr/bin/python3 gex_push.py --loop
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl enable gex-push
sudo systemctl start gex-push
sudo systemctl status gex-push    # check status
journalctl -u gex-push -f         # view logs
```

### Linux / Mac - Crontab (run every 5 min during market hours)

```bash
crontab -e
```

Add:

```cron
*/5 9-16 * * 1-5 cd /path/to/tv && python gex_push.py >> /path/to/tv/logs/cron.log 2>&1
```

This runs every 5 minutes, Monday-Friday, 9 AM - 4 PM.

---

## Logs

All logs are saved to:

```
C:\tv\logs\gex_push_YYYYMMDD.log
```

A new log file is created each day. Logs include:
- GEX report summary (key levels, bias, expected move)
- CDP connection status
- Pine Script injection and compilation results
- Errors with full stack traces

---

## What the Indicator Shows

| Feature | Description |
|---------|-------------|
| **Call Wall** (red) | Highest call OI strike - resistance level |
| **Put Wall** (green) | Highest put OI strike - support level |
| **HVL** (orange) | High Volume Level - cumulative GEX flip point |
| **Local Flip** (yellow) | Nearest gamma flip level to spot |
| **Peak Gamma** (magenta) | Highest gamma strike - magnet level |
| **Max Pain** (cyan) | Expiry pin level where options expire worthless |
| **Expected Move** (blue zone) | 1SD daily and weekly expected range from IV |
| **Top GEX Strikes** (dotted) | Top 5 net GEX strikes |
| **OI Buildup** (green dashed) | Strikes with highest OI increase day-over-day |
| **Info Table** | All metrics in a table (top-right corner) |

### BIAS Score (/7)

Composite score from 6 signals:
- Gamma condition (positive/negative)
- Gamma tilt (bullish/bearish near spot)
- Spot position relative to key levels
- Net GEX sign
- Put/Call GEX ratio
- Distance to Call/Put walls

| Score | Label |
|-------|-------|
| 6-7 | STRONG BULL |
| 4-5 | BULL |
| 3 | NEUTRAL |
| 1-2 | BEAR |
| 0 | STRONG BEAR |

---

## Troubleshooting

### "Cannot reach CDP at port 9222"

TradingView is not running with `--remote-debugging-port=9222`. Restart it with the flag.

### "No TradingView tab found"

TradingView is running but no chart is open. Open a chart tab.

### "Monaco editor not found"

The Pine Editor failed to initialize. Open Pine Editor manually once in TradingView, then re-run.

### 401 / Authentication errors from Dhan

Your API token has expired. Generate a new one at Dhan and update `.env`.

### "Handshake status 403 Forbidden"

If you see origin-related errors, make sure TradingView is launched with:

```
--remote-debugging-port=9222 --remote-allow-origins=*
```

### "[PUBLISH] Failed HTTP 4xx"

Your TradingView session has expired. Log in to TradingView Desktop again — the cookie is refreshed automatically on next run.

### "[PUBLISH] 'GEX Levels' not found in published scripts"

The published script name in `gex_push.py` doesn't match. Check `PUBLISH_SCRIPT_NAME` at the top of the file and update it to match the exact name of your published script.

### OI Change shows "No prev data"

Normal on first run. OI snapshots are saved to `C:\tv\oi_history\`. Run again the next day to see day-over-day changes.

---

## File Reference

```
C:\tv\
  gex_calculator.py           # Core GEX calculation engine
  gex_tv_push.py              # Pine Script generator
  gex_push.py                 # CDP push + auto-republish script (run this)
  republish.py                # Standalone republish utility (optional)
  capture_publish.js          # Dev tool: capture TradingView network requests
  .env                        # Dhan API credentials
  gex_combined_indicator.pine # Generated Pine (NIFTY + BANKNIFTY)
  gex_nifty_indicator.pine    # Generated Pine (NIFTY only)
  gex_banknifty_indicator.pine# Generated Pine (BANKNIFTY only)
  oi_history/                 # Daily OI snapshots for change tracking
  logs/                       # Daily log files
```

---

## How Auto-Republish Works

`gex_push.py` republishes the public TradingView script automatically after each push — no browser interaction needed.

**How:**
- Reads the `sessionid` cookie directly from TradingView Desktop's local cookie store (`AppData\Roaming\TradingView\Network\Cookies`)
- Calls TradingView's internal API: `POST pine-facade.tradingview.com/pine-facade/publish/next/{PUB_ID}`
- The published script name is configured via `PUBLISH_SCRIPT_NAME` at the top of `gex_push.py`

**To change which script gets republished**, edit this line in `gex_push.py`:
```python
PUBLISH_SCRIPT_NAME = "GEX Levels"
```
