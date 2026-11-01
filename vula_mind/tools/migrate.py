"""
tools/migrate.py — apply migrations/NNN_*.sql over a direct Postgres connection, and record them.

Until 31 Oct 2026 every migration was pasted by hand into the Supabase SQL editor. This runs the
same files, in the same order Master › Health uses (vula/migrations_files.py), and writes one row
per file to vula_schema_migrations (migration 240) once that file has succeeded.

Connects with DATABASE_URL (Supabase › Project settings › Database › Connection string, the
direct or session-pooler URI: PostgREST can't run DDL). The password is never printed.

Run from vula_mind/:

    python tools/migrate.py status   --target staging
    python tools/migrate.py baseline --target staging --upto 239          # dry run: lists files
    python tools/migrate.py baseline --target staging --upto 239 --yes    # records them, runs nothing
    python tools/migrate.py apply    --target staging                     # dry run: lists files
    python tools/migrate.py apply    --target staging --yes               # runs pending files
    python tools/migrate.py apply    --target staging --only 241 --yes    # just one file

`--target staging|production` is required and the (masked) host is printed. Writing to
production also needs `--i-mean-production`, and `--target staging` is refused when
DATABASE_URL names the production project (SUPABASE_URL's project ref while VULA_ENV isn't
"staging"). A production database with nothing recorded must be baselined before `apply`:
production already has 1..239, applied by hand.

`apply` runs each file in its own transaction and stops at the first failure; nothing from the
failing file is kept and no row is written for it. Files are never re-run once recorded; a file
whose hash changed after it ran is reported as "changed" (re-run it deliberately with --only).
Files starting with `_` (the _APPLY_* bundles) are not migrations and are skipped.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

_HERE = Path(__file__).resolve().parents[1]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from vula.migrations_files import MigrationFile, list_migrations  # noqa: E402

TABLE = "vula_schema_migrations"
BOOTSTRAP_FILE = "240_schema_migrations.sql"   # creates TABLE; run before the first record

_INSERT = (f"insert into {TABLE} (filename, number, sha256, applied_by, baseline) "
           "values (%s, %s, %s, %s, %s) "
           "on conflict (filename) do update set sha256 = excluded.sha256, applied_at = now(), "
           "applied_by = excluded.applied_by, baseline = excluded.baseline")


# ── Connection string helpers (never print the password) ────────────────────────────────────

def _parts(dsn: str):
    return urlsplit(dsn or "")


def dsn_host(dsn: str) -> str:
    return _parts(dsn).hostname or ""


def mask_dsn(dsn: str) -> str:
    """user@host:port/db with the password removed — safe to print."""
    p = _parts(dsn)
    user = unquote(p.username or "")
    port = f":{p.port}" if p.port else ""
    db = p.path or ""
    return f"{user + '@' if user else ''}{p.hostname or '?'}{port}{db}"


def scrub(text: str, dsn: str) -> str:
    """An error message with the connection password (if any) blanked out."""
    pw = _parts(dsn).password
    out = str(text)
    if pw:
        out = out.replace(pw, "***").replace(unquote(pw), "***")
    return out.replace(dsn, mask_dsn(dsn)) if dsn else out


def supabase_ref(supabase_url: str) -> str:
    """The project ref in https://<ref>.supabase.co, or ""."""
    m = re.match(r"^https?://([a-z0-9]+)\.supabase\.(?:co|in)\b", (supabase_url or "").strip(), re.I)
    return m.group(1).lower() if m else ""


def dsn_mentions_ref(dsn: str, ref: str) -> bool:
    """Supabase's direct host is db.<ref>.supabase.co; the pooler's user is postgres.<ref>."""
    if not ref:
        return False
    p = _parts(dsn)
    return ref in (p.hostname or "").lower() or ref in unquote(p.username or "").lower()


def check_target(target: str, dsn: str, *, writes: bool, i_mean_production: bool,
                 supabase_url: str = "", vula_env: str = "production") -> Optional[str]:
    """None when it's safe to go on, else why not."""
    if not dsn:
        return "DATABASE_URL is not set"
    if target not in ("staging", "production"):
        return "--target must be staging or production"
    if target == "production" and writes and not i_mean_production:
        return "refusing to write to production without --i-mean-production"
    if target == "staging" and (vula_env or "").lower() != "staging":
        ref = supabase_ref(supabase_url)
        if dsn_mentions_ref(dsn, ref):
            return (f"--target staging, but DATABASE_URL names project {ref}, which is SUPABASE_URL's "
                    "project while VULA_ENV isn't staging (production). Check DATABASE_URL")
    return None


