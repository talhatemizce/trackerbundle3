import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str | None = os.getenv("TELEGRAM_BOT_TOKEN")
    api_base: str = os.getenv("API_BASE", "http://127.0.0.1:8000")

def get_settings() -> Settings:
    return Settings()
