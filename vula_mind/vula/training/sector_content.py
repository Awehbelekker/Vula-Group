"""
vula/training/sector_content.py

Curated South African sector knowledge packs — one small shared collection per business type
(tenant_id "sector_<type>", collection vula_sector_<type>), searched after a tenant's OWN
documents and before the general business_basics corpus.

Why (2026-09-30, Ian: "if I'm onboarding a new tenant, how are we accessing the current knowledge
base for general business processes?"): a new tenant has no documents of its own on day one, and
the starter KB (vula/commerce/starter_kb.py) is model-drafted with [placeholders]. These packs are
written once, reviewed by the Vula team, and shared by every tenant of that type.

Rules for this file — same discipline as business_content.py:
- General sector practice and South African regulation only. Never a tenant's own policy, price
  or product figure — those come only from the tenant's documents.
- State a rule plainly only when it's settled; where it depends on a manufacturer, municipality
  or contract, say so ("check the manufacturer's data sheet").
- REVIEW_STATUS records which packs the team has signed off. Unreviewed packs are still searched,
  but answers from them carry the "general guidance" framing the skills already apply to shared
  knowledge.
"""
from __future__ import annotations

from typing import Dict, List

from vula.training.content import TrainingDocument


def sector_tenant_id(business_type: str) -> str:
    return f"sector_{(business_type or 'other').strip().lower()}"


REVIEW_STATUS: Dict[str, str] = {
    "food": "draft", "retail": "draft", "trades": "draft", "services": "draft",
    "health": "draft", "rep": "draft",
}


_CPA_RETAIL = TrainingDocument(
    filename="consumer_protection_for_sellers.md",
    topic="Consumer Protection Act — what a seller must do",
    content="""# Consumer Protection Act (CPA) — what a South African seller must do

## Defective goods: the six-month implied warranty
Under section 56 of the Consumer Protection Act 68 of 2008, goods must be of good quality, in good
working order and free of defects. If goods fail within SIX MONTHS of delivery, the CUSTOMER
chooses repair, replacement or a refund — not the seller. A repaired item that fails again
within three months of the repair must be replaced or refunded. Normal wear and tear, misuse and
goods sold "voetstoots" with the specific defect disclosed are not covered.

## Change-of-mind returns
There is no general legal right to return goods because a customer changed their mind in a shop;
a store's own returns policy governs that. Exceptions: goods bought through DIRECT MARKETING
(the seller approached the customer, e.g. a call or door-to-door) have a five business-day
cooling-off period, and goods the customer didn't have a chance to examine before delivery may
be returned within 10 business days if unsuitable.

## Prices
The price displayed is the price. If two prices are displayed for the same item, the customer
is entitled to the LOWER one (section 23). A price must include VAT for a VAT-registered seller.

## Lay-bys and deposits
Goods on lay-by are at the seller's risk until collected. If the customer cancels, the seller may
keep only a reasonable cancellation charge, not the whole deposit.

## Record keeping
Keep proof of the sale date (invoice/receipt) — the six-month period is measured from delivery.
""")

_STOCK_BASICS = TrainingDocument(
    filename="stock_management_basics.md",
    topic="Stock control for small shops",
    content="""# Stock control for a small South African business

## Reorder point
Reorder point = average daily sales × supplier lead time (days) + safety stock. Safety stock
covers late deliveries and busy days; a common starting point is a few days' extra sales, adjusted
once you see how reliable the supplier is.

## Stock-takes
Count everything at least once a year for the financial statements (SARS expects closing stock to
be valued), and count fast-moving or high-value lines more often (weekly or monthly cycle counts).
Count before trading starts, freeze movements while counting, and investigate every variance —
the difference between the system quantity and the counted quantity is shrinkage (theft, damage,
admin errors, supplier short-deliveries).

## Receiving deliveries
Check every delivery against the delivery note and the purchase order BEFORE signing: quantities,
damage, expiry dates. Note short or damaged items on the delivery note and have the driver sign.
A signed clean delivery note makes a later claim against the supplier very hard.

## Valuing stock
Stock is valued at cost (not selling price), using FIFO or weighted average consistently. Slow
and obsolete stock should be written down.

## Margin vs mark-up
Mark-up is profit ÷ cost; gross margin is profit ÷ selling price. A 50% mark-up is only a 33%
margin. Price from the margin you need, and remember the selling price includes VAT for a VAT
vendor — margin is calculated on the VAT-exclusive price.
""")


