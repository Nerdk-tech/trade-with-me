#!/usr/bin/env python3
import types, sys, os, json, time, re, traceback, asyncio
from threading import Thread
from dotenv import load_dotenv

# Fix for Python 3.13 removing imghdr
if "imghdr" not in sys.modules:
    sys.modules["imghdr"] = types.SimpleNamespace(what=lambda *a, **kw: None)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    idle
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
BOT_TOKEN = os.getenv('BOT_TOKEN')
DATA_DIR = os.getenv('DATA_DIR', 'data')
FERNET_KEY = os.getenv('FERNET_KEY', '').strip()
WALLETCONNECT_PROJECT_ID = os.getenv('WALLETCONNECT_PROJECT_ID', '').strip()
ADMIN_ID = int(os.getenv('ADMIN_ID', '6332035756'))
ADMIN_PASS = os.getenv('ADMIN_PASS', 'blazeddddd')

if not BOT_TOKEN:
    raise RuntimeError('BOT_TOKEN required')

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

def data_path(p): return os.path.join(DATA_DIR, p)

def load_json(name, default):
    try:
        with open(data_path(name), 'r') as f:
            return json.load(f)
    except Exception:
        return default

def save_json(name, obj):
    with open(data_path(name), 'w') as f:
        json.dump(obj, f, indent=2)

# defaults
defaults = {
    'users.json': {},
    'orders.json': [],
    'limit_orders.json': [],
    'referrals.json': [],
    'assets.json': [
        {'symbol': 'BTC/USDT', 'name': 'Bitcoin'},
        {'symbol': 'ETH/USDT', 'name': 'Ethereum'}
    ],
    'logs.json': []
}

for k, v in defaults.items():
    if not os.path.exists(data_path(k)):
        save_json(k, v)

# fernet safe
FERNET = None
if FERNET_KEY and Fernet is not None:
    try:
        FERNET = Fernet(FERNET_KEY.encode())
        print('[INFO] Fernet loaded: encryption enabled')
    except Exception as e:
        FERNET = None
        print('[WARN] Invalid FERNET_KEY — encryption disabled:', e)
else:
    print('[INFO] No FERNET_KEY — running without encryption')

def encrypt_secret(s): return s if not FERNET else FERNET.encrypt(s.encode()).decode()

def decrypt_secret(s):
    if not FERNET:
        return s
    try:
        return FERNET.decrypt(s.encode()).decode()
    except Exception:
        return s

_priv = [
    re.compile(r'\b(private key|mnemonic|seed)\b', re.I),
    re.compile(r'\b(0x)?[A-Fa-f0-9]{64}\b')
]

def looks_like_priv(text):
    for p in _priv:
        if p.search(text):
            return True
    words = [w for w in re.split(r'\s+', text) if w]
    return 10 <= len(words) <= 24

def walletconnect_url():
    pid = WALLETCONNECT_PROJECT_ID
    return f'https://walletconnect.com/connect?projectId={pid}' if pid else 'https://walletconnect.com/'

# logging
async def log_action(app, uid, uname, action, details=''):
    logs = load_json('logs.json', [])
    logs.append({'ts': int(time.time()), 'user_id': uid, 'username': uname, 'action': action, 'details': details})
    save_json('logs.json', logs)
    try:
        if int(uid) != int(ADMIN_ID):
            await app.bot.send_message(int(ADMIN_ID), f'[LOG] {uname or uid} — {action} — {details}')
    except Exception:
        pass

