"""Safe on-disk names for files whose name came from outside (email attachments, Drive/OneDrive
files, dashboard uploads).

A sender controls an email attachment's filename, so writing `upload_dir / tenant / name` with
the raw name let a name like "../../other-tenant/x.pdf" (or an absolute path, which pathlib's
`/` treats as a new root) write outside the tenant's upload folder. Keeps the human-readable
name (spaces, brackets) — only path structure and control characters are removed.
"""
from __future__ import annotations

import re
from pathlib import Path

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def safe_filename(name: str, default: str = "upload") -> str:
    base = str(name or "").replace("\\", "/").split("/")[-1]
    base = _CONTROL.sub("", base).strip().lstrip(".") or default
    if len(base) > 180:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 180 - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else base[:180]
    return base


def safe_upload_path(directory: Path, name: str) -> Path:
    """directory / safe_filename(name), guaranteed to stay inside directory."""
    directory = Path(directory)
    p = directory / safe_filename(name)
    if p.resolve().parent != directory.resolve():
        p = directory / "upload"
    return p
