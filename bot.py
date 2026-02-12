import os
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "TrackerBundle Bot ✅\n"
        "Komutlar:\n"
        "/status - servis durumu\n"
        "/health - health check"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = requests.get(f"{API_BASE}/status", timeout=5)
    await update.message.reply_text(r.text)

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = requests.get(f"{API_BASE}/health", timeout=5)
    await update.message.reply_text(r.text)

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing (check /etc/trackerbundle.env)")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("health", health))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
