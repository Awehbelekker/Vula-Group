"""
vula/training/content.py

Shared SA construction and QS knowledge base.
Ingested into tenant_id="vula_training" so the WhatsApp AI and portal
can answer general construction questions even when a client's own KB
has no relevant documents.

Covers: QS/BOQ terms, CIDB grades, SA contracts, materials, drawing
reading, measurement rules, construction process, and pricing methodology.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class TrainingDocument:
    filename: str
    topic: str
    content: str


TRAINING_DOCUMENTS: List[TrainingDocument] = [

    # ── QS and BOQ terminology ────────────────────────────────────────────────
    TrainingDocument(
        filename="qs_terms.md",
        topic="QS and BOQ terminology",
        content="""# Quantity Surveying and BOQ Terminology — South Africa

## What is a Bill of Quantities (BOQ)?
A Bill of Quantities (BOQ) is a document that lists all the materials, labour, and other costs required to complete a construction project. It is prepared by a Quantity Surveyor (QS) and is used for pricing by contractors during the tender process. In South Africa, BOQs are measured in accordance with the ASAQS (Association of South African Quantity Surveyors) Standard System of Measurement (SSM).

## Prime Cost (PC) Sum
A Prime Cost Sum (PC Sum) is a provisional allowance included in a BOQ or contract for work or materials whose exact cost cannot be determined at tender stage. Examples include fittings, fixtures, or specialist subcontract work. The contractor adds a margin (profit and attendance) on top of the PC Sum. PC Sums are adjusted to actual cost during final account.

## Provisional Sum
A Provisional Sum is an allowance in the contract documents for work that cannot be fully defined at tender stage. Unlike PC Sums, provisional sums cover work where the full scope is unknown. They may or may not be spent, and the contract is adjusted accordingly. The JBCC and NEC contracts handle provisional sums slightly differently — always refer to the specific contract conditions.

## Preliminaries
Preliminaries (Prelims) are the contractor's general overhead costs for setting up and managing the project. They are not tied to specific work items but cover:
- Site establishment (site office, fencing, storage, temporary services)
- Construction plant and equipment
- Health and safety requirements
- Project management and supervision
- Insurance and bonds
- Cleaning up and clearing the site at completion
In South Africa, prelims typically range from 8% to 15% of the project value depending on site conditions and project duration.

## Contingency
A contingency allowance is added to a construction budget to cover unforeseen events and risks. Typical contingencies:
- Design development contingency: 5–10% during early design stages
- Construction contingency: 3–5% for well-defined projects
- Risk contingency: additional for projects with high uncertainty (remote sites, complex services, heritage buildings)
The contingency belongs to the client, not the contractor, and unused portions revert to the client.

## Escalation
Construction costs in South Africa escalate due to inflation in materials and labour. Escalation is calculated from the tender date to the anticipated midpoint of construction. AECOM publishes quarterly construction cost indices for South Africa. For multi-year contracts, a formula-based escalation clause (typically based on Statistics SA PPI indices) is used.

## Profit and Overhead
Contractors add profit and overhead to their net construction costs:
- Site overhead: site-specific costs not included in prelims
- Head office overhead: typically 5–8% of project turnover
- Profit: typically 5–10% depending on market conditions
- Total margin on cost: typically 12–18% in competitive SA markets

## Retention
Retention is a percentage of each payment certificate withheld by the client as security against defects. Standard retention in SA:
- 10% retention until practical completion
- Reduces to 5% at practical completion
- Balance released after the defects liability period (typically 12 months)
Maximum retention cap is usually 5% of the contract sum.

## Daywork
Daywork is a method of paying for work on a time and materials basis when it cannot be measured and valued using BOQ rates. It is used for:
- Minor variations
- Work in restricted or inaccessible areas
- Emergency repairs
Daywork schedules list labour rates, plant rates, and a percentage addition for materials.

## Defects Liability Period (DLP)
The DLP is the period after practical completion during which the contractor must return to fix defects at their own cost. Standard DLP in SA: 12 months from practical completion. The final retention is released at the end of the DLP once all defects are rectified.

## Practical Completion
Practical Completion (PC) is the stage where the works are substantially complete and fit for occupation, even though minor snag items remain. At PC:
- Half the retention is released
- The defects liability period begins
- Risk of the building transfers to the client (insurance changes)
- The maintenance period begins

## Variation Order (VO)
A Variation Order (VO) is an instruction to the contractor to change the scope of work from what was originally contracted. VOs can add or omit work. They must be:
- Issued in writing by the principal agent
- Valued at contract rates where applicable, or negotiated daywork rates
- Included in the final account
Contractors are entitled to an extension of time for VOs that delay the works.

## Extension of Time (EOT)
An Extension of Time (EOT) extends the contract completion date due to delays caused by events beyond the contractor's control, such as:
- Client-issued variations
- Force majeure events (floods, strikes)
- Late delivery of client-supplied materials
- Delays by nominated subcontractors
EOTs prevent the contractor from being penalised for delays they did not cause.

