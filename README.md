# FlyVPN Telegram Mini App

Telegram bot with a static Telegram Mini App for selling VPN/proxy access via Telegram Stars.

## Mini App

The Mini App is stored in `miniapp/` and can be published as a static HTTPS site, for example with GitHub Pages.

## Bot setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Set environment variables:

```bash
set BOT_TOKEN=your_telegram_bot_token
set ADMIN_ID=your_telegram_user_id
set WEB_APP_URL=https://kamilla-git.github.io/flyvpn-miniapp/miniapp/
set PROXYLINE_API_KEY=your_proxyline_api_key
```

Run:

```bash
python bot.py
```

`PROXYLINE_API_KEY` is optional if you add proxies manually with `/addproxy`.
