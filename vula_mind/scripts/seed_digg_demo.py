"""
scripts/seed_digg_demo.py

Seeds DIGG (Judy Downing's architecture practice) as a demo tenant.

What this does:
  1. Creates the DIGG tenant in Supabase (or updates it if it exists)
  2. Ingests realistic DIGG business documents into Qdrant
  3. Creates a sample project with contractors and tasks (field ops demo)
  4. Prints a demo script for Richard to use in client meetings

Run:
    cd vula_mind
    python -m scripts.seed_digg_demo

Requires:
    - vula_mind/.env with SUPABASE_URL + SUPABASE_SERVICE_KEY
    - Qdrant running on localhost:6333
    - Ollama running on localhost:11434 with bge-m3
"""
from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path

# Add vula_mind root to path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings

DIGG_TENANT_ID = "digg-demo"

# ─── Synthetic DIGG documents ─────────────────────────────────────────────────
# These are realistic DIGG architecture firm docs that Vula would be trained on.

DIGG_DOCUMENTS = [
    {
        "filename": "digg_fee_schedule_2025.txt",
        "content": textwrap.dedent("""
            DIGG ARCHITECTURE — FEE SCHEDULE 2025/2026
            Registered with SACAP | Practice number PAT44740093

            STAGE 1: INCEPTION & FEASIBILITY
            - Site analysis, zoning, feasibility study
            - Fee: 2.5% of estimated construction cost
            - Timeline: 2-3 weeks

            STAGE 2: CONCEPT DESIGN
            - Sketch designs, spatial layouts, concept report
            - Fee: 5% of estimated construction cost
            - Timeline: 3-4 weeks

            STAGE 3: DESIGN DEVELOPMENT
            - Developed drawings, materials selection, preliminary BOQ
            - Fee: 7.5% of estimated construction cost
            - Timeline: 4-6 weeks

            STAGE 4: DOCUMENTATION & PROCUREMENT
            - Working drawings, specifications, tender documents
            - NHBRC enrolment where applicable
            - Fee: 12.5% of estimated construction cost
            - Timeline: 6-10 weeks

            STAGE 5: CONSTRUCTION ADMINISTRATION
            - Site visits (fortnightly), progress reports, payment certificates
            - Practical completion sign-off
            - Fee: 7.5% of estimated construction cost
            - Timeline: Duration of construction

            TOTAL FULL SERVICE: 35% of estimated construction cost

            ADDITIONAL SERVICES (billed separately):
            - NHBRC enrolment: R3,500 (residential) / R8,500 (commercial)
            - Electrical/mechanical drawings: from R15,000
            - Landscape design: from R12,000 per 100sqm
            - Interior design: from R850/sqm
            - 3D visualisation: from R6,500 per view

            DISBURSEMENTS: Cost + 15% admin fee
            Municipality fees, specialist consultant fees charged at cost.

            PAYMENT TERMS: 30 days from invoice
            CONTACT: judy@digg.co.za | +27 21 000 0000
        """).strip(),
    },
    {
        "filename": "digg_standard_contract.txt",
        "content": textwrap.dedent("""
            DIGG ARCHITECTURE — STANDARD CLIENT AGREEMENT
            Based on PROCSA Form of Agreement

            PARTIES:
            DIGG Architecture (Pty) Ltd ("Architect")
            Client name: [To be completed]
            Project: [To be completed]

            SCOPE OF SERVICES:
            The Architect will provide professional architectural services in accordance
            with the SACAP scope of work for the stages agreed and initialled below.

            FEES:
            As per the current DIGG Fee Schedule (attached). Fees are based on
            estimated construction cost at time of appointment and adjusted to
            final construction cost at practical completion.

            OWNERSHIP OF DOCUMENTS:
            All drawings, models, and documents produced by DIGG remain the intellectual
            property of DIGG Architecture (Pty) Ltd. A licence to use these documents
            for the specified project is granted upon full payment of fees.

            INDEMNITY:
            DIGG Architecture holds Professional Indemnity Insurance. Our liability is
            limited to our professional fees on this project.

            CANCELLATION:
            14 days written notice required. Fees for completed stages are due in full.

            CONSTRUCTION ADMINISTRATION:
            Site visits fortnightly. Emergency visits billed at R3,500 per visit.
            Payment certificates issued monthly. Contractor disputes mediated per JBCC.

            SUSTAINABILITY:
            DIGG promotes green building principles. All projects consider passive
            solar design, natural ventilation, and SANS 10400-XA compliance.

            SACAP REGISTRATION: PAT44740093
        """).strip(),
    },
    {
        "filename": "digg_project_portfolio.txt",
        "content": textwrap.dedent("""
            DIGG ARCHITECTURE — PROJECT PORTFOLIO (Selected Projects)

            RESIDENTIAL
            1. Bloubergstrand Private Residence
               - 380sqm, 4 bed/3 bath, double storey
               - Construction cost: R4.2M
               - Completed: March 2024
               - Highlights: Passive solar design, indigenous landscaping, NHBRC certified

            2. Melkbosstrand Beach House Renovation
               - 220sqm existing + 85sqm addition
               - Construction cost: R1.8M
               - Completed: August 2024
               - Highlights: Sea views optimised, concrete finishes, deck extension

            3. Parklands Cluster Development (6 units)
               - 145sqm per unit, sectional title
               - Construction cost: R11.4M total
               - In progress: Expected completion Q3 2025
               - Highlights: Shared amenities, green roofs, fibre-ready

            COMMERCIAL
            4. Table View Commercial Office Fitout
               - 500sqm, open plan with 8 offices
               - Construction cost: R3.5M
               - Completed: January 2025
               - Highlights: Biophilic design, standing desks, solar backup

            5. Century City Retail Conversion
               - 1,200sqm, A-grade to retail
               - Construction cost: R8.2M
               - In progress: Expected completion Q4 2025
               - Highlights: Double-volume atrium, full MEP redesign

            TYPICAL CLIENT QUESTIONS WE ANSWER:
            - What does an architect cost in Cape Town? See our fee schedule.
            - How long does plan approval take? Typically 6-12 weeks for residential.
            - Do you need NHBRC enrollment? Yes for all new residential construction.
            - Can you do turnkey projects? Yes — we manage contractors directly.
            - What is SACAP? South African Council for the Architectural Profession.
        """).strip(),
    },
    {
        "filename": "digg_construction_process.txt",
        "content": textwrap.dedent("""
            DIGG ARCHITECTURE — CLIENT GUIDE: THE CONSTRUCTION PROCESS

            STEP 1: APPOINTMENT & INCEPTION (Weeks 1-2)
            Sign DIGG client agreement. Pay stage 1 deposit (50% of stage fee).
            Provide site information: title deed, survey diagram, Loadshedding schedule.

            STEP 2: MUNICIPALITY SUBMISSION (Weeks 8-20)
            We submit to City of Cape Town (CTMM) or local municipality.
            Required documents: Site plan, floor plans, elevations, drainage plan.
            Municipal fees: approx R15,000-R45,000 depending on size and location.

            STEP 3: CONTRACTOR TENDER (Weeks 21-26)
            We prepare tender documents and invite 3 contractors to price.
            We evaluate tenders, check NHBRC registration, and recommend.
            You sign JBCC contract with selected contractor.

            STEP 4: CONSTRUCTION BEGINS
            Contractor takes possession of site. Duration depends on scope:
            - Small residential renovation: 8-16 weeks
            - New residential build (< 400sqm): 20-32 weeks
            - Commercial fitout: 12-20 weeks

            FORTNIGHTLY SITE MEETINGS:
            DIGG attends fortnightly with you and the contractor.
            We check progress, quality, and issue payment certificates.

            STEP 5: PRACTICAL COMPLETION
            Final snag list issued. All defects corrected before handover.
            Occupation certificate issued by municipality.
            NHBRC 5-year structural warranty begins.

            WHAT CAN GO WRONG (and how we handle it):
            - Contractor delays: We enforce contract penalties (JBCC clause 22)
            - Cost overruns: Change orders require written approval
            - Material defects: Supplier liability, NHBRC warranty coverage
            - Scope changes: Amendment to scope of works, revised fees
        """).strip(),
    },
    {
        "filename": "digg_cape_town_rates_2025.txt",
        "content": textwrap.dedent("""
            DIGG ARCHITECTURE — CAPE TOWN CONSTRUCTION RATES 2025/2026
            Source: AECOM 2025/26, local tender data, DIGG project history

            RESIDENTIAL CONSTRUCTION RATES (per m²):
            Budget (basic): R8,500 - R11,000/m²
            Standard (mid-range): R12,000 - R16,500/m²
            Premium: R17,000 - R24,000/m²
            Ultra-high-end: R25,000+/m²

            TYPICAL BREAKDOWN (standard residential, 300sqm):
            - Foundations & slab: 8-12% of total
            - Superstructure (walls, roof): 25-30%
            - Wet works (plumbing, drainage): 8-10%
            - Electrical: 8-10%
            - Joinery (windows, doors, built-ins): 10-15%
            - Finishes (tiles, paint, flooring): 12-18%
            - External works: 5-8%
            - Preliminaries & profit: 10-15%

            CAPE TOWN MUNICIPALITY FEES (approximate):
            - Plan approval (residential < 500m²): R18,000 - R35,000
            - Occupation certificate: R4,500 - R8,000
            - Connection fees (new services): R25,000 - R60,000

            NHBRC ENROLMENT FEES 2025:
            - R0 - R500,000 construction value: R3,500
            - R500,001 - R1.5M: R5,800
            - R1.5M - R5M: R9,200
            - R5M - R10M: R14,500
            - Over R10M: 0.15% of construction value

            NOTE: All rates exclude VAT. Always get 3 contractor quotes.
            These rates are indicative — actual prices vary by location,
            access, ground conditions, and current steel/cement prices.
        """).strip(),
    },
]