# ── What's applied, pending, changed (pure) ─────────────────────────────────────────────────

@dataclass
class Status:
    applied: list[MigrationFile] = field(default_factory=list)
    pending: list[MigrationFile] = field(default_factory=list)
    changed: list[MigrationFile] = field(default_factory=list)   # recorded, file edited since
    unknown: list[str] = field(default_factory=list)              # recorded, no such file now
    recorded: int = 0


def compute_status(files: list[MigrationFile], applied: dict[str, dict]) -> Status:
    st = Status(recorded=len(applied))
    names = set()
    for f in files:
        names.add(f.name)
        row = applied.get(f.name)
        if row is None:
            st.pending.append(f)
        elif row.get("sha256") != f.sha256:
            st.changed.append(f)
        else:
            st.applied.append(f)
    st.unknown = sorted(n for n in applied if n not in names)
    return st


def plan_apply(st: Status, only: Optional[int] = None) -> tuple[list[MigrationFile], Optional[str]]:
    """Files `apply` would run, in order, and an error if --only can't be honoured."""
    if only is None:
        return list(st.pending), None
    for f in st.pending + st.changed:
        if f.number == only:
            return [f], None
    if any(f.number == only for f in st.applied):
        return [], f"migration {only} is already applied (same hash) — nothing to do"
    return [], f"no migration file numbered {only}"


def plan_baseline(files: list[MigrationFile], applied: dict[str, dict], upto: int) -> list[MigrationFile]:
    return [f for f in files if f.number <= upto and f.name not in applied]


# ── Database (psycopg 3, or psycopg2) ───────────────────────────────────────────────────────

def connect(dsn: str):
    try:
        import psycopg
        # No server-side prepared statements: they break behind Supabase's transaction pooler.
        return psycopg.connect(dsn, prepare_threshold=None)
    except ImportError:
        import psycopg2
        return psycopg2.connect(dsn)


def table_exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(f"select to_regclass('public.{TABLE}') is not null")
        row = cur.fetchone()
    conn.rollback()   # read-only; don't leave a transaction open
    return bool(row and row[0])


def fetch_applied(conn) -> dict[str, dict]:
    if not table_exists(conn):
        return {}
    with conn.cursor() as cur:
        cur.execute(f"select filename, number, sha256, applied_at, applied_by, baseline from {TABLE}")
        rows = cur.fetchall()
    conn.rollback()
    keys = ("filename", "number", "sha256", "applied_at", "applied_by", "baseline")
    return {r[0]: dict(zip(keys, r)) for r in rows}


def ensure_table(conn, files: list[MigrationFile]) -> None:
    """Create TABLE by running migration 240 itself (idempotent), so there's one definition."""
    boot = next((f for f in files if f.name == BOOTSTRAP_FILE), None)
    if boot is None:
        raise RuntimeError(f"{BOOTSTRAP_FILE} not found")
    with conn.cursor() as cur:
        cur.execute(boot.read_text())
    conn.commit()


def apply_files(conn, files: list[MigrationFile], applied_by: str,
                log: Callable[[str], None] = print) -> tuple[int, Optional[str]]:
    """Run each file in its own transaction, recording it on success. Stops at the first
    failure. Returns (how many succeeded, failing filename or None)."""
    done = 0
    for f in files:
        try:
            with conn.cursor() as cur:
                cur.execute(f.read_text())
                cur.execute(_INSERT, (f.name, f.number, f.sha256, applied_by, False))
            conn.commit()
        except Exception as exc:  # noqa: BLE001 — report it and stop
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            log(f"  FAILED {f.name}: {type(exc).__name__}: {exc}")
            return done, f.name
        done += 1
        log(f"  applied {f.name}")
    return done, None


def record_baseline(conn, files: list[MigrationFile], applied_by: str) -> int:
    """Mark files as applied without running them (one transaction)."""
    try:
        with conn.cursor() as cur:
            for f in files:
                cur.execute(_INSERT, (f.name, f.number, f.sha256, applied_by, True))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(files)


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────

