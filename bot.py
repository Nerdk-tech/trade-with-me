
import os, io, json, time, asyncio, re, traceback
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")  # set in Render env
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT","10000"))
DATA_DIR = os.getenv("DATA_DIR","data")
WALLETCONNECT_PROJECT_ID = os.getenv("WALLETCONNECT_PROJECT_ID", "afc112a9a583db93347b7af66be9337f")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN required in environment variables")

def data_path(name): return os.path.join(DATA_DIR, name)
os.makedirs(DATA_DIR, exist_ok=True)

def load_json(name, default):
    try:
        with open(data_path(name),"r") as f: return json.load(f)
    except Exception:
        return default

def save_json(name, obj):
    with open(data_path(name),"w") as f: json.dump(obj,f,indent=2)

defaults = {
  "users.json": {},
  "orders.json": [],
  "referrals.json": [],
  "assets.json": [{"symbol":"BTC/USDT","name":"Bitcoin"},{"symbol":"ETH/USDT","name":"Ethereum"}],
  "limit_orders.json": [],
  "copy_followers.json": []
}
for k,v in defaults.items():
    p = data_path(k)
    if not os.path.exists(p):
        save_json(k,v)

def make_connect_url():
    pid = WALLETCONNECT_PROJECT_ID.strip()
    if not pid:
        return "https://walletconnect.com/"
    return "https://walletconnect.com/connect?projectId=" + pid

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; uid=str(user.id)
    users = load_json("users.json", {})
    if uid not in users:
        users[uid] = {"id":uid,"username":user.username,"first_name":user.first_name,"wallets":[], "points":0, "settings":{}}
        save_json("users.json", users)
    await send_main_menu(update)

