"""No secret in code that ships to a browser or a phone (2026-10-26).

The mobile app had the server API key written into vula_mobile/src/api/vula.js, so anyone with
the app could act on every business. Code under the client folders below is bundled and handed
to every user — anything in it is public. This fails CI when one of those files holds a
secret-looking literal or reads a secret-named build variable.

Allowed: the Supabase publishable / anon key (it is meant for the browser — RLS guards the data),
and words like "api_key" used as a label. Run from vula_mind/: python tools/check_client_secrets.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLIENT_DIRS = ["vula_dashboard/src", "vula_mobile/src", "vula_mobile/app", "vula_site/src"]
EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".html"}

PATTERNS = [
    ("long hex key", re.compile(r"""["'`][0-9a-f]{40,}["'`]""")),
    ("OpenRouter key", re.compile(r"sk-or-v1-[0-9a-zA-Z]{20,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("Supabase secret key", re.compile(r"sb_secret_[A-Za-z0-9_\-]{10,}")),
    ("Supabase service-role JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]*c2VydmljZV9yb2xl")),
    ("Meta access token", re.compile(r"\bEAA[A-Za-z0-9]{60,}")),
    ("Cloudflare Access secret", re.compile(r"cfast_[A-Za-z0-9]{20,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # A build variable whose name says secret is inlined into the bundle by the bundler.
    ("secret build variable", re.compile(
        r"(?:import\.meta\.env|process\.env)\.(?:VITE_|EXPO_PUBLIC_|NEXT_PUBLIC_|REACT_APP_)?"
        r"(?!SUPABASE_ANON_KEY|SUPABASE_PUBLISHABLE_KEY)[A-Z0-9_]*(?:SECRET|API_KEY|PRIVATE|TOKEN|PASSWORD)[A-Z0-9_]*")),
]


def scan(root: Path = ROOT) -> list:
    found = []
    for d in CLIENT_DIRS:
        base = root / d
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix not in EXTS or "node_modules" in path.parts or not path.is_file():
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                for label, rx in PATTERNS:
                    if rx.search(line):
                        found.append((str(path.relative_to(root)), n, label))
    return found


def main() -> int:
    found = scan()
    if found:
        print("Secrets in client code (shipped to every browser/phone — move them server-side):")
        for f, n, label in found:
            print(f"  {f}:{n}  {label}")
        return 1
    print("OK: no secrets in client code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