# ccxt exchange helper
def get_exchange_for_user(user):
    if not ccxt:
        return None, 'ccxt not installed'
    if not user:
        return None, 'no user'
    exid = user.get('exchange_id')
    k = user.get('exchange_key')
    s = user.get('exchange_secret')
    if not exid or not k or not s:
        return None, 'not configured'
    try:
        api_key = decrypt_secret(k)
        api_secret = decrypt_secret(s)
        excls = getattr(ccxt, exid)
        ex = excls({'apiKey': api_key, 'secret': api_secret, 'enableRateLimit': True})
        return ex, None
    except Exception as e:
        return None, str(e)

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    uid = str(u.id)
    users = load_json('users.json', {})
    if uid not in users:
        users[uid] = {'id': uid, 'username': u.username, 'first_name': u.first_name, 'wallets': [], 'points': 0, 'settings': {}}
        save_json('users.json', users)

    kb = [
        [InlineKeyboardButton('🔗 Connect Wallet', callback_data='connect')],
        [InlineKeyboardButton('📈 Price', callback_data='price'), InlineKeyboardButton('📊 Assets', callback_data='assets')],
        [InlineKeyboardButton('🛒 Buy', callback_data='buy'), InlineKeyboardButton('💱 Sell', callback_data='sell')],
        [InlineKeyboardButton('👛 Wallets', callback_data='wallets'), InlineKeyboardButton('🔗 Invite', callback_data='invite')],
        [InlineKeyboardButton('❓ Help', callback_data='help')]
    ]
    await update.message.reply_text('Welcome to Trade With Me', reply_markup=InlineKeyboardMarkup(kb))
    await log_action(context.application, uid, u.username, 'start')

