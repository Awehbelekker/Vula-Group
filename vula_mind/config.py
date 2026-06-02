"""
Central configuration — all tunables in one place, loaded from .env.

Usage anywhere in the codebase:
    from config import settings
    print(settings.ollama_base)
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Ollama ──────────────────────────────────────────────────────────────
    ollama_base: str = "http://localhost:11434"
    model_edge: str = "llama3.1:8b"
    model_worker: str = "deepseek-r1:8b"
    model_reasoner: str = "deepseek-r1:14b"
    model_embed: str = "bge-m3"
    model_ocr: str = "llava:7b"
    model_vision: str = "anthropic/claude-3.5-sonnet"  # OpenRouter vision model for Smart Scanner

    # ── Qdrant ──────────────────────────────────────────────────────────────
    qdrant_base: str = "http://localhost:6333"
    qdrant_api_key: str = ""            # set if Qdrant is running with auth

    # ── API Server ──────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 7438
    api_key: str = ""                   # require this header: X-API-Key
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    debug: bool = False

    # ── Storage ─────────────────────────────────────────────────────────────
    # Railway sets DATA_DIR=/data (volume mount). Locally falls back to home dir.
    upload_dir: Path = Path("/data/uploads") if Path("/data").exists() else Path(tempfile.gettempdir()) / "vula_uploads"
    takeoff_upload_dir: Path = Path("/data/takeoff") if Path("/data").exists() else Path(tempfile.gettempdir()) / "vula_takeoff"
    data_dir: Path = Path("/data") if Path("/data").exists() else Path.home() / ".vula" / "data"
    reflection_db: Path = Path("/data/reflection.db") if Path("/data").exists() else Path.home() / ".vula" / "reflection.db"

    # ── Chunking ────────────────────────────────────────────────────────────
    chunk_size: int = 512
    chunk_overlap: int = 64
    max_file_mb: int = 50

    # ── Mesh ────────────────────────────────────────────────────────────────
    mesh_port: int = 7437
    mesh_role: str = "desktop"          # desktop | laptop | mobile | server
    vram_total_gb: float = 24.0

    # ── Reflection ──────────────────────────────────────────────────────────
    reflection_model: str = "deepseek-r1:8b"

    # ── Onboarding / Tenant provisioning ────────────────────────────────────
    supabase_url: str = ""
    supabase_service_key: str = ""
    whatsapp_api_url: str = "https://graph.facebook.com/v19.0"
    whatsapp_phone_id: str = ""
    whatsapp_token: str = ""
    team_whatsapp: str = "+27820000000"   # Richard/Judy notifications
    whatsapp_verify_token: str = ""       # Meta webhook verification token
    vula_base_url: str = "https://app.vula.ai"

    # ── Cloud LLM fallback (used on Railway where local Ollama is unavailable) ──
    # Set OPENROUTER_API_KEY to enable cloud inference via OpenRouter.
    # When set, the server uses OpenRouter for generation and embeddings.
    openrouter_api_key: str = ""

    # ── PayFast ─────────────────────────────────────────────────────────────
    payfast_merchant_id: str = ""
    payfast_merchant_key: str = ""

    # ── Yoco (commerce payments) ─────────────────────────────────────────────
    yoco_secret_key: str = ""
    yoco_public_key: str = ""
    yoco_webhook_secret: str = ""

    # ── Vula Facebook App (for WhatsApp Embedded Signup) ────────────────────
    # Create at developers.facebook.com — one app for all Vula Commerce clients
    vula_fb_app_id: str = ""       # Facebook App ID
    vula_fb_app_secret: str = ""   # Facebook App Secret
    vula_fb_config_id: str = ""    # Embedded Signup configuration ID

    # ── Vula Commerce ────────────────────────────────────────────────────────
    # Supabase service role key alias (commerce service uses this name)
    supabase_service_role_key: str = ""
    n8n_webhook_base: str = ""           # e.g. https://n8n.vula.co.za/webhook
    # Per-tenant store URLs for Yoco redirect URLs (JSON string)
    # e.g. '{"off-the-hook": "https://offthehook.co.za"}'
    store_urls_json: str = '{"off-the-hook": "https://offthehook.co.za", "awake-sa": "https://awakesa.co.za"}'
    whatsapp_phone_number_id: str = ""   # Meta phone_number_id for primary WhatsApp

    @property
    def store_urls(self) -> dict[str, str]:
        import json
        try:
            return json.loads(self.store_urls_json)
        except Exception:
            return {}

    # ── Email (Resend) ────────────────────────────────────────────────────────
    resend_api_key: str = ""
    from_email: str = "Vula Group <hello@vula.ai>"
    team_email: str = ""                # team notifications (Richard/Judy)

    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.takeoff_upload_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reflection_db.parent.mkdir(parents=True, exist_ok=True)

    def warn_missing(self) -> list[str]:
        """Return list of warnings about missing production config."""
        import logging
        log = logging.getLogger("vula.config")
        warnings = []
        if not self.api_key:
            warnings.append("API_KEY not set — API is unauthenticated (dev mode only)")
        if not self.supabase_url or "your-project" in self.supabase_url:
            warnings.append("SUPABASE_URL not configured — tenant provisioning disabled")
        if not self.supabase_service_key or "your-service" in self.supabase_service_key:
            warnings.append("SUPABASE_SERVICE_KEY not configured — tenant provisioning disabled")
        if not self.whatsapp_token or "your-permanent" in self.whatsapp_token:
            warnings.append("WHATSAPP_TOKEN not configured — signup notifications disabled")
        if not self.payfast_merchant_id:
            warnings.append("PAYFAST_MERCHANT_ID not configured — payment links disabled")
        if not self.resend_api_key:
            warnings.append("RESEND_API_KEY not configured — email notifications disabled")
        for w in warnings:
            log.warning("Config: %s", w)
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
settings.ensure_dirs()
