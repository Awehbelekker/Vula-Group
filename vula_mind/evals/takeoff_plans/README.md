# Free public-plan takeoff corpus

These are real, freely redistributable construction drawings, each with a known answer, used to
measure takeoff accuracy. Every file comes from OpenTakeoff's last Apache-2.0 commit
(`apache-2.0-final` @ `84c7f48e458f6cd74f694e3a970dd011c73a8f2e`, see `NOTICE.opentakeoff`). Rebuild
it with `build_from_opentakeoff.py`, which converts every figure to metric once. No client plans
are stored here.

| Case | Plan type | Scale | What the truth is |
|---|---|---|---|
| `synthetic-four-room` | synthetic control | 1/4" = 1'-0" | **analytic**: exact by construction |
| `va-stcloud-af101` | healthcare finish plan (VA, St. Cloud) | 1/8" = 1'-0" | 6 agent-traced reference rings. One room has a **printed** area on the sheet (134A: 16 SF; the ring is +2.2%) |
| `porterville-adu-a1-101` | residential ADU (public domain) | 1/4" = 1'-0" | 6 agent-traced reference rings. Scale checked against 6 printed dimensions |
| `va-roseburg-a03a-nts` | clinic offices, **not to scale** | none | must come back **unmeasured**. The sheet is NTS and its boxes contradict its printed areas |
| `va-dublin-a601-counts` | admin finish plan + schedule (VA, Dublin) | 1/8" = 1'-0" | **hand count** of room types (restroom 4, mechanical 2, IV testing 2) |
| `va-shreveport-c300-lengths` | civil site utility (VA, Shreveport) | 1" = 20' | **printed** lengths: 400' sewer line; 100' service line (a rounded call-out) |

Tolerance (proposed, `truth.json` → `tolerances`): area 2%, length 2%, counts exact.

## Read this before quoting a score

- **The reference rings are not ground truth yet.** They were traced from the PDF linework by an
  AI agent, and every vertex cites its source segments, but no person has reviewed them
  (`review.human_reviewed: false`). Matching them proves agreement with the ring, not accuracy.
  To make them truth, a person checks the overlays (OpenTakeoff `evals/mcp-workflow-bench/plan-set/*/crops-v2/`)
  and sets `human_reviewed: true` with their name.
- **Six cases, not ten.** These are the free real plans reachable from this environment. More
  public plans exist: city pre-approved ADU programs (e.g. Boise, explicitly public domain) and VA
  bid sets on SAM.gov. Those hosts have to be allowed in the session's network settings first,
  and each needs its truth added the same way. Nothing replaces a few of DIGG's own plans with
  hand measurements.
- **Licences.** The VA sheets are public US federal solicitation postings, redistributed (as
  OpenTakeoff does) for engineering test purposes only; the A/E on each sheet keeps whatever rights
  it has. Porterville states public domain on the sheet. The synthetic sheet is Apache-2.0.

## Running it

- `tests/test_takeoff_sidecar_eval.py` (in CI): the corpus is intact (hashes, truth kinds,
  licences) and the scorer grades correctly.
- `python -m evals.takeoff_sidecar` runs it against a live sidecar (`OPENTAKEOFF_URL` set). It
  reports area error % per room and per plan type.
- Counts and lengths are in the truth files, but the sidecar bridge measures rooms only so far.
  They are reported as "not covered", never as passed.
