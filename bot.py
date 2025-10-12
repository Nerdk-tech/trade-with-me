#!/usr/bin/env python3
try:
    import imghdr
except ModuleNotFoundError:
    import imghdr3 as imghdr
import os, json, time, re, traceback
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters

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

# config
BOT_TOKEN = os.getenv('BOT_TOKEN')
DATA_DIR = os.getenv('DATA_DIR','data')
FERNET_KEY = os.getenv('FERNET_KEY','').strip()
WALLETCONNECT_PROJECT_ID = os.getenv('WALLETCONNECT_PROJECT_ID','').strip()
ADMIN_ID = int(os.getenv('ADMIN_ID','6332035756'))
ADMIN_PASS = os.getenv('ADMIN_PASS','blazeddddd')

if not BOT_TOKEN:
    raise RuntimeError('BOT_TOKEN required')

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

def data_path(p): return os.path.join(DATA_DIR,p)
def load_json(name, default):
    try:
        with open(data_path(name),'r') as f: return json.load(f)
    except Exception:
        return default
def save_json(name,obj):
    with open(data_path(name),'w') as f: json.dump(obj,f,indent=2)

# defaults
defaults = {'users.json':{}, 'orders.json':[], 'limit_orders.json':[], 'referrals.json':[], 'assets.json':[{'symbol':'BTC/USDT','name':'Bitcoin'},{'symbol':'ETH/USDT','name':'Ethereum'}], 'logs.json':[]}
for k,v in defaults.items():
    if not os.path.exists(data_path(k)):
        save_json(k,v)

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
    if not FERNET: return s
    try: return FERNET.decrypt(s.encode()).decode()
    except Exception: return s

_priv = [re.compile(r'\b(private key|mnemonic|seed)\b', re.I), re.compile(r'\b(0x)?[A-Fa-f0-9]{64}\b')]
def looks_like_priv(text):
    for p in _priv:
        if p.search(text): return True
    words = [w for w in re.split(r'\s+', text) if w]
    return 10 <= len(words) <= 24

def walletconnect_url():
    pid = WALLETCONNECT_PROJECT_ID
    return 'https://walletconnect.com/connect?projectId=' + pid if pid else 'https://walletconnect.com/'

# logging
def log_action(uid, uname, action, details=''):
    logs = load_json('logs.json', [])
    logs.append({'ts':int(time.time()), 'user_id':uid, 'username':uname, 'action':action, 'details':details})
    save_json('logs.json', logs)
    try:
        if int(uid) != int(ADMIN_ID):
            up = _UPDATER
            if up:
                up.bot.send_message(int(ADMIN_ID), f'[LOG] {uname or uid} — {action} — {details}')
    except Exception:
        pass

# basic exchange helper (ccxt)
def get_exchange_for_user(user):
    if not ccxt: return None, 'ccxt not installed'
    if not user: return None, 'no user'
    exid = user.get('exchange_id'); k=user.get('exchange_key'); s=user.get('exchange_secret')
    if not exid or not k or not s: return None, 'not configured'
    try:
        api_key = decrypt_secret(k); api_secret = decrypt_secret(s)
        excls = getattr(ccxt, exid); ex = excls({'apiKey':api_key,'secret':api_secret,'enableRateLimit':True})
        return ex, None
    except Exception as e:
        return None, str(e)

_UPDATER = None

# handlers
def start(update: Update, context):
    u = update.effective_user; uid=str(u.id)
    users = load_json('users.json', {})
    if uid not in users:
        users[uid] = {'id':uid,'username':u.username,'first_name':u.first_name,'wallets':[],'points':0,'settings':{}}
        save_json('users.json', users)
    kb = [[InlineKeyboardButton('🔗 Connect Wallet', callback_data='connect')],[InlineKeyboardButton('📈 Price', callback_data='price'), InlineKeyboardButton('📊 Assets', callback_data='assets')],[InlineKeyboardButton('🛒 Buy', callback_data='buy'), InlineKeyboardButton('💱 Sell', callback_data='sell')],[InlineKeyboardButton('👛 Wallets', callback_data='wallets'), InlineKeyboardButton('🔗 Invite', callback_data='invite')],[InlineKeyboardButton('❓ Help', callback_data='help')]]
    update.message.reply_text('Welcome to Trade With Me', reply_markup=InlineKeyboardMarkup(kb))
    log_action(uid, u.username, 'start', '')