async def send_main_menu(update: Update):
    kb = [
        [InlineKeyboardButton("🔗 Connect Wallet / Exchange", callback_data="connect")],
        [InlineKeyboardButton("📈 View Price", callback_data="price"), InlineKeyboardButton("📊 Assets", callback_data="assets")],
        [InlineKeyboardButton("🛒 Buy (demo)", callback_data="buy"), InlineKeyboardButton("💱 Sell (demo)", callback_data="sell")],
        [InlineKeyboardButton("👛 Wallets", callback_data="wallet"), InlineKeyboardButton("🔗 Invite friends", callback_data="invite")],
        [InlineKeyboardButton("⏳ Limit Orders", callback_data="limit"), InlineKeyboardButton("🤝 Copy Trading", callback_data="copy")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="settings"), InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    await update.message.reply_text("Welcome to Trade With Me — tap Connect Wallet to link your wallet (opens your wallet app).", reply_markup=InlineKeyboardMarkup(kb))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data; uid = str(update.effective_user.id)
    users = load_json("users.json", {}); user = users.get(uid, {})
    if data == "connect":
        connect_url = make_connect_url()
        keyboard = [
            [InlineKeyboardButton("🔵 Open WalletConnect (universal)", url=connect_url)],
            [InlineKeyboardButton("🦊 MetaMask", url=connect_url), InlineKeyboardButton("🔵 Trust Wallet", url=connect_url)],
            [InlineKeyboardButton("💎 Coinbase Wallet", url=connect_url), InlineKeyboardButton("🟢 Binance Wallet", url=connect_url)],
            [InlineKeyboardButton("🟣 OKX Wallet", url=connect_url), InlineKeyboardButton("🟠 SafePal", url=connect_url)],
            [InlineKeyboardButton("🔷 WalletConnect (all wallets)", url=connect_url)],
            [InlineKeyboardButton("Cancel", callback_data="cancel")]
        ]
        await q.edit_message_text(
            "Tap your wallet app below — your wallet will open and ask for permission. Do NOT paste private keys here.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif data == "price":
        context.user_data["awaiting_price"] = True; await q.edit_message_text("Send symbol (e.g. BTC/USDT).")
    elif data == "assets":
        assets = load_json("assets.json", []); await q.edit_message_text("\n".join([a["symbol"] + " - " + a["name"] for a in assets]))
    elif data == "buy":
        context.user_data["awaiting_buy"] = True; await q.edit_message_text("Buy selected (demo). Send: SYMBOL AMOUNT (e.g. BTC/USDT 0.001)")
    elif data == "sell":
        context.user_data["awaiting_sell"] = True; await q.edit_message_text("Sell selected (demo). Send: SYMBOL AMOUNT (e.g. BTC/USDT 0.001)")
    elif data == "wallet":
        wallets = user.get("wallets", []); pts = user.get("points", 0)
        txt = "Wallets: " + (str(wallets) if wallets else "None") + "\nReferral points: " + str(pts)
        await q.edit_message_text(txt)
    elif data == "invite":
        code = str(abs(hash(uid)) % (10**8))
        bot_username = (await update.effective_bot.get_me()).username
        link = "https://t.me/" + bot_username + "?start=ref_" + code
        await q.edit_message_text("Share this link to invite users: " + link)
    elif data == "limit":
        context.user_data["awaiting_limit"] = True; await q.edit_message_text("Create limit: SYMBOL AMOUNT TARGET SIDE")
    elif data == "copy":
        await q.edit_message_text("Copy Trading: FOLLOW <leader_id> <multiplier> <max_stake>")
    elif data == "settings":
        await q.edit_message.reply_text("Settings: SET_SHARE_ON / SET_SHARE_OFF")
    elif data == "help":
        await q.edit_message.reply_text("Use /help for more information.")
    elif data == "cancel":
        context.user_data.clear(); await q.edit_message.reply_text("Cancelled.")

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip(); uid = str(update.effective_user.id)
    users = load_json("users.json", {})
    if uid not in users:
        await start_command(update, context); return
    user = users.get(uid)
    if context.user_data.get("awaiting_price"):
        symbol = text.replace("/","").upper(); context.user_data.pop("awaiting_price",None)
        await update.message.reply_text(f"{symbol} price (demo): 12345.67")
        return
    if context.user_data.get("awaiting_buy") or context.user_data.get("awaiting_sell"):
        parts = text.split()
        if len(parts) < 2:
            await update.message.reply_text("Usage: SYMBOL AMOUNT")
            return
        symbol = parts[0].upper(); amount = parts[1]; side = "BUY" if context.user_data.get("awaiting_buy") else "SELL"
        context.user_data.pop("awaiting_buy",None); context.user_data.pop("awaiting_sell",None)
        orders = load_json("orders.json", [])
        oid = "ord_" + str(len(orders)+1)
        order = {"id":oid,"user_id":uid,"symbol":symbol,"amount":amount,"side":side,"status":"filled (demo)","created_at":int(time.time())}
        orders.append(order); save_json("orders.json", orders)
        await update.message.reply_text("Order placed (demo): " + order["id"] + " — " + side + " " + amount + " " + symbol)
        return
    if context.user_data.get("awaiting_limit"):
        parts = text.split();
        if len(parts) < 4:
            await update.message.reply_text("Usage: SYMBOL AMOUNT TARGET SIDE"); return
        symbol = parts[0].upper(); amount = parts[1]; target = parts[2]; side = parts[3].upper()
        lot = load_json("limit_orders.json", []); lid = "l_" + str(len(lot)+1); obj = {"id":lid,"user_id":uid,"symbol":symbol,"amount":amount,"target":target,"side":side,"status":"open"}
        lot.append(obj); save_json("limit_orders.json", lot); context.user_data.pop("awaiting_limit",None)
        await update.message.reply_text("Limit order created (demo): " + obj["id"])
        return
    if text.lower() == "/import_wallet" or context.user_data.get("import_wallet_flow"):
        await import_wallet_flow_handler(update, context); return
    await update.message.reply_text("I didn't understand. Use /start.")

async def import_wallet_start(update, context):
    context.user_data['import_wallet_flow'] = 'step1'
    await update.message.reply_text("🔐 Import Wallet - Step 1 of 2\n\nWhat would you like to name this wallet?\nLetters and numbers only.\nFor example: \"MainWallet\" or \"Wallet123\".")

async def import_wallet_flow_handler(update, context):
    text = update.message.text.strip(); uid = str(update.effective_user.id)
    users = load_json("users.json", {})
    state = context.user_data.get('import_wallet_flow')
    if text.lower() == "/cancel":
        context.user_data.pop('import_wallet_flow', None); context.user_data.pop('import_wallet_name', None)
        await update.message.reply_text("Cancelled"); return
    if state == 'step1':
        if not re.fullmatch(r'[A-Za-z0-9]{1,30}', text):
            await update.message.reply_text("Invalid name. Use letters and numbers only."); return
        context.user_data['import_wallet_name'] = text; context.user_data['import_wallet_flow'] = 'step2'
        await update.message.reply_text("🔐 Import Wallet - Step 2 of 2\n\nPlease paste your public wallet address to import your existing wallet:\n\n⚠️ Do not disclose your private key or mnemonic to others. Paste public address only.")
        return
    if state == 'step2':
        if re.search(r'(private key|mnemonic|seed phrase|seed)', text, re.I) or len(text.split())>=10:
            await update.message.reply_text("🚫 I can't accept private keys or seed phrases. Paste public address only."); return
        addr = text
        if len(addr) < 10:
            await update.message.reply_text("That doesn't look like a public address. Try again or send /cancel."); return
        if uid not in users:
            users[uid] = {"id":uid,"username":update.effective_user.username,"first_name":update.effective_user.first_name,"wallets":[], "points":0, "settings":{}}
        name = context.user_data.pop('import_wallet_name', 'Wallet')
        users[uid].setdefault('wallets', []).append({"name":name,"address":addr,"imported_at":int(time.time())})
        save_json("users.json", users); context.user_data.pop('import_wallet_flow', None)
        await update.message.reply_text(f"✅ Wallet \"{name}\" imported (public address saved).")
        return

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("import_wallet", import_wallet_start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    app.post_init.append(lambda app: app.create_task(asyncio.sleep(0)))  # no-op
    print("Starting webhook...")
    app.run_webhook(listen="0.0.0.0", port=PORT, webhook_url=WEBHOOK_URL)

if __name__ == "__main__":
    main()
