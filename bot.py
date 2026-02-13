import os, json, re
import httpx
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
API_BASE  = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

MENU = ReplyKeyboardMarkup(
    [
        ["📌 Status", "🩺 Health", "📚 List"],
        ["➕ Add ISBN", "🗑️ Del ISBN"],
        ["🧠 Decide ASIN"],
    ],
    resize_keyboard=True,
)

def clean_isbn(s: str) -> str:
    return re.sub(r"[^0-9Xx]", "", (s or "")).strip().upper()

def is_valid_isbn(s: str) -> bool:
    s = clean_isbn(s)
    return len(s) in (10, 13) and all(ch.isdigit() or ch == "X" for ch in s)

def clean_asin(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (s or "")).strip().upper()

def is_valid_asin(s: str) -> bool:
    s = clean_asin(s)
    return len(s) == 10 and s.isalnum()

def pretty(text: str) -> str:
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except Exception:
        return text

def _money_int(x):
    try:
        return int(round(float(x)))
    except Exception:
        return None

def _minmax_total(top2):
    totals = []
    for it in (top2 or []):
        if isinstance(it, dict) and it.get("total") is not None:
            v = _money_int(it.get("total"))
            if v is not None:
                totals.append(v)
    if not totals:
        return None
    return (min(totals), max(totals))

def _buybox_total(obj):
    # obj: top2 list veya direkt {"buybox": {...}} içeren yapı olabilir
    if isinstance(obj, dict) and "buybox" in obj:
        bb = obj.get("buybox") or {}
        if isinstance(bb, dict) and bb.get("total") is not None:
            return _money_int(bb.get("total"))

    top2 = obj if isinstance(obj, list) else []
    for it in (top2 or []):
        if isinstance(it, dict) and it.get("buybox") is True and it.get("total") is not None:
            return _money_int(it.get("total"))
    return None

def format_decision_short(text: str) -> str:
    try:
        data = json.loads(text)
    except Exception:
        return text

    asin = data.get("asin") or "-"

    new_block  = data.get("new") or {}
    used_block = data.get("used") or {}

    new_top2 = new_block.get("top2") or []
    used_top2 = used_block.get("top2") or []

    new_mm = _minmax_total(new_top2)
    used_mm = _minmax_total(used_top2)

    # buybox varsa onu göster (önce block içinden, yoksa top2 içinden)
    new_bb  = _buybox_total(new_block)  or _buybox_total(new_top2)
    used_bb = _buybox_total(used_block) or _buybox_total(used_top2)

    new_range = "-" if not new_mm else f"{new_mm[0]}-{new_mm[1]}"
    used_range = "-" if not used_mm else f"{used_mm[0]}-{used_mm[1]}"

    new_bb_s = "-" if new_bb is None else str(new_bb)
    used_bb_s = "-" if used_bb is None else str(used_bb)

    return (
        f"ASIN {asin}\n"
        f"Used prices {used_range} | Used buybox {used_bb_s}\n"
        f"New prices {new_range} | New buybox {new_bb_s}"
    )

async def api(method: str, path: str, **kwargs):
    async with httpx.AsyncClient(timeout=25) as c:
        return await c.request(method, f"{API_BASE}{path}", **kwargs)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting", None)
    await update.message.reply_text("TrackerBundle Bot ✅\nMenüden seç.", reply_markup=MENU, parse_mode=None)

async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = await api("GET", "/health")
    await update.message.reply_text(f"HTTP {r.status_code}\n{pretty(r.text)}", reply_markup=MENU, parse_mode=None)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = await api("GET", "/status")
    await update.message.reply_text(f"HTTP {r.status_code}\n{pretty(r.text)}", reply_markup=MENU, parse_mode=None)

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = await api("GET", "/isbns")
    await update.message.reply_text(f"HTTP {r.status_code}\n{pretty(r.text)}", reply_markup=MENU, parse_mode=None)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    # Menü
    if txt == "📌 Status":
        return await cmd_status(update, context)
    if txt == "🩺 Health":
        return await cmd_health(update, context)
    if txt == "📚 List":
        return await cmd_list(update, context)

    # Add / Del / Decide akışı
    if txt == "➕ Add ISBN":
        context.user_data["awaiting"] = "add"
        return await update.message.reply_text("ISBN gönder (10 veya 13 hane) (örn: 9780132350884):", reply_markup=MENU, parse_mode=None)

    if txt == "🗑️ Del ISBN":
        context.user_data["awaiting"] = "del"
        return await update.message.reply_text("Silinecek ISBN gönder (10 veya 13 hane):", reply_markup=MENU, parse_mode=None)

    if txt == "🧠 Decide ASIN":
        context.user_data["awaiting"] = "decide"
        return await update.message.reply_text("ASIN gönder (10 karakter) (örn: 0821551051):", reply_markup=MENU, parse_mode=None)

    awaiting = context.user_data.get("awaiting")

    if awaiting == "add":
        isbn = clean_isbn(txt)
        if not is_valid_isbn(isbn):
            return await update.message.reply_text("ISBN yanlış. 10 veya 13 hane olmalı.", reply_markup=MENU, parse_mode=None)
        r = await api("POST", "/isbns", json={"isbn": isbn})
        context.user_data.pop("awaiting", None)
        return await update.message.reply_text(f"HTTP {r.status_code}\n{pretty(r.text)}", reply_markup=MENU, parse_mode=None)

    if awaiting == "del":
        isbn = clean_isbn(txt)
        if not is_valid_isbn(isbn):
            return await update.message.reply_text("ISBN yanlış. 10 veya 13 hane olmalı.", reply_markup=MENU, parse_mode=None)
        r = await api("DELETE", f"/isbns/{isbn}")
        context.user_data.pop("awaiting", None)
        return await update.message.reply_text(f"HTTP {r.status_code}\n{pretty(r.text)}", reply_markup=MENU, parse_mode=None)

    if awaiting == "decide":
        asin = clean_asin(txt)
        if not is_valid_asin(asin):
            return await update.message.reply_text("ASIN yanlış. 10 karakter olmalı.", reply_markup=MENU, parse_mode=None)
        r = await api("GET", "/decide/asin", params={"asin": asin})
        context.user_data.pop("awaiting", None)
        if r.status_code == 200:
            return await update.message.reply_text(format_decision_short(r.text), reply_markup=MENU, parse_mode=None)
        return await update.message.reply_text(f"HTTP {r.status_code}\n{pretty(r.text)}", reply_markup=MENU, parse_mode=None)

    # Diğer her şey
    await update.message.reply_text(
        "Menüden seç: Status / Health / List / Add ISBN / Del ISBN / Decide ASIN",
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
