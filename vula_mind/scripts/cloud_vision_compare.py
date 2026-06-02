"""Quick A/B: run the same sample receipt through a cloud vision model and
report extraction accuracy + real token cost. Compares against local llava."""
import asyncio, base64, json, os, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()
from test_smart_scanner import make_sample_receipt  # noqa
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from test_smart_scanner import make_sample_receipt  # noqa

MODEL = sys.argv[1] if len(sys.argv) > 1 else "google/gemini-2.5-flash"
PRICE_PER_M_IN = 0.30   # $/M input tokens (gemini-2.5-flash)
PRICE_PER_M_OUT = 2.50  # $/M output tokens

SYSTEM = (
    "You are a document-extraction engine for a food business admin system. "
    "Look at the image and return ONLY a valid JSON object — no prose, no fences. "
    'Schema: {"doc_type","supplier","customer","date","due_date","category",'
    '"total_cents","vat_cents","line_items":[{"description","quantity","unit",'
    '"unit_price_cents","total_cents"}],"confidence","notes"}. '
    "All money values in CENTS (Rands x 100)."
)

async def main():
    import litellm
    litellm.drop_params = True
    img = base64.b64encode(make_sample_receipt()).decode()
    print(f"Model: openrouter/{MODEL}\n")
    resp = await litellm.acompletion(
        model=f"openrouter/{MODEL}",
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": "Extract this supplier invoice."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}},
            ]},
        ],
        temperature=0.1, max_tokens=1500,
        api_key=os.environ["OPENROUTER_API_KEY"],
        api_base="https://openrouter.ai/api/v1",
    )
    content = resp.choices[0].message.content or ""
    import re
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    try:
        ex = json.loads(content)
    except Exception:
        m = re.search(r"\{.*\}", content, re.DOTALL); ex = json.loads(m.group(0)) if m else {}
    print(json.dumps(ex, indent=2, ensure_ascii=False))

    print("\n── ACCURACY (vs known sample R6 170.00, 5 items) ──")
    checks = [
        ("supplier 'Ocean Fresh'", "ocean fresh" in (ex.get("supplier") or "").lower()),
        ("total ≈ R6170", abs((ex.get("total_cents") or 0) - 617000) <= 1000),
        ("vat ≈ R804.78", abs((ex.get("vat_cents") or 0) - 80478) <= 2000),
        ("5 line items", len(ex.get("line_items") or []) == 5),
        ("date 2026-05-28", (ex.get("date") or "") == "2026-05-28"),
    ]
    for label, ok in checks:
        print(f"  {'✅' if ok else '❌'} {label}")

    u = resp.usage
    cost = (u.prompt_tokens / 1e6) * PRICE_PER_M_IN + (u.completion_tokens / 1e6) * PRICE_PER_M_OUT
    print(f"\n── COST ──\n  in={u.prompt_tokens} out={u.completion_tokens} tokens")
    print(f"  ≈ ${cost:.5f} per scan  →  ${cost*1000:.2f} per 1000 receipts")

asyncio.run(main())