## Nominated Subcontractor
A Nominated Subcontractor (NSC) is a specialist subcontractor selected and nominated by the client or principal agent. The main contractor is required to use them, but the NSC's contract is usually between the NSC and main contractor. The main contractor is responsible for the NSC's work. Examples: lifts, air conditioning, specialist facades.

## Named Subcontractor
A Named Subcontractor is similar to a nominated subcontractor but is selected from a pre-approved list. The main contractor has more control over the selection.

## Sectional Completion
Sectional Completion allows different parts of a building to reach practical completion at different times. Common on phased developments or large retail projects. Retention and DLP apply separately to each section.

## Measured Works
Measured works are the bulk of a BOQ — the actual construction items measured in units (m², m³, m, nr, kg, etc.) and priced by the contractor. Measurement rules are specified in the ASAQS SSM.

## Schedule of Rates
A Schedule of Rates is used when the exact quantities cannot be defined upfront. The contractor prices a rate per unit, and the actual quantity is measured upon completion. Common for infrastructure, earthworks, and maintenance contracts.
""",
    ),

    # ── CIDB grades and contractor registration ───────────────────────────────
    TrainingDocument(
        filename="cidb_grades.md",
        topic="CIDB contractor grades",
        content="""# CIDB Contractor Grades — South Africa

## What is the CIDB?
The Construction Industry Development Board (CIDB) is a statutory body established to promote, regulate, and develop the construction industry in South Africa. All contractors wishing to tender for government construction contracts must be registered with the CIDB.

## CIDB Grading Designation
A CIDB grading designation consists of a number (1–9) indicating financial capability and a letter indicating the class of construction work:
- **GB**: General Building
- **CE**: Civil Engineering
- **ME**: Mechanical Engineering
- **EL**: Electrical Engineering
- **SB**: Specialist Building (e.g. fire protection, HVAC)

Example: "6GB" = Grade 6 in General Building.

## CIDB Grade Thresholds (approximate 2024 values)
| Grade | Maximum Contract Value | Typical Tender Cap |
|-------|----------------------|-------------------|
| 1     | Up to R200,000       | Minor works       |
| 2     | Up to R650,000       | Small works       |
| 3     | Up to R2 million     | Small/medium      |
| 4     | Up to R6.5 million   | Medium works      |
| 5     | Up to R20 million    | Medium/large      |
| 6     | Up to R65 million    | Large works       |
| 7     | Up to R200 million   | Major works       |
| 8     | Up to R650 million   | Very large works  |
| 9     | No upper limit       | Mega projects     |

## Registration Requirements
Contractors must demonstrate:
- **Financial capability**: Based on annual turnover, assets, and banking facilities
- **Track record**: Completed projects of relevant size and type
- **Health and Safety**: Compliance with OHS Act
- **BEE/BBBEE**: Preference points awarded for transformation

## Targeted Procurement
Government contracts in South Africa use preferential procurement to advance BEE goals. Points are allocated:
- 90/10 system: 90 points for price, 10 for BEE for contracts over R50 million
- 80/20 system: 80 points for price, 20 for BEE for contracts R30k–R50 million

## CIDB Best Practice Register
Contractors convicted of corrupt practices are listed on the CIDB Register of Tender Defaulters and may be banned from government work.

## Contractor Development Programme
The CIDB runs programmes to help Grade 1–4 contractors improve their grading, access mentorship, and grow their businesses.
""",
    ),

    # ── South African construction contracts ─────────────────────────────────
    TrainingDocument(
        filename="sa_contracts.md",
        topic="South African construction contracts",
        content="""# South African Construction Contracts

## JBCC (Joint Building Contracts Committee)
The JBCC is the most widely used standard building contract in South Africa for private sector building work.

### JBCC Series 2000 documents:
- **JBCC Principal Building Agreement (PBA)**: Main contract between client and main contractor
- **JBCC Nominated/Selected Subcontract Agreement**: Between main contractor and nominated/selected subcontractors
- **JBCC Minor Works Agreement**: For smaller projects (typically under R5 million)

### Key JBCC provisions:
- **Principal Agent**: The professional (usually architect) who administers the contract on behalf of the client
- **Payment certificates**: Issued monthly based on work done; must be honoured within the payment period
- **Practical Completion**: Defined as "completion of the works in accordance with the contract, subject only to minor defects"
- **Penalties**: Late completion penalties (liquidated damages) specified in the contract data
- **Dispute resolution**: Adjudication is the primary dispute resolution mechanism; arbitration for unresolved disputes

## NEC (New Engineering Contract)
The NEC4 Engineering and Construction Contract is increasingly used in South Africa, particularly for public sector infrastructure. Promoted by the CIDB as a preferred contract for government projects.

### NEC main options:
- **Option A**: Priced contract with activity schedule (lump sum)
- **Option B**: Priced contract with bill of quantities (re-measurable)
- **Option C**: Target contract with activity schedule (pain/gain share)
- **Option D**: Target contract with bill of quantities
- **Option E**: Cost reimbursable contract
- **Option F**: Management contract

### NEC key features:
- Early Warning system: contractor must notify of potential problems early
- Programme: detailed programme required from day 1
- Compensation Events: the NEC equivalent of variations/EOTs
- Z-clauses: project-specific additional conditions

