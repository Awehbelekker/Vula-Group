"""vula/migrations_files.py — the migrations/NNN_*.sql files, in the order they run.

One listing shared by Master › Health's paste-able script (vula/api/master.py) and the runner
(tools/migrate.py), so both agree on which files exist and in what order. Files that don't start
with a number are skipped: the `_APPLY_*` bundles are hand-made concatenations of numbered files,
not migrations of their own.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

_NAME_RE = re.compile(r"^(\d+)_.+\.sql$")


@dataclass(frozen=True)
class MigrationFile:
    number: int
    name: str
    path: Path

    def read_text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def sha256(self) -> str:
        return file_sha256(self.path)


def file_sha256(path: Path) -> str:
    """Hash of the file with CRLF normalised to LF, so a Windows checkout (autocrlf) and Linux
    CI hash the same file the same way."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def list_migrations(directory: Optional[Path] = None) -> list[MigrationFile]:
    """Every numbered migration file, ordered by number (then name)."""
    d = Path(directory) if directory is not None else MIGRATIONS_DIR
    out = []
    for p in d.glob("*.sql"):
        m = _NAME_RE.match(p.name)
        if m:
            out.append(MigrationFile(int(m.group(1)), p.name, p))
    return sorted(out, key=lambda f: (f.number, f.name))


def files_for_numbers(numbers: Iterable[str], directory: Optional[Path] = None) -> list[Path]:
    """migrations/<num>_*.sql for each number, in migration order. Numbers that aren't digits
    are ignored."""
    wanted = {int(n) for n in numbers if str(n).isdigit()}
    return [f.path for f in list_migrations(directory) if f.number in wanted]