def _settings_value(name: str, default: str = "") -> str:
    if os.environ.get(name.upper()):
        return os.environ[name.upper()]
    try:
        from config import settings
        return str(getattr(settings, name, default) or default)
    except Exception:  # noqa: BLE001
        return default


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="migrate.py", description=__doc__.split("\n\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "apply", "baseline"])
    ap.add_argument("--target", required=True, choices=["staging", "production"])
    ap.add_argument("--i-mean-production", action="store_true",
                    help="required to write to --target production")
    ap.add_argument("--yes", action="store_true", help="actually do it (default is a dry run)")
    ap.add_argument("--only", type=int, help="apply: just this migration number")
    ap.add_argument("--upto", type=int, help="baseline: mark files numbered <= N as applied")
    ap.add_argument("--database-url-env", default="DATABASE_URL",
                    help="env var holding the connection string (default DATABASE_URL)")
    ap.add_argument("--migrations-dir", type=Path, default=None, help=argparse.SUPPRESS)
    return ap


def main(argv: Optional[list[str]] = None, *, connect_fn: Callable = connect,
         env: Optional[dict] = None, out: Callable[[str], None] = print) -> int:
    args = _parser().parse_args(argv)
    env = os.environ if env is None else env
    dsn = env.get(args.database_url_env, "")
    writes = args.command in ("apply", "baseline") and args.yes
    if args.command == "baseline" and args.upto is None:
        out("baseline needs --upto N")
        return 2
    if args.only is not None and args.command != "apply":
        out("--only is for apply")
        return 2
    supabase_url = env.get("SUPABASE_URL") or (_settings_value("supabase_url") if env is os.environ else "")
    vula_env = env.get("VULA_ENV") or (_settings_value("vula_env", "production") if env is os.environ
                                       else "production")
    err = check_target(args.target, dsn, writes=writes, i_mean_production=args.i_mean_production,
                       supabase_url=supabase_url, vula_env=vula_env)
    out(f"target: {args.target}  database: {mask_dsn(dsn) if dsn else '(none)'}")
    if err:
        out(f"REFUSED: {err}")
        return 2

    files = list_migrations(args.migrations_dir)
    try:
        conn = connect_fn(dsn)
    except Exception as exc:  # noqa: BLE001
        out(f"could not connect: {scrub(f'{type(exc).__name__}: {exc}', dsn)}")
        return 1
    try:
        return _run(args, conn, files, out, dsn)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _run(args, conn, files: list[MigrationFile], out, dsn: str) -> int:
    has_table = table_exists(conn)
    applied = fetch_applied(conn) if has_table else {}
    st = compute_status(files, applied)
    who = os.environ.get("MIGRATE_APPLIED_BY") or f"{getpass.getuser()} via tools/migrate.py"

    if args.command == "status":
        out(f"{len(files)} migration files; {st.recorded} recorded in {TABLE}"
            + ("" if has_table else f" ({TABLE} doesn't exist yet)"))
        out(f"  applied: {len(st.applied)}   pending: {len(st.pending)}   changed: {len(st.changed)}")
        for f in st.pending:
            out(f"  PENDING  {f.name}")
        for f in st.changed:
            out(f"  CHANGED  {f.name} (edited after it ran; re-run with apply --only {f.number})")
        for n in st.unknown:
            out(f"  UNKNOWN  {n} (recorded, but no such file)")
        if not st.recorded:
            out("  Nothing recorded yet. For a database whose migrations were applied by hand "
                "(production), run baseline --upto <last applied> first.")
        return 0

    if args.command == "baseline":
        todo = plan_baseline(files, applied, args.upto)
        out(f"baseline: {len(todo)} file(s) <= {args.upto} to mark applied WITHOUT running them")
        for f in todo:
            out(f"  {f.name}")
        if not args.yes:
            out("dry run — nothing written. Add --yes to record them.")
            return 0
        if not has_table:
            ensure_table(conn, files)
            out(f"  created {TABLE} (ran {BOOTSTRAP_FILE})")
        n = record_baseline(conn, todo, who)
        out(f"recorded {n} baseline row(s)")
        return 0

    # apply
    todo, err = plan_apply(st, args.only)
    if err:
        out(err)
        return 1 if "no migration" in err else 0
    if st.changed and args.only is None:
        out(f"note: {len(st.changed)} changed file(s) are NOT re-run (see status)")
    out(f"apply: {len(todo)} file(s), in order:")
    for f in todo:
        out(f"  {f.name}")
    if not todo:
        out("nothing to apply")
        return 0
    if not args.yes:
        out("dry run — nothing executed. Add --yes to run them.")
        return 0
    if args.target == "production" and not st.recorded:
        out(f"REFUSED: nothing is recorded in {TABLE} on production. Run baseline --upto 239 "
            "first, or every file would be re-run.")
        return 2
    if not has_table:
        ensure_table(conn, files)
        out(f"  created {TABLE} (ran {BOOTSTRAP_FILE})")
    done, failed = apply_files(conn, todo, who, log=lambda s: out(scrub(s, dsn)))
    if failed:
        out(f"stopped: {done} applied, {failed} failed (rolled back, not recorded); "
            f"{len(todo) - done - 1} not attempted")
        return 1
    out(f"done: {done} applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
