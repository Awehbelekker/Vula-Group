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
    model_edge: str = "deepseek-r1:1.5b"
    model_worker: str = "deepseek-r1:7b"
    model_reasoner: str = "deepseek-r1:14b"
    model_embed: str = "bge-m3"
    model_ocr: str = "glm-ocr"

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
    reflection_model: str = "deepseek-r1:1.5b"

    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.takeoff_upload_dir.mkdir(parents=True, exist_ok=True)
        self.reflection_db.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
settings.ensure_dirs()
