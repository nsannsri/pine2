"""
GEX Auto-Push: Fetch GEX levels from Dhan API → Generate Pine Script → Push to TradingView via CDP
Zero token cost. Runs entirely locally.

Usage:
  python gex_push.py              (one-shot: fetch + push)
  python gex_push.py --loop       (auto-refresh every 5 min)
  python gex_push.py --push-only  (push existing .pine file without re-fetching)

Requirements:
  pip install websocket-client
  TradingView Desktop must be running with --remote-debugging-port=9222
"""

import sys
import os
import json
import time
import logging
import sqlite3
import shutil
import tempfile
import requests
from datetime import datetime

# Published script name on TradingView to republish after push
PUBLISH_SCRIPT_NAME = "GEX Levels"


def republish_to_tv(pine_source):
    """Republish pine_source to the TradingView public script."""
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

    headers = {
        'cookie': '; '.join(f'{k}={v}' for k, v in cookies.items()),
        'origin': 'https://in.tradingview.com',
        'referer': 'https://in.tradingview.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }

    # Get published script list and find our script's PUB ID
    r = requests.get('https://pine-facade.tradingview.com/pine-facade/list?filter=published', headers=headers, timeout=10)
    r.raise_for_status()
    scripts = {s['scriptName']: s['scriptIdPart'] for s in r.json()}
    pub_id = scripts.get(PUBLISH_SCRIPT_NAME)
    if not pub_id:
        log.warning(f"[PUBLISH] '{PUBLISH_SCRIPT_NAME}' not found in published scripts")
        return False

    url = f'https://pine-facade.tradingview.com/pine-facade/publish/next/{requests.utils.quote(pub_id, safe="")}'
    r = requests.post(url, data={'source': pine_source}, headers=headers, timeout=15)
    if r.status_code == 200:
        log.info(f"[PUBLISH] '{PUBLISH_SCRIPT_NAME}' republished OK")
        return True
    else:
        log.error(f"[PUBLISH] Failed HTTP {r.status_code}: {r.text[:200]}")
        return False

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
LOG_DIR = "C:/tv/logs"
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, f"gex_push_{datetime.now().strftime('%Y%m%d')}.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("gex_push")

# ---------------------------------------------------------------------------
# CDP (Chrome DevTools Protocol) helpers
# ---------------------------------------------------------------------------
CDP_PORT = 9222
PINE_FILE = "C:/tv/gex_combined_indicator.pine"

# JS: Find Monaco editor instance via React fiber tree
FIND_MONACO_JS = r"""
(function(){
    var c = document.querySelector(".monaco-editor.pine-editor-monaco");
    if (!c) return null;
    var el = c;
    var fk;
    for (var i = 0; i < 20; i++) {
        if (!el) break;
        fk = Object.keys(el).find(function(k){ return k.startsWith("__reactFiber$"); });
        if (fk) break;
        el = el.parentElement;
    }
    if (!fk) return null;
    var cur = el[fk];
    for (var d = 0; d < 15; d++) {
        if (!cur) break;
        if (cur.memoizedProps && cur.memoizedProps.value && cur.memoizedProps.value.monacoEnv) {
            var env = cur.memoizedProps.value.monacoEnv;
            if (env.editor && typeof env.editor.getEditors === "function") {
                var eds = env.editor.getEditors();
                if (eds.length > 0) return "FOUND";
            }
        }
        cur = cur.return;
    }
    return null;
})()
"""

# JS: Set source code in Monaco editor
SET_SOURCE_JS = r"""
(function(){
    var c = document.querySelector(".monaco-editor.pine-editor-monaco");
    if (!c) return "ERR:no_monaco_element";
    var el = c;
    var fk;
    for (var i = 0; i < 20; i++) {
        if (!el) break;
        fk = Object.keys(el).find(function(k){ return k.startsWith("__reactFiber$"); });
        if (fk) break;
        el = el.parentElement;
    }
    if (!fk) return "ERR:no_react_fiber";
    var cur = el[fk];
    for (var d = 0; d < 15; d++) {
        if (!cur) break;
        if (cur.memoizedProps && cur.memoizedProps.value && cur.memoizedProps.value.monacoEnv) {
            var env = cur.memoizedProps.value.monacoEnv;
            if (env.editor && typeof env.editor.getEditors === "function") {
                var eds = env.editor.getEditors();
                if (eds.length > 0) {
                    eds[0].setValue(__SOURCE__);
                    return "OK:" + eds[0].getModel().getLineCount();
                }
            }
        }
        cur = cur.return;
    }
    return "ERR:editor_not_found";
})()
"""

# JS: Click compile/save button
COMPILE_JS = r"""
(function(){
    var btns = document.querySelectorAll("button");
    for (var i = 0; i < btns.length; i++) {
        var t = btns[i].textContent.trim();
        if (/save and add to chart/i.test(t)) { btns[i].click(); return "Save and add to chart"; }
        if (/^(Add to chart)/i.test(t)) { btns[i].click(); return "Add to chart"; }
        if (/^(Update on chart)/i.test(t)) { btns[i].click(); return "Update on chart"; }
    }
    for (var i = 0; i < btns.length; i++) {
        if (btns[i].className.indexOf("saveButton") !== -1 && btns[i].offsetParent !== null) {
            btns[i].click();
            return "Pine Save";
        }
    }
    return null;
})()
"""

# JS: Open Pine Editor panel
OPEN_EDITOR_JS = r"""
(function(){
    try {
        if (window.TradingView && window.TradingView.bottomWidgetBar) {
            window.TradingView.bottomWidgetBar.activateScriptEditorTab();
            return "OK:api";
        }
    } catch(e) {}
    var btn = document.querySelector('[aria-label="Pine"]') || document.querySelector('[data-name="pine-editor"]');
    if (btn) { btn.click(); return "OK:click"; }
    return "ERR:no_editor";
})()
"""

# JS: Load a saved script by name to initialize Monaco editor
LOAD_SCRIPT_JS = r"""
(function(){
    var target = "gex levels";
    return fetch('https://pine-facade.tradingview.com/pine-facade/list/?filter=saved',
        {credentials: 'include'})
        .then(function(r){ return r.json(); })
        .then(function(scripts){
            var match = null;
            for (var i = 0; i < scripts.length; i++) {
                var sn = (scripts[i].scriptName || '').toLowerCase();
                var st = (scripts[i].scriptTitle || '').toLowerCase();
                if (sn.indexOf(target) !== -1 || st.indexOf(target) !== -1) {
                    match = scripts[i]; break;
                }
            }
            if (!match) return "ERR:script_not_found";
            var id = match.scriptIdPart;
            var ver = match.version || 1;
            return fetch('https://pine-facade.tradingview.com/pine-facade/get/' + id + '/' + ver,
                {credentials: 'include'})
                .then(function(r2){ return r2.json(); })
                .then(function(data){
                    var source = data.source || '';
                    if (!source) return "ERR:empty_source";
                    var c = document.querySelector(".monaco-editor.pine-editor-monaco");
                    if (!c) return "OK:loaded_no_monaco";
                    var el = c; var fk;
                    for (var i = 0; i < 20; i++) {
                        if (!el) break;
                        fk = Object.keys(el).find(function(k){ return k.startsWith("__reactFiber$"); });
                        if (fk) break;
                        el = el.parentElement;
                    }
                    if (!fk) return "OK:loaded_no_fiber";
                    var cur = el[fk];
                    for (var d = 0; d < 15; d++) {
                        if (!cur) break;
                        if (cur.memoizedProps && cur.memoizedProps.value && cur.memoizedProps.value.monacoEnv) {
                            var env = cur.memoizedProps.value.monacoEnv;
                            if (env.editor && typeof env.editor.getEditors === "function") {
                                var eds = env.editor.getEditors();
                                if (eds.length > 0) {
                                    eds[0].setValue(source);
                                    return "OK:loaded_and_set";
                                }
                            }
                        }
                        cur = cur.return;
                    }
                    return "OK:loaded_no_editor";
                });
        })
        .catch(function(e){ return "ERR:" + e.message; });
})()
"""

# JS: Get compilation errors from Monaco markers
GET_ERRORS_JS = r"""
(function(){
    var c = document.querySelector(".monaco-editor.pine-editor-monaco");
    if (!c) return [];
    var el = c;
    var fk;
    for (var i = 0; i < 20; i++) {
        if (!el) break;
        fk = Object.keys(el).find(function(k){ return k.startsWith("__reactFiber$"); });
        if (fk) break;
        el = el.parentElement;
    }
    if (!fk) return [];
    var cur = el[fk];
    for (var d = 0; d < 15; d++) {
        if (!cur) break;
        if (cur.memoizedProps && cur.memoizedProps.value && cur.memoizedProps.value.monacoEnv) {
            var env = cur.memoizedProps.value.monacoEnv;
            if (env.editor && typeof env.editor.getEditors === "function") {
                var eds = env.editor.getEditors();
                if (eds.length > 0) {
                    var model = eds[0].getModel();
                    var markers = env.editor.getModelMarkers({resource: model.uri});
                    return markers.map(function(m){ return {line: m.startLineNumber, msg: m.message}; });
                }
            }
        }
        cur = cur.return;
    }
    return [];
})()
"""


def cdp_connect():
    """Connect to TradingView via Chrome DevTools Protocol."""
    import urllib.request
    import websocket

    log.info(f"Connecting to CDP on port {CDP_PORT}...")
    try:
        resp = urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json/list", timeout=5)
        targets = json.loads(resp.read().decode())
    except Exception as e:
        log.error(f"Cannot reach CDP at port {CDP_PORT}. Is TradingView running with --remote-debugging-port={CDP_PORT}?")
        log.error(f"Error: {e}")
        return None, None

    tv_target = None
    for t in targets:
        if "tradingview.com" in t.get("url", ""):
            tv_target = t
            break

    if not tv_target:
        log.error("No TradingView tab found in CDP targets")
        log.debug(f"Available targets: {[t.get('url','?')[:60] for t in targets]}")
        return None, None

    ws_url = tv_target["webSocketDebuggerUrl"]
    log.info(f"Found TradingView tab: {tv_target['url'][:80]}")
    log.debug(f"WebSocket URL: {ws_url}")

    # Suppress origin to avoid Chrome's origin check, or set header to bypass
    ws = websocket.create_connection(
        ws_url, timeout=10,
        suppress_origin=True,
        header={"Host": "localhost:9222"},
    )
    log.info("CDP WebSocket connected")

    # Enable Runtime domain
    _cdp_send(ws, "Runtime.enable", {})
    return ws, tv_target


_msg_id = 0


def _cdp_send(ws, method, params):
    """Send a CDP command and return the result."""
    global _msg_id
    _msg_id += 1
    msg = {"id": _msg_id, "method": method, "params": params}
    ws.send(json.dumps(msg))

    while True:
        resp = json.loads(ws.recv())
        if resp.get("id") == _msg_id:
            if "error" in resp:
                log.error(f"CDP error: {resp['error']}")
            return resp.get("result", {})


def cdp_eval(ws, expression):
    """Evaluate JS expression via CDP and return the value."""
    result = _cdp_send(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
    })
    return result.get("result", {}).get("value")


