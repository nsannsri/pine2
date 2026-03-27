import json
import os
import time
import hmac
import hashlib
import requests

# ── Binance REST Core ──────────────────────────────────────────────────────────

def _base():
    use_testnet = os.environ.get('USE_TESTNET', 'false').lower() == 'true'
    return 'https://testnet.binancefuture.com' if use_testnet else 'https://fapi.binance.com'

def _api_key():
    use_testnet = os.environ.get('USE_TESTNET', 'false').lower() == 'true'
    return os.environ.get('BINANCE_TESTNET_API_KEY' if use_testnet else 'BINANCE_API_KEY', '')

def _secret():
    use_testnet = os.environ.get('USE_TESTNET', 'false').lower() == 'true'
    return os.environ.get('BINANCE_TESTNET_API_SECRET' if use_testnet else 'BINANCE_API_SECRET', '')

def _sign(params):
    query = '&'.join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(_secret().encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + f"&signature={sig}"

def _auth_headers():
    return {
        'X-MBX-APIKEY': _api_key(),
        'Content-Type': 'application/x-www-form-urlencoded'
    }

def _check(result):
    if isinstance(result, dict) and result.get('code', 0) < 0:
        raise Exception(f"APIError(code={result['code']}): {result.get('msg', 'Unknown error')}")
    return result

def _get(path, params=None):
    r = requests.get(_base() + path, params=params or {}, headers={'X-MBX-APIKEY': _api_key()})
    return r.json()

def _signed_get(path, params=None):
    p = dict(params or {})
    p['timestamp'] = int(time.time() * 1000)
    r = requests.get(f"{_base()}{path}?{_sign(p)}", headers={'X-MBX-APIKEY': _api_key()})
    return _check(r.json())

def _signed_post(path, params):
    p = dict(params)
    p['timestamp'] = int(time.time() * 1000)
    r = requests.post(_base() + path, data=_sign(p), headers=_auth_headers())
    return _check(r.json())

def _signed_delete(path, params):
    p = dict(params)
    p['timestamp'] = int(time.time() * 1000)
    r = requests.delete(f"{_base()}{path}?{_sign(p)}", headers={'X-MBX-APIKEY': _api_key()})
    return r.json()

# ── Binance Operations ─────────────────────────────────────────────────────────

def get_price(symbol):
    result = _get('/fapi/v1/ticker/price', {'symbol': symbol})
    return float(result['price'])

def get_symbol_precision(symbol):
    info = _get('/fapi/v1/exchangeInfo')
    for s in info['symbols']:
        if s['symbol'] == symbol:
            return s['pricePrecision'], s['quantityPrecision']
    return 2, 2

def set_leverage(symbol, leverage):
    _signed_post('/fapi/v1/leverage', {'symbol': symbol, 'leverage': leverage})

def cancel_all_orders(symbol):
    try:
        _signed_delete('/fapi/v1/allOpenOrders', {'symbol': symbol})
        print(f"✓ Cancelled all open orders for {symbol}")
    except Exception as e:
        print(f"⚠️ Could not cancel orders: {str(e)}")
    # Also cancel algo orders (TP/SL placed via /fapi/v1/algoOrder)
    try:
        resp = _signed_get('/fapi/v1/algoOrders/openOrders', {'symbol': symbol})
        open_algos = resp.get('orders', []) if isinstance(resp, dict) else resp
        for algo in open_algos:
            _signed_delete('/fapi/v1/algoOrder', {'algoId': algo['algoId']})
        if open_algos:
            print(f"✓ Cancelled {len(open_algos)} algo orders for {symbol}")
    except Exception as e:
        print(f"⚠️ Could not cancel algo orders: {str(e)}")

def get_position(symbol):
    positions = _signed_get('/fapi/v2/positionRisk', {'symbol': symbol})
    for pos in positions:
        amt = float(pos['positionAmt'])
        if amt > 0:
            return {'type': 'LONG', 'quantity': amt, 'entry_price': float(pos['entryPrice']), 'unrealized_pnl': float(pos['unRealizedProfit'])}
        elif amt < 0:
            return {'type': 'SHORT', 'quantity': abs(amt), 'entry_price': float(pos['entryPrice']), 'unrealized_pnl': float(pos['unRealizedProfit'])}
    return {'type': None}

def place_order(symbol, side, order_type, **kwargs):
    params = {'symbol': symbol, 'side': side, 'type': order_type}
    params.update(kwargs)
    return _signed_post('/fapi/v1/order', params)

# ── Trend Filter ───────────────────────────────────────────────────────────────

def _ema(values, period):
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def get_trend(symbol):
    klines = _get('/fapi/v1/klines', {'symbol': symbol, 'interval': '15m', 'limit': 100})
    closes = [float(c[4]) for c in klines]
    highs  = [float(c[2]) for c in klines]
    lows   = [float(c[3]) for c in klines]

    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    e20, e50 = ema20[-1], ema50[-1]
    price = closes[-1]
    score = 0

    score += 1 if e20 > e50 else -1
    if price > e20 and price > e50:
        score += 1
    elif price < e20 and price < e50:
        score -= 1
    if highs[-1] > highs[-6] and lows[-1] > lows[-6]:
        score += 1
    elif highs[-1] < highs[-6] and lows[-1] < lows[-6]:
        score -= 1
    if ema20[-1] > ema20[-3] and ema50[-1] > ema50[-3]:
        score += 1
    elif ema20[-1] < ema20[-3] and ema50[-1] < ema50[-3]:
        score -= 1

    if score >= 2:
        return 'UPTREND', score
    elif score <= -2:
        return 'DOWNTREND', score
    return 'SIDEWAYS', score

def _place_algo_tpsl(symbol, tp_side, tp_price, sl_side, sl_price, enable_sl):
    """Place TP/SL via Binance Algo Order API (/fapi/v1/algoOrder)."""
    tp_order_id = None
    sl_order_id = None

    # TP via algoOrder TAKE_PROFIT_MARKET
    try:
        result = _signed_post('/fapi/v1/algoOrder', {
            'algoType': 'CONDITIONAL',
            'symbol': symbol,
            'side': tp_side,
            'type': 'TAKE_PROFIT_MARKET',
            'triggerPrice': tp_price,
            'closePosition': 'true',
            'positionSide': 'BOTH',
            'workingType': 'CONTRACT_PRICE',
        })
        tp_order_id = result.get('algoId')
        print(f"✅ Algo TP placed: {tp_order_id}")
    except Exception as e:
        print(f"❌ Algo TP failed: {e}")

    # SL via algoOrder STOP_MARKET
    if enable_sl:
        try:
            result = _signed_post('/fapi/v1/algoOrder', {
                'algoType': 'CONDITIONAL',
                'symbol': symbol,
                'side': sl_side,
                'type': 'STOP_MARKET',
                'triggerPrice': sl_price,
                'closePosition': 'true',
                'positionSide': 'BOTH',
                'workingType': 'CONTRACT_PRICE',
            })
            sl_order_id = result.get('algoId')
            print(f"✅ Algo SL placed: {sl_order_id}")
        except Exception as e:
            print(f"❌ Algo SL failed: {e}")

    return tp_order_id, sl_order_id


# ── Trade Execution ────────────────────────────────────────────────────────────

def close_position(symbol, position):
    cancel_all_orders(symbol)
    side = 'SELL' if position['type'] == 'LONG' else 'BUY'
    order = place_order(symbol, side, 'MARKET', quantity=position['quantity'], reduceOnly='true')
    return {'quantity': position['quantity'], 'pnl': position['unrealized_pnl'], 'order_id': order['orderId']}


def open_position(symbol, direction, is_scalping, payload_tp, payload_sl, payload_quantity, payload_leverage, payload_enable_sl):
    cancel_all_orders(symbol)

    # Leverage
    leverage = int(payload_leverage) if payload_leverage is not None else int(os.environ.get('LEVERAGE', '50'))
    lev_source = "PAYLOAD" if payload_leverage is not None else "ENV"

    # Stop-loss toggle
    if payload_enable_sl is not None:
        enable_sl = payload_enable_sl.lower() == 'true' if isinstance(payload_enable_sl, str) else bool(payload_enable_sl)
        sl_source = "PAYLOAD"
    else:
        enable_sl = os.environ.get('ENABLE_STOP_LOSS', 'false').lower() == 'true'
        sl_source = "ENV"

    if payload_sl is not None and payload_sl in [0, False, "false", "0"]:
        enable_sl = False
        sl_source = "PAYLOAD (disabled by value)"

    # Quantity
    if payload_quantity is not None:
        fixed_quantity = str(payload_quantity)
        qty_source = "PAYLOAD"
    else:
        fixed_quantity = os.environ.get('FIXED_QUANTITY')
        qty_source = "ENV"

    if not fixed_quantity:
        raise Exception("FIXED_QUANTITY not set in environment variables")

    # TP/SL amounts
    if is_scalping:
        tp_usd = float(payload_tp) if payload_tp is not None else float(os.environ.get('SCALPING_TP_USD', '0.5'))
        sl_usd = float(payload_sl) if payload_sl is not None else float(os.environ.get('SCALPING_SL_USD', '0.5'))
    else:
        tp_usd = float(payload_tp) if payload_tp is not None else float(os.environ.get('TAKE_PROFIT_USD', '1.0'))
        sl_usd = float(payload_sl) if payload_sl is not None else float(os.environ.get('STOP_LOSS_USD', '1.0'))

    tp_source = "PAYLOAD" if payload_tp is not None else "ENV"
    log_and_notify(f"ℹ️ Leverage: {leverage}x ({lev_source}) | Qty: {fixed_quantity} ({qty_source}) | TP: ${tp_usd} ({tp_source}) | SL: {enable_sl} ({sl_source})")

    # Set leverage and get price/precision
    set_leverage(symbol, leverage)
    price_precision, qty_precision = get_symbol_precision(symbol)
    price = get_price(symbol)

    quantity = round(float(fixed_quantity), qty_precision)
    position_value = quantity * price

    # Entry side and TP/SL sides
    entry_side = 'BUY' if direction == 'LONG' else 'SELL'

    if direction == 'LONG':
        tp_price = round(price + tp_usd, price_precision)
        sl_price = round(price - sl_usd, price_precision)
        tp_side = 'SELL'
        sl_side = 'SELL'
    else:
        tp_price = round(price - tp_usd, price_precision)
        sl_price = round(price + sl_usd, price_precision)
        tp_side = 'BUY'
        sl_side = 'BUY'

    tp_order_id = None
    sl_order_id = None

    # Step 1: Place entry market order
    order = place_order(symbol, entry_side, 'MARKET', quantity=quantity)
    time.sleep(0.5)

    # Step 2: Try Algo Order API for TP/SL
    tp_order_id, sl_order_id = _place_algo_tpsl(
        symbol, tp_side, tp_price, sl_side, sl_price, enable_sl
    )

    # Step 3: Fallback to LIMIT TP if position TP failed
    if tp_order_id is None:
        log_and_notify("⚠️ Position TP failed, falling back to LIMIT order")
        try:
            tp_order = place_order(symbol, tp_side, 'LIMIT',
                                   price=tp_price, quantity=quantity,
                                   timeInForce='GTC', reduceOnly='true')
            tp_order_id = tp_order['orderId']
            log_and_notify(f"✅ TP (LIMIT fallback) placed: {tp_order_id}")
        except Exception as tp_error:
            log_and_notify(f"⚠️ TP fallback also failed: {str(tp_error)}")

    # Build notification
    mode_prefix = "🎯 [SCALPING]" if is_scalping else ("🟢" if direction == 'LONG' else "🔴")
    sl_info = f"Stop Loss: ${sl_price} ({'+' if direction == 'LONG' else '-'}${sl_usd}) | SL Order: {sl_order_id}" if enable_sl else "Stop Loss: DISABLED"

    log_and_notify(
        f"{mode_prefix} {direction} Opened\n"
        f"Quantity: {quantity} {symbol}\n"
        f"Entry: ${price}\n"
        f"Take Profit: ${tp_price} ({'+'if direction=='LONG' else '-'}${tp_usd}) | TP Order: {tp_order_id}\n"
        f"{sl_info}\n"
        f"Leverage: {leverage}x | Position Value: ${position_value:.2f}"
    )

    return {
        'side': direction,
        'quantity': quantity,
        'entry_price': price,
        'tp_price': tp_price,
        'sl_price': sl_price if enable_sl else None,
        'tp_order_id': tp_order_id,
        'sl_order_id': sl_order_id,
        'position_value': position_value,
        'leverage': leverage,
        'order_id': order['orderId'],
        'stop_loss_enabled': enable_sl,
        'scalping_mode': is_scalping
    }

# ── Lambda Handler ─────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    try:
        # Parse payload (API Gateway or direct invoke)
        if 'body' in event:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        # Security token check
        if body.get('security_token') != os.environ.get('SECURITY_TOKEN'):
            log_and_notify("⚠️ SECURITY ALERT: Invalid token received!", alert=True)
            return {'statusCode': 401, 'body': json.dumps({'error': 'Unauthorized'})}

        signal   = body.get('signal', '').upper()
        symbol   = body.get('symbol', 'SOLUSDT')

        payload_tp        = body.get('take_profit')
        payload_sl        = body.get('stop_loss')
        payload_quantity  = body.get('quantity')
        payload_leverage  = body.get('leverage')
        payload_enable_sl = body.get('enable_stop_loss')

        if signal not in ['BUY', 'SELL', 'EXIT']:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Invalid signal. Must be BUY, SELL, or EXIT'})}

        use_testnet = os.environ.get('USE_TESTNET', 'false').lower() == 'true'
        is_scalping = os.environ.get('IS_SCALPING_ENABLED', 'false').lower() == 'true'
        network     = "TESTNET" if use_testnet else "MAINNET"

        log_and_notify(f"📡 {signal} signal received on {network}")

        # Trend filter
        trend_filter_enabled = os.environ.get('TREND_FILTER_ENABLED', 'true').lower() == 'true'
        trend, trend_score = 'DISABLED', 0
        if trend_filter_enabled and signal in ['BUY', 'SELL']:
            try:
                trend, trend_score = get_trend(symbol)
                log_and_notify(f"📊 15m Trend: {trend} (score {trend_score:+d}/4)")
            except Exception as te:
                log_and_notify(f"⚠️ Trend check failed, proceeding: {str(te)}")
                trend = 'DISABLED'

        current_position = get_position(symbol)
        result = {}
        trade_status = 'success'

        # ── EXIT ──────────────────────────────────────────────────────────────
        if signal == 'EXIT':
            if current_position['type'] in ['LONG', 'SHORT']:
                close_result = close_position(symbol, current_position)
                log_and_notify(
                    f"🚪 EXIT — Closed {current_position['type']}\n"
                    f"Quantity: {close_result['quantity']} {symbol} | P&L: ${close_result['pnl']:.2f}"
                )
                result['closed_position'] = close_result
            else:
                log_and_notify("ℹ️ EXIT received but no open position")
                result['action'] = 'no_position_to_exit'

        # ── BUY ───────────────────────────────────────────────────────────────
        elif signal == 'BUY':
            # SIDEWAYS: exit any position, no new trade
            if trend_filter_enabled and trend == 'SIDEWAYS':
                if current_position['type'] in ['LONG', 'SHORT']:
                    close_result = close_position(symbol, current_position)
                    log_and_notify(f"🔄 SIDEWAYS — Closed {current_position['type']} | P&L: ${close_result['pnl']:.2f}")
                    result['closed_position'] = close_result
                else:
                    log_and_notify("🔄 SIDEWAYS — No position to close")
                    result['action'] = 'sideways_no_position'
                return {'statusCode': 200, 'body': json.dumps({'status': 'sideways_exit', 'trend': trend, 'result': result})}

            # DOWNTREND + BUY: close SELL (exit only), don't open BUY
            if trend_filter_enabled and trend == 'DOWNTREND':
                if current_position['type'] == 'SHORT':
                    close_result = close_position(symbol, current_position)
                    log_and_notify(f"🔄 BUY signal in DOWNTREND — Closed SHORT (exit only) | P&L: ${close_result['pnl']:.2f}")
                    result['closed_short'] = close_result
                else:
                    log_and_notify(f"🚫 BUY blocked — 15m trend is DOWNTREND (score {trend_score:+d})")
                    result['action'] = 'buy_blocked_downtrend'
                return {'statusCode': 200, 'body': json.dumps({'status': 'exit_only', 'trend': trend, 'result': result})}

            # UPTREND + BUY: open LONG
            if current_position['type'] == 'SHORT':
                close_result = close_position(symbol, current_position)
                log_and_notify(f"✅ Closed SHORT | P&L: ${close_result['pnl']:.2f}")
                result['closed_short'] = close_result
                current_position = {'type': None}

            if current_position['type'] == 'LONG':
                log_and_notify("ℹ️ Already in LONG, no action")
                result['action'] = 'ignored_already_long'
            elif current_position['type'] is None:
                try:
                    result['opened_long'] = open_position(symbol, 'LONG', is_scalping, payload_tp, payload_sl, payload_quantity, payload_leverage, payload_enable_sl)
                except Exception as e:
                    result['error'] = str(e)
                    trade_status = 'failed'

        # ── SELL ──────────────────────────────────────────────────────────────
        elif signal == 'SELL':
            # SIDEWAYS: exit any position, no new trade
            if trend_filter_enabled and trend == 'SIDEWAYS':
                if current_position['type'] in ['LONG', 'SHORT']:
                    close_result = close_position(symbol, current_position)
                    log_and_notify(f"🔄 SIDEWAYS — Closed {current_position['type']} | P&L: ${close_result['pnl']:.2f}")
                    result['closed_position'] = close_result
                else:
                    log_and_notify("🔄 SIDEWAYS — No position to close")
                    result['action'] = 'sideways_no_position'
                return {'statusCode': 200, 'body': json.dumps({'status': 'sideways_exit', 'trend': trend, 'result': result})}

            # UPTREND + SELL: close BUY (exit only), don't open SELL
            if trend_filter_enabled and trend == 'UPTREND':
                if current_position['type'] == 'LONG':
                    close_result = close_position(symbol, current_position)
                    log_and_notify(f"🔄 SELL signal in UPTREND — Closed LONG (exit only) | P&L: ${close_result['pnl']:.2f}")
                    result['closed_long'] = close_result
                else:
                    log_and_notify(f"🚫 SELL blocked — 15m trend is UPTREND (score {trend_score:+d})")
                    result['action'] = 'sell_blocked_uptrend'
                return {'statusCode': 200, 'body': json.dumps({'status': 'exit_only', 'trend': trend, 'result': result})}

            # DOWNTREND + SELL: open SHORT
            if current_position['type'] == 'LONG':
                close_result = close_position(symbol, current_position)
                log_and_notify(f"✅ Closed LONG | P&L: ${close_result['pnl']:.2f}")
                result['closed_long'] = close_result
                current_position = {'type': None}

            if current_position['type'] == 'SHORT':
                log_and_notify("ℹ️ Already in SHORT, no action")
                result['action'] = 'ignored_already_short'
            elif current_position['type'] is None:
                try:
                    result['opened_short'] = open_position(symbol, 'SHORT', is_scalping, payload_tp, payload_sl, payload_quantity, payload_leverage, payload_enable_sl)
                except Exception as e:
                    result['error'] = str(e)
                    trade_status = 'failed'

        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'message': f'Trade {trade_status}',
                'network': network,
                'signal': signal,
                'mode': 'SCALPING' if is_scalping else 'NORMAL',
                'status': trade_status,
                'result': result
            })
        }

    except Exception as e:
        log_and_notify(f"❌ Error: {str(e)}", alert=True)
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}

# ── Notifications ──────────────────────────────────────────────────────────────

def log_and_notify(message, alert=False):
    print(message)
    try:
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        chat_id   = os.environ.get('TELEGRAM_CHAT_ID')
        if bot_token and chat_id:
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'},
                timeout=5
            )
    except Exception as e:
        print(f"Telegram error: {str(e)}")
