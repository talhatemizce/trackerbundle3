import os
import asyncio
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

def now_utc():
    return datetime.now(timezone.utc).isoformat()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("TrackerBundle Bot ✅\nKomutlar: /status")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"OK ✅\nUTC: {now_utc()}")

async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await app.updater.idle()

if __name__ == "__main__":
    asyncio.run(main())