def button_cb(update: Update, context):
    q = update.callback_query; data=q.data; uid=str(q.from_user.id)
    users = load_json('users.json', {}); user = users.get(uid, {})
    if data=='connect':
        url = walletconnect_url()
        kb = [[InlineKeyboardButton('🌐 WalletConnect', url=url)], [InlineKeyboardButton('❌ Cancel', callback_data='cancel')]]
        q.edit_message_text('Open your wallet app (it will ask permission).', reply_markup=InlineKeyboardMarkup(kb))
        log_action(uid, q.from_user.username, 'open_connect', '')
    elif data=='price':
        context.user_data['awaiting_price']=True; q.edit_message_text('Send symbol (e.g. BTC/USDT).')
    elif data=='assets':
        assets = load_json('assets.json', []); q.edit_message_text('\n'.join([f"{a['symbol']} - {a['name']}" for a in assets]))
    elif data=='buy':
        context.user_data['awaiting_buy']=True; q.edit_message_text('Buy — send: SYMBOL AMOUNT')
    elif data=='sell':
        context.user_data['awaiting_sell']=True; q.edit_message_text('Sell — send: SYMBOL AMOUNT')
    elif data=='wallets':
        q.edit_message_text(f"Wallets: {user.get('wallets',[]) or 'None'}")
    elif data=='invite':
        code = 'R' + str(abs(hash(uid))%(10**8)); botname = context.bot.get_me().get('username'); q.edit_message_text('Invite: https://t.me/'+botname+'?start='+code)
    elif data=='cancel':
        context.user_data.clear(); q.edit_message_text('Cancelled ✅')
    else:
        q.edit_message_text('Unknown.')

