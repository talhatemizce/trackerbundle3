from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # eBay
    ebay_env: str = Field(default="production", validation_alias="EBAY_ENV")  # production|sandbox
    ebay_client_id: str | None = Field(default=None, validation_alias="EBAY_CLIENT_ID")
    ebay_client_secret: str | None = Field(default=None, validation_alias="EBAY_CLIENT_SECRET")
    ebay_app_id: str | None = Field(default=None, validation_alias="EBAY_APP_ID")  # Finding API (default=client_id)

    # Amazon SP-API
    lwa_client_id: str | None = Field(default=None, validation_alias="LWA_CLIENT_ID")
    lwa_client_secret: str | None = Field(default=None, validation_alias="LWA_CLIENT_SECRET")
    lwa_refresh_token: str | None = Field(default=None, validation_alias="LWA_REFRESH_TOKEN")
    aws_access_key_id: str | None = Field(default=None, validation_alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(default=None, validation_alias="AWS_SECRET_ACCESS_KEY")
    aws_region: str = Field(default="us-east-1", validation_alias="AWS_REGION")
    spapi_endpoint: str = Field(default="https://sellingpartnerapi-na.amazon.com", validation_alias="SPAPI_ENDPOINT")
    spapi_marketplace_id: str = Field(default="ATVPDKIKX0DER", validation_alias="SPAPI_MARKETPLACE_ID")

    # Telegram (scheduler direct notify)
    telegram_bot_token: str | None = Field(default=None, validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, validation_alias="TELEGRAM_CHAT_ID")

    # Paths (override optional)
    data_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2] / "data")
    isbn_store: Path | None = Field(default=None, validation_alias="ISBN_STORE")
    rules_file: Path | None = Field(default=None, validation_alias="RULES_FILE")
    ebay_token_file: Path | None = Field(default=None, validation_alias="EBAY_TOKEN_FILE")

    # Scheduler
    sched_tick_seconds: int = Field(default=300, validation_alias="SCHED_TICK_SECONDS")
    sched_batch_limit: int = Field(default=5, validation_alias="SCHED_BATCH_LIMIT")

    # Price limits (base)
    default_new_limit: float = Field(default=50.0, validation_alias="DEFAULT_NEW_LIMIT")
    default_good_limit: float = Field(default=30.0, validation_alias="DEFAULT_GOOD_LIMIT")

    # AI Analysis — Multi-LLM
    gemini_api_key:      str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    groq_api_key:        str | None = Field(default=None, validation_alias="GROQ_API_KEY")
    cerebras_api_key:    str | None = Field(default=None, validation_alias="CEREBRAS_API_KEY")
    openrouter_api_key:  str | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")

    # Buyback APIs
    bookscouter_api_key: str | None = Field(default=None, validation_alias="BOOKSCOUTER_API_KEY")
    booksrun_api_key:    str | None = Field(default=None, validation_alias="BOOKSRUN_API_KEY")
    # perplexity_api_key kaldırıldı — ay başı $5 kredi, kart riski, değmez
    # sambanova_api_key kaldırıldı — sadece $5 expiring credit, gerçek free tier değil

    # Make offer ceiling multiplier
    make_offer_multiplier: float = Field(default=1.30, validation_alias="MAKE_OFFER_MULTIPLIER")

    # CALCULATED shipping heuristic (0.0 = disabled → item skipped)
    calculated_ship_estimate_usd: float = Field(default=0.0, validation_alias="CALCULATED_SHIP_ESTIMATE_USD")

    def resolved_data_dir(self) -> Path:
        p = self.data_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    def resolved_isbn_store(self) -> Path:
        return self.isbn_store or (self.resolved_data_dir() / "isbns.json")

    def resolved_rules_file(self) -> Path:
        return self.rules_file or (self.resolved_data_dir() / "rules.json")

    def resolved_ebay_token_file(self) -> Path:
        return self.ebay_token_file or (self.resolved_data_dir() / "ebay_token.json")

    def resolved_notified_file(self) -> Path:
        return self.resolved_data_dir() / "notified.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.resolved_data_dir()
    return s
