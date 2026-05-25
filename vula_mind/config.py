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
    upload_dir: Path = Path(tempfile.gettempdir()) / "vula_uploads"
    takeoff_upload_dir: Path = Path(tempfile.gettempdir()) / "vula_takeoff"
    reflection_db: Path = Path.home() / ".vula" / "reflection.db"

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

    # ── PayFast ─────────────────────────────────────────────────────────────
    payfast_merchant_id: str = ""
    payfast_merchant_key: str = ""

    # ── Email (Resend) ────────────────────────────────────────────────────────
    resend_api_key: str = ""
    from_email: str = "Vula Group <hello@vula.ai>"
    team_email: str = ""                # team notifications (Richard/Judy)

    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.takeoff_upload_dir.mkdir(parents=True, exist_ok=True)
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
