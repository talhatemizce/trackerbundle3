import os, json
import httpx

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
API_BASE = (os.getenv("API_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")

MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📌 Status"), KeyboardButton("🩺 Health")],
        [KeyboardButton("📚 List")],
        [KeyboardButton("➕ Add ISBN"), KeyboardButton("🗑️ Del ISBN")],
    ],
    resize_keyboard=True,
)

def clean_isbn(s: str) -> str:
    return s.replace("-", "").replace(" ", "").strip()

def pretty(text: str) -> str:
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except Exception:
        return text

async def api(method: str, path: str, **kwargs):
    url = f"{API_BASE}{path}"
    async with httpx.AsyncClient(timeout=20) as c:
        return await c.request(method, url, **kwargs)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting", None)
    await update.message.reply_text(
        "TrackerBundle Bot ✅\nButonlardan seç.",
        reply_markup=MENU,
        parse_mode=None,
        disable_web_page_preview=True,
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = await api("GET", "/status")
    await update.message.reply_text(f"HTTP {r.status_code}\n{pretty(r.text)}", parse_mode=None)

async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = await api("GET", "/health")
    await update.message.reply_text(f"HTTP {r.status_code}\n{pretty(r.text)}", parse_mode=None)

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = await api("GET", "/isbns")
    await update.message.reply_text(f"HTTP {r.status_code}\n{pretty(r.text)}", parse_mode=None)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    # Menü butonları
    if txt == "📌 Status":
        return await cmd_status(update, context)
    if txt == "🩺 Health":
        return await cmd_health(update, context)
    if txt == "📚 List":
        return await cmd_list(update, context)

    # Add / Del akışı
    if txt == "➕ Add ISBN":
        context.user_data["awaiting"] = "add"
        return await update.message.reply_text("ISBN gönder (örn: 9780132350884):", parse_mode=None)

    if txt == "🗑️ Del ISBN":
        context.user_data["awaiting"] = "del"
        return await update.message.reply_text("Silinecek ISBN gönder (örn: 9780132350884):", parse_mode=None)

    awaiting = context.user_data.get("awaiting")
    if awaiting in ("add", "del"):
        isbn = clean_isbn(txt)
        if not isbn:
            return await update.message.reply_text("ISBN boş geldi. Tekrar gönder.", parse_mode=None)

        if awaiting == "add":
            r = await api("POST", "/isbns", json={"isbn": isbn})
        else:
            r = await api("DELETE", f"/isbns/{isbn}")

        context.user_data.pop("awaiting", None)
        await update.message.reply_text(f"HTTP {r.status_code}\n{pretty(r.text)}", parse_mode=None, reply_markup=MENU)
        return

    # Diğer her şey
    await update.message.reply_text(
        "Menüden seç: Status / Health / List / Add ISBN / Del ISBN",
        reply_markup=MENU,
        parse_mode=None,
    )

def main():
    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