## GCC (General Conditions of Contract for Construction Works)
Published by SAICE (South African Institution of Civil Engineering), used primarily for civil engineering contracts:
- Infrastructure projects (roads, water, sewers)
- Municipal and provincial government work
- Compatible with CIDB procurement requirements

## FIDIC (International Federation of Consulting Engineers)
Used for large international projects and development bank-funded contracts in SA:
- **Red Book**: Construction — for building and civil engineering
- **Yellow Book**: Plant and Design-Build
- **Silver Book**: EPC/Turnkey
- **Gold Book**: Design, Build, Operate

## The Construction Industry Development Board (CIDB) Standard for Uniformity
The CIDB Standard for Uniformity establishes procurement requirements for government contracts, including:
- Compilation of procurement documents
- Evaluation criteria
- Reporting requirements
- Integration with CIDB registration requirements

## Health and Safety (OHS Act)
The Occupational Health and Safety Act 85 of 1993 and the Construction Regulations 2014 govern safety on SA construction sites:
- **Health and Safety Plan**: Required before work starts
- **Safety Officer**: Full-time safety officer required on large sites
- **Fall protection plan**: Required for work above 2 metres
- **Penalties**: Criminal prosecution for serious OHS violations
""",
    ),

    # ── Reading architectural drawings ────────────────────────────────────────
    TrainingDocument(
        filename="drawing_reading.md",
        topic="Reading architectural drawings",
        content="""# Reading Architectural and Construction Drawings

## Types of Drawings

### Site Plan
Shows the building on its plot of land. Includes:
- North point
- Plot boundaries and servitudes
- Building footprint
- Setbacks from boundaries
- Services connections (sewer, water, electricity)
- Access and parking
- Contour lines (levels)

### Floor Plan
A horizontal cut through the building at approximately 1.2m above floor level, looking down. Shows:
- Room layout and dimensions
- Wall thicknesses
- Door positions and swing directions
- Window positions
- Sanitary fittings in wet areas
- Staircase layout
- Structural elements (columns, beams)

### Section
A vertical cut through the building, showing internal heights and construction details:
- Floor-to-ceiling heights (FFL = Finished Floor Level)
- Roof pitch and eaves detail
- Foundation depths
- Structural elements (beams, lintels)
- Ground levels

### Elevation
The external face of the building as seen straight on:
- North, South, East, West elevations
- Window and door positions
- External finishes
- Roof pitch and gutter lines
- Height dimensions

### Detail Drawing
Large-scale drawings showing specific construction details:
- Window and door frames
- Roof edge details
- Wet area waterproofing
- Expansion joints
- Fixing details

## Drawing Scales
Drawings are drawn to scale — a stated ratio between drawing dimension and actual dimension:
- 1:100 — Floor plans and elevations for most buildings
- 1:50 — Sections and larger-scale plans
- 1:20 — Construction details, wet areas, kitchens
- 1:5 — Fine details (window frames, connections)
- 1:1 — Full-size details (reinforcement bar bending)

**Rule**: Always use a scale rule, never an ordinary ruler. Check the stated scale on every drawing.

## Drawing Conventions

### Line types:
- **Solid thick line**: Visible edges of walls and elements
- **Solid thin line**: Dimensions and reference lines
- **Dashed line**: Hidden elements below the cut (foundations, below-slab services)
- **Chain line (dash-dot)**: Centre lines, grid lines

### Hatching:
- **Brick hatching**: 45° diagonal lines = brickwork in section
- **Concrete hatching**: Triangle/shading = concrete in section
- **Earth hatching**: Irregular shading = earthwork/fill
- **Insulation**: Zigzag = insulation material

### Symbols:
- **North point**: Arrow indicating north — always check this first
- **Break lines**: Wavy or zigzag line = drawing continues but isn't shown
- **Cloud**: Revision marker — area of drawing that has changed

## Grid Lines
Structural drawings use a numbered/lettered grid system:
- Numbers (1, 2, 3...) for columns in one direction
- Letters (A, B, C...) for columns in the other direction
- Columns are identified as "C1/A" meaning grid line 1/A intersection
- Beams run between grid lines: "B1–2/A" = beam from column 1/A to 2/A

## Levels (Datum)
- **NGL**: Natural Ground Level — existing ground before construction
- **FFL**: Finished Floor Level — top of completed floor slab/screed
- **SSL**: Structural Slab Level — top of structural slab before screed
- **RL**: Reduced Level — level above mean sea level (used in civil engineering)
- Levels are shown as a triangle symbol with the dimension alongside

## Drawing Numbers
Drawings are numbered systematically:
- First letters indicate discipline: A (Architecture), S (Structural), M (Mechanical), E (Electrical), P (Plumbing)
- Numbers indicate drawing sequence: A001, A002, A003...
- Revision suffix: A001 Rev A, A001 Rev B (each revision letter indicates a change)

## Title Block
Every drawing has a title block (usually bottom right) showing:
- Project name and address
- Drawing title
- Drawing number and revision
- Scale
- Date
- Drawn by / checked by / approved by
- North point reference
""",
    ),

    # ── Construction materials ────────────────────────────────────────────────
    TrainingDocument(
        filename="materials.md",
        topic="Construction materials knowledge",
        content="""# Construction Materials — South Africa

