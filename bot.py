import os, json, re, time, logging
import httpx
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logger = logging.getLogger("trackerbundle.bot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
API_BASE  = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

# Kullanıcı "➕ Add ISBN" dedikten sonra cevap beklenebilecek maksimum süre (saniye).
# Bu süre geçince awaiting state otomatik sıfırlanır.
AWAITING_TIMEOUT = int(os.getenv("BOT_AWAITING_TIMEOUT", "300"))

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
    """API çağrısı — ağ/timeout hatası olursa HTTPStatusError benzeri obje döndürür."""
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            return await c.request(method, f"{API_BASE}{path}", **kwargs)
    except httpx.TimeoutException:
        logger.warning("API timeout: %s %s", method, path)
        raise RuntimeError("API timeout — sunucu cevap vermedi (>25s)")
    except httpx.ConnectError:
        logger.warning("API connect error: %s %s", method, path)
        raise RuntimeError("API'ye bağlanılamadı — servis çalışıyor mu?")
    except httpx.RequestError as exc:
        logger.warning("API request error: %s %s — %s", method, path, exc)
        raise RuntimeError(f"Ağ hatası: {exc}")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("awaiting", None)
    await update.message.reply_text("TrackerBundle Bot ✅\nMenüden seç.", reply_markup=MENU, parse_mode=None)

async def _reply(update: Update, text: str) -> None:
    """Ortak reply helper — metin çok uzunsa kesiyor."""
    await update.message.reply_text(text[:4000], reply_markup=MENU, parse_mode=None)


async def _api_reply(update: Update, method: str, path: str, **kwargs) -> bool:
    """API çağır, sonucu kullanıcıya gönder. Hata olursa Türkçe mesaj ver. True = başarı."""
    try:
        r = await api(method, path, **kwargs)
        text = f"HTTP {r.status_code}\n{pretty(r.text)}"
        await _reply(update, text)
        return r.status_code < 400
    except RuntimeError as e:
        await _reply(update, f"⚠️ {e}")
        return False


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _api_reply(update, "GET", "/health")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _api_reply(update, "GET", "/status")

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _api_reply(update, "GET", "/isbns")

def _awaiting_expired(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Awaiting durumu AWAITING_TIMEOUT saniyeden eskiyse True döner ve state'i temizler."""
    ts = context.user_data.get("awaiting_ts", 0)
    if ts and (time.time() - ts) > AWAITING_TIMEOUT:
        context.user_data.clear()
        return True
    return False


def _set_awaiting(context: ContextTypes.DEFAULT_TYPE, state: str) -> None:
    context.user_data["awaiting"] = state
    context.user_data["awaiting_ts"] = time.time()


def _parse_price(txt: str):
    """'50', '50.5', boş → None (varsayılan kullanılacak). Hatalı → ValueError."""
    txt = txt.strip()
    if not txt or txt.lower() in ("skip", "-", "default", "varsayılan", "v"):
        return None
    try:
        v = float(txt)
        if v <= 0 or v > 9999:
            raise ValueError(f"Fiyat 0-9999 arasında olmalı, aldım: {v}")
        return round(v, 2)
    except ValueError:
        raise ValueError(f"Geçersiz fiyat: '{txt}'. Sayı gir (örn: 45) ya da boş bırak.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    # ── İptal komutu ────────────────────────────────────────────────────────
    if txt.lower() in ("/cancel", "iptal", "cancel"):
        context.user_data.clear()
        return await _reply(update, "İşlem iptal edildi.")

    # ── Menü butonları ───────────────────────────────────────────────────────
    if txt == "📌 Status":
        context.user_data.clear()
        return await cmd_status(update, context)
    if txt == "🩺 Health":
        context.user_data.clear()
        return await cmd_health(update, context)
    if txt == "📚 List":
        context.user_data.clear()
        return await cmd_list(update, context)

    # ── Awaiting timeout kontrolü ────────────────────────────────────────────
    if _awaiting_expired(context):
        await _reply(update, "⏰ İşlem zaman aşımına uğradı, sıfırlandı. Tekrar seç.")

    # ── Yeni akış başlatma ───────────────────────────────────────────────────
    if txt == "➕ Add ISBN":
        _set_awaiting(context, "add_isbn")
        return await _reply(update, "ISBN gönder (10 veya 13 hane, tire kabul edilir):\nörn: 9780132350884 ya da 978-0132350884")

    if txt == "🗑️ Del ISBN":
        _set_awaiting(context, "del")
        return await _reply(update, "Silinecek ISBN gönder:")

    if txt == "🧠 Decide ASIN":
        _set_awaiting(context, "decide")
        return await _reply(update, "ASIN gönder (10 karakter):")

    awaiting = context.user_data.get("awaiting")

    # ── Del ISBN ─────────────────────────────────────────────────────────────
    if awaiting == "del":
        isbn = clean_isbn(txt)
        if not is_valid_isbn(isbn):
            return await _reply(update, "ISBN yanlış. 10 veya 13 hane olmalı.")
        try:
            r = await api("DELETE", f"/isbns/{isbn}")
            context.user_data.clear()
            return await _reply(update, f"HTTP {r.status_code}\n{pretty(r.text)}")
        except RuntimeError as e:
            context.user_data.clear()
            return await _reply(update, f"⚠️ {e}")

    # ── Decide ASIN ──────────────────────────────────────────────────────────
    if awaiting == "decide":
        asin = clean_asin(txt)
        if not is_valid_asin(asin):
            return await _reply(update, "ASIN yanlış. 10 karakter olmalı.")
        try:
            r = await api("GET", "/decide/asin", params={"asin": asin})
            context.user_data.clear()
            if r.status_code == 200:
                return await _reply(update, format_decision_short(r.text))
            return await _reply(update, f"HTTP {r.status_code}\n{pretty(r.text)}")
        except RuntimeError as e:
            context.user_data.clear()
            return await _reply(update, f"⚠️ {e}")

    # ── Add ISBN — çok adımlı akış ──────────────────────────────────────────
    # Adım 1: ISBN al
    if awaiting == "add_isbn":
        isbn = clean_isbn(txt)
        if not is_valid_isbn(isbn):
            return await _reply(update, "ISBN yanlış (checksum hatası veya uzunluk). Tekrar gönder.")
        context.user_data["pending_isbn"] = isbn
        _set_awaiting(context, "add_new_max")
        return await _reply(update, f"ISBN: {isbn} ✓\n\nNew (sıfır/yeni) için max fiyat? (USD)\nörnk: 50 — boş bırakırsan varsayılan (50) kullanılır.")

    # Adım 2: New max
    if awaiting == "add_new_max":
        try:
            new_max = _parse_price(txt)
        except ValueError as e:
            return await _reply(update, f"⚠️ {e}")
        context.user_data["pending_new_max"] = new_max
        _set_awaiting(context, "add_used_max")
        default_hint = "30" if new_max is None else str(int(round(new_max * 0.60)))
        return await _reply(update, f"Used (kullanılmış) Good kondisyon için max fiyat? (USD)\nörnk: {default_hint} — boş bırakırsan varsayılan (30) kullanılır.\n\nNot: Acceptable={int(round(float(default_hint)*0.8))}, VeryGood={int(round(float(default_hint)*1.1))}, LikeNew={int(round(float(default_hint)*1.15))} otomatik türetilir.")

    # Adım 3: Used max → kaydet
    if awaiting == "add_used_max":
        try:
            used_max = _parse_price(txt)
        except ValueError as e:
            return await _reply(update, f"⚠️ {e}")

        isbn = context.user_data.get("pending_isbn")
        new_max = context.user_data.get("pending_new_max")
        context.user_data.clear()

        if not isbn:
            return await _reply(update, "⚠️ Oturum hatası, tekrar başlat.")

        try:
            # 1. ISBN watchlist'e ekle
            r = await api("POST", "/isbns", json={"isbn": isbn})
            added = r.status_code == 200 and r.json().get("added", False)

            # 2. Limitleri kaydet (varsa)
            if new_max is not None or used_max is not None:
                await api("PUT", f"/rules/{isbn}/override", json={
                    "new_max": new_max,
                    "used_all_max": used_max,
                })

            lines = [f"✅ ISBN {isbn} {'eklendi' if added else 'zaten vardı'}"]
            if new_max is not None:
                lines.append(f"  New max: ${new_max}")
            if used_max is not None:
                lines.append(f"  Used max: ${used_max}")
            lines.append("(Limitler güncellendi)" if (new_max or used_max) else "(Varsayılan limitler)")
            return await _reply(update, "\n".join(lines))

        except RuntimeError as e:
            return await _reply(update, f"⚠️ {e}")

    # ── Bilinmiyor ───────────────────────────────────────────────────────────
    await _reply(update, "Menüden seç: Status / Health / List / Add ISBN / Del ISBN / Decide ASIN\n(/cancel ile işlem iptal edilir)")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", lambda u, c: handle_text(u, c)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
