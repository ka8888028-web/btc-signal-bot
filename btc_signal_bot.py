import os
import json
import logging
from datetime import datetime

import ccxt
import pandas as pd
import pandas_ta as ta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAME = "15m"
CHECK_INTERVAL = 50
SETTINGS_FILE = "settings.json"

DEFAULT_SETTINGS = {
    "stop_loss_pct": 1.5,
    "take_profit_pct": 3.0
}

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

exchange = ccxt.binance({"enableRateLimit": True})
last_signals = {symbol: None for symbol in SYMBOLS}

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)

settings = load_settings()

def get_ohlcv(symbol, limit=100):
    ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

def calculate_indicators(df):
    df["ema9"] = ta.ema(df["close"], length=9)
    df["ema21"] = ta.ema(df["close"], length=21)
    df["rsi"] = ta.rsi(df["close"], length=14)
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    return df

def get_signal(df):
    if len(df) < 30:
        return "WAIT", None, None, None, None

    current = df.iloc[-1]
    prev = df.iloc[-2]

    price = float(current["close"])
    rsi = float(current["rsi"])
    ema9 = float(current["ema9"])
    atr = float(current["atr"])

    cross_up = prev["ema9"] <= prev["ema21"] and current["ema9"] > current["ema21"]
    cross_down = prev["ema9"] >= prev["ema21"] and current["ema9"] < current["ema21"]

    if cross_up and rsi > 45:
        return "BUY", price, rsi, ema9, atr
    elif cross_down and rsi < 55:
        return "SELL", price, rsi, ema9, atr
    else:
        trend = "HOLD_LONG" if ema9 > current["ema21"] else "HOLD_SHORT"
        return trend, price, rsi, ema9, atr

def make_message(symbol, signal, price, rsi, ema9, atr):
    coin = symbol.split("/")[0]
    sl_pct = settings["stop_loss_pct"]
    tp_pct = settings["take_profit_pct"]

    if signal == "BUY":
        sl = price * (1 - sl_pct / 100)
        tp = price * (1 + tp_pct / 100)
        emoji = f"🟢 {coin} — ПОКУПКА"
    elif signal == "SELL":
        sl = price * (1 + sl_pct / 100)
        tp = price * (1 - tp_pct / 100)
        emoji = f"🔴 {coin} — ПРОДАЖА"
    else:
        sl = tp = None
        emoji = f"🟡 {coin} — {signal}"

    text = (
        f"<b>{emoji}</b>\n\n"
        f"💰 Цена: <b>${price:,.4f}</b>\n"
        f"📊 RSI: <b>{rsi:.1f}</b>\n"
        f"📈 EMA 9: <b>{ema9:.4f}</b>\n"
    )

    if sl and tp:
        text += (
            f"\n🛡️ Stop-Loss: <b>${sl:,.4f}</b> (−{sl_pct}%)\n"
            f"🎯 Take-Profit: <b>${tp:,.4f}</b> (+{tp_pct}%)\n"
        )

    text += f"\n⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    return text

def main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("BTC", callback_data="symbol_BTC/USDT"),
            InlineKeyboardButton("ETH", callback_data="symbol_ETH/USDT"),
            InlineKeyboardButton("SOL", callback_data="symbol_SOL/USDT"),
        ],
        [
            InlineKeyboardButton("📡 Все сигналы", callback_data="all_signals"),
            InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Привет! Я твой сигнальный бот.\n\n"
        "Слежу за: <b>BTC, ETH, SOL</b>\n"
        "Стратегия: EMA 9 + EMA 21 + RSI\n"
        "Таймфрейм: 15 минут\n\n"
        "Команды:\n"
        "/sl 1.5 — изменить Stop-Loss %\n"
        "/tp 3.0 — изменить Take-Profit %\n"
        "/setchat — показать твой Chat ID"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_keyboard())

async def setchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"Твой Chat ID: <code>{chat_id}</code>\n\n"
        f"Добавь его в переменные окружения как CHAT_ID",
        parse_mode="HTML"
    )

async def set_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(context.args[0])
        if value <= 0 or value > 20:
            await update.message.reply_text("Укажи значение от 0.1 до 20")
            return
        settings["stop_loss_pct"] = value
        save_settings(settings)
        await update.message.reply_text(f"✅ Stop-Loss установлен: <b>{value}%</b>", parse_mode="HTML")
    except:
        await update.message.reply_text("Использование: /sl 1.5")

async def set_tp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(context.args[0])
        if value <= 0 or value > 50:
            await update.message.reply_text("Укажи значение от 0.1 до 50")
            return
        settings["take_profit_pct"] = value
        save_settings(settings)
        await update.message.reply_text(f"✅ Take-Profit установлен: <b>{value}%</b>", parse_mode="HTML")
    except:
        await update.message.reply_text("Использование: /tp 3.0")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("symbol_"):
        symbol = data.replace("symbol_", "")
        df = calculate_indicators(get_ohlcv(symbol))
        signal, price, rsi, ema9, atr = get_signal(df)
        msg = make_message(symbol, signal, price, rsi, ema9, atr)
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=main_keyboard())

    elif data == "all_signals":
        texts = []
        for symbol in SYMBOLS:
            df = calculate_indicators(get_ohlcv(symbol))
            signal, price, rsi, ema9, atr = get_signal(df)
            coin = symbol.split("/")[0]
            texts.append(f"<b>{coin}</b>: {signal} | ${price:,.2f} | RSI {rsi:.1f}")
        text = "📡 <b>Текущие сигналы:</b>\n\n" + "\n".join(texts)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=main_keyboard())

    elif data == "settings":
        text = (
            f"⚙️ <b>Текущие настройки</b>\n\n"
            f"Stop-Loss: <b>{settings['stop_loss_pct']}%</b>\n"
            f"Take-Profit: <b>{settings['take_profit_pct']}%</b>\n\n"
            f"Изменить:\n/sl 1.5\n/tp 3.0"
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=main_keyboard())

async def check_signals(context: ContextTypes.DEFAULT_TYPE):
    global last_signals

    if not CHAT_ID:
        return

    for symbol in SYMBOLS:
        try:
            df = calculate_indicators(get_ohlcv(symbol))
            signal, price, rsi, ema9, atr = get_signal(df)

            if signal in ["BUY", "SELL"] and signal != last_signals[symbol]:
                msg = make_message(symbol, signal, price, rsi, ema9, atr)
                await context.bot.send_message(
                    chat_id=CHAT_ID,
                    text=msg,
                    parse_mode="HTML",
                    reply_markup=main_keyboard()
                )
                last_signals[symbol] = signal
                logger.info(f"Сигнал {signal} по {symbol}")

        except Exception as e:
            logger.error(f"Ошибка {symbol}: {e}")

def main():
    if not TELEGRAM_TOKEN:
        print("❌ Не указан TELEGRAM_TOKEN")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setchat", setchat))
    app.add_handler(CommandHandler("sl", set_sl))
    app.add_handler(CommandHandler("tp", set_tp))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.job_queue.run_repeating(check_signals, interval=CHECK_INTERVAL, first=10)

    print("✅ Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