## Concrete

### Concrete Grades (MPa = characteristic compressive strength at 28 days)
- **C10 (10 MPa)**: Blinding / lean concrete — non-structural (under footings, as a base)
- **C20 (20 MPa)**: Strip footings, lightly loaded slabs, mass concrete
- **C25 (25 MPa)**: Standard reinforced concrete slabs, beams, columns
- **C30 (30 MPa)**: High-traffic slabs, suspended slabs, columns with high load
- **C35 (35 MPa)**: Prestressed concrete, water-retaining structures
- **C40+ (40 MPa)**: High-performance structural elements

### Concrete mix components:
- **Cement**: CEM I (ordinary Portland), CEM II (blended), CEM III (fly ash blend)
- **Aggregate**: River sand (fine) + crushed stone 19mm or 13mm (coarse)
- **Water-cement ratio**: Lower = stronger and more durable
- **Admixtures**: Plasticisers (workability), retarders (hot weather), accelerators (cold weather)

### Reinforced Concrete (RC)
- **Y-bars (High Tensile)**: Y10, Y12, Y16, Y20, Y25, Y32 — most structural applications
- **R-bars (Mild Steel)**: R6, R8, R10 — stirrups, links, light reinforcement
- **Mesh**: BRC 193, BRC 283, etc. — slabs on grade, screeds

## Masonry

### Brick types in South Africa:
- **Face brick**: Exposed-face quality, no plaster required (FBS — face brick standard)
- **Plaster brick**: Rough surface, designed to be plastered over (NFX — no face standard)
- **Semi-face**: Can be used exposed or plastered
- Standard SA brick size: 222mm × 106mm × 73mm
- Mortar joint: typically 10mm, making laid course = 83mm (brick + mortar)

### Brick strengths:
- Class I: High strength, low water absorption (external work, damp conditions)
- Class II: Standard strength
- Class III: Low strength (internal non-load-bearing walls only)

### Concrete blocks:
- **Hollow blocks**: 390 × 190 × 190mm or 390 × 140 × 190mm — faster laying, lighter
- **Solid blocks**: Better acoustic and thermal performance
- Used for boundary walls, factory buildings, retaining walls

### Mortar:
- **1:6 mix** (1 part cement : 6 parts sand): Standard for brickwork
- **1:4 mix**: Stronger — below DPC, high-load applications
- **1:3 mix**: Very strong — below ground, high-sulfate soils

### DPC (Damp Proof Course):
- A physical barrier installed at the base of all external and internal walls to prevent rising damp
- Standard: 2 courses of class I bricks in 1:4 mortar or a proprietary DPC membrane
- Typically at 150mm above FFL

## Steel

### Structural steel sections:
- **I-sections (IPE/HEA/HEB)**: Beams and columns
- **SHS (Square Hollow Section)**: Columns, frames
- **RHS (Rectangular Hollow Section)**: Beams, purlins
- **CHS (Circular Hollow Section)**: Columns, handrails
- **Angles (L-sections)**: Bracing, cleats, lintels
- **Flat bars**: Stiffeners, gussets
- Steel grade: S355 (355 MPa yield strength) most common structural steel in SA

### Reinforcing steel:
- Manufactured to SANS 920 (deformed high-tensile) and SANS 1024 (mesh)
- Y-bars: Ribbed/deformed for bond — standard structural reinforcement
- Supplied in 13m lengths or cut to length

## Timber

### SA timber for construction:
- **Pine (SA pine / radiata pine)**: Framing, trusses, cladding, windows (pressure-treated for external)
- **Meranti**: Joinery, windows, doors, internal use
- **Saligna (Eucalyptus)**: Fencing, poles, flooring
- **Hardwoods (Balau, Ironwood, Teak)**: External decking, heavy-duty flooring

### Timber preservative treatment:
- H2: Above ground, protected (roof framing)
- H3: Above ground, exposed to weather (external cladding)
- H4: In ground or concrete contact (poles, posts)
- H5: In ground, high decay hazard

## Waterproofing

### Types of waterproofing used in SA:
- **Torch-on membranes**: Modified bitumen, applied with a gas torch — roofs, basements
- **APP/SBS**: Types of bitumen polymer — APP is more UV resistant (better for exposed roofs)
- **Liquid-applied membranes**: Polyurethane, acrylic — wet areas, planters, balconies
- **Cementitious coatings**: Crystalline technology — water tanks, basements
- **Sheet membranes**: HDPE, PVC — below-slab waterproofing, green roofs

### Wet area waterproofing:
- SA standard: All shower floors and walls (min 1.8m high), bath surrounds, and balconies require waterproofing before tiling
- Must cure fully before tiling (typically 24–48 hours)
- Screed falls must be a minimum of 1:80 to drain points

## Insulation

### Thermal insulation:
- **Glasswool**: Roof insulation batts — 135mm or 100mm for ceiling insulation
- **Rigid EPS (polystyrene)**: Under slabs, cavity walls
- **PIR board**: High-performance rigid insulation for cool rooms, facades
- SANS 10400-XA requires minimum insulation values based on climate zone

