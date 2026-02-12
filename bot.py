import os
import logging
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")

def _help_text():
    return (
        "TrackerBundle Bot ✅\n"
        "Komutlar:\n"
        "/status\n"
        "/health\n"
        "/list\n"
        "/add <isbn>\n"
        "/del <isbn>\n"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_help_text())

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with httpx.AsyncClient(timeout=7.0) as client:
            r = await client.get(f"{API_BASE}/status")
            r.raise_for_status()
            data = r.json()
        await update.message.reply_text(
            "OK ✅\n"
            f"UTC: {data.get('time_utc')}\n"
            f"ISBN count: {data.get('isbn_count')}\n"
            f"API: {API_BASE}"
        )
    except Exception as e:
        await update.message.reply_text(f"Status hata: {e}")

async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with httpx.AsyncClient(timeout=7.0) as client:
            r = await client.get(f"{API_BASE}/health")
            r.raise_for_status()
            data = r.json()
        await update.message.reply_text(f"API Health ✅\n{data}\nPanel: {API_BASE.replace('8000','')}/health")
    except Exception as e:
        await update.message.reply_text(f"Health hata: {e}")

async def list_isbns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with httpx.AsyncClient(timeout=7.0) as client:
            r = await client.get(f"{API_BASE}/isbns")
            r.raise_for_status()
            data = r.json()
        items = data.get("items", [])
        if not items:
            await update.message.reply_text("Liste boş.")
            return
        # çok uzarsa kırp
        preview = items[:50]
        msg = "ISBN Listesi:\n" + "\n".join(preview)
        if len(items) > len(preview):
            msg += f"\n... (+{len(items)-len(preview)} daha)"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"List hata: {e}")

async def add_isbn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    isbn = " ".join(context.args).strip()
    if not isbn:
        await update.message.reply_text("Kullanım: /add <isbn>")
        return
    try:
        async with httpx.AsyncClient(timeout=7.0) as client:
            r = await client.post(f"{API_BASE}/isbns", json={"isbn": isbn})
            r.raise_for_status()
            data = r.json()
        await update.message.reply_text(
            f"Add ✅\n"
            f"isbn: {data.get('isbn')}\n"
            f"added: {data.get('added')}\n"
            f"count: {data.get('count')}"
        )
    except Exception as e:
        await update.message.reply_text(f"Add hata: {e}")

async def del_isbn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    isbn = " ".join(context.args).strip()
    if not isbn:
        await update.message.reply_text("Kullanım: /del <isbn>")
        return
    try:
        async with httpx.AsyncClient(timeout=7.0) as client:
            r = await client.delete(f"{API_BASE}/isbns/{isbn}")
            r.raise_for_status()
            data = r.json()
        await update.message.reply_text(
            f"Del ✅\n"
            f"isbn: {data.get('isbn')}\n"
            f"deleted: {data.get('deleted')}\n"
            f"count: {data.get('count')}"
        )
    except Exception as e:
        await update.message.reply_text(f"Del hata: {e}")

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("list", list_isbns))
    app.add_handler(CommandHandler("add", add_isbn))
    app.add_handler(CommandHandler("del", del_isbn))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
