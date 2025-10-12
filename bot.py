#!/usr/bin/env python3
import os, json, time, re, traceback
from threading import Thread
from flask import Flask, request, abort, render_template_string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters

# Optional external libs
try:
    import ccxt
except Exception:
    ccxt = None

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:
    Fernet = None

# ---------- Config (from env) ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))
DATA_DIR = os.getenv("DATA_DIR", "data")
FERNET_KEY = os.getenv("FERNET_KEY", "")
WALLETCONNECT_PROJECT_ID = os.getenv("WALLETCONNECT_PROJECT_ID", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_PASS = os.getenv("ADMIN_PASS", "changeme")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
ensure_data_dir()

def data_path(name): return os.path.join(DATA_DIR, name)
def load_json(name, default):
    try:
        with open(data_path(name), "r") as f: return json.load(f)
    except Exception:
        return default
def save_json(name, obj):
    with open(data_path(name), "w") as f: json.dump(obj, f, indent=2)

# init
defaults = {
  "users.json": {},
  "orders.json": [],
  "referrals.json": [],
  "assets.json": [{"symbol":"BTC/USDT","name":"Bitcoin"},{"symbol":"ETH/USDT","name":"Ethereum"}],
  "limit_orders.json": [],
  "copy_followers.json": [],
  "logs.json": []
}
for k,v in defaults.items():
    if not os.path.exists(data_path(k)):
        save_json(k,v)

# encryption
def get_fernet():
    if not FERNET_KEY or Fernet is None: return None
    try: return Fernet(FERNET_KEY.encode())
    except Exception: return None
def encrypt_secret(plaintext):
    f = get_fernet(); return plaintext if not f else f.encrypt(plaintext.encode()).decode()
def decrypt_secret(token):
    f = get_fernet()
    if not f: return token
    try: return f.decrypt(token.encode()).decode()
    except InvalidToken: return token

# logging
def log_action(user_id, username, action, details=""):
    logs = load_json("logs.json", [])
    entry = {"ts": int(time.time()), "user_id": user_id, "username": username, "action": action, "details": details}
    logs.append(entry)
    save_json("logs.json", logs)

# pk detector
_priv_patterns = [re.compile(r'\\b(private key|mnemonic|seed)\\b', re.I), re.compile(r'\\b(0x)?[A-Fa-f0-9]{64}\\b')]
def looks_like_private_key(text):
    for p in _priv_patterns:
        if p.search(text): return True
    words = [w for w in re.split(r'\\s+', text) if w]
    return 10 <= len(words) <= 24

def make_connect_url():
    pid = WALLETCONNECT_PROJECT_ID.strip()
    if not pid: return "https://walletconnect.com/"
    return "https://walletconnect.com/connect?projectId=" + pid

def get_exchange_for_user(user):
    if not ccxt: return None, "ccxt not installed"
    ex_id = user.get("exchange_id"); enc_key = user.get("exchange_key"); enc_secret = user.get("exchange_secret")
    if not ex_id or not enc_key or not enc_secret: return None, "no exchange configured"
    try:
        api_key = decrypt_secret(enc_key); api_secret = decrypt_secret(enc_secret)
        excls = getattr(ccxt, ex_id)
        ex = excls({"apiKey": api_key, "secret": api_secret, "enableRateLimit": True})
        return ex, None
    except Exception as e:
        return None, str(e)

def generate_ref_code(uid): return "R" + str(abs(hash(str(uid))) % (10**8))

# Telegram handlers
def start(update, context):
    user = update.effective_user; uid = str(user.id)
    users = load_json("users.json", {})
    if uid not in users:
        users[uid] = {"id": uid, "username": user.username, "first_name": user.first_name, "wallets": [], "points":0, "settings":{}}
        save_json("users.json", users)
    send_main_menu(update, context)
    log_action(uid, user.username, "start", "")

def send_main_menu(update, context):
    kb = [
        [InlineKeyboardButton("🔗 Connect Wallet / Exchange", callback_data="connect")],
        [InlineKeyboardButton("📈 Price", callback_data="price"), InlineKeyboardButton("📊 Assets", callback_data="assets")],
        [InlineKeyboardButton("🛒 Buy", callback_data="buy"), InlineKeyboardButton("💱 Sell", callback_data="sell")],
        [InlineKeyboardButton("👛 Wallets", callback_data="wallets"), InlineKeyboardButton("🔗 Invite friends", callback_data="invite")],
        [InlineKeyboardButton("⏳ Limit Orders", callback_data="limit"), InlineKeyboardButton("🤝 Copy Trading", callback_data="copy")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="settings"), InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    update.message.reply_text("Welcome to Trade With Me — use the buttons below.", reply_markup=InlineKeyboardMarkup(kb))

def help_cmd(update, context):
    update.message.reply_text("/start - menu\n/import_wallet - import public address\n/CONNECT_EXCHANGE <id> - connect exchange (Binance, Bybit, OKX)")

def button_handler(update, context):
    query = update.callback_query; data = query.data; uid = str(query.from_user.id)
    users = load_json("users.json", {}); user = users.get(uid, {})
    if data == "connect":
        connect_url = make_connect_url()
        keyboard = [
            [InlineKeyboardButton("🌐 WalletConnect (Universal)", url=connect_url)],
            [InlineKeyboardButton("🦊 MetaMask", url=connect_url), InlineKeyboardButton("🔵 Trust Wallet", url=connect_url)],
            [InlineKeyboardButton("💎 Coinbase", url=connect_url), InlineKeyboardButton("🟢 Binance Wallet", url=connect_url)],
            [InlineKeyboardButton("💼 Bybit Wallet", url=connect_url), InlineKeyboardButton("🔥 Bitget", url=connect_url)],
            [InlineKeyboardButton("➡️ More wallets", callback_data="more_wallets")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ]
        query.edit_message_text("Tap your wallet app below — your wallet will open and ask for permission. Do NOT paste private keys here.", reply_markup=InlineKeyboardMarkup(keyboard))
        log_action(uid, query.from_user.username, "open_connect", "")
    elif data == "more_wallets":
        connect_url = make_connect_url()
        keyboard = [[InlineKeyboardButton("🔷 TokenPocket", url=connect_url), InlineKeyboardButton("🌈 Rainbow", url=connect_url)], [InlineKeyboardButton("💳 Ledger Live", url=connect_url), InlineKeyboardButton("🛡️ SafePal", url=connect_url)], [InlineKeyboardButton("⬅️ Back", callback_data="connect"), InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        query.edit_message_text("More wallets — tap to open your wallet.", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "price":
        context.user_data["awaiting_price"] = True; query.edit_message_text("Send symbol (e.g. BTC/USDT)."); log_action(uid, query.from_user.username, "price_request", "")
    elif data == "assets":
        assets = load_json("assets.json", []); query.edit_message_text("\n".join([f"{a['symbol']} - {a['name']}" for a in assets]))
    elif data == "buy":
        context.user_data["awaiting_buy"] = True; query.edit_message_text("Buy selected. Send: SYMBOL AMOUNT"); log_action(uid, query.from_user.username, "buy_start", "")
    elif data == "sell":
        context.user_data["awaiting_sell"] = True; query.edit_message_text("Sell selected. Send: SYMBOL AMOUNT"); log_action(uid, query.from_user.username, "sell_start", "")
    elif data == "wallets":
        wallets = user.get("wallets", []); pts = user.get("points", 0); query.edit_message_text(f"Wallets: {wallets or 'None'}\\nReferral points: {pts}")
    elif data == "invite":
        code = generate_ref_code(uid); bot_username = context.bot.get_me().get('username'); link = f"https://t.me/{bot_username}?start=ref_{code}"; query.edit_message_text(f"Share this link to invite users: {link}"); log_action(uid, query.from_user.username, "invite_link", link)
    elif data == "limit":
        context.user_data["awaiting_limit"] = True; query.edit_message_text("Create a limit order: SYMBOL AMOUNT TARGET_PRICE SIDE (BUY/SELL)"); log_action(uid, query.from_user.username, "limit_start", "")
    elif data == "copy":
        query.edit_message_text("Copy trading: FOLLOW <leader_id> <multiplier> <max_stake> or ENABLE_LEADER")
    elif data == "settings":
        query.edit_message_text("Settings: SET_SHARE_ON / SET_SHARE_OFF")
    elif data == "help":
        query.edit_message_text("Use /help for commands.")
    elif data == "cancel":
        context.user_data.clear(); query.edit_message_text("Cancelled ✅")
    else:
        query.edit_message_text("Unknown action.")

def text_handler(update, context):
    text = (update.message.text or "").strip(); uid = str(update.effective_user.id)
    users = load_json("users.json", {})
    if uid not in users: start(update, context); return
    user = users.get(uid)

    if context.user_data.get("awaiting_price"):
        symbol = text.replace("/", "").upper(); context.user_data.pop("awaiting_price", None); update.message.reply_text(f"{symbol} price (mock): 12345.67"); return

    if context.user_data.get("awaiting_buy") or context.user_data.get("awaiting_sell"):
        parts = text.split()
        if len(parts) < 2: update.message.reply_text("Usage: SYMBOL AMOUNT"); return
        symbol = parts[0].upper()
        try: amount = float(parts[1])
        except: update.message.reply_text("Invalid amount"); return
        side = "buy" if context.user_data.get("awaiting_buy") else "sell"
        context.user_data.pop("awaiting_buy", None); context.user_data.pop("awaiting_sell", None)
        ex, err = get_exchange_for_user(user)
        if ex:
            try:
                place_symbol = symbol if "/" in symbol else symbol[:-4] + "/" + symbol[-4:] if len(symbol) > 4 else symbol
                if hasattr(ex, 'create_market_order'):
                    order = ex.create_market_order(place_symbol, side, amount)
                else:
                    order = ex.create_order(place_symbol, 'market', side, amount)
                status = f"filled(exchange:{order.get('id')})"
            except Exception as e:
                status = f"failed_exchange:{e}"
        else:
            status = "filled (mock)"
        orders = load_json("orders.json", [])
        oid = "ord_" + str(len(orders) + 1)
        obj = {"id": oid, "user_id": uid, "symbol": symbol, "amount": amount, "side": side.upper(), "status": status, "created_at": int(time.time())}
        orders.append(obj); save_json("orders.json", orders)
        update.message.reply_text(f"Order placed: {obj['id']} — {obj['status']}")
        log_action(uid, update.effective_user.username, "place_order", f"{symbol} {amount} {status}")
        return

    if context.user_data.get("awaiting_limit"):
        parts = text.split()
        if len(parts) < 4: update.message.reply_text("Usage: SYMBOL AMOUNT TARGET SIDE"); return
        symbol = parts[0].upper(); amount = float(parts[1]); target = float(parts[2]); side = parts[3].upper()
        lot = load_json("limit_orders.json", [])
        lid = "l_" + str(len(lot) + 1)
        obj = {"id": lid, "user_id": uid, "symbol": symbol, "amount": amount, "target": target, "side": side, "status": "open", "created_at": int(time.time())}
        lot.append(obj); save_json("limit_orders.json", lot); context.user_data.pop("awaiting_limit", None); update.message.reply_text(f"Limit order created: {obj['id']}"); log_action(uid, update.effective_user.username, "create_limit", f"{symbol} {amount} @ {target}"); return

    if text.upper().startswith("CONNECT_EXCHANGE "):
        parts = text.split()
        if len(parts) < 2: update.message.reply_text("Usage: CONNECT_EXCHANGE <exchange_id>"); return
        exid = parts[1].lower(); context.user_data['connect_exchange'] = {'exchange': exid, 'step': 1}; update.message.reply_text(f"Connect exchange: {exid} - Step 1: Send API KEY (public). /cancel to abort."); return
    if context.user_data.get('connect_exchange'):
        flow = context.user_data['connect_exchange']
        if flow.get('step') == 1:
            if looks_like_private_key(text): update.message.reply_text("🚫 Detected private key/seed. Paste API KEY only."); return
            flow['api_key'] = text.strip(); flow['step'] = 2; context.user_data['connect_exchange'] = flow; update.message.reply_text('Step 2: Send API SECRET (it will be encrypted).'); return
        elif flow.get('step') == 2:
            if looks_like_private_key(text): update.message.reply_text("🚫 Detected private key/seed. Paste API SECRET only."); return
            api_key = flow.get('api_key'); api_secret = text.strip(); users[uid]['exchange_id'] = flow.get('exchange'); users[uid]['exchange_key'] = encrypt_secret(api_key); users[uid]['exchange_secret'] = encrypt_secret(api_secret); save_json('users.json', users); context.user_data.pop('connect_exchange', None); update.message.reply_text(f"✅ Exchange {flow.get('exchange')} saved and encrypted for your account."); log_action(uid, update.effective_user.username, 'connect_exchange', flow.get('exchange')); return

    if text.lower() == "/import_wallet" or context.user_data.get('import_wallet_flow'):
        state = context.user_data.get('import_wallet_flow')
        if not state:
            context.user_data['import_wallet_flow'] = 'step1'; update.message.reply_text("🔐 Import Wallet - Step 1 of 2\\nWhat do you want to name this wallet? Letters and numbers only."); return
        if state == 'step1':
            if not re.fullmatch(r"[A-Za-z0-9]{1,30}", text): update.message.reply_text("Invalid name. Use letters and numbers only"); return
            context.user_data['import_wallet_name'] = text; context.user_data['import_wallet_flow'] = 'step2'; update.message.reply_text("🔐 Import Wallet - Step 2 of 2\\nPaste public address (DO NOT paste private keys)."); return
        if state == 'step2':
            if looks_like_private_key(text): update.message.reply_text("🚫 Dont send private keys. Only public addresses"); return
            addr = text
            if len(addr) < 10: update.message.reply_text("Invalid address"); return
            if uid not in users: users[uid] = {'id': uid, 'username': update.effective_user.username, 'first_name': update.effective_user.first_name, 'wallets': [], 'points': 0, 'settings': {}}
            name = context.user_data.pop('import_wallet_name', 'Wallet'); users[uid].setdefault('wallets', []).append({'name': name, 'address': addr, 'imported_at': int(time.time())}); save_json('users.json', users); context.user_data.pop('import_wallet_flow', None); update.message.reply_text(f"✅ Wallet '{name}' imported (public address saved)."); log_action(uid, update.effective_user.username, 'import_wallet', addr); return

    if text.lower() == "/orders":
        orders = load_json('orders.json', []); my = [o for o in orders if o.get('user_id') == uid]
        if not my: update.message.reply_text('You have no orders.'); return
        update.message.reply_text('\n'.join([f"{o['id']}: {o['side']} {o['symbol']} {o['amount']} — {o['status']}" for o in my])); return

    # admin-only commands
    if update.effective_user and update.effective_user.id == ADMIN_ID:
        if text.lower().startswith('/stats'):
            users = load_json('users.json', {}); orders = load_json('orders.json', []); refs = load_json('referrals.json', []); update.message.reply_text(f"Users: {len(users)}\\nOrders: {len(orders)}\\nReferrals: {len(refs)}"); return
        if text.lower().startswith('/logs'):
            logs = load_json('logs.json', []); last = logs[-10:]; update.message.reply_text('\n'.join([f"{l['ts']}: {l['user_id']} {l['action']} {l['details']}" for l in last])); return
        if text.lower().startswith('/broadcast '):
            msg = text[len('/broadcast '):]; users = load_json('users.json', {});
            for uid_k,u in users.items():
                try: context.bot.send_message(int(uid_k), f"[ADMIN BROADCAST] {msg}")
                except Exception: pass
            update.message.reply_text('Broadcast sent.'); return

    update.message.reply_text("I didn't understand. Use /start or /help.")

# limit watcher
def limit_watcher_loop(updater):
    while True:
        try:
            lot = load_json('limit_orders.json', []); changed = False
            for o in list(lot):
                if o.get('status') != 'open': continue
                try: price = float(10000 + (hash(o.get('symbol','')) % 50000) / 100.0)
                except: price = 10000.0
                if (o['side'] == 'BUY' and price <= float(o['target'])) or (o['side'] == 'SELL' and price >= float(o['target'])):
                    users = load_json('users.json', {}); user = users.get(o.get('user_id'))
                    ex, err = get_exchange_for_user(user)
                    status = 'filled (mock)'
                    if ex:
                        try:
                            place_symbol = o['symbol'] if '/' in o['symbol'] else o['symbol'][:-4] + '/' + o['symbol'][-4:]
                            if hasattr(ex, 'create_market_order'):
                                order_resp = ex.create_market_order(place_symbol, o['side'].lower(), float(o['amount']))
                            else:
                                order_resp = ex.create_order(place_symbol, 'market', o['side'].lower(), float(o['amount']))
                            status = f"filled(exchange:{order_resp.get('id')})"
                        except Exception as e:
                            status = f"failed_exchange:{e}"
                    o['status'] = status; changed = True
                    try: updater.bot.send_message(int(o['user_id']), f"Limit order {o['id']} executed: {status}")
                    except Exception: pass
            if changed: save_json('limit_orders.json', lot)
        except Exception: traceback.print_exc()
        time.sleep(15)

# Flask admin
app = Flask(__name__)
ADMIN_HTML = """
<!doctype html>
<title>Trade With Me — Admin</title>
<h1>Admin Dashboard</h1>
<p>Simple admin panel. Use ?pass=YOUR_PASS</p>
<p><a href="/admin?action=users&pass={{pass}}">Users</a> | <a href="/admin?action=orders&pass={{pass}}">Orders</a> | <a href="/admin?action=logs&pass={{pass}}">Logs</a> | <a href="/admin?action=reset&pass={{pass}}">Reset Data</a></p>
<pre>{{content}}</pre>
"""

@app.route('/admin')
def admin_panel():
    p = request.args.get('pass','')
    if p != ADMIN_PASS: abort(401)
    action = request.args.get('action','users')
    content = ''
    if action == 'users': content = json.dumps(load_json('users.json', {}), indent=2)
    elif action == 'orders': content = json.dumps(load_json('orders.json', []), indent=2)
    elif action == 'logs': content = json.dumps(load_json('logs.json', []), indent=2)
    elif action == 'reset': save_json('orders.json', []); save_json('logs.json', []); content = 'orders and logs cleared.'
    return render_template_string(ADMIN_HTML, content=content, pass=ADMIN_PASS)

# main
def main():
    updater = Updater(token=BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler('start', start))
    dp.add_handler(CommandHandler('help', help_cmd))
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, text_handler))

    t = Thread(target=limit_watcher_loop, args=(updater,), daemon=True)
    t.start()

    def run_flask():
        app.run(host='0.0.0.0', port=PORT, threaded=True)
    tf = Thread(target=run_flask, daemon=True)
    tf.start()

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