def cdp_eval_async(ws, expression):
    """Evaluate JS expression that returns a Promise, wait for result."""
    result = _cdp_send(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    })
    return result.get("result", {}).get("value")


def cdp_keypress(ws, key="Enter", modifiers=2):
    """Send a keyboard event (default: Ctrl+Enter)."""
    _cdp_send(ws, "Input.dispatchKeyEvent", {
        "type": "keyDown",
        "modifiers": modifiers,
        "key": key,
        "code": key,
        "windowsVirtualKeyCode": 13,
    })
    _cdp_send(ws, "Input.dispatchKeyEvent", {
        "type": "keyUp",
        "key": key,
        "code": key,
    })


# ---------------------------------------------------------------------------
# Push workflow
# ---------------------------------------------------------------------------

def push_pine_to_tv(pine_source):
    """Push Pine Script source to TradingView and compile it."""
    ws, target = cdp_connect()
    if not ws:
        return False

    try:
        # Step 1: Open Pine Editor and ensure Monaco is loaded
        log.info("Opening Pine Editor panel...")
        monaco_ready = False

        # Quick check if Monaco is already there
        check = cdp_eval(ws, FIND_MONACO_JS)
        if check == "FOUND":
            log.info("  Monaco already ready")
            monaco_ready = True
        else:
            # Open editor panel
            result = cdp_eval(ws, OPEN_EDITOR_JS)
            log.info(f"  Editor panel: {result}")
            time.sleep(1)

            # Try clicking "Open script" or the script list to trigger Monaco load
            log.info("  Looking for Open/New buttons to initialize Monaco...")
            btn_result = cdp_eval(ws, r"""
                (function(){
                    // Try clicking "Open" tab or button in Pine Editor
                    var btns = document.querySelectorAll('button, [role="tab"], [class*="tab"]');
                    for (var i = 0; i < btns.length; i++) {
                        var t = (btns[i].textContent || '').trim().toLowerCase();
                        if (t === 'open' || t === 'open script') {
                            btns[i].click(); return "clicked:open";
                        }
                    }
                    // Try "New indicator" or "New" button
                    for (var i = 0; i < btns.length; i++) {
                        var t = (btns[i].textContent || '').trim().toLowerCase();
                        if (t === 'new indicator' || t === 'new') {
                            btns[i].click(); return "clicked:new";
                        }
                    }
                    // Try Pine dialog button
                    var pine = document.querySelector('[data-name="pine-dialog-button"]');
                    if (pine) { pine.click(); return "clicked:pine-dialog"; }
                    return "none";
                })()
            """)
            log.info(f"  Button: {btn_result}")

            if btn_result and "open" in str(btn_result):
                # "Open" was clicked - now need to click the GEX Levels script in the list
                time.sleep(2)
                log.info("  Searching for GEX Levels in script list...")
                click_script = cdp_eval(ws, r"""
                    (function(){
                        var items = document.querySelectorAll('[class*="scriptList"] div, [class*="list"] [class*="item"], [class*="row"]');
                        for (var i = 0; i < items.length; i++) {
                            var t = (items[i].textContent || '').toLowerCase();
                            if (t.indexOf('gex levels') !== -1) {
                                items[i].click();
                                return "clicked:" + items[i].textContent.trim().substring(0, 40);
                            }
                        }
                        // Broader search
                        var all = document.querySelectorAll('div, span, td');
                        for (var i = 0; i < all.length; i++) {
                            var t = (all[i].textContent || '').trim();
                            if (t === 'GEX Levels' || t === 'GEX Levels - NIFTY') {
                                all[i].click();
                                return "clicked_broad:" + t;
                            }
                        }
                        return "not_found";
                    })()
                """)
                log.info(f"  Script list: {click_script}")
                time.sleep(2)

            elif btn_result and "new" in str(btn_result):
                time.sleep(2)

            # Wait for Monaco to appear (up to 15 seconds)
            log.info("  Waiting for Monaco editor...")
            for attempt in range(30):
                check = cdp_eval(ws, FIND_MONACO_JS)
                if check == "FOUND":
                    log.info(f"  Monaco ready (attempt {attempt + 1})")
                    monaco_ready = True
                    break
                if attempt % 5 == 4:
                    log.debug(f"  Attempt {attempt + 1}: not ready...")
                time.sleep(0.5)

        if not monaco_ready:
            log.error("Monaco editor not found. Please open Pine Editor manually once, then re-run.")
            log.error("Tip: Open TradingView → Pine Editor → open any script. Then gex_push.py will work.")
            return False

        # Step 3: Inject source
        escaped = json.dumps(pine_source)
        inject_js = SET_SOURCE_JS.replace("__SOURCE__", escaped)
        log.info(f"Injecting Pine Script ({len(pine_source.splitlines())} lines)...")
        result = cdp_eval(ws, inject_js)
        log.info(f"  Result: {result}")
        if not result or result.startswith("ERR"):
            log.error(f"Failed to inject source: {result}")
            return False

        time.sleep(0.5)

        # Step 4: Click compile button
        log.info("Compiling...")
        clicked = cdp_eval(ws, COMPILE_JS)
        if clicked:
            log.info(f"  Button clicked: {clicked}")
        else:
            log.info("  No button found, using Ctrl+Enter fallback")
            cdp_keypress(ws)

        # Step 5: Wait and check errors
        log.info("Waiting for compilation (3s)...")
        time.sleep(3)

        errors = cdp_eval(ws, GET_ERRORS_JS)
        if errors and len(errors) > 0:
            log.error(f"Compilation errors ({len(errors)}):")
            for e in errors:
                log.error(f"  Line {e['line']}: {e['msg']}")
            return False
        else:
            log.info("Compiled clean - 0 errors")
            return True

    except Exception as e:
        log.error(f"Push failed: {e}", exc_info=True)
        return False
    finally:
        try:
            ws.close()
        except:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = [a.lower() for a in sys.argv[1:]]
    loop_mode = "--loop" in args
    push_only = "--push-only" in args

    log.info("=" * 60)
    log.info(f"  GEX Push - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    while True:
        start = time.time()

        if not push_only:
            # Step 1: Run GEX calculator + Pine generator
            log.info("Running gex_tv_push.py to fetch data and generate Pine...")
            import subprocess
            proc = subprocess.run(
                [sys.executable, "C:/tv/gex_tv_push.py"],
                capture_output=True, text=True, cwd="C:/tv"
            )
            if proc.returncode != 0:
                log.error(f"gex_tv_push.py failed:\n{proc.stderr}")
                if not loop_mode:
                    return
                time.sleep(60)
                continue

            # Print GEX report summary (not full output)
            for line in proc.stdout.splitlines():
                if any(k in line for k in ["BIAS:", "Call Wall", "Put Wall", "Peak Gamma",
                                           "Max Pain", "EXPECTED MOVE", "Daily:", "OI CHANGE",
                                           "Spot price:", "GEX REPORT", "======"]):
                    log.info(f"  {line.strip()}")

        # Step 2: Read generated Pine file
        if not os.path.exists(PINE_FILE):
            log.error(f"Pine file not found: {PINE_FILE}")
            if not loop_mode:
                return
            time.sleep(60)
            continue

        with open(PINE_FILE, "r", encoding="utf-8") as f:
            pine_source = f.read()

        log.info(f"Pine file loaded: {len(pine_source.splitlines())} lines, {len(pine_source)} chars")

        # Step 3: Push to TradingView (inject into Monaco editor)
        success = push_pine_to_tv(pine_source)

        # Step 4: Republish public script
        if success:
            try:
                republish_to_tv(pine_source)
            except Exception as e:
                log.error(f"[PUBLISH] Error: {e}")

        elapsed = time.time() - start
        if success:
            log.info(f"DONE - pushed in {elapsed:.1f}s")
        else:
            log.error(f"FAILED after {elapsed:.1f}s")

        if not loop_mode:
            break

        log.info(f"Next update in 5 minutes...")
        time.sleep(300)


if __name__ == "__main__":
    main()
