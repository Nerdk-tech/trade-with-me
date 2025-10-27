#!/usr/bin/env python3
import types, sys, os, json, time, re, traceback, asyncio
from threading import Thread
from dotenv import load_dotenv

# Fix for Python 3.13 removing imghdr
if "imghdr" not in sys.modules:
    sys.modules["imghdr"] = types.SimpleNamespace(what=lambda *a, **kw: None)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# optional libs
try:
    import ccxt
except Exception:
    ccxt = None

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:
    Fernet = None
    InvalidToken = Exception

# Load environment
load_dotenv()

# config
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATA_DIR = os.getenv("DATA_DIR", "data")
FERNET_KEY = os.getenv("FERNET_KEY", "").strip()
WALLETCONNECT_PROJECT_ID = os.getenv("WALLETCONNECT_PROJECT_ID", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "6332035756"))
ADMIN_PASS = os.getenv("ADMIN_PASS", "blazeddddd")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN required")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

def data_path(p):
    return os.path.join(DATA_DIR, p)

def load_json(name, default):
    try:
        with open(data_path(name), "r") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(name, obj):
    with open(data_path(name), "w") as f:
        json.dump(obj, f, indent=2)

# defaults
defaults = {
    "users.json": {},
    "orders.json": [],
    "limit_orders.json": [],
    "referrals.json": [],
    "assets.json": [
        {"symbol": "BTC/USDT", "name": "Bitcoin"},
        {"symbol": "ETH/USDT", "name": "Ethereum"},
    ],
    "logs.json": [],
}

for k, v in defaults.items():
    if not os.path.exists(data_path(k)):
        save_json(k, v)

# fernet safe
FERNET = None
if FERNET_KEY and Fernet is not None:
    try:
        FERNET = Fernet(FERNET_KEY.encode())
        print("[INFO] Fernet loaded: encryption enabled")
    except Exception as e:
        FERNET = None
        print("[WARN] Invalid FERNET_KEY — encryption disabled:", e)
else:
    print("[INFO] No FERNET_KEY — running without encryption")

def encrypt_secret(s):
    return s if not FERNET else FERNET.encrypt(s.encode()).decode()

def decrypt_secret(s):
    if not FERNET:
        return s
    try:
        return FERNET.decrypt(s.encode()).decode()
    except Exception:
        return s

_priv = [
    re.compile(r"\b(private key|mnemonic|seed)\b", re.I),
    re.compile(r"\b(0x)?[A-Fa-f0-9]{64}\b"),
]

def looks_like_priv(text):
    for p in _priv:
        if p.search(text):
            return True
    words = [w for w in re.split(r"\s+", text) if w]
    return 10 <= len(words) <= 24

def walletconnect_url():
    pid = WALLETCONNECT_PROJECT_ID
    return (
        f"https://walletconnect.com/connect?projectId={pid}"
        if pid
        else "https://walletconnect.com/"
    )

# logging
async def log_action(app, uid, uname, action, details=""):
    logs = load_json("logs.json", [])
    logs.append(
        {
            "ts": int(time.time()),
            "user_id": uid,
            "username": uname,
            "action": action,
            "details": details,
        }
    )
    save_json("logs.json", logs)
    try:
        if int(uid) != int(ADMIN_ID):
            await app.bot.send_message(
                int(ADMIN_ID), f"[LOG] {uname or uid} — {action} — {details}"
            )
    except Exception:
        pass

# ccxt exchange helper
def get_exchange_for_user(user):
    if not ccxt:
        return None, "ccxt not installed"
    if not user:
        return None, "no user"
    exid = user.get("exchange_id")
    k = user.get("exchange_key")
    s = user.get("exchange_secret")
    if not exid or not k or not s:
        return None, "not configured"
    try:
        api_key = decrypt_secret(k)
        api_secret = decrypt_secret(s)
        excls = getattr(ccxt, exid)
        ex = excls({"apiKey": api_key, "secret": api_secret, "enableRateLimit": True})
        return ex, None
    except Exception as e:
        return None, str(e)

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    uid = str(u.id)
    users = load_json("users.json", {})
    if uid not in users:
        users[uid] = {
            "id": uid,
            "username": u.username,
            "first_name": u.first_name,
            "wallets": [],
            "points": 0,
            "settings": {},
        }
        save_json("users.json", users)

    kb = [
        [InlineKeyboardButton("🔗 Connect Wallet", callback_data="connect")],
        [
            InlineKeyboardButton("📈 Price", callback_data="price"),
            InlineKeyboardButton("📊 Assets", callback_data="assets"),
        ],
        [
            InlineKeyboardButton("🛒 Buy", callback_data="buy"),
            InlineKeyboardButton("💱 Sell", callback_data="sell"),
        ],
        [
            InlineKeyboardButton("👛 Wallets", callback_data="wallets"),
            InlineKeyboardButton("🔗 Invite", callback_data="invite"),
        ],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    await update.message.reply_text(
        "Welcome to Trade With Me", reply_markup=InlineKeyboardMarkup(kb)
    )
    await log_action(context.application, uid, u.username, "start")

async def button_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid = str(q.from_user.id)
    users = load_json("users.json", {})
    user = users.get(uid, {})

    if data == "connect":
        url = walletconnect_url()
        kb = [
            [InlineKeyboardButton("🌐 WalletConnect", url=url)],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
        ]
        await q.edit_message_text(
            "Open your wallet app (it will ask permission).",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        await log_action(context.application, uid, q.from_user.username, "open_connect")
    elif data == "price":
        context.user_data["awaiting_price"] = True
        await q.edit_message_text("Send symbol (e.g. BTC/USDT).")
    elif data == "assets":
        assets = load_json("assets.json", [])
        await q.edit_message_text(
            "\n".join([f"{a['symbol']} - {a['name']}" for a in assets])
        )
    else:
        await q.edit_message_text("Unknown.")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    await update.message.reply_text(f"You said: {text}")

# background thread
def limit_watcher(app):
    while True:
        try:
            lot = load_json("limit_orders.json", [])
            for o in list(lot):
                if o.get("status") != "open":
                    continue
                price = float(10000 + (hash(o.get("symbol", "")) % 50000) / 100.0)
                if (o["side"] == "BUY" and price <= float(o["target"])) or (
                    o["side"] == "SELL" and price >= float(o["target"])
                ):
                    o["status"] = "filled (mock)"
                    try:
                        asyncio.run(
                            app.bot.send_message(
                                int(o["user_id"]),
                                f"Limit order {o['id']} executed",
                            )
                        )
                    except Exception:
                        pass
            save_json("limit_orders.json", lot)
        except Exception:
            traceback.print_exc()
        time.sleep(15)

# === MAIN ===
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    Thread(target=limit_watcher, args=(app,), daemon=True).start()
    print("Bot started...")

    await app.initialize()
    await app.start()
    print("Polling started...")
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "already running" in str(e):
            loop = asyncio.get_event_loop()
            loop.create_task(main())
            loop.run_forever()
        else:
            raise