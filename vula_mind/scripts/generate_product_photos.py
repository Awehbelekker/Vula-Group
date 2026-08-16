#!/usr/bin/env python
"""
generate_product_photos.py — AI-generate realistic catalog photos for a tenant,
styled on the tenant's own reference photo.

Usage (from vula_mind/):
    python scripts/generate_product_photos.py --tenant off-the-hook --dry-run
    python scripts/generate_product_photos.py --tenant off-the-hook --only hake-fillets
    python scripts/generate_product_photos.py --tenant off-the-hook --all-missing

Idempotent: skips products that already have image_url (unless --force).
Every image passes a vision QA gate before being written to the store.
Cost ≈ $0.04/image (gemini-2.5-flash-image via OpenRouter).
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

import httpx
from supabase import create_client

from core.image_gen import (
    ImageGenError,
    build_product_prompt,
    describe_reference_style,
    generate_image,
    qa_check_image,
)

BUCKET = "product-images"


def _sb():
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_SERVICE_KEY"],
    )


# Hand-written, visually concrete subjects for products the generic template
# fails on. Key insight: the QA gate can only verify what's VISIBLE — sales-name
# details like "80g", "deboned", "flecked" aren't visually checkable, so both
# generation and QA need the visual essence instead.
SUBJECT_OVERRIDES = {
    "whole-octopus-unclean": "one whole fresh raw octopus with curled tentacles, glistening, centred on the board",
    "deboned-whole-chicken-fresh": "one whole raw chicken butterflied open and flattened (spatchcock style), skin on, on the board",
    "deboned-whole-chicken-frozen": "one whole raw chicken butterflied open and flattened, skin on, with a light frost, on the board",
    "snoek-head-off-gutted-flecked": "one long silver Cape snoek fish, headless and butterflied open lengthwise showing pale flesh, on the board",
    "smoked-trout-slices-80g": "thin translucent slices of deep orange smoked trout fanned out on the board",
    "half-shell-mussels-800g": "a dozen cooked mussels on the half shell, orange flesh visible, arranged on the board with light frost",
    "jacopever-fillets": "small red-skinned fish fillets with white flesh, arranged on the board",
    "kingklip-centre-cuts-skinless": "thick skinless white fish portions, firm pale flesh, stacked on the board",
    "crumbed-fish-cakes-50g-1kg-20-cakes": "small round golden-crumbed fish cakes stacked in a pyramid on the board",
    # Bundles — show the box contents together as a hamper.
    "braai-box": "a braai hamper on the board: a butterflied silver snoek, raw chicken pieces and drumsticks arranged together",
    "family-fish-box": "a family fish selection on the board: white fish fillets, golden crumbed fish cakes and pale calamari tubes arranged together",
    "freezer-stock-up-box": "a frozen seafood selection on the board: white fish portions, whole pink prawns and half-shell mussels with light frost",
}


def subject_for(product: dict) -> str:
    """Realistic, category-aware subject line — raw cuts / retail packs, never cooked."""
    override = SUBJECT_OVERRIDES.get(product.get("slug") or "")
    if override:
        return override
    name = product["name"]
    pack = product.get("pack_size") or ""
    cat = product.get("category") or ""
    pack_txt = f" ({pack})" if pack else ""
    if cat == "fresh_fish":
        return f"fresh raw {name}{pack_txt}, as sold at a premium Cape Town fishmonger"
    if cat == "fresh_chicken":
        return f"fresh raw pasture-raised chicken — {name}{pack_txt}, uncooked, as sold at a butcher"
    if cat == "frozen_chicken":
        return f"frozen pasture-raised chicken — {name}{pack_txt}, shown as the frozen retail portions"
    if cat == "frozen_seafood":
        return f"frozen seafood — {name}{pack_txt}, shown as the frozen retail portions as sold"
    return f"{name}{pack_txt}, the retail product as sold"


# ── Composition variety ───────────────────────────────────────────────────────
# Keep the house style (board, black backdrop, soft light) as the unifying
# thread, but vary angle/props/arrangement so the catalog doesn't look like 42
# copies of one scene. Variant index = gallery position; the per-product hash
# staggers arrangement so neighbouring products differ too.

ANGLES = [
    "slightly elevated three-quarter angle",
    "directly overhead flat-lay",
    "close-up 45-degree angle emphasising the texture of the product",
]

PROPS = {
    "fresh_fish": [
        "a few lemon slices, fresh dill and coarse sea salt",
        "lemon wedges, sprigs of rosemary and cracked black pepper",
        "a small bowl of coarse salt and fresh parsley",
    ],
    "fresh_chicken": [
        "sprigs of rosemary and thyme with whole garlic cloves",
        "cracked peppercorns, fresh thyme and a small dish of spice rub",
        "fresh sage leaves and a linen cloth corner",
    ],
    "frozen_chicken": [
        "minimal props, a light natural frost visible on the product",
        "a folded kitchen cloth, product showing light frost",
        "no props, clean board, light frost on the portions",
    ],
    "frozen_seafood": [
        "minimal props, a light natural frost visible on the product",
        "crushed ice scattered around the portions",
        "no props, clean board, light frost on the portions",
    ],
    "extras": [
        "no food props — the retail container is the hero, clean composition",
        "a plain linen cloth beside the container",
        "minimal styling, product label facing camera",
    ],
}

ARRANGEMENTS = ["board angled diagonally", "board straight-on, subject centred",
                "subject slightly off-centre with negative space"]


def composition_for(product: dict, variant_idx: int) -> str:
    cat = product.get("category") or "extras"
    h = sum(ord(c) for c in product["name"])
    angle = ANGLES[variant_idx % len(ANGLES)]
    props = PROPS.get(cat, PROPS["extras"])[(h + variant_idx) % 3]
    arrangement = ARRANGEMENTS[(h + variant_idx) % 3]
    return (
        f"Composition for this shot: {angle}; {arrangement}; props: {props}. "
        "Keep the same board, backdrop and lighting as the house style."
    )


def compress_jpeg(data: bytes, max_edge: int = 1600, quality: int = 82) -> bytes:
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((max_edge, max_edge))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def upload(sb, tenant: str, slug: str, data: bytes) -> str:
    path = f"{tenant}/products/{int(time.time() * 1000)}-ai-{slug}.jpg"
    sb.storage.from_(BUCKET).upload(
        path, data, {"content-type": "image/jpeg", "upsert": "true"}
    )
    url = sb.storage.from_(BUCKET).get_public_url(path)
    return url.rstrip("?")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="off-the-hook")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="slug of a single product to generate")
    ap.add_argument("--all-missing", action="store_true")
    ap.add_argument("--redo-ai", action="store_true",
                    help="also regenerate covers that are AI images (never real photos)")
    ap.add_argument("--add-variants", type=int, default=0, metavar="N",
                    help="after covers, add N extra gallery angles per product")
    ap.add_argument("--force", action="store_true", help="regenerate even if a photo exists")
    ap.add_argument("--lenient-qa", action="store_true",
                    help="publish the 2nd attempt even if QA still objects (FLAGGED for human review)")
    ap.add_argument("--save-dir", help="also save every generated image locally for review")
    args = ap.parse_args()

    sb = _sb()
    rows = (
        sb.table("commerce_products").select("*")
        .eq("tenant_id", args.tenant).order("category").order("name").execute()
    ).data or []
    if not rows:
        sys.exit(f"No products for tenant {args.tenant}")

    # Reference = the tenant's REAL photo (house style), never an AI-generated one.
    ref_row = (next((r for r in rows if r.get("image_url") and "-ai-" not in r["image_url"]), None)
               or next((r for r in rows if r.get("image_url")), None))
    reference = None
    style_desc = ""
    if ref_row:
        print(f"Style reference: {ref_row['name']}")
        async with httpx.AsyncClient(timeout=60) as c:
            reference = (await c.get(ref_row["image_url"])).content
        style_desc = await describe_reference_style(reference)
        print(f"Extracted style: {style_desc}\n")
    else:
        print("No reference photo found — using prompt-only style.\n")

    def _is_ai(url: str | None) -> bool:
        return bool(url) and "-ai-" in url

    # Cover targets: missing photos, plus (with --redo-ai) AI covers to re-do with
    # the new varied compositions. REAL photos are never regenerated.
    targets = [
        r for r in rows
        if (args.force or not r.get("image_url") or (args.redo_ai and _is_ai(r.get("image_url"))))
        and not (r.get("image_url") and not _is_ai(r.get("image_url")))  # protect real photos
        and (not args.only or r.get("slug") == args.only)
    ]
    if args.only and not targets:
        sys.exit(f"Product '{args.only}' not found or already has a real photo.")
    if not args.only and not args.all_missing and not args.dry_run and not args.add_variants:
        sys.exit("Pass --only <slug>, --all-missing, --add-variants N, or --dry-run.")
    if not args.all_missing and not args.only:
        targets = []  # variants-only run

    n_variant_shots = args.add_variants * len(rows) if args.add_variants else 0
    est = 0.04 * (len(targets) + n_variant_shots)
    print(f"{len(targets)} cover(s) + ~{n_variant_shots} variant shot(s) to generate (~${est:.2f})")
    if args.dry_run:
        for r in targets:
            print(f"  cover: [{r['category']}] {r['name']}  →  {composition_for(r, 0)}")
        return

    crafted_subjects: dict = {}  # product_id -> LLM-crafted subject (one call per product)

    async def resolve_subject(r) -> str:
        """Hand-override → LLM prompt generator → category template."""
        if SUBJECT_OVERRIDES.get(r.get("slug") or ""):
            return SUBJECT_OVERRIDES[r["slug"]]
        if r["id"] in crafted_subjects:
            return crafted_subjects[r["id"]]
        try:
            from core.image_gen import craft_photo_subject
            subject = await craft_photo_subject(r)
        except Exception:
            subject = subject_for(r)
        crafted_subjects[r["id"]] = subject
        return subject

    async def gen_one(r, variant_idx: int):
        """Generate one shot for product r at the given variant angle; returns url."""
        subject = await resolve_subject(r)
        prompt = (build_product_prompt(subject, style_desc, has_reference_image=bool(reference))
                  + " " + composition_for(r, variant_idx))
        img = await generate_image(prompt, reference)
        if not await qa_check_image(img, subject):
            img = await generate_image(prompt, reference)
            if not await qa_check_image(img, subject):
                if not args.lenient_qa:
                    raise ImageGenError("QA failed twice")
                print("FLAGGED(qa) ", end="", flush=True)
        if args.save_dir:
            os.makedirs(args.save_dir, exist_ok=True)
            with open(os.path.join(args.save_dir, f"{r.get('slug') or r['id']}-v{variant_idx}.jpg"), "wb") as fh:
                fh.write(compress_jpeg(img))
        return upload(sb, args.tenant, r.get("slug") or r["id"], compress_jpeg(img))

    ok = failed = 0

    # Pass 1 — covers (variant 0 composition)
    for i, r in enumerate(targets, 1):
        print(f"[cover {i}/{len(targets)}] {r['name']} … ", end="", flush=True)
        try:
            url = upload_url = await gen_one(r, 0)
            gallery = [url] + [u for u in (r.get("images") or []) if u and not _is_ai(u)][:4]
            sb.table("commerce_products").update(
                {"image_url": url, "images": gallery}
            ).eq("tenant_id", args.tenant).eq("id", r["id"]).execute()
            r["image_url"], r["images"] = url, gallery
            print(f"OK  {url}")
            ok += 1
        except Exception as exc:
            print(f"ERROR: {exc}")
            failed += 1
        await asyncio.sleep(3)

    # Pass 2 — extra gallery angles for every product with a cover
    if args.add_variants:
        vt = [r for r in rows if r.get("image_url")]
        for i, r in enumerate(vt, 1):
            gallery = [u for u in (r.get("images") or []) if u]
            want = 1 + args.add_variants
            if len(gallery) >= want:
                print(f"[var {i}/{len(vt)}] {r['name']} — gallery full, skip")
                continue
            print(f"[var {i}/{len(vt)}] {r['name']} … ", end="", flush=True)
            for v in range(len(gallery), want):
                try:
                    url = await gen_one(r, v)
                    gallery.append(url)
                    ok += 1
                    print(f"+{v} ", end="", flush=True)
                except Exception as exc:
                    print(f"ERR({exc}) ", end="", flush=True)
                    failed += 1
                await asyncio.sleep(3)
            sb.table("commerce_products").update(
                {"images": gallery[:5], "image_url": gallery[0]}
            ).eq("tenant_id", args.tenant).eq("id", r["id"]).execute()
            print("saved")

    print(f"\nDone: {ok} generated, {failed} failed/skipped.")


if __name__ == "__main__":
    asyncio.run(main())