### Acoustic insulation:
- **Glasswool between studs**: Party walls in multi-unit residential
- **Mass loaded vinyl**: High-performance acoustic barrier
- **Rockwool (stone wool)**: Both thermal and acoustic, also fire-resistant

## Tiling

### Tile types:
- **Ceramic**: Interior floors and walls, not frost-resistant
- **Porcelain**: Dense, low water absorption, suitable for exterior and high-traffic areas
- **Slate/Natural stone**: Feature areas, requires sealing
- **Quarry tiles**: Unglazed clay tiles, kitchens, commercial floor applications

### Tile sizes common in SA:
- 600×600mm: Standard floor tile
- 300×600mm: Wall tiles
- 1200×600mm: Large format — requires specialist installation
- Mosaic: 20×20mm to 100×100mm

### Installation:
- Minimum 80% bed coverage required
- Use appropriate adhesive (S1 for internal, S2 flexible for wet areas and large format)
- Expansion joints every 4.5m in floors, at all changes of plane
""",
    ),

    # ── Measurement and pricing methodology ──────────────────────────────────
    TrainingDocument(
        filename="measurement.md",
        topic="Measurement and pricing methodology",
        content="""# Construction Measurement and Pricing — South Africa

## ASAQS Standard System of Measurement (SSM)
All South African BOQs should be measured in accordance with the ASAQS Standard System of Measurement. Key principles:

### Units of measurement:
- **m²** (square metres): Slabs, walls, floors, ceilings, waterproofing, tiling
- **m³** (cubic metres): Earthworks, concrete, fill, demolition by volume
- **m** (linear metres): Kerbs, pipes, gutters, rails, door frames
- **nr** (number): Doors, windows, sanitary fittings, light fittings, trees, piles
- **kg** (kilograms): Reinforcing steel, structural steelwork
- **t** (tonnes): Earthworks (sometimes), aggregates
- **sum** (lump sum): Items not easily quantified — site establishment, allowances

### Deductions:
- Openings in walls and slabs greater than 1.0 m² are deducted
- Openings less than 1.0 m² are generally NOT deducted (wastage factor)
- Reinforcing steel is measured as net weight (with no allowance for laps; laps are included in the rate)

## How to Measure Common Items

### Concrete slabs (m²):
- Measure the plan area of the slab
- Describe: thickness, mix (C25), and finish (power float / hand float / broom)
- Reinforcement: measured separately in kg or included in the rate as a composite item

### Brickwork (m²):
- Measure the face area of walls
- Deduct openings > 1.0 m²
- Describe: brick type, mortar mix, bond pattern, thickness (half-brick = 110mm, single brick = 220mm, one-and-a-half brick = 330mm)

### Earthworks (m³):
- Measure excavation volumes: length × width × depth
- **Bulking factor**: Excavated material bulks up — 1.0 m³ in situ = 1.25 m³ loose (varies by soil type)
- **Settlement factor**: Fill compacts — measure to finished levels
- Describe: type of material (topsoil, soft rock, hard rock), depth ranges, disposal distance

### Reinforcing steel (kg or t):
- Take off from structural drawings — each bar type, diameter, quantity, length
- Calculate weight: 7850 kg/m³ steel density, or use kg/m per bar size:
  - Y10 = 0.617 kg/m
  - Y12 = 0.888 kg/m
  - Y16 = 1.579 kg/m
  - Y20 = 2.467 kg/m
  - Y25 = 3.854 kg/m
  - Y32 = 6.313 kg/m

### Structural steelwork (t):
- Calculate from section sizes and lengths
- Add 5% for cleats, bolts, and minor items
- Describe: grade, surface treatment, erection method

## Pricing Rates (SA 2024/25 approximate ranges)

### Concrete:
- C25 slab including formwork, reinforcement, pour, finish: R1,800–R2,800/m² (depending on span, thickness, and access)
- In-situ concrete per m³ (materials + pour, exc. reinforcement and formwork): R2,500–R3,500/m³

### Brickwork (supply + lay):
- Half-brick plaster brick wall: R380–R520/m²
- Single brick face brick wall: R850–R1,200/m²
- Boundary wall (single brick with DPC and cappings): R1,200–R1,800/m²

### Earthworks:
- Bulk excavation in natural ground: R120–R250/m³
- Trench excavation (hand): R350–R600/m³
- Compacted fill (import): R150–R350/m³

### Tiling:
- Supply and lay ceramic floor tiles (mid-range material): R280–R450/m²
- Supply and lay porcelain 600×600mm: R350–R550/m²
- Supply and lay wall tiles: R250–R400/m²

### Painting:
- 2-coat PVA internal walls: R55–R90/m²
- 2-coat external acrylic: R80–R130/m²
- Gloss to woodwork (per coat): R45–R70/m²

### Roofing:
- IBR/Chromadek sheeting on purlins: R380–R550/m²
- Concrete roof tiles (supply, lay, battens): R320–R480/m²
- Flat roof torch-on waterproofing (2-layer): R250–R400/m²

