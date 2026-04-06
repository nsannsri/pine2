"""
TradingView Pine Script republisher
Uses sessionid from TradingView's local cookie store.
"""

import requests
import sqlite3
import shutil
import tempfile
import os
import json
import sys


COOKIE_PATH = r'C:\Users\teju\AppData\Roaming\TradingView\Network\Cookies'


def get_session_cookie():
    tmp = tempfile.mktemp(suffix='.db')
    shutil.copy2(COOKIE_PATH, tmp)
    try:
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        cur.execute("SELECT name, value FROM cookies WHERE host_key LIKE '%tradingview%' AND name IN ('sessionid', 'sessionid_sign')")
        cookies = dict(cur.fetchall())
        conn.close()
        return cookies
    finally:
        os.unlink(tmp)


def make_headers(cookies):
    cookie_str = '; '.join(f'{k}={v}' for k, v in cookies.items())
    return {
        'cookie': cookie_str,
        'origin': 'https://in.tradingview.com',
        'referer': 'https://in.tradingview.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }


def list_published_scripts(headers):
    r = requests.get(
        'https://pine-facade.tradingview.com/pine-facade/list?filter=published',
        headers=headers
    )
    r.raise_for_status()
    return r.json()


def republish_script(pub_id, source_code, headers):
    """
    pub_id: e.g. 'PUB;5ca46d483b004287a15dcd0ad0667de8'
    source_code: Pine Script v5/v6 source as a string
    """
    url = f'https://pine-facade.tradingview.com/pine-facade/publish/next/{requests.utils.quote(pub_id, safe="")}'
    r = requests.post(url, data={'source': source_code}, headers=headers)
    return r.status_code, r.text


def main():
    cookies = get_session_cookie()
    if 'sessionid' not in cookies:
        print('ERROR: sessionid not found in cookie store')
        sys.exit(1)

    headers = make_headers(cookies)

    # List published scripts
    scripts = list_published_scripts(headers)
    print(f'Found {len(scripts)} published scripts:\n')
    for i, s in enumerate(scripts):
        print(f'  [{i}] {s["scriptName"]}')
        print(f'       PUB ID : {s["scriptIdPart"]}')
        print(f'       Source : {s["extra"].get("originalScriptId", "?")}')
        print()

    # ---- Example: republish GEX Levels with new source ----
    # Uncomment and edit to use:
    #
    # target_name = 'GEX Levels'
    # script = next(s for s in scripts if s['scriptName'] == target_name)
    # pub_id = script['scriptIdPart']
    #
    # with open('my_script.pine', 'r') as f:
    #     source = f.read()
    #
    # status, resp = republish_script(pub_id, source, headers)
    # print(f'Republish {target_name}: HTTP {status}')
    # print(resp[:300])


if __name__ == '__main__':
    main()