# ─── Sample project data for field ops demo ───────────────────────────────────

JUDY_WHATSAPP = "+27827077080"  # 082 7077080

DEMO_PROJECT = {
    "project_id": "digg-blouberg-001",
    "name": "Bloubergstrand Residence — New Build",
    "team": [
        {"name": "Judy Downing", "phone": JUDY_WHATSAPP, "trade": "architect", "role": "architect"},
        {"name": "Site Manager Bob", "phone": "+27821000002", "trade": "site_manager", "role": "site_manager"},
        {"name": "Pieter (Bricklayer)", "phone": "+27821000003", "trade": "bricklayer", "role": "contractor"},
        {"name": "Sipho (Plumber)", "phone": "+27821000004", "trade": "plumber", "role": "contractor"},
        {"name": "Nomsa (Electrician)", "phone": "+27821000005", "trade": "electrician", "role": "contractor"},
    ],
    "tasks": [
        {"title": "Pour foundation slab", "trade": "concrete", "assigned_to": "Pieter (Bricklayer)", "status": "complete"},
        {"title": "Brickwork — ground floor walls", "trade": "bricklayer", "assigned_to": "Pieter (Bricklayer)", "status": "in_progress"},
        {"title": "Install stormwater drainage", "trade": "plumber", "assigned_to": "Sipho (Plumber)", "status": "pending"},
        {"title": "Rough electrical conduit — ground floor", "trade": "electrician", "assigned_to": "Nomsa (Electrician)", "status": "pending"},
    ],
}

