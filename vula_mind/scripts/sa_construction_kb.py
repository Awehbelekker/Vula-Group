"""
scripts/sa_construction_kb.py — Comprehensive SA construction knowledge base.

Authoritative South African construction & architecture knowledge, ingested
into the shared training KB (vula_vula_training) so EVERY construction tenant
gets expert-level grounding. Written from public SA standards.

Covers: SACAP fee scale, PROCSA appointments, JBCC/NEC contracts, NHBRC,
SANS 10400 building regs, municipal plan submission, CIDB grades, BBBEE,
OHS construction regs, heritage/SAHRA, town planning/zoning, professional team.

Run:
    cd vula_mind
    MODEL_EMBED=text-embedding-3-small python -m scripts.sa_construction_kb
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

TENANT = "vula_training"

DOCS: dict[str, str] = {

"sacap_fee_scale.md": """# SACAP Architectural Fee Scale (South Africa)

The South African Council for the Architectural Profession (SACAP) publishes a
recommended fee guideline. Fees are a percentage of the total construction cost,
split across the standard work stages.

## Standard work stages and fee split
1. **Stage 1 — Inception** (~5% of total fee): establish client requirements, site
   appraisal, advise on consultants needed.
2. **Stage 2 — Concept & Viability** (~15%): concept design, spatial planning,
   preliminary cost estimate.
3. **Stage 3 — Design Development** (~20%): develop the approved concept, coordinate
   engineers, materials and finishes, refine costs.
4. **Stage 4 — Documentation & Procurement** (~25-27%): working drawings, specifications,
   municipal submission, tender documents, evaluate tenders, recommend contractor.
5. **Stage 5 — Construction** (~33-35%): contract administration, site inspections,
   payment certificates, instructions, practical completion.
6. **Stage 6 — Close-out** (~2-3%): final account, defects, as-built records.

## Typical total fee (percentage of construction cost)
- Smaller / simpler projects: higher percentage (10-15%).
- Larger / repetitive projects: lower percentage (6-9%).
- New residential homes: roughly 8-12%.
- Alterations & additions: higher (often +50% loading) because they're more complex per rand.
- Commercial / institutional: roughly 6-10%.

A fee is calculated on the agreed construction cost and adjusted to the final
construction cost at completion. Always confirm the current SACAP guideline and
that fees are a guideline, not a fixed tariff (competition law).
""",

"procsa_appointment.md": """# PROCSA Professional Appointment (South Africa)

PROCSA (Professional Consultants Services Agreement Committee) publishes the standard
client–consultant agreements used across the SA built environment. A PROCSA agreement
defines the appointment of architects, engineers, quantity surveyors, etc.

Key contents:
- Scope of services per stage (aligned to SACAP / SACPCMP / ECSA / SACQSP stages).
- Fee basis (percentage, time-based, or lump sum) and payment terms.
- Appointment of the principal agent.
- Professional indemnity insurance requirements.
- Intellectual property — drawings remain the consultant's IP, licensed to the client
  on full payment.
- Termination and dispute resolution.

The principal agent (often the architect on building projects) administers the building
contract between client and contractor.
""",

"jbcc_nec_contracts.md": """# Building Contracts in South Africa: JBCC and NEC

## JBCC (Joint Building Contracts Committee)
The most widely used building contract suite in SA. Main forms:
- **JBCC Principal Building Agreement (PBA)** — main contract between employer and contractor.
- **JBCC Nominated/Selected Subcontract Agreement** — for subcontractors.
- **JBCC Minor Works Agreement** — smaller, simpler projects.

JBCC key features: monthly payment certificates, retention (typically 5% to 10%, half
released at practical completion, balance at final completion), penalties for late
completion, defects liability period, variations via contract instructions, recovery
of delay through extension-of-time claims.

## NEC (New Engineering Contract)
Used on larger infrastructure and engineering projects, and increasingly by public
clients. Emphasises proactive management, early warnings, and a programme-driven approach.
Options A–F cover priced, target-cost, cost-reimbursable and management contracts.

## GCC
The General Conditions of Contract for Construction Works — common on civil/government
engineering projects (SANRAL, municipalities).
""",

"nhbrc.md": """# NHBRC — National Home Builders Registration Council

The NHBRC protects housing consumers. For new residential construction:
- The **home builder must be registered** with the NHBRC.
- Each new home must be **enrolled** with the NHBRC before construction starts.
- Enrolment funds a **5-year structural defects warranty** for the homeowner.
- The NHBRC inspects at key stages (foundations, etc.).

Enrolment fees scale with the construction value of the home. Banks generally require
NHBRC enrolment before releasing a building loan for a new home. Major structural
defects are covered for 5 years; roof leaks ~12 months; other defects ~3 months.
""",

"sans_10400.md": """# SANS 10400 — National Building Regulations (South Africa)

SANS 10400 is the set of standards giving deemed-to-satisfy rules for the National
Building Regulations (NBR). Local authorities require compliance for plan approval.

Key parts (by letter):
- Part A: General principles and requirements
- Part B: Structural design
- Part C: Dimensions
- Part D: Public safety
- Part F: Site operations
- Part J: Floors
- Part K: Walls
- Part L: Roofs
- Part M: Stairways
- Part N: Glazing
- Part O: Lighting and ventilation
- Part P: Drainage
- Part Q: Non-water-borne means of sanitary disposal
- Part R: Stormwater disposal
- Part S: Facilities for persons with disabilities
- Part T: Fire protection
- **Part XA: Energy usage in buildings** (energy efficiency — orientation, insulation,
  hot water, fenestration). Compliance is mandatory for new buildings.

Building plans must be drawn by a competent person and submitted to the local authority
for approval before construction.
""",

"municipal_submission.md": """# Municipal Building Plan Submission (e.g. City of Cape Town)