async def button_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid = str(q.from_user.id)
    users = load_json('users.json', {})
    user = users.get(uid, {})

    if data == 'connect':
        url = walletconnect_url()
        kb = [[InlineKeyboardButton('🌐 WalletConnect', url=url)], [InlineKeyboardButton('❌ Cancel', callback_data='cancel')]]
        await q.edit_message_text('Open your wallet app (it will ask permission).', reply_markup=InlineKeyboardMarkup(kb))
        await log_action(context.application, uid, q.from_user.username, 'open_connect')
    elif data == 'price':
        context.user_data['awaiting_price'] = True
        await q.edit_message_text('Send symbol (e.g. BTC/USDT).')
    elif data == 'assets':
        assets = load_json('assets.json', [])
        await q.edit_message_text('\n'.join([f"{a['symbol']} - {a['name']}" for a in assets]))
    elif data == 'buy':
        context.user_data['awaiting_buy'] = True
        await q.edit_message_text('Buy — send: SYMBOL AMOUNT')
    elif data == 'sell':
        context.user_data['awaiting_sell'] = True
        await q.edit_message_text('Sell — send: SYMBOL AMOUNT')
    elif data == 'wallets':
        await q.edit_message_text(f"Wallets: {user.get('wallets',[]) or 'None'}")
    elif data == 'invite':
        code = 'R' + str(abs(hash(uid))%(10**8)); botname = context.application.bot.username
        await q.edit_message_text('Invite: https://t.me/'+botname+'?start='+code)
    elif data == 'cancel':
        context.user_data.clear(); await q.edit_message_text('Cancelled ✅')
    else:
        await q.edit_message_text('Unknown.')

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip(); uid=str(update.effective_user.id)
    users = load_json('users.json', {})
    if uid not in users:
        # reuse start logic
        await start(update, context); return
    user = users.get(uid)

    if context.user_data.get('awaiting_price'):
        symbol = text.replace('/','').upper(); context.user_data.pop('awaiting_price', None); await update.message.reply_text(f"{symbol} price (mock): 12345.67"); return

    if context.user_data.get('awaiting_buy') or context.user_data.get('awaiting_sell'):
        parts = text.split()
        if len(parts)<2:
            await update.message.reply_text('Usage: SYMBOL AMOUNT'); return
        sym = parts[0].upper()
        try:
            amt = float(parts[1])
        except:
            await update.message.reply_text('Invalid amount'); return
        side = 'buy' if context.user_data.get('awaiting_buy') else 'sell'
        context.user_data.pop('awaiting_buy', None); context.user_data.pop('awaiting_sell', None)
        ex, err = get_exchange_for_user(user)
        status = 'filled (mock)'
        if ex:
            try:
                place_sym = sym if '/' in sym else sym[:-4]+'/'+sym[-4:]
                if hasattr(ex,'create_market_order'):
                    order = ex.create_market_order(place_sym, side, amt)
                else:
                    order = ex.create_order(place_sym, 'market', side, amt)
                status = f"filled(exchange:{order.get('id')})"
            except Exception as e:
                status = f'failed_exchange:{e}'
        orders = load_json('orders.json', []); oid='ord_'+str(len(orders)+1)
        obj={'id':oid,'user_id':uid,'symbol':sym,'amount':amt,'side':side.upper(),'status':status,'created_at':int(time.time())}
        orders.append(obj); save_json('orders.json', orders)
        await update.message.reply_text(f"Order placed: {obj['id']} — {obj['status']}")
        await log_action(context.application, uid, update.effective_user.username, 'place_order', f"{sym} {amt} {status}")
        return

    # import wallet flow (similar simplification)
    if (update.message.text or '').lower()=="/import_wallet" or context.user_data.get('import_wallet_flow'):
        state = context.user_data.get('import_wallet_flow')
        if not state:
            context.user_data['import_wallet_flow']='step1'; await update.message.reply_text('Import Wallet - Step 1: Name (letters+numbers)'); return
        if state=='step1':
            if not re.fullmatch(r'[A-Za-z0-9]{1,30}', (update.message.text or '')): await update.message.reply_text('Invalid name'); return
            context.user_data['import_wallet_name']=update.message.text; context.user_data['import_wallet_flow']='step2'; await update.message.reply_text('Step 2: Paste public address (DO NOT paste private keys)'); return
        if state=='step2':
            text = update.message.text
            if looks_like_priv(text): await update.message.reply_text('Detected private key/seed — abort.'); return
            addr=text
            if len(addr)<10: await update.message.reply_text('Invalid address'); return
            name=context.user_data.pop('import_wallet_name','Wallet'); users.setdefault(uid,{'id':uid,'username':update.effective_user.username,'first_name':update.effective_user.first_name,'wallets':[],'points':0,'settings':{}})
            users[uid].setdefault('wallets',[]).append({'name':name,'address':addr,'imported_at':int(time.time())}); save_json('users.json', users)
            context.user_data.pop('import_wallet_flow', None); await update.message.reply_text(f"✅ Wallet '{name}' imported (public address saved)"); await log_action(context.application, uid, update.effective_user.username, 'import_wallet', addr); return

    if (update.message.text or '').lower()=='/orders':
        orders = load_json('orders.json', []); my=[o for o in orders if o.get('user_id')==uid]
        if not my: await update.message.reply_text('You have no orders'); return
        await update.message.reply_text('\n'.join([f"{o['id']}: {o['side']} {o['symbol']} {o['amount']} — {o['status']}" for o in my])); return

    # admin commands
    if update.effective_user and update.effective_user.id==ADMIN_ID:
        lt = (update.message.text or '').lower()
        if lt.startswith('/stats'):
            users_all=load_json('users.json',{}); orders_all=load_json('orders.json',[]); refs=load_json('referrals.json',[]); logs=load_json('logs.json',[])
            await update.message.reply_text(f"Users: {len(users_all)}\nOrders: {len(orders_all)}\nRefs: {len(refs)}\nLogs: {len(logs)}"); return
        if lt.startswith('/vieworders'):
            orders_all=load_json('orders.json',[])
            if not orders_all: await update.message.reply_text('No orders'); return
            last=orders_all[-20:]; await update.message.reply_text('\n'.join([f"{o['id']}: {o['user_id']} {o['side']} {o['symbol']} {o['amount']} — {o['status']}" for o in last])); return
        if lt.startswith('/users'):
            users_all=load_json('users.json',{}); sample=list(users_all.values())[-20:]; await update.message.reply_text('\n'.join([f"{u.get('id')} @{u.get('username')} {u.get('first_name')}" for u in sample]) or 'No users'); return
        if lt.startswith('/broadcast '):
            msg = (update.message.text or '')[len('/broadcast '):]; users_all=load_json('users.json',{})
            for uid_k,u in users_all.items():
                try: await context.application.bot.send_message(int(uid_k), f"[ADMIN] {msg}")
                except Exception: pass
            await update.message.reply_text('Broadcast sent'); return

    await update.message.reply_text('I did not understand. Use /start or /help')