# ─── DEMO QUESTIONS (print at end) ────────────────────────────────────────────

DEMO_QUESTIONS = [
    "What does DIGG charge for a full architectural service?",
    "How long does plan approval take in Cape Town?",
    "What is the construction cost per m² for a premium Cape Town home?",
    "Draft a fee proposal for a 450sqm commercial office fitout",
    "What NHBRC fees apply to a R3.5M residential project?",
    "Explain the JBCC contract to me in simple terms",
    "What happens when a contractor is late on a DIGG project?",
]


# ─── Main seed function ───────────────────────────────────────────────────────

async def seed_digg():
    print("\n" + "═" * 60)
    print("  DIGG Demo Seed — Vula Group")
    print("═" * 60)

    # 1. Ingest documents
    print("\n[1/3] Ingesting DIGG documents into Qdrant...")
    from vula.ingestion.pipeline import VulaIngestionPipeline

    pipeline = VulaIngestionPipeline(tenant_id=DIGG_TENANT_ID)
    ingested = 0
    for doc in DIGG_DOCUMENTS:
        tmp = Path(settings.upload_dir) / DIGG_TENANT_ID
        tmp.mkdir(parents=True, exist_ok=True)
        file_path = tmp / doc["filename"]
        file_path.write_text(doc["content"], encoding="utf-8")
        try:
            result = await pipeline.ingest_file(file_path)
            print(f"  ✓ {doc['filename']} → {result.chunks_stored} chunks ({result.status})")
            ingested += 1
        except Exception as exc:
            print(f"  ✗ {doc['filename']} — {exc}")

    print(f"\n  {ingested}/{len(DIGG_DOCUMENTS)} documents ingested")

    # 2. Seed field ops demo project
    print("\n[2/3] Creating demo project and contractor team...")
    from vula.models.field_ops import get_field_ops_db

    db = get_field_ops_db()
    contractor_map = {}

    for member in DEMO_PROJECT["team"]:
        c = db.upsert_contractor(DIGG_TENANT_ID, member["name"], member["phone"], member["trade"])
        db.assign_to_project(DIGG_TENANT_ID, DEMO_PROJECT["project_id"], c.id, member["role"])
        contractor_map[member["name"]] = c
        print(f"  ✓ {member['name']} ({member['trade']}) — {member['role']}")

    for t in DEMO_PROJECT["tasks"]:
        assigned_contractor = contractor_map.get(t["assigned_to"])
        task = db.create_task(
            DIGG_TENANT_ID,
            DEMO_PROJECT["project_id"],
            t["title"],
            t["trade"],
            assigned_to=assigned_contractor.id if assigned_contractor else "",
            due_date="2026-06-30",
        )
        if t["status"] != "pending":
            db.update_task_status(task.id, t["status"])
        print(f"  ✓ Task: {t['title']} [{t['status']}]")

    # 3. Print demo summary
    print("\n[3/3] Demo is ready!\n")
    print("─" * 60)
    print("TENANT ID:  digg-demo")
    print("API:        http://localhost:7438")
    print("PROJECT:    " + DEMO_PROJECT["project_id"])
    print("─" * 60)
    print("\nTEST THESE QUESTIONS IN THE DEMO:")
    for i, q in enumerate(DEMO_QUESTIONS, 1):
        print(f"  {i}. {q}")
    print("\nFIELD OPS WhatsApp TEST FLOW:")
    print("  1. Assign a task via POST /v1/field/task/assign")
    print("  2. Contractor replies DONE → task moves to awaiting_sign_off")
    print("  3. Contractor sends photo → evidence stored, manager notified")
    print("  4. Architect replies APPROVE → task marked complete")
    print(f"\n  Architect phone: {JUDY_WHATSAPP} (Judy)")
    print("  Site manager:    +27821000002")
    print("  Contractor:      +27821000003 (Pieter), +27821000004 (Sipho)")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(seed_digg())