To build or alter a structure, approved building plans are required from the local
authority. General process (City of Cape Town and most metros):

1. **Pre-application** — confirm zoning, building lines, height, coverage, heritage status.
2. **Prepare plans** — site plan, floor plans, elevations, sections, drainage, SANS 10400
   compliance, energy (Part XA), drawn by a competent person.
3. **Submit** — via the municipality's building development management (often online, e.g.
   CoCT's e-services). Pay plan scrutiny fees (scale with building size/value).
4. **Circulation & scrutiny** — building control, town planning, heritage (if applicable),
   fire, health, services all comment.
5. **Approval** — once all departments approve, plans are stamped. Statutory turnaround is
   ~30 days (<500 m²) or ~60 days (≥500 m²), but practical timing varies.
6. **Build** — appoint NHBRC-registered builder (for homes), notify start, allow inspections.
7. **Occupation Certificate** — required before legal occupation; issued after final inspection.

Departures (relaxing building lines, coverage, etc.) require a separate land-use application
under the Municipal Planning By-law and public participation.
""",

"cidb_grades.md": """# CIDB Contractor Grades (South Africa)

The Construction Industry Development Board (CIDB) grades contractors 1–9 by financial
and works capability. The grade caps the value of public-sector work a contractor may
tender for:
- Grade 1: up to ~R200k
- Grade 2: up to ~R650k
- Grade 3: up to ~R2m
- Grade 4: up to ~R4m
- Grade 5: up to ~R6.5m
- Grade 6: up to ~R13m
- Grade 7: up to ~R40m
- Grade 8: up to ~R130m
- Grade 9: no upper limit

Each grade is also tied to a class of works (e.g. GB = general building, CE = civil
engineering). Public tenders specify a minimum grade and class. Registration is via the
CIDB; values are reviewed periodically.
""",

"bbbee_construction.md": """# B-BBEE in Construction (South Africa)

Broad-Based Black Economic Empowerment matters for tendering, especially public work.
- Contractors and consultants hold a **B-BBEE status level (1–8)** based on a verified
  scorecard (ownership, management, skills development, enterprise & supplier development,
  socio-economic development).
- Public tenders award **preference points** for B-BBEE under the Preferential Procurement
  Policy Framework Act (PPPFA) — typically an 80/20 or 90/10 split between price and
  preference depending on tender value.
- Level 1 gives maximum recognition. EMEs (small enterprises) and QSEs get simplified
  scorecards; EMEs are often automatically Level 1 or 2.

A valid B-BBEE certificate or sworn affidavit (for EMEs/QSEs) is usually required with
tenders.
""",

"ohs_construction.md": """# Occupational Health & Safety — Construction Regulations (South Africa)

Construction work is governed by the OHS Act (Act 85 of 1993) and the Construction
Regulations 2014.
- The **client** must prepare a health & safety specification and appoint a competent
  principal contractor.
- For notifiable work, submit a **construction work permit / notification** to the
  Department of Employment and Labour.
- The principal contractor must have a **health & safety plan**, appoint a construction
  H&S officer/manager (SACPCMP registered for larger projects), do risk assessments,
  and keep a H&S file.
- Requirements cover fall protection, excavations, scaffolding, plant, PPE, and welfare.

Non-compliance can stop a site and carries personal liability.
""",

"heritage_sahra.md": """# Heritage Approvals (SAHRA / Provincial Heritage)

Under the National Heritage Resources Act (Act 25 of 1999):
- Any structure **older than 60 years** may not be altered or demolished without a permit
  from the relevant heritage authority (SAHRA nationally, or a provincial body like
  Heritage Western Cape).
- Developments above certain size thresholds, or in heritage areas, may require a
  **Heritage Impact Assessment (HIA)**.
- Graves, archaeological sites, and protected areas have specific protections.

For Cape Town, Heritage Western Cape (HWC) is the provincial authority. Heritage comment
is part of the building plan circulation where applicable, and can add time — flag heritage
status early.
""",

"professional_team.md": """# The SA Built-Environment Professional Team

A typical building project team and their councils:
- **Architect** — design and (often) principal agent. Registered with **SACAP**.
- **Quantity Surveyor (QS)** — cost management, BOQs, payment valuations. Registered with
  **SACQSP**. Measures per the **ASAQS** Standard System of Measurement.
- **Structural / Civil Engineer** — structure, foundations. Registered with **ECSA**.
- **Electrical / Mechanical / Wet Services Engineers** — services. ECSA.
- **Construction Project / H&S Manager** — registered with **SACPCMP**.
- **Land Surveyor** — site survey, pegging. **PLATO**.
- **Town Planner** — land use, rezoning, departures. **SACPLAN**.

The QS prepares the Bill of Quantities (BOQ): a measured list of all work and materials
used by contractors to price a tender on a like-for-like basis. Common BOQ terms:
PC sum (prime cost), provisional sum, preliminaries (contractor's site overheads, ~8-15%),
contingency (client's allowance for the unknown, ~5-10%), escalation (inflation to
construction midpoint).
""",
}


async def main():
    from vula.ingestion.pipeline import VulaIngestionPipeline
    pipeline = VulaIngestionPipeline(tenant_id=TENANT)
    total = 0
    for filename, content in DOCS.items():
        doc_id = "sakb_" + filename.replace(".md", "")
        try:
            r = await pipeline.ingest_text(content=content, filename=filename, doc_id=doc_id)
            print(f"  {filename}: {r.chunks_stored} chunks")
            total += r.chunks_stored
        except Exception as exc:
            print(f"  {filename}: FAILED — {exc}")
    print(f"\nSA construction KB ingested: {total} chunks into '{TENANT}'")


if __name__ == "__main__":
    asyncio.run(main())