## Markup and Margin
Construction pricing is complex. Be clear about the difference:
- **Mark-up**: % added to cost (e.g. cost R100 + 20% markup = R120)
- **Gross margin**: % of selling price (R120 selling, R100 cost = 16.7% margin)
- A contractor pricing a project adds:
  1. Direct costs (materials + labour + plant)
  2. Subcontractor costs
  3. Preliminaries and general items
  4. Risk contingency (2–5%)
  5. Head office overhead (5–8%)
  6. Profit (5–10%)

## AECOM Construction Cost Guide
AECOM publishes an annual guide to SA construction costs (the standard industry reference). It provides:
- Cost per m² by building type (offices, housing, hospitals, schools, etc.)
- Regional adjustment factors (Cape Town vs Johannesburg vs rural areas)
- Escalation indices (quarterly updates)
- Professional fee guidelines (aligned with SACQSP scales)
""",
    ),

    # ── Construction process ─────────────────────────────────────────────────
    TrainingDocument(
        filename="construction_process.md",
        topic="Construction process and sequence",
        content="""# Construction Process — South Africa

## Project Phases Overview

### 1. Pre-Construction
- Client brief: Define requirements, budget, programme
- Feasibility: Is the project viable on this site within this budget?
- Design development: Architectural, structural, services design
- Approvals: Local authority (building plan approval), heritage, environmental
- Tender: Invite contractors to price the work
- Award: Select contractor and sign contract

### 2. Construction
- Site establishment
- Substructure (foundations, ground floor)
- Superstructure (frame, walls, roof)
- Envelope (windows, external cladding, waterproofing)
- Fit-out (internal finishes, services installation)
- Commissioning (test all systems)
- Practical completion

### 3. Post-Construction
- Defects liability period
- Final account
- As-built drawings
- O&M manuals

## Foundation Types

### Strip Foundations
- Continuous concrete strips under load-bearing walls
- Typical for domestic and light commercial buildings
- Depth: 600mm below natural ground level minimum (or to firm founding level)
- Width: Minimum 500mm, calculated based on bearing capacity

### Pad Foundations
- Isolated concrete pads under columns
- Sized based on column load and soil bearing capacity
- Can be linked by ground beams

### Raft Foundation
- A reinforced concrete slab covering the full building footprint
- Used when soil is weak or variable, or where differential settlement is a risk
- Common in dolomite risk areas of SA (Centurion, Midrand, parts of Johannesburg)

### Pile Foundations
- Deep foundations transferring load to a stable stratum
- **Precast driven piles**: Hammered into the ground — fast, but vibration and noise
- **Bored cast-in-situ piles**: Drilled then filled with concrete — less vibration
- Used where founding conditions are poor or loads are very high

### Dolomite Areas
Large parts of Gauteng and North West Province have dolomitic geology:
- Sinkholes and dolines can form without warning
- Geotechnical investigation is essential
- SANS 1936 governs construction on dolomite land
- Raft or piled foundations typically required
- Special drainage requirements (no dispersal systems)

## Structural Systems

### Load-Bearing Masonry
- Walls carry the vertical load to the foundation
- Common for residential buildings up to 3 storeys
- Limiting factor: cannot easily create large open-plan spaces

### Reinforced Concrete Frame
- Columns and beams carry loads, allowing flexible floor plans
- Flat slabs (no beams) or beam-and-slab system
- Used for multi-storey commercial, residential, industrial

### Structural Steel Frame
- Fast to erect, long spans possible
- Common for industrial buildings, warehouses, large commercial buildings
- Requires fire protection (intumescent paint or board)

### Timber Frame
- Lightweight, fast to construct
- Common for residential extensions, outbuildings, eco-friendly buildings
- Requires specific waterproofing and pest protection in SA climate

## Wet Trades

### Plastering
- Backing coat (scratch coat): 12mm thick plaster applied to brickwork, keyed
- Finishing coat (plaster): 6mm smooth finish, or 2-coat system
- Standard plaster mix: 1:4 (cement:sand) for undercoat, 1:6 for finishing coat
- Allow 4–6 weeks for plaster to cure before painting

### Floor Screeds
- **Cement screed**: 1:4 mix, minimum 40mm thick for foot traffic
- **Self-levelling screed**: For fast finish over underfloor heating or uneven slabs
- **Power-floated concrete**: Direct finish on structural slab — no screed needed
- Allow 28 days before tiling

### Waterproofing (wet rooms)
- Torch-on membrane or liquid-applied: Apply to screed before tiling
- Must be tested (flood test for 24 hours) before tiling

## Dry Works (Partitions and Ceilings)

### Drylining (Gypsum board)
- Metal stud framing with gypsum board cladding
- Faster and lighter than masonry partitions
- Easy to install services (cables, pipes) within wall cavity
- Products: Rhinoboard, Knauf Fireboard, etc.

### Suspended Ceilings
- T-bar grid suspended from structure with hangers
- Acoustic tiles, gypsum board, or metal tiles inserted
- Provides space for ceiling-mounted services (HVAC, lighting, data)
- Standard grid: 600×600mm or 600×1200mm

## Services (MEP)

