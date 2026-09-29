"""One-off codemod (2026-09-29): hardcoded palette hex → design tokens, so every page follows
the brand, the corner/density style and (later) dark mode. Idempotent; run from vula_dashboard/.

- Neutral/brand/status hex literals → var(--token).
- `${C.x}NN` / `C.x + "NN"` (a hex alpha appended to a colour — broken the moment the colour is
  a var(), and already broken for every palette that mapped green to var(--accent)) →
  color-mix(in srgb, <colour> P%, transparent).
- `fontFamily: 'system-ui'` → removed (the body font applies); 'Cormorant Garamond' → var(--font-display).
Skips theme/, the Puck storefront renderer (its own --brand-* scheme) and this tool.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path("src")
SKIP = {"theme", "puck", "__preview"}
# own deliberate dark looks: the pre-login marketing wizard and Takeoff's blueprint view
SKIP_FILES = {"VulaPageRender.jsx", "tenantThemes.js", "tokens.js", "VulaOnboarding.jsx", "VulaTakeoff.jsx"}
# A colour that is DATA (saved, compared, a colour-picker's value) must stay a real hex.
DATA_LINE = re.compile(r"useState\(|hex6\(|accent_color|ink_color|secondary_color|setAccent|setInk|"
                       r"toUpperCase\(\)\s*===|type=['\"]color")

HEX = {
    "8A8680": "var(--muted)", "B5B0A8": "var(--faint)", "DDD8CE": "var(--border)",
    "2A2A2A": "var(--text)", "1E1E1E": "var(--ink)", "F0EDE5": "var(--surface-alt)",
    "F7F4EE": "var(--bg)", "ECE8DF": "var(--border-soft)", "EEE9DF": "var(--border-soft)",
    "2C5545": "var(--accent)", "234436": "var(--accent-dark)",
    "EF4444": "var(--danger)", "C0392B": "var(--danger)", "A23B2D": "var(--danger)", "DC2626": "var(--danger)",
    "B7791F": "var(--warn)", "C4861A": "var(--warn)", "F59E0B": "var(--warn)", "D97706": "var(--warn)",
    "2E7D32": "var(--ok)", "16A34A": "var(--ok)", "22C55E": "var(--ok)", "15803D": "var(--ok)",
    "2B5797": "var(--info)",
    # near-duplicates that crept in over time (second pass)
    "6B7280": "var(--muted)", "9CA3AF": "var(--faint)", "F5F2EC": "var(--surface-alt)",
    "F0EDE8": "var(--surface-alt)", "EDE9DF": "var(--border-soft)", "FAF9F6": "var(--bg)",
    "1A1A1A": "var(--ink)", "111111": "var(--ink)", "2C7A4B": "var(--ok)", "FEF2F2": "var(--danger-soft)",
    # third pass: soft status backgrounds and stray greys (third-party brand colours stay)
    "FECACA": "var(--danger-soft)", "FDEDEC": "var(--danger-soft)", "991B1B": "var(--danger)",
    "F0FDF4": "var(--ok-soft)", "E1F0E3": "var(--ok-soft)", "EAF2EF": "var(--ok-soft)",
    "166534": "var(--ok)", "1E7145": "var(--ok)", "10B981": "var(--ok)", "3D7260": "var(--accent-dark)",
    "A8780A": "var(--warn)", "B45309": "var(--warn)", "FEF9E7": "var(--warn-soft)", "FFF8EE": "var(--warn-soft)",
    "FFF3C4": "var(--warn-soft)", "FBF7E9": "var(--warn-soft)", "FDE68A": "var(--warn-soft)", "F9E79F": "var(--warn-soft)",
    "4B5563": "var(--text)", "3A3A3A": "var(--text)", "0A0A0A": "var(--ink)", "F7F5EF": "var(--bg)",
    "FAFAF8": "var(--bg)", "E5DFCF": "var(--border)",
}
SHORT = {"444": "var(--text)", "333": "var(--text)", "555": "var(--muted)", "666": "var(--muted)",
         "DDD": "var(--border)", "FEE": "var(--danger-soft)", "FCC": "var(--danger-soft)"}
hex_re = re.compile(r"#(" + "|".join(HEX) + r")(?![0-9a-fA-F])", re.I)
short_re = re.compile(r"#(" + "|".join(SHORT) + r")(?![0-9a-fA-F])", re.I)
# var(--x, #hex) fallbacks become plain var(--x) first, so the hex inside isn't rewritten twice
fallback_re = re.compile(r"var\((--[a-z0-9-]+),\s*#[0-9a-fA-F]{3,8}\)")
white_bg_re = re.compile(r"((?:background(?:Color)?|surface|card|cardBg|panel|paper|bg|base|white|inputBg)\s*:\s*)(['\"])(?:#(?:fff|ffffff)|white)\2", re.I)
suffix_tpl = re.compile(r"\$\{([A-Za-z_][\w.]*)\}([0-9a-fA-F]{2})(?![0-9a-fA-F])")
suffix_cat = re.compile(r"([A-Za-z_][\w]*\.[A-Za-z_]\w*)\s*\+\s*(['\"])([0-9a-fA-F]{2})\2")
sysfont_re = re.compile(r",?\s*fontFamily\s*:\s*(['\"])system-ui(?:,[^'\"]*)?\1\s*,?")
cormorant_re = re.compile(r"(['\"])'?Cormorant Garamond'?(?:,\s*[^'\"]*)?\1")


def pct(nn: str) -> str:
    return f"{round(int(nn, 16) / 255 * 100)}%"


def fix_sysfont(m):
    # keep exactly one comma between neighbours when an object entry is removed
    s = m.group(0)
    return "," if s.startswith(",") and s.rstrip().endswith(",") else ""


broken_var_suffix = re.compile(r"var\((--[a-z0-9-]+)\)([0-9a-fA-F]{2})(?![0-9a-fA-F])")


def _colours(line: str) -> str:
    if DATA_LINE.search(line):
        return line
    line = fallback_re.sub(lambda m: f"var({m.group(1)})", line)
    line = white_bg_re.sub(lambda m: f"{m.group(1)}{m.group(2)}var(--surface){m.group(2)}", line)
    line = hex_re.sub(lambda m: HEX[m.group(1).upper()], line)
    return short_re.sub(lambda m: SHORT[m.group(1).upper()], line)


def convert(text: str) -> str:
    text = "".join(_colours(line) for line in text.splitlines(keepends=True))
    text = broken_var_suffix.sub(lambda m: f"color-mix(in srgb, var({m.group(1)}) {pct(m.group(2))}, transparent)", text)
    text = suffix_tpl.sub(lambda m: f"color-mix(in srgb, ${{{m.group(1)}}} {pct(m.group(2))}, transparent)", text)
    text = suffix_cat.sub(lambda m: f"`color-mix(in srgb, ${{{m.group(1)}}} {pct(m.group(3))}, transparent)`", text)
    text = sysfont_re.sub(fix_sysfont, text)
    text = cormorant_re.sub(lambda m: f"{m.group(1)}var(--font-display){m.group(1)}", text)
    return text


def main(write: bool) -> None:
    changed = 0
    for p in ROOT.rglob("*.js*"):
        if any(part in SKIP for part in p.parts) or p.name in SKIP_FILES:
            continue
        old = p.read_text()
        new = convert(old)
        if new != old:
            changed += 1
            if write:
                p.write_text(new)
    print(f"{changed} files {'changed' if write else 'would change'}")


if __name__ == "__main__":
    main("--write" in sys.argv)
