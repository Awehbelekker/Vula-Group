"""Rebuild the free public-plan takeoff corpus from a checkout of OpenTakeoff's last Apache-2.0
commit. Reproducible: same input commit → byte-identical plan.pdf files and truth.json.

    git -C /tmp/ot init -q && git -C /tmp/ot fetch -q --depth 1 \
        https://github.com/Kentucky-ai/opentakeoff 84c7f48e458f6cd74f694e3a970dd011c73a8f2e \
        && git -C /tmp/ot checkout -q FETCH_HEAD
    python evals/takeoff_plans/build_from_opentakeoff.py /tmp/ot

Only data is read from the checkout (PDFs and reference JSON); none of its code is run.
Truth figures are converted to metric here, once. See README.md in this folder for what each
truth tier does and doesn't prove.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

COMMIT = "84c7f48e458f6cd74f694e3a970dd011c73a8f2e"
M_PER_FT = 0.3048
HERE = Path(__file__).resolve().parent
VIA = f"OpenTakeoff apache-2.0-final @ {COMMIT[:8]} (Apache-2.0)"
TOLERANCES = {"area_pct": 2.0, "length_pct": 2.0, "count_exact": True}
UNREVIEWED = {"human_reviewed": False,
              "prepared_by": "AI agent from the PDF vector linework (OpenTakeoff plan-set v2); "
                             "every vertex cites its source segments. Not yet checked by a person."}


def shoelace(verts):
    n = len(verts)
    return abs(sum(verts[i][0] * verts[(i + 1) % n][1] - verts[(i + 1) % n][0] * verts[i][1]
                   for i in range(n))) / 2


def perimeter(verts):
    n = len(verts)
    return sum(((verts[(i + 1) % n][0] - verts[i][0]) ** 2 + (verts[(i + 1) % n][1] - verts[i][1]) ** 2) ** 0.5
               for i in range(n))


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rooms_from(ref: dict, with_perimeter: bool) -> list:
    m_per_px = ref["scale"]["feet_per_image_px"] * M_PER_FT
    out = []
    for r in ref["rooms"]:
        row = {"label": r["label"], "finish": r.get("finish"),
               "area_m2": round(shoelace(r["verts_px"]) * m_per_px ** 2, 3), "truth": "reference_ring"}
        if with_perimeter:
            row["perimeter_m"] = round(perimeter(r["verts_px"]) * m_per_px, 3)
        if r.get("printed_sf") is not None:
            row["printed_area_m2"] = round(r["printed_sf"] * M_PER_FT ** 2, 3)
        out.append(row)
    return out


def write_case(ot: Path, case: str, src_pdf: str, truth: dict) -> None:
    d = HERE / case
    d.mkdir(exist_ok=True)
    shutil.copyfile(ot / src_pdf, d / "plan.pdf")
    truth = {"case": case, "file": "plan.pdf", "sha256": sha256(d / "plan.pdf"), "page": 1,
             **truth, "tolerances": TOLERANCES}
    truth["source"]["via"] = f"{VIA}: {src_pdf}"
    (d / "truth.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False) + "\n")


def main(ot: Path) -> None:
    head = subprocess.run(["git", "-C", str(ot), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != COMMIT:
        sys.exit(f"Checkout is at {head or '?'}; the corpus must come from {COMMIT} (last Apache-2.0 commit).")
    ps = ot / "evals/mcp-workflow-bench/plan-set"
    j = lambda p: json.loads((ot / p).read_text())  # noqa: E731

    ctrl = j("evals/mcp-workflow-bench/reference.json")
    write_case(ot, "synthetic-four-room", "demo/sample-plan.pdf", {
        "plan_type": "synthetic_control",
        "source": {"description": "Original synthetic A-101 four-room sheet (demo/make_sample_plan.py)",
                   "licence": "Apache-2.0 (OpenTakeoff)"},
        "scale": {"label": "1/4\" = 1'-0\"", "drawn_to_scale": True,
                  "m_per_px": ctrl["scale"]["feet_per_image_px"] * M_PER_FT},
        "expect": {"measurable": True},
        "rooms": [{**r, "truth": "analytic"} for r in rooms_from(ctrl, with_perimeter=True)],
        "review": {"human_reviewed": False, "prepared_by": "analytic: exact by construction"},
    })

    st = j("evals/mcp-workflow-bench/plan-set/stcloud/reference.json")
    write_case(ot, "va-stcloud-af101", "demo/sample-finish-plan.pdf", {
        "plan_type": "healthcare_finish_plan",
        "source": {"description": "VA 656-19-306 Renovate Bldg 28 1st Floor East RRTP, St. Cloud VAMC — "
                                  "sheet AF101 First Floor Finish Plan (page 2 is the AF600 finish schedule)",
                   "licence": "US federal solicitation drawing posted publicly on SAM.gov; A/E retains its rights; "
                              "redistributed for engineering test purposes only"},
        "scale": {"label": "1/8\" = 1'-0\"", "drawn_to_scale": True,
                  "m_per_px": st["scale"]["feet_per_image_px"] * M_PER_FT},
        "expect": {"measurable": True},
        "rooms": rooms_from(st, with_perimeter=False),
        "review": UNREVIEWED,
    })

    pv = j("evals/mcp-workflow-bench/plan-set/porterville/reference.json")
    write_case(ot, "porterville-adu-a1-101", str((ps / "porterville/porterville-adu-a1-101.pdf").relative_to(ot)), {
        "plan_type": "residential_adu",
        "source": {"description": "City of Porterville CA Pre-Approved ADU prototypes (07/05/23), sheet A1-101",
                   "licence": "Public domain (stated on the sheet by the City of Porterville)"},
        "scale": {"label": "1/4\" = 1'-0\"", "drawn_to_scale": True,
                  "m_per_px": pv["scale"]["feet_per_image_px"] * M_PER_FT,
                  "basis": "checked against six printed dimension strings (all within 0.3 px)"},
        "expect": {"measurable": True},
        "rooms": rooms_from(pv, with_perimeter=False),
        "review": UNREVIEWED,
    })

    write_case(ot, "va-roseburg-a03a-nts", str((ps / "roseburg/va-roseburg-a03a.pdf").relative_to(ot)), {
        "plan_type": "clinic_office_not_to_scale",
        "source": {"description": "VA 653-24-103 Replace Finishes B1AC D-Wing, Roseburg — sheet A-03a "
                                  "(SAM.gov c71cd5d50c0b488daf648b2f744a3d62, Attachment 6)",
                   "licence": "US federal solicitation drawing posted publicly on SAM.gov (in-house VA); "
                              "redistributed for engineering test purposes only"},
        "scale": {"label": None, "drawn_to_scale": False,
                  "basis": "Sheet is marked NTS and its room boxes contradict the printed net SF. "
                           "No scale exists to measure with."},
        "expect": {"measurable": False,
                   "why": "An NTS sheet must come back unmeasured and flagged — never measured at a guessed scale."},
        "rooms": [], "review": UNREVIEWED,
    })

    write_case(ot, "va-dublin-a601-counts", "evals/four-asks-2026-09-02/sheets/va-dublin-bldg9a-finish-plan-A601.pdf", {
        "plan_type": "admin_finish_plan_with_schedule",
        "source": {"description": "VA 557-21-703 Renovate Bldg 9A, Carl Vinson VAMC Dublin GA — sheet A-601 "
                                  "(SAM.gov 85e50629af6f4e62885e766bc9e6f009, RFP Attachment 13)",
                   "licence": "US federal solicitation drawing posted publicly on SAM.gov; A/E retains its rights; "
                              "redistributed for engineering test purposes only"},
        "scale": {"label": "1/8\" = 1'-0\"", "drawn_to_scale": True, "basis": "title block (no drawn note detected)"},
        "expect": {"measurable": True},
        "counts": [{"label": "RESTROOM", "count": 4, "truth": "hand_count", "rooms": ["110", "111", "112", "113"]},
                   {"label": "MECHANICAL", "count": 2, "truth": "hand_count", "rooms": ["101", "109"]},
                   {"label": "IV TESTING", "count": 2, "truth": "hand_count", "rooms": ["103", "106"]}],
        "review": {"human_reviewed": False, "prepared_by": "hand count published with OpenTakeoff evals/four-asks"},
    })

    write_case(ot, "va-shreveport-c300-lengths",
               "evals/four-asks-2026-09-02/sheets/va-shreveport-fisher-house-site-utility-C300.pdf", {
        "plan_type": "civil_site_utility",
        "source": {"description": "VA 667-23-113 Fisher House Site Prep, Overton Brooks VAMC Shreveport — sheet C-300 "
                                  "(SAM.gov 7a75c7c60b0d49638a80b7f9ea67d06e, sheet 6 of 16)",
                   "licence": "US federal solicitation drawing posted publicly on SAM.gov; A/E retains its rights; "
                              "redistributed for engineering test purposes only"},
        "scale": {"label": "1\" = 20'", "drawn_to_scale": True, "basis": "drawn scale note on the sheet"},
        "expect": {"measurable": True},
        "lengths": [{"label": "8\" sewer line MH to MH", "length_m": round(400 * M_PER_FT, 3), "truth": "printed",
                     "printed": "400' OF 8\" SEWER LINE @ 0.4%"},
                    {"label": "6\" sewer service line", "length_m": round(100 * M_PER_FT, 3), "truth": "printed_nominal",
                     "printed": "100' OF 6\" SEWER SERVICE LINE @ 1.0% MIN. (a rounded call-out)"}],
        "review": {"human_reviewed": False, "prepared_by": "the sheet's own printed lengths"},
    })

    shutil.copyfile(ot / "NOTICE", HERE / "NOTICE.opentakeoff")
    print("corpus written:", sorted(p.name for p in HERE.iterdir() if p.is_dir()))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
