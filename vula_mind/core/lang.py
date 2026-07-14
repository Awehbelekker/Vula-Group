"""
core/lang.py — lightweight South African language detection + naming.

Used to remember a customer's language preference. Voice notes give us a reliable language
code straight from Whisper; this heuristic covers TEXT messages where we have no such signal.

It is deliberately conservative: it only returns a non-English code when there's a clear marker
word, otherwise None (meaning "unknown — don't overwrite what we already know"). Distinguishing
isiZulu from isiXhosa perfectly from a few words is hard; we make a best effort and lean on the
Whisper signal for voice. English is never force-detected here — the assistant already mirrors it.
"""
from __future__ import annotations

import re
from typing import Optional

LANG_NAMES = {
    "en": "English", "af": "Afrikaans", "zu": "isiZulu", "xh": "isiXhosa",
    "st": "Sesotho", "nso": "Sepedi", "tn": "Setswana", "ts": "Xitsonga",
    "ve": "Tshivenda", "ss": "siSwati", "nr": "isiNdebele",
}

# Marker words strongly associated with each language (lowercased, whole-word matched).
# The Nguni group (zu/xh/ss/nr) and Sotho-Tswana group (st/nso/tn) overlap heavily; perfect
# separation from a few words is impossible, so this is best-effort — a close-family match
# (e.g. isiZulu for a siSwati speaker) still beats defaulting to English.
_MARKERS = {
    "af": {"ek", "jy", "nie", "asseblief", "dankie", "goeie", "wil", "hê", "kan", "'n",
           "baie", "hoeveel", "bestel", "vis", "hoender", "aflewering", "more", "vandag", "graag"},
    "zu": {"sawubona", "ngiyabonga", "ngicela", "yebo", "cha", "unjani", "ngifuna",
           "malini", "inhlanzi", "ngingathanda", "usuku", "namuhla", "ngiyafuna"},
    "xh": {"molo", "molweni", "enkosi", "ndicela", "ndifuna", "uxolo", "kunjani",
           "ewe", "hayi", "malini", "intlanzi", "ndingathanda", "ndiyabulela"},
    "st": {"dumela", "leboha", "hobane", "hantle", "hle", "kea", "nna", "hle", "tjhelete"},
    "nso": {"dumela", "leboga", "nyaka", "gabotse", "eng", "bjang", "ke", "kgopela"},
    "tn": {"dumela", "leboga", "kopa", "tsamaya", "eng", "batla", "tswela", "ke"},
    "ts": {"avuxeni", "inkomu", "ndzi", "lava", "kombela", "njhani", "xikwembu", "ha"},
    "ve": {"ndaa", "aa", "livhuwa", "toda", "khou", "vha", "ndi", "vhukati"},
    "ss": {"sawubona", "ngiyabonga", "ngicela", "yebo", "unjani", "ngiceli", "siswati", "kunjani"},
    "nr": {"lotjhani", "ngiyathokoza", "salibonani", "ngicela", "unjani", "ndebele"},
}


def detect_language(text: Optional[str]) -> Optional[str]:
    """Best-effort language code from a text message. None when unsure (don't overwrite)."""
    if not text:
        return None
    words = set(re.findall(r"[a-zà-ÿ']+", text.lower()))
    if not words:
        return None
    best, score = None, 0
    for code, markers in _MARKERS.items():
        hits = len(words & markers)
        if hits > score:
            best, score = code, hits
    # Require at least one clear marker; single very-short messages need a real hit.
    return best if score >= 1 else None


# Of the 11 SA official languages, Whisper only handles English + Afrikaans as speech.
# A voice note Whisper labels as anything else is unreliable → ask the customer to type.
VOICE_LANGS = {"en", "af"}


def is_voice_supported(code: Optional[str]) -> bool:
    c = normalize_lang(code)
    return c in VOICE_LANGS if c else True   # unknown → don't block (proceed)


def normalize_lang(code: Optional[str]) -> Optional[str]:
    """Normalise a Whisper/heuristic code to our short set (e.g. strip regions)."""
    if not code:
        return None
    c = code.strip().lower().split("-")[0]
    return c or None


def language_name(code: Optional[str]) -> Optional[str]:
    code = normalize_lang(code)
    return LANG_NAMES.get(code) if code else None