def text_handler(update: Update, context):
    text = (update.message.text or '').strip(); uid=str(update.effective_user.id)
    users = load_json('users.json', {})
    if uid not in users:
        start(update, context); return
    user = users.get(uid)

    if context.user_data.get('awaiting_price'):
        symbol = text.replace('/','').upper(); context.user_data.pop('awaiting_price', None); update.message.reply_text(f"{symbol} price (mock): 12345.67"); return

    if context.user_data.get('awaiting_buy') or context.user_data.get('awaiting_sell'):
        parts = text.split(); 
        if len(parts)<2: update.message.reply_text('Usage: SYMBOL AMOUNT'); return
        sym=parts[0].upper(); 
        try: amt=float(parts[1])
        except: update.message.reply_text('Invalid amount'); return
        side = 'buy' if context.user_data.get('awaiting_buy') else 'sell'
        context.user_data.pop('awaiting_buy', None); context.user_data.pop('awaiting_sell', None)
        ex, err = get_exchange_for_user(user)
        status = 'filled (mock)'
        if ex:
            try:
                place_sym = sym if '/' in sym else sym[:-4]+'/'+sym[-4:]
                if hasattr(ex,'create_market_order'): order = ex.create_market_order(place_sym, side, amt)
                else: order = ex.create_order(place_sym, 'market', side, amt)
                status = f"filled(exchange:{order.get('id')})"
            except Exception as e:
                status = f'failed_exchange:{e}'
        orders = load_json('orders.json', []); oid='ord_'+str(len(orders)+1); obj={'id':oid,'user_id':uid,'symbol':sym,'amount':amt,'side':side.upper(),'status':status,'created_at':int(time.time())}; orders.append(obj); save_json('orders.json', orders)
        update.message.reply_text(f"Order placed: {obj['id']} — {obj['status']}"); log_action(uid, update.effective_user.username, 'place_order', f"{sym} {amt} {status}"); return

    # import wallet flow
    if text.lower()=="/import_wallet" or context.user_data.get('import_wallet_flow'):
        state = context.user_data.get('import_wallet_flow')
        if not state:
            context.user_data['import_wallet_flow']='step1'; update.message.reply_text('Import Wallet - Step 1: Name (letters+numbers)'); return
        if state=='step1':
            if not re.fullmatch(r'[A-Za-z0-9]{1,30}', text): update.message.reply_text('Invalid name'); return
            context.user_data['import_wallet_name']=text; context.user_data['import_wallet_flow']='step2'; update.message.reply_text('Step 2: Paste public address (DO NOT paste private keys)'); return
        if state=='step2':
            if looks_like_priv(text): update.message.reply_text('Detected private key/seed — abort.'); return
            addr=text
            if len(addr)<10: update.message.reply_text('Invalid address'); return
            name=context.user_data.pop('import_wallet_name','Wallet'); users.setdefault(uid,{'id':uid,'username':update.effective_user.username,'first_name':update.effective_user.first_name,'wallets':[],'points':0,'settings':{}})
            users[uid].setdefault('wallets',[]).append({'name':name,'address':addr,'imported_at':int(time.time())}); save_json('users.json', users)
            context.user_data.pop('import_wallet_flow', None); update.message.reply_text(f"✅ Wallet '{name}' imported (public address saved)"); log_action(uid, update.effective_user.username, 'import_wallet', addr); return

    if text.lower()=='/orders':
        orders = load_json('orders.json', []); my=[o for o in orders if o.get('user_id')==uid]
        if not my: update.message.reply_text('You have no orders'); return
        update.message.reply_text('\n'.join([f"{o['id']}: {o['side']} {o['symbol']} {o['amount']} — {o['status']}" for o in my])); return

    # admin commands
    if update.effective_user and update.effective_user.id==ADMIN_ID:
        lt = text.lower()
        if lt.startswith('/stats'):
            users_all=load_json('users.json',{}); orders_all=load_json('orders.json',[]); refs=load_json('referrals.json',[]); logs=load_json('logs.json',[])
            update.message.reply_text(f"Users: {len(users_all)}\nOrders: {len(orders_all)}\nRefs: {len(refs)}\nLogs: {len(logs)}"); return
        if lt.startswith('/vieworders'):
            orders_all=load_json('orders.json',[])
            if not orders_all: update.message.reply_text('No orders'); return
            last=orders_all[-20:]; update.message.reply_text('\n'.join([f"{o['id']}: {o['user_id']} {o['side']} {o['symbol']} {o['amount']} — {o['status']}" for o in last])); return
        if lt.startswith('/users'):
            users_all=load_json('users.json',{}); sample=list(users_all.values())[-20:]; update.message.reply_text('\n'.join([f"{u.get('id')} @{u.get('username')} {u.get('first_name')}" for u in sample]) or 'No users'); return
        if lt.startswith('/broadcast '):
            msg = text[len('/broadcast '):]; users_all=load_json('users.json',{})
            for uid_k,u in users_all.items():
                try: _UPDATER.bot.send_message(int(uid_k), f"[ADMIN] {msg}")
                except Exception: pass
            update.message.reply_text('Broadcast sent'); return

    update.message.reply_text('I did not understand. Use /start or /help')

# limit watcher (background)
def limit_watcher(updater):
    while True:
        try:
            lot = load_json('limit_orders.json', []); changed=False
            for o in list(lot):
                if o.get('status')!='open': continue
                try: price = float(10000 + (hash(o.get('symbol','')) % 50000)/100.0)
                except: price = 10000.0
                if (o['side']=='BUY' and price<=float(o['target'])) or (o['side']=='SELL' and price>=float(o['target'])):
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
                    try: updater.bot.send_message(int(o['user_id']), f"Limit order {o['id']} executed: {status}")
                    except Exception: pass
            if changed: save_json('limit_orders.json', lot)
        except Exception: traceback.print_exc()
        time.sleep(15)

def main():
    global _UPDATER
    updater = Updater(token=BOT_TOKEN, use_context=True)
    _UPDATER = updater
    dp = updater.dispatcher
    dp.add_handler(CommandHandler('start', start))
    dp.add_handler(CommandHandler('help', lambda u,c: u.message.reply_text('Use /start')))
    dp.add_handler(CallbackQueryHandler(button_cb))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, text_handler))
    t = Thread(target=limit_watcher, args=(updater,), daemon=True); t.start()
    updater.start_polling(); updater.idle()

if __name__=='__main__':
    main()