SECTOR_DOCUMENTS: Dict[str, List[TrainingDocument]] = {
    "retail": [_CPA_RETAIL, _STOCK_BASICS],

    "food": [
        TrainingDocument(
            filename="food_premises_rules.md",
            topic="Food premises, hygiene and the Certificate of Acceptability",
            content="""# Selling food in South Africa — premises and hygiene

## Certificate of Acceptability (CoA)
Any business that handles, stores, prepares or sells food needs a Certificate of Acceptability
for its premises from the local municipality's environmental health office, under the
Regulations Governing General Hygiene Requirements for Food Premises (R638 of 2018, under the
Foodstuffs, Cosmetics and Disinfectants Act 54 of 1972). This includes home-based kitchens and
vehicles used to transport food. The CoA is issued to the person in charge for those premises
and must be displayed.

## Person in charge and training
The person in charge must be trained in food safety and must make sure every food handler is
trained, wears clean protective clothing, and doesn't handle food while ill with a transmissible
condition.

## Temperature control
Keep chilled foods at 0–5 °C and frozen foods at −18 °C or colder; keep hot food above 65 °C.
Never refreeze thawed fish or meat. Fresh fish is best kept on ice at close to 0 °C. Log fridge
and freezer temperatures daily — a written log is your evidence if an inspector or a customer
complaint comes.

## Transport
Food vehicles must keep food at the right temperature for the whole trip (cooler boxes with ice
or a refrigerated vehicle) and must be kept clean; raw and ready-to-eat food are kept apart.

## Labelling
Prepackaged food must be labelled according to the labelling regulations (R146): product name,
ingredients, allergens, net quantity, date marking (best before / use by), and the name and
address of the manufacturer or seller.
"""),
        TrainingDocument(
            filename="seafood_selling_basics.md",
            topic="Selling seafood — sustainability, quality and customer care",
            content="""# Selling seafood — practical basics

## Sustainability (SASSI)
The Southern African Sustainable Seafood Initiative (SASSI, run by WWF-SA) rates species green
(best choice), orange (think twice) or red (don't buy; often illegal to sell). Customers and
restaurants increasingly ask — know the rating of what you sell, and check SASSI's current list
because ratings change.

## Freshness checks
Fresh whole fish: bright clear eyes, red gills, firm flesh that springs back, a clean sea smell.
Fillets: moist, translucent, no browning at the edges. Frozen product: no freezer burn or ice
crystals inside the packaging (a sign it thawed and refroze).

## Weights and pricing
Sell by weight on a trade-approved scale. State whether a price is per kilogram, per piece or per
pack, and whether it is whole, gutted, filleted or portioned — the yield differs a lot (a whole
fish can lose half its weight when filleted).

## Deliveries and the cold chain
Pack chilled orders with ice or ice packs, deliver frozen product frozen, and give customers a
realistic delivery window. A customer who is not home is the main cold-chain risk — agree a
safe-drop or collection rule up front.

## Complaints
A customer who reports spoiled product is entitled to a remedy under the Consumer Protection Act
(repair doesn't apply to food — replace or refund). Ask for a photo and the order number, then
check your temperature log for that batch.
"""),
        _CPA_RETAIL,
    ],

    "trades": [
        TrainingDocument(
            filename="construction_compliance_basics.md",
            topic="Contractor registration and site compliance in South Africa",
            content="""# Construction and trades — registration and compliance basics

## CIDB
Contractors tendering for PUBLIC sector work must be registered with the Construction Industry
Development Board (CIDB) in the right class of works, at a grade (1–9) that covers the tender
value. Private clients may also ask for a CIDB grading as a quality signal.

## NHBRC (home building)
Anyone building new homes must be registered with the National Home Builders Registration Council
and must enrol each new home before construction starts (Housing Consumers Protection Measures
Act 95 of 1998). Renovations and alterations to existing homes generally don't need enrolment.

## Health and safety
The Construction Regulations 2014 (under the Occupational Health and Safety Act) require a
health and safety plan and a site file; certain larger projects need a construction work permit
from the Department of Employment and Labour before work starts. Workers must be inducted and
given the right protective equipment.

## COIDA
Register with the Compensation Fund and pay the annual assessment for your employees (Compensation
for Occupational Injuries and Diseases Act). Main contractors often ask subcontractors for a
letter of good standing before paying them.

## Contracts, variations and retention
Work to a written contract (JBCC, GCC or a clear written agreement). Get every VARIATION to the
scope in writing — signed by the client or their agent — before doing the extra work, with the
price or the basis for pricing it; unapproved extras are the most common reason contractors don't
get paid. Retention (commonly 5–10% of each payment) is held until practical completion and
released per the contract, often half at practical and half at final completion. Track retention
held on every certificate — it's money owed to you, and it's easy to forget to claim it. If you
became VAT registered after the work was certified, ask your accountant whether the retention
release carries VAT before you invoice it.
"""),
        TrainingDocument(
            filename="resilient_flooring_basics.md",
            topic="Resilient flooring — what the common specification terms mean",
            content="""# Resilient (vinyl, linoleum, rubber) flooring — reading a specification

These explain what the terms MEAN. The figure for a specific product always comes from that
product's own data sheet or test report — never assume one range's rating applies to another.

## Slip resistance
- R-rating (DIN 51130, ramp test with shoes): R9 (lowest) to R13 (highest). R9 suits dry
  indoor areas; wet areas, kitchens and ramps need higher ratings.
- EN 16165 is the newer European standard that includes the ramp method (Annex B gives the
  R-classification) and pendulum and other methods.
- Barefoot wet areas (showers, pool surrounds) are rated A/B/C (DIN 51097), C being the most
  slip-resistant.
- Pendulum Test Value (PTV): 36 or more is generally considered low slip risk in wet conditions.

## Fire
EN 13501-1 classifies floorings with an "fl" suffix: Bfl-s1 is a high rating commonly required
for public buildings; Cfl-s1 and Dfl-s1 are lower. "s1" means low smoke.

## Use classes (EN ISO 10874)
21–23 domestic (light to heavy), 31–34 commercial (moderate to very heavy), 41–43 light
industrial. A product for a busy hospital corridor needs a higher class than one for a bedroom.

## Thickness and wear layer
Total thickness is the whole product; the wear layer is the top layer that takes the traffic.
In homogeneous vinyl the full thickness is the wear layer; heterogeneous vinyl has a separate
printed layer under a clear wear layer.

## Acoustic
Impact-sound reduction is stated as ΔLw in dB (higher is quieter underfoot for the room below).

## Before installing
The subfloor must be dry, flat and clean. Moisture limits depend on the product and adhesive —
use the manufacturer's installation guide (commonly tested by hygrometer as relative humidity).
Acclimatise the material and adhesive at site temperature as the guide requires.
"""),
    ],

    "services": [
        TrainingDocument(
            filename="professional_services_basics.md",
            topic="Running a professional services practice — scope, fees, collections",
            content="""# Professional services — scope, fees and getting paid

## Engagement letter first
Before starting, send a written appointment / engagement letter: the scope (what IS and ISN'T
included), deliverables, fee basis (fixed, percentage of project cost, or hourly), payment
stages, and what happens to extra work. Work that starts on a handshake is the usual cause of
scope creep and fee disputes.

## Scope creep and additional services
When the client asks for something outside the agreed scope, say so at the time and confirm the
extra fee in writing before doing it. Keep a simple log of requests and the date you flagged
each one.

## Fees on a percentage of project cost
A fee as a percentage of construction cost rises and falls with the cost — agree how cost is
measured (tender price, final account) and when stages are invoiced (e.g. concept, council
submission, tender, construction, close-out).

## Invoicing and collections
Invoice promptly at each stage; state the due date (30 days is common) and your banking details.
Follow up on day 1 overdue, day 7 and day 14 before escalating. Ordinary debts prescribe after
THREE years under the Prescription Act — don't let an old invoice sit.

## Professional registration
Many professions require registration with a statutory body to practise and to sign off work —
for example SACAP for architectural professionals, ECSA for engineers, SACQSP for quantity
surveyors. Check the registration category allows the work you're signing.
"""),
    ],

    "health": [
        TrainingDocument(
            filename="health_practice_basics.md",
            topic="Running a small health practice — registration, records and privacy",
            content="""# Small health practice basics (South Africa)

## Registration
Practitioners must be registered with their statutory council (e.g. HPCSA, SANC for nurses, SAPC
for pharmacy, AHPCSA for allied health) and keep registration current; the practice number from
the Board of Healthcare Funders (BHF) is needed to bill medical schemes.

## Patient records
Keep clinical records for at least six years after the last visit (HPCSA guidance) — longer for
minors (until they turn 21) and for some occupational health records. Records must be kept
securely and patients may request access to their own records.

## Privacy (POPIA)
Health information is SPECIAL personal information under POPIA: process it only with consent or
another specific legal ground, restrict who in the practice can see it, and never discuss a
patient's details on an unsecured channel or with anyone the patient hasn't authorised.

## Bookings and cancellations
Publish your cancellation / no-show policy up front and apply it consistently; send reminders a
day before appointments.

## Billing
Medical scheme claims need the correct tariff codes and ICD-10 diagnosis codes. Tell patients
before treatment if your fees are above the scheme rate so they know the shortfall.
"""),
    ],

    "rep": [
        TrainingDocument(
            filename="sales_rep_operations.md",
            topic="Field sales rep — pipeline, specifiers, samples and follow-up",
            content="""# Field sales rep — working a territory

## Who to call on
In building products, the SPECIFIER (architect, interior designer, quantity surveyor) writes the
product into the specification; the CONTRACTOR installs and buys; the DISTRIBUTOR stocks and
delivers. A product that isn't specified is usually value-engineered out — spend time with
specifiers early in a project, and keep contractors informed once it's specified.

## Pipeline
Log every meeting with: who, company, project, what they need, next step and a date. A lead with
no next step and date is not a lead. Review the pipeline weekly: move, chase or close.

## Follow-up cadence
After a meeting or quote: follow up within 2 working days (thank-you + anything promised), again
at 1 week, then at the client's decision date. Always send what you promised — samples, data
sheets, a price — in the first follow-up.

## Samples and data sheets
Record which samples went to whom and for which project. Send the current data sheet and test
reports with every sample — specifiers need the slip, fire and wear figures in writing, straight
from the manufacturer's documents, never from memory.

## Stock and lead times
Before promising a delivery date, check the distributor's current stock-on-hand and incoming
stock; quote the lead time for anything not on hand.

## Travel and expenses (SARS)
To claim business travel against a travel allowance, SARS requires a logbook of business trips
(date, destination, purpose, kilometres). Keep every fuel slip and toll receipt, and submit
expense claims with the slips attached, monthly.
"""),
    ],
}

SECTOR_DOCUMENTS["other"] = []


def sector_collection_for(tenant_id: str) -> str | None:
    """The sector pack tenant id for this tenant's business type, or None (no pack / unknown)."""
    try:
        from vula.api.tenants import tenant_profile
        bt = (tenant_profile(tenant_id) or {}).get("business_type") or ""
    except Exception:
        return None
    return sector_tenant_id(bt) if SECTOR_DOCUMENTS.get(bt) else None
