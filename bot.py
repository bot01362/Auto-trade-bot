"""
ETC/USDT Telegram Signal Bot
MA5/MA10/MA30 crossover + RSI(6) confirmation
Price source: Binance public API
Deploy target: Render (free tier) + Flask keep-alive + UptimeRobot
"""

import os
import time
import threading
import requests
from collections import deque
from flask import Flask

# ============ CONFIG ============
TELEGRAM_BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID_HERE")

SYMBOL = "ETCUSDT"
BINANCE_PRICE_URL = f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}"

POLL_INTERVAL = 60          # seconds
MA_SHORT = 5
MA_MID = 10
MA_LONG = 30
RSI_PERIOD = 6
MA_SPREAD_THRESHOLD = 0.0008  # 0.08%
RSI_UPPER = 75
RSI_LOWER = 25
CONFIRMATION_STREAK = 3

MAX_HISTORY = 200  # rolling price buffer

# ============ STATE ============
price_history = deque(maxlen=MAX_HISTORY)
last_signal = None
confirmation_count = 0
bot_running = False
bot_thread = None

app = Flask(__name__)


# ============ TELEGRAM ============
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"[telegram] send error: {e}")


def get_telegram_updates(offset=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        resp = requests.get(url, params=params, timeout=35)
        return resp.json().get("result", [])
    except Exception as e:
        print(f"[telegram] getUpdates error: {e}")
        return []


# ============ PRICE FETCH ============
def get_current_price():
    try:
        resp = requests.get(BINANCE_PRICE_URL, timeout=10)
        data = resp.json()
        return float(data["price"])
    except Exception as e:
        print(f"[price] fetch error: {e}")
        return None


# ============ INDICATORS ============
def calc_ma(prices, period):
    if len(prices) < period:
        return None
    return sum(list(prices)[-period:]) / period


def calc_rsi(prices, period):
    if len(prices) < period + 1:
        return None
    prices_list = list(prices)[-(period + 1):]
    gains = []
    losses = []
    for i in range(1, len(prices_list)):
        delta = prices_list[i] - prices_list[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(delta))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ============ SIGNAL LOGIC ============
def evaluate_signal():
    global last_signal, confirmation_count

    ma5 = calc_ma(price_history, MA_SHORT)
    ma10 = calc_ma(price_history, MA_MID)
    ma30 = calc_ma(price_history, MA_LONG)
    rsi = calc_rsi(price_history, RSI_PERIOD)

    if None in (ma5, ma10, ma30, rsi):
        return None, ma5, ma10, ma30, rsi

    spread = abs(ma5 - ma30) / ma30

    raw_signal = "HOLD"
    if ma5 > ma10 > ma30 and spread >= MA_SPREAD_THRESHOLD and rsi > RSI_UPPER:
        raw_signal = "BUY"
    elif ma5 < ma10 < ma30 and spread >= MA_SPREAD_THRESHOLD and rsi < RSI_LOWER:
        raw_signal = "SELL"

    # confirmation streak logic
    if raw_signal == last_signal and raw_signal != "HOLD":
        confirmation_count += 1
    elif raw_signal != "HOLD":
        confirmation_count = 1
        last_signal = raw_signal
    else:
        confirmation_count = 0
        last_signal = "HOLD"

    confirmed = confirmation_count >= CONFIRMATION_STREAK and raw_signal != "HOLD"

    return (raw_signal if confirmed else "HOLD"), ma5, ma10, ma30, rsi


# ============ MAIN LOOP ============
def bot_loop():
    global bot_running, confirmation_count, last_signal
    print("[bot] ETC/USDT signal loop started")
    already_alerted_for_streak = False

    while bot_running:
        price = get_current_price()
        if price is not None:
            price_history.append(price)
            signal, ma5, ma10, ma30, rsi = evaluate_signal()

            print(f"[bot] price={price:.4f} ma5={ma5} ma10={ma10} ma30={ma30} "
                  f"rsi6={rsi} signal={signal} streak={confirmation_count}")

            if signal in ("BUY", "SELL") and not already_alerted_for_streak:
                msg = (
                    f"*ETC/USDT Signal: {signal}*\n"
                    f"Price: `{price:.4f}`\n"
                    f"MA5: `{ma5:.4f}` | MA10: `{ma10:.4f}` | MA30: `{ma30:.4f}`\n"
                    f"RSI(6): `{rsi:.2f}`\n"
                    f"Confirmed over {CONFIRMATION_STREAK} cycles"
                )
                send_telegram_message(msg)
                already_alerted_for_streak = True
            elif signal == "HOLD":
                already_alerted_for_streak = False

        time.sleep(POLL_INTERVAL)

    print("[bot] ETC/USDT signal loop stopped")


def start_bot():
    global bot_running, bot_thread
    if bot_running:
        return False
    bot_running = True
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.start()
    return True


def stop_bot():
    global bot_running
    if not bot_running:
        return False
    bot_running = False
    return True


# ============ TELEGRAM COMMAND LISTENER ============
def command_listener():
    offset = None
    while True:
        updates = get_telegram_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message", {})
            text = message.get("text", "")
            chat_id = str(message.get("chat", {}).get("id", ""))

            if chat_id != str(TELEGRAM_CHAT_ID):
                continue

            if text == "/start":
                started = start_bot()
                send_telegram_message(
                    "✅ ETC/USDT bot started." if started else "Bot already running."
                )
            elif text == "/stop":
                stopped = stop_bot()
                send_telegram_message(
                    "🛑 ETC/USDT bot stopped." if stopped else "Bot already stopped."
                )
            elif text == "/status":
                status = "running" if bot_running else "stopped"
                send_telegram_message(f"Status: {status}")


# ============ FLASK KEEP-ALIVE ============
@app.route("/")
def home():
    return f"ETC/USDT bot alive. Running: {bot_running}"


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ============ ENTRYPOINT ============
if __name__ == "__main__":
    token_ok = TELEGRAM_BOT_TOKEN != "YOUR_BOT_TOKEN_HERE"
    chat_ok = TELEGRAM_CHAT_ID != "YOUR_CHAT_ID_HERE"
    print(f"[startup] BOT_TOKEN loaded: {token_ok}")
    print(f"[startup] CHAT_ID loaded: {chat_ok}")
    if not (token_ok and chat_ok):
        print("[startup] WARNING: missing env vars, bot cannot talk to Telegram.")

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    command_listener()
