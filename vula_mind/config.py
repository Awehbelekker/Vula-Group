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
    model_vision: str = "google/gemini-2.5-flash"  # OpenRouter vision model for Smart Scanner (cheap, strong OCR)
    # Cheap tier for mechanical LLM work (doc analysis, classification): local-first via
    # the ollama.vula-ai.com tunnel, then this cheap cloud model, then escalate to the 70B.
    model_worker_cheap_local: str = "llama3.1:8b"          # free, on the local GPU via the tunnel
    model_worker_cheap: str = "google/gemini-2.5-flash"    # cloud fallback when the tunnel is down

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
    # Ian's own WhatsApp number — a tenant owner/team message that sounds like a question about
    # the Vula PLATFORM itself (not their business) gets forwarded here (vula/integrations/
    # platform_support.py). Blank = feature is a no-op (never blocks normal routing).
    platform_support_phone: str = ""

    # ── Cloud LLM fallback (used on Railway where local Ollama is unavailable) ──
    # Set OPENROUTER_API_KEY to enable cloud inference via OpenRouter.
    # When set, the server uses OpenRouter for generation and embeddings.
    openrouter_api_key: str = ""

    # ── Voice-note transcription (WhatsApp audio → text) ───────────────────────
    # OpenAI-compatible /audio/transcriptions endpoint. Local-first: point
    # TRANSCRIBE_BASE at a faster-whisper server on the SA GPU when available;
    # otherwise a cloud provider (Groq whisper-large-v3, OpenAI whisper-1).
    # If none is configured, voice notes degrade gracefully (ask the customer to type).
    transcribe_base: str = ""          # e.g. https://api.groq.com/openai/v1  or  http://whisper.vula-ai.com/v1
    transcribe_api_key: str = ""       # key for that endpoint (blank for a local server)
    transcribe_model: str = "whisper-large-v3"
    openai_api_key: str = ""           # fallback: transcribe via api.openai.com (whisper-1)

    # ── Twilio WhatsApp (alternative to Meta — test via Twilio Sandbox) ─────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""   # e.g. "whatsapp:+14155238886" (sandbox number)

    # ── Hybrid LLM: local model (Ollama) + cloud fallback model (OpenRouter) ────
    # model_worker = local Ollama model name (used via the vula-ai.com tunnel).
    # model_worker_cloud = the OpenRouter model used when local Ollama is down.
    model_worker_cloud: str = "meta-llama/llama-3.3-70b-instruct"
    # When true, generation always uses the cloud model (smart 70B) regardless of
    # local Ollama availability. Embeddings are unaffected (routed separately by
    # MODEL_EMBED). Use for accuracy-first production; the local GPU stays free
    # for embeddings.
    prefer_cloud_llm: bool = False

    # Requirement-(c) complexity threshold for llm_router: local-first is kept unless the estimated
    # prompt size (chars/4 ≈ tokens) reaches this cap, in which case generation escalates to the
    # cloud model (logged with reason). Tune to Vula's real workload.
    local_complexity_token_cap: int = 8000

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

    # ── ClickUp OAuth app (one app for all Vula tenants) ────────────────────
    # Create at ClickUp → Settings → Apps; redirect URL = <api>/v1/clickup/oauth/callback
    clickup_client_id: str = ""
    clickup_client_secret: str = ""
    # Public base URL the OAuth redirect comes back to (defaults to Railway prod)
    public_base_url: str = "https://vula-group-production.up.railway.app"

    # ── Google OAuth app (one app for all tenants — Drive + Gmail) ──────────
    # Create in Google Cloud Console; redirect URI = <api>/v1/google/oauth/callback
    google_client_id: str = ""
    google_client_secret: str = ""

    # ── Microsoft (Azure AD) OAuth app — OneDrive + Outlook via Graph ───────
    # Register in Azure portal; redirect URI = <api>/v1/microsoft/oauth/callback
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_authority: str = "https://login.microsoftonline.com/common"

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

    # ── Verified-reasoning: per-skill verification (core/verification.py) ────
    # JSON map of skill name → policy ("none"|"deterministic"|"adversarial"), overriding the
    # skill's class attribute. Flip per skill via env, no redeploy: e.g. '{"reasoning": "adversarial"}'
    verification_policy_overrides: str = "{}"
    verification_adversarial_action: str = "caveat"   # caveat | escalate (escalate reserved)
    verification_checker_timeout_s: float = 8.0       # hard cap on the adversarial pass
    verification_checker_max_tokens: int = 300
    readback_verify_enabled: bool = True              # admin mutating-tool read-back gate

    @property
    def verification_policies(self) -> dict[str, str]:
        import json
        try:
            data = json.loads(self.verification_policy_overrides)
            return data if isinstance(data, dict) else {}
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
