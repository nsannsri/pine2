import MetaTrader5 as mt5
from flask import Flask, request, jsonify

app = Flask(__name__)

SECRET_TOKEN = "xau-tv-9x2k7p"  # change this to something unique

# Connect to MT5
if not mt5.initialize():
    print("MT5 initialize failed:", mt5.last_error())
else:
    print("MT5 connected successfully")
    print("Account:", mt5.account_info().login)


def close_existing(symbol):
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return
    for pos in positions:
        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        tick  = mt5.symbol_info_tick(symbol)
        price = tick.bid if pos.type == 0 else tick.ask
        result = mt5.order_send({
            "action"       : mt5.TRADE_ACTION_DEAL,
            "symbol"       : symbol,
            "volume"       : pos.volume,
            "type"         : close_type,
            "position"     : pos.ticket,
            "price"        : price,
            "deviation"    : 20,
            "magic"        : 1001,
            "comment"      : "TV reverse close",
            "type_filling" : mt5.ORDER_FILLING_FOK,
        })
        print("Closed position:", pos.ticket, "Result:", result.retcode)


def open_trade(symbol, action, lots, tp=None):
    order_type = mt5.ORDER_TYPE_BUY if action == 'buy' else mt5.ORDER_TYPE_SELL
    tick  = mt5.symbol_info_tick(symbol)
    price = tick.ask if action == 'buy' else tick.bid

    order = {
        "action"       : mt5.TRADE_ACTION_DEAL,
        "symbol"       : symbol,
        "volume"       : lots,
        "type"         : order_type,
        "price"        : price,
        "deviation"    : 20,
        "magic"        : 1001,
        "comment"      : "TradingView signal",
        "type_time"    : mt5.ORDER_TIME_GTC,
        "type_filling" : mt5.ORDER_FILLING_FOK,
    }

    if tp:
        order["tp"] = price + tp if action == 'buy' else price - tp

    result = mt5.order_send(order)
    print("Opened trade:", result)
    return result


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("Signal received:", data)

    if data.get('token') != SECRET_TOKEN:
        print("Unauthorized request blocked")
        return jsonify({"status": "unauthorized"}), 401

    symbol = data.get('symbol', 'XAUUSD')
    action = data.get('action', 'buy').lower()
    lots   = float(data.get('lots', 0.1))
    tp     = float(data['tp']) if data.get('tp') else None

    if action == 'close':
        close_existing(symbol)
        return jsonify({"status": "closed"}), 200

    # Close any opposite position first
    positions = mt5.positions_get(symbol=symbol)
    if positions:
        for pos in positions:
            is_buy  = pos.type == 0
            if (action == 'buy' and not is_buy) or (action == 'sell' and is_buy):
                print("Opposite position found, closing first...")
                close_existing(symbol)
                break

    # Open new trade
    result = open_trade(symbol, action, lots, tp)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        return jsonify({"status": "success", "order": result.order}), 200
    else:
        return jsonify({"status": "error", "code": result.retcode, "comment": result.comment}), 400


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