### Mechanical
- HVAC: Air conditioning (split units, VRV/VRF, central AHU systems)
- Ventilation: Extract fans (bathrooms, kitchens), fresh air supply
- Fire: Sprinkler systems (SANS 10287), fire hose reels, fire extinguishers

### Electrical
- Distribution: MDB (Main Distribution Board) → DBs per floor/area
- Reticulation: Wire in conduit or trunking
- Lighting: LED fittings specified by lux levels (SANS 10114)
- Earthing and surge protection required
- Solar PV: Common for new commercial buildings in SA (load shedding resilience)

### Plumbing
- Water supply: CPVC or copper pipe
- Drainage: uPVC pipe (SANS 1008)
- Water meters per unit (Water Bylaw requirement)
- Hot water geysers: 100–200L electric or solar
- Minimum pressure: 100kPa at any outlet

## Finishes

### External
- Plaster and paint (most common SA residential finish)
- Face brick (no plaster, high quality brickwork)
- Cement plaster with textured finish (Marmorino, Tyrolean)
- Rainscreen cladding (fibre cement, aluminium, ACM panels)

### Internal Walls
- PVA paint on plaster (standard residential)
- Semi-gloss (wet areas — kitchens, bathrooms)
- Skim coat and paint (high-quality finish in corporate offices)

### Floors
- Ceramic/porcelain tile (most common)
- Vinyl tile/sheet (commercial, hospitals)
- Timber boards (residential premium)
- Polished/sealed concrete (contemporary commercial)
- Carpet tiles (offices, bedrooms)
""",
    ),

    # ── Preliminaries detail ─────────────────────────────────────────────────
    TrainingDocument(
        filename="preliminaries.md",
        topic="Preliminaries and general items",
        content="""# Preliminaries and General Items — South Africa

## What Are Preliminaries?
Preliminaries (commonly called "Prelims") cover all the contractor's general costs for setting up, managing, and demobilising a construction project. They are not tied to specific work items but are essential to executing the works.

## Site Establishment

### Temporary offices and facilities:
- Contractor's site office (portable building or container)
- Client/principal agent's office (on larger contracts)
- Secure storage/lockup containers
- Ablution facilities (toilets, showers) for workers
- Canteen/rest area

### Site fencing and access:
- Hoarding (solid timber or Chromadek hoarding for urban sites)
- Security fencing with razor wire (factory/commercial sites)
- Security guard hut and 24-hour security
- Site gate with controlled access
- Signboards (contract details, H&S notices, principal contractor sign)

## Plant and Equipment

### Lifting equipment:
- Tower crane (multi-storey buildings): Typically R80,000–R180,000/month to hire
- Mobile crane (short-term lifts): R8,000–R25,000/day
- Passenger/goods hoist (for access in multi-storey): R30,000–R60,000/month

### Earthmoving:
- TLB (Tractor Loader Backhoe): R5,000–R9,000/day
- Excavator (20T): R7,000–R12,000/day
- Tipper trucks for spoil removal: R900–R1,500/load
- Compactor/roller: R3,000–R6,000/day

### Concrete equipment:
- Concrete pump (truck-mounted): R8,000–R15,000/pour
- Vibrators: Essential for compaction (buy or hire)
- Formwork: Shutterply + props + clamps (R180–R280/m² formwork area)

## Temporary Services

### Electrical:
- Bulk supply connection (from Eskom/municipality)
- Distribution boards and wiring on site
- Lighting for night work or enclosed areas
- Metered supply (client is billed, or included in prelims)
- Generator for load shedding (essential in SA 2023–2025 environment)
  - 100kVA generator: R35,000–R60,000/month hire
  - Diesel costs: Budgeted separately

### Water:
- Temporary water supply connection
- Plumbing to ablutions, curing points, concrete mixing
- Water storage tank if supply is unreliable

### Telecommunications:
- Internet connectivity for site management
- CCTV cameras (required on larger contracts)

## Health and Safety

### Legally required (Construction Regulations 2014):
- **Health and Safety Plan**: Prepared before work starts, updated throughout
- **Safety Officer**: Required for projects > R13.5 million or > 180 person-days
- **Safety file**: Daily H&S records, risk assessments, toolbox talks
- **Fall protection**: All work above 2m requires a fall protection plan
- **Scaffolding**: Erected by competent person, inspected regularly
- **PPE**: Hard hats, safety boots, high-vis vests, gloves mandatory on all SA sites

### Costs:
- Safety officer full-time: R25,000–R45,000/month
- Safety equipment (PPE, signs, first aid): R15,000–R50,000 (project)
- H&S plan preparation: R8,000–R20,000

## Quality Control

### Testing requirements:
- Concrete cube tests: 3 cubes per 10m³ poured (SANS 5863)
- Compaction tests (nuclear density gauge): Earthworks and roads
- Waterproofing: Flood test (24-hour standing water)
- Electrical: COC (Certificate of Compliance) required
- Plumbing: Pressure test (hydraulic test) for supply pipes

### Costs:
- Concrete testing (per set of cubes): R800–R1,500
- Compaction testing: R400–R800/test
- External testing laboratory: Budget R15,000–R50,000 for a medium project