# background thread (safe scheduling to main loop)
def limit_watcher(app, loop):
    while True:
        try:
            lot = load_json('limit_orders.json', [])
            changed = False
            for o in list(lot):
                if o.get('status') != 'open':
                    continue
                try: price = float(10000 + (hash(o.get('symbol', '')) % 50000) / 100.0)
                except: price = 10000.0
                if (o['side'] == 'BUY' and price <= float(o['target'])) or (o['side'] == 'SELL' and price >= float(o['target'])):
                    users = load_json('users.json',{}); user = users.get(o.get('user_id'))
                    ex, err = get_exchange_for_user(user); status='filled (mock)'
                    if ex:
                        try:
                            place_symbol = o['symbol'] if '/' in o['symbol'] else o['symbol'][:-4]+'/'+o['symbol'][-4:]
                            if hasattr(ex,'create_market_order'): order_resp = ex.create_market_order(place_symbol, o['side'].lower(), float(o['amount']))
                            else: order_resp = ex.create_order(place_symbol, 'market', o['side'].lower(), float(o['amount']))
                            status = f"filled(exchange:{order_resp.get('id')})"
                        except Exception as e: status=f'failed_exchange:{e}'
                    o['status'] = status; changed=True
                    # schedule send_message on the main event loop safely
                    try:
                        coro = app.bot.send_message(int(o['user_id']), f"Limit order {o['id']} executed: {status}")
                        asyncio.run_coroutine_threadsafe(coro, loop)
                    except Exception:
                        pass
            if changed: save_json('limit_orders.json', lot)
        except Exception:
            traceback.print_exc()
        time.sleep(15)

# optional tiny HTTP server to satisfy Render web-service port check
def _keep_alive_server():
    import http.server, socketserver
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Keep-alive HTTP server running on port {port}")
        httpd.serve_forever()

# === MAIN ===
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # start background thread (we'll pass main loop later)
    # start fake server so Render web service port scan passes
    Thread(target=_keep_alive_server, daemon=True).start()

    print("Bot started...")

    # initialize/start the application in async style
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await idle()  # keep bot running
    await app.stop()
    await app.shutdown()

if __name__ == "__main__":
    # create a fresh loop for Render and run main as a background task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # start the limit_watcher thread and give it access to this loop
    # but we need the app object to exist — we start the watcher after the app starts
    # So we schedule a helper to wait for the application to be available and then start the watcher.
    async def _start_watcher_when_ready():
        # wait for Application to be built and available on the loop tasks
        # we will search for the Application instance by looking at running tasks (simple heuristic)
        # NOTE: this is a small helper — if you prefer explicit wiring, we can refactor to pass app directly.
        # Sleep a moment to allow app to initialize
        await asyncio.sleep(1)
        # Try to get an Application instance from tasks' coro closures (best-effort)
        # If not found, skip starting watcher (it will still be OK).
        for t in asyncio.all_tasks(loop):
            coro = t.get_coro()
            if hasattr(coro, 'cr_frame') and coro.cr_frame is not None:
                # crude approach — look for 'app' in locals
                f_locals = coro.cr_frame.f_locals
                app = f_locals.get('app') or f_locals.get('application')
                if app:
                    # start the watcher thread with this app and the loop
                    Thread(target=limit_watcher, args=(app, loop), daemon=True).start()
                    return
        # fallback: no app found — start watcher with None (it will skip sends)
        # Thread(target=limit_watcher, args=(None, loop), daemon=True).start()

    # schedule main and helper
    loop.create_task(main())
    loop.create_task(_start_watcher_when_ready())
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass