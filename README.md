Trade With Me - Real trading ready with Admin Dashboard (Render)

This package supports:
- Telegram bot (python-telegram-bot v13.15)
- WalletConnect universal link (WalletConnect Cloud Project ID)
- Optional real-exchange trading via ccxt (per-user API keys encrypted with Fernet)
- Import public wallets (safe)
- Referral, limit orders, copy-trading (demo/real mixed)
- Admin dashboard (/admin?pass=YOUR_PASS) and logging (logs.json)

Files included:
- bot.py                     # main bot + Flask admin (v13.15 compatible)
- requirements.txt           # dependencies (includes flask)
- generate_fernet.py         # prints a Fernet key to use as FERNET_KEY
- README.md                  # this file
- start.sh                   # run script
- example.env                # example environment variables
- data/                      # initial JSON data files (users, orders, logs...)

Render notes:
- This package runs Flask admin on the same PORT and uses polling for the Telegram bot.
- Polling is easier to run here; if you prefer webhook mode, we can adapt to run webhook through Flask endpoints.
- Set environment variables in Render:
  BOT_TOKEN, FERNET_KEY, WALLETCONNECT_PROJECT_ID, ADMIN_ID, ADMIN_PASS, PORT (10000), DATA_DIR (data)

Security notes:
- Admin dashboard is protected by ADMIN_PASS; do not share it publicly.
- FERNET_KEY must be kept secret.
