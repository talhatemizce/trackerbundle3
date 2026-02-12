from fastapi import FastAPI
import os
from datetime import datetime, timezone
from fastapi.responses import HTMLResponse

app = FastAPI(title="TrackerBundle Panel API")

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <h1>TrackerBundle Panel API</h1>
    <p>OK ✅</p>
    <ul>
      <li><a href="/docs">Docs (Swagger)</a></li>
      <li><a href="/status">Status</a></li>
      <li><a href="/health">Health</a></li>
    </ul>
    """

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/status")
def status():
    return {
        "ok": True,
        "service": "trackerbundle-panel",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "has_bot_token": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
    }
