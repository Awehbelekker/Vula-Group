# Real plans (anonymised)

One folder per plan: the drawing (`plan.dxf` / `plan.pdf` / `plan.png`) and a `truth.json` with
the areas, lengths and counts measured **by hand** (see `../../takeoff_bench.py` for the format).
Remove client names, addresses and title-block details before committing.

    python -m evals.takeoff_bench --dir evals/takeoff_plans/real          # DXF + PDF schedules, no model
    python -m evals.takeoff_bench --dir evals/takeoff_plans/real --live   # the AI reader too

Pass mark: every room and the total area within 2%, GFA within 2%, door/window counts exact.