## Programme Management

### Typical contract requirements:
- Baseline programme required at contract award
- Monthly programme updates
- Look-ahead programmes (3-week look-ahead)
- As-built programme for final account

### Software used in SA:
- MS Project: Most common for building work
- Primavera P6: Large infrastructure and mega projects
- Asta Powerproject: Some contractors
- Simple Excel Gantt: Smaller projects

## Insurances and Bonds

### Required on most SA contracts:
- **Contract Works (All Risk) Insurance**: Covers the works against damage during construction
- **Public Liability Insurance**: Third-party bodily injury and property damage (min R10 million, often R50 million)
- **COID (Workmen's Compensation)**: Statutory requirement — covers worker injuries
- **Performance Bond**: 10% of contract value, guaranteeing completion (not always required on private sector)
- **Retention Bond**: Alternative to cash retention — bank guarantee of equal value

## Prelims as a Percentage of Contract Value

Typical ranges:
| Project Type | Prelims % |
|---|---|
| Small residential addition | 8–12% |
| Standard house (new build) | 10–15% |
| Medium commercial building | 10–15% |
| Large multi-storey | 8–12% |
| Fast-track project | 12–18% |
| Remote site project | 15–25% |

Higher prelims % reflects: remote location, long duration, complex programme, large H&S requirements, or fast-track schedule.
""",
    ),

    # ── Professional fees in SA ───────────────────────────────────────────────
    TrainingDocument(
        filename="professional_fees.md",
        topic="Professional fees and project costs",
        content="""# Professional Fees and Project Costs — South Africa

## Professional Team
A typical SA building project requires the following professionals:

### Architect
- Designs the building and manages the design process
- Administers the building contract (as Principal Agent on JBCC contracts)
- Fees: SACAP guidelines suggest 6–10% of construction cost for full service
  - Pre-contract services (design through tender): typically 60% of total fee
  - Contract administration: typically 40%

### Quantity Surveyor (QS)
- Prepares cost estimates, BOQ, tender documents
- Evaluates tenders, certifies payments, manages final account
- SACQSP guideline fees: 3–5% of construction cost

### Structural Engineer
- Designs foundations, structural frame, slabs, retaining walls
- Fees: typically 1.5–3% of construction cost

### Mechanical Engineer (HVAC/plumbing)
- Designs air conditioning, ventilation, wet services
- Fees: 1.5–3% of construction cost

### Electrical Engineer
- Designs electrical distribution, lighting, data, fire detection
- Fees: 1–2% of construction cost

### Total professional fees (typical range):
- For residential: 10–18% of construction cost
- For commercial: 12–20% of construction cost
- These are additional to the construction contract value

## Development Costs (Full Project Budget)
When budgeting a development, include:
| Item | % of Construction Cost (approx) |
|---|---|
| Land cost | Variable |
| Construction contract | 100% (base) |
| Professional fees | 12–18% |
| VAT on construction (15%) | 15% |
| VAT on professional fees | 15% |
| Escalation during construction | 3–8% per year |
| Contingency | 5–10% |
| Financing costs | Variable |
| Legal, conveyancing, council fees | 1–3% |
| Marketing and sales | 2–5% (if residential development) |
| Agent's commission | 2–3% (on sale) |

## Payment Process on Building Contracts (JBCC)
1. Contractor submits payment claim monthly
2. Principal Agent (Architect) certifies a payment recommendation
3. QS checks quantities and rates
4. Client pays within the payment period (typically 30 days of certificate)
5. Non-payment is a serious breach — contractor can suspend work

## Construction Cost Benchmarks in SA (2024/25)
These are AECOM-based mid-range figures (excl. VAT, excl. professional fees, excl. site works):

| Building Type | R/m² range |
|---|---|
| Basic residential (low cost) | R6,000–R8,000 |
| Standard residential house | R10,000–R15,000 |
| Upmarket residential | R18,000–R35,000 |
| Office (open plan fit-out) | R12,000–R18,000 |
| Office (prestige) | R20,000–R30,000 |
| Retail (shell and core) | R8,000–R13,000 |
| Retail (fitted) | R12,000–R18,000 |
| Industrial/warehouse | R4,500–R8,000 |
| Hospital/clinic | R20,000–R35,000 |
| School classroom block | R8,000–R14,000 |
| Parking garage | R6,000–R10,000 |

Note: These are rough guidelines only. Actual costs depend on specification, location, site conditions, market conditions, and timing.

## VAT on Construction in SA
- Standard VAT rate: 15%
- Construction contracts are subject to VAT
- VAT is payable on professional fees
- Residential construction VAT can be claimed back if the developer is VAT-registered
- Social housing and certain government projects may have different VAT treatment

## Escalation
SA construction costs escalate due to material price inflation, labour cost increases, and exchange rate effects on imported materials. AECOM publishes quarterly escalation indices. 2022–2024 saw unusually high escalation (10–16% per annum) due to global supply chain disruption and load shedding costs. Budget for escalation on projects with construction periods longer than 12 months.
""",
    ),
]

TRAINING_TENANT_ID = "vula_training"
