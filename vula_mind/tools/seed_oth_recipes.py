"""
tools/seed_oth_recipes.py — seed a starter SA fish/chicken recipe pack into a tenant's knowledge
base, so the commerce assistant grounds its recipe recommendations in real recipes (not invented
ones). Each recipe names likely catalog items so the assistant can offer them to add to the cart.

Usage:
    python tools/seed_oth_recipes.py --tenant off-the-hook --commit
Idempotent-ish: ingest_text keys on filename; re-running re-ingests the same docs.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RECIPES: dict[str, str] = {
    "Grilled Hake with Lemon Butter": (
        "A quick weeknight favourite. Ingredients: hake fillets, butter, garlic, lemon, parsley, "
        "salt & pepper. Method: Pat hake dry, season. Melt butter with crushed garlic and a squeeze "
        "of lemon. Grill or pan-fry hake 3–4 min a side until it flakes. Spoon over the lemon butter "
        "and scatter parsley. Serve with rice or a fresh salad. Serves 4."),
    "Cape Malay Fish Curry": (
        "Warm, mild and aromatic. Ingredients: hake or kingklip, onion, garlic, ginger, curry powder, "
        "turmeric, tinned tomato, coconut milk. Method: Fry onion, garlic and ginger; add spices and "
        "cook till fragrant. Add tomato and coconut milk, simmer 10 min. Slide in fish chunks and "
        "poach gently 6–8 min. Finish with coriander. Serve with rice and sambals."),
    "Braaied Snoek with Apricot Glaze": (
        "A Cape classic for the fire. Ingredients: whole snoek, apricot jam, butter, garlic, lemon. "
        "Method: Butterfly the snoek. Mix melted butter, apricot jam, crushed garlic and lemon. Braai "
        "skin-side down first, basting often with the glaze, until just cooked and sticky. Serve with "
        "roosterkoek."),
    "Pan-Seared Yellowtail": (
        "Simple and lets the fish shine. Ingredients: yellowtail steaks, olive oil, garlic, lemon, "
        "black pepper. Method: Season steaks. Sear in hot oil 3 min a side. Rest, then finish with "
        "lemon and cracked pepper. Great with roast baby potatoes and greens."),
    "Garlic Butter Prawns": (
        "Ready in 10 minutes. Ingredients: prawns, butter, lots of garlic, chilli, lemon, parsley. "
        "Method: Melt butter, fry garlic and chilli, add prawns and cook 2–3 min until pink. Squeeze "
        "lemon, toss with parsley. Serve with crusty bread to mop the sauce."),
    "Crumbed Calamari": (
        "Crowd-pleasing starter. Ingredients: calamari tubes, flour, egg, breadcrumbs, lemon, oil. "
        "Method: Slice calamari into rings, dredge in flour, egg then crumbs. Shallow-fry 1–2 min "
        "until golden — don't overcook or it toughens. Serve with lemon and tartare."),
    "Peri-Peri Chicken": (
        "Smoky and spicy. Ingredients: chicken pieces, peri-peri, garlic, lemon, paprika, oil. "
        "Method: Marinate chicken in blended peri-peri, garlic, lemon and paprika for 30 min. Braai "
        "or roast, basting, until cooked through and charred at the edges. Serve with chips and slaw."),
    "Chicken Potjie": (
        "Slow-cooked comfort. Ingredients: chicken pieces, onion, potato, carrot, stock, herbs. "
        "Method: Brown chicken in the pot. Layer onions, potatoes and carrots on top, add stock and "
        "herbs. Lid on, simmer gently over coals 1.5–2 hrs without stirring. Serve with rice or pap."),
    "Smoked Snoek Pâté": (
        "A five-minute snack. Ingredients: smoked snoek, cream cheese, lemon, spring onion, black "
        "pepper. Method: Flake snoek, removing bones. Blend with cream cheese, lemon and spring onion. "
        "Season with pepper. Serve on crackers or fresh bread."),
    "Prawn & Chorizo Pasta": (
        "Weeknight luxury. Ingredients: prawns, chorizo, garlic, tinned tomato, chilli, pasta, "
        "parsley. Method: Fry chorizo till its oil renders, add garlic and chilli, then tomato; simmer. "
        "Add prawns for the last 3 min. Toss through cooked pasta with parsley."),
    "Beer-Battered Fish & Chips": (
        "The takeaway classic at home. Ingredients: hake, flour, beer, potatoes, oil, salt, vinegar. "
        "Method: Whisk flour and cold beer to a batter. Dip hake, deep-fry until golden and crisp. "
        "Serve with hand-cut chips, salt and vinegar."),
    "Grilled Kingklip with Herb Butter": (
        "Firm, meaty and elegant. Ingredients: kingklip, butter, parsley, garlic, lemon. Method: "
        "Season kingklip, grill 4 min a side. Top with herb-garlic butter and lemon. Serve with "
        "steamed veg."),
    "Kabeljou Baked in Foil": (
        "Foolproof and moist. Ingredients: kabeljou fillet, lemon, thyme, olive oil, garlic. Method: "
        "Lay fish on foil, top with lemon slices, thyme, garlic and oil. Seal and bake 180°C for "
        "18–20 min. Serve in the parcel."),
    "Seared Tuna with Sesame": (
        "Restaurant-style, five minutes. Ingredients: tuna steak, sesame seeds, soy, ginger, oil. "
        "Method: Roll tuna in sesame, sear 1 min a side (rare centre). Slice thin, drizzle soy-ginger. "
        "Serve with rice or salad."),
    "Cape Fish Frikkadels": (
        "Comforting fish meatballs. Ingredients: hake, onion, egg, breadcrumbs, parsley, tomato. "
        "Method: Flake and mix hake with onion, egg, crumbs and parsley. Shape balls, brown, then "
        "simmer in a light tomato sauce 15 min. Serve with rice."),
    "Grilled Sole Meunière": (
        "Delicate and buttery. Ingredients: sole, flour, butter, lemon, parsley. Method: Dust sole in "
        "flour, pan-fry in butter 2 min a side. Add lemon and parsley to the pan and spoon over."),
    "Mussels in White Wine": (
        "A Cape West Coast treat. Ingredients: mussels, white wine, garlic, onion, cream, parsley. "
        "Method: Sweat onion and garlic, add wine and mussels, cover 4 min until they open. Stir in "
        "cream and parsley. Serve with bread."),
    "Fish Tacos with Slaw": (
        "Fresh and fun. Ingredients: hake, tortillas, cabbage, lime, yoghurt, coriander. Method: Season "
        "and pan-fry hake, flake. Toss cabbage with lime and yoghurt. Fill tortillas with fish, slaw "
        "and coriander."),
    "Crumbed Chicken Schnitzel": (
        "Kids' favourite. Ingredients: chicken breasts, flour, egg, breadcrumbs, lemon. Method: Flatten "
        "breasts, crumb in flour-egg-crumbs, shallow-fry until golden. Serve with lemon and chips."),
    "Roast Lemon & Herb Chicken": (
        "Sunday go-to. Ingredients: whole chicken, lemon, garlic, thyme, butter. Method: Rub chicken "
        "with butter, garlic and thyme, stuff with lemon. Roast 200°C ~1 hr 15 until juices run clear. "
        "Rest before carving."),
    "Butter Chicken": (
        "Mild, creamy curry. Ingredients: chicken, yoghurt, tomato, butter, garam masala, cream. "
        "Method: Marinate chicken in yoghurt and spice, then brown. Simmer in tomato-butter sauce, "
        "finish with cream. Serve with rice or naan."),
    "Chicken Sosaties": (
        "Braai staple. Ingredients: chicken, apricot jam, curry powder, onion, dried apricots. Method: "
        "Marinate chicken cubes in curry-apricot sauce overnight. Thread onto skewers with onion and "
        "apricots. Braai, basting, until cooked."),
    "Durban Chicken Curry": (
        "Bold and fragrant. Ingredients: chicken, onion, tomato, curry masala, potato, curry leaves. "
        "Method: Braise onion, spices and curry leaves, add tomato and chicken, then potato. Simmer "
        "until tender. Serve with rice and sambals."),
    "Peri-Peri Chicken Livers": (
        "Spicy starter. Ingredients: chicken livers, peri-peri, garlic, butter, cream. Method: Fry "
        "livers in butter with garlic and peri-peri, deglaze, add a splash of cream. Serve with bread."),
    "Chicken & Mushroom Pie": (
        "Hearty. Ingredients: chicken, mushrooms, onion, cream, puff pastry. Method: Cook chicken with "
        "onion and mushrooms, bind with cream, cool. Top with pastry, bake 200°C until golden."),
    "Honey-Soy Chicken Wings": (
        "Sticky crowd-pleaser. Ingredients: chicken wings, honey, soy, garlic, ginger. Method: Toss "
        "wings in honey-soy-garlic-ginger, roast 200°C ~35 min, turning, until sticky."),
    "Garlic Butter Calamari": (
        "Quick and tender. Ingredients: calamari, butter, garlic, chilli, lemon, parsley. Method: "
        "Flash-fry calamari in garlic butter with chilli 2 min, finish with lemon and parsley. Don't "
        "overcook."),
    "Crayfish Thermidor": (
        "Special occasion. Ingredients: crayfish, butter, cream, mustard, cheese. Method: Halve "
        "crayfish, brush with butter, grill. Top with a mustard-cream-cheese sauce and grill until "
        "bubbling."),
    "Snoek & Sweet Potato Curry": (
        "West Coast comfort. Ingredients: snoek, sweet potato, onion, curry powder, coconut milk. "
        "Method: Braise onion and spice, add sweet potato and coconut milk, simmer, then fold in "
        "flaked snoek. Serve with rice."),
    "Angelfish on the Braai": (
        "Simple and smoky. Ingredients: angelfish, lemon, butter, garlic, parsley. Method: Butterfly, "
        "baste with garlic-lemon butter, braai skin-side down until just cooked. Squeeze lemon."),
    "Salmon with Dill Yoghurt": (
        "Light and fresh. Ingredients: salmon fillet, yoghurt, dill, lemon, cucumber. Method: Pan-sear "
        "or bake salmon. Mix yoghurt, dill, lemon and grated cucumber; spoon alongside."),
    "Pickled Fish (Cape Malay)": (
        "Easter tradition. Ingredients: hake, onion, curry powder, turmeric, vinegar, sugar, bay. "
        "Method: Fry floured fish, set aside. Simmer onions in curried vinegar-sugar, layer with fish, "
        "chill 2 days. Serve cold with bread."),
    "Fish Cakes with Tartare": (
        "Uses up any white fish. Ingredients: hake, potato, spring onion, egg, breadcrumbs. Method: "
        "Mix flaked fish with mash and spring onion, shape, crumb, fry until golden. Serve with tartare."),
    "Prawn Curry": (
        "Rich and quick. Ingredients: prawns, onion, tomato, coconut milk, curry masala, curry leaves. "
        "Method: Braise onion and spice, add tomato and coconut, simmer, add prawns 4 min. Serve with "
        "rice."),
    "Chicken Stir-Fry": (
        "Fast midweek. Ingredients: chicken strips, mixed veg, soy, ginger, garlic, honey. Method: "
        "Stir-fry chicken hot and fast, add veg, then soy-ginger-garlic-honey. Toss and serve with "
        "noodles or rice."),
    "Yellowtail Fish Braai Parcel": (
        "Hands-off on the coals. Ingredients: yellowtail, onion, tomato, lemon, herbs, butter. Method: "
        "Layer fish with onion, tomato, lemon and herbs in foil with butter. Braai 15–20 min. Open and "
        "serve."),
    "Creamy Garlic Mussels Pasta": (
        "Comforting bowl. Ingredients: mussels, cream, garlic, white wine, pasta, parsley. Method: Steam "
        "mussels in wine and garlic, add cream, toss with pasta and parsley."),
    "Grilled Prawn Skewers": (
        "Braai winner. Ingredients: prawns, olive oil, garlic, lemon, chilli, parsley. Method: Thread "
        "prawns, brush with garlic-chilli-lemon oil, braai 2 min a side. Finish with parsley."),
    "Baked Fish Mornay": (
        "Retro comfort. Ingredients: hake, cheese, milk, flour, butter, mustard. Method: Poach fish, "
        "make a cheese sauce, pour over, top with more cheese and grill until golden."),
    "Chicken Potjie (Curry Style)": (
        "Slow and rich. Ingredients: chicken, potato, onion, curry powder, coconut milk. Method: Brown "
        "chicken, layer veg, add curry and coconut, simmer over coals 1.5 hr without stirring."),
}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="off-the-hook")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    print(f"{len(RECIPES)} recipes for tenant {args.tenant}")
    if not args.commit:
        for t in RECIPES:
            print("  -", t)
        print("\nDRY-RUN — re-run with --commit to ingest into the KB.")
        return 0

    from vula.ingestion.pipeline import VulaIngestionPipeline
    pipe = VulaIngestionPipeline(tenant_id=args.tenant)
    n = 0
    for title, body in RECIPES.items():
        slug = title.lower().replace(" ", "-").replace("&", "and")
        content = f"# {title}\n\n{body}\n\n(Off the Hook recipe)"
        try:
            await pipe.ingest_text(content=content, filename=f"recipe-{slug}.md")
            n += 1
            print("  ingested:", title)
        except Exception as exc:
            print("  FAILED:", title, exc)
    print(f"\nIngested {n}/{len(RECIPES)} recipes into {args.tenant}'s knowledge base.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
