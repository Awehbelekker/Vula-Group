# OpenTakeoff sidecar

This gives Vula's takeoff measured geometry for PDF plans. Without it, every PDF area is a number an LLM read
off the drawing text (see `docs/takeoff-audit.md`, item 1).

- **Engine:** [OpenTakeoff](https://github.com/Kentucky-ai/opentakeoff), pinned to tag
  `apache-2.0-final` = commit `84c7f48e458f6cd74f694e3a970dd011c73a8f2e`. That is the last
  Apache-2.0 release; later upstream commits are AGPL. **Never bump or merge past this commit.**
  `vula_mind/tests/test_opentakeoff_sidecar_pin.py` fails CI if the pin changes. The Docker build
  re-checks that the fetched commit is the pinned one and that its LICENSE is Apache-2.0.
- **Licence files:** upstream `LICENSE`, `NOTICE` and `THIRD-PARTY-NOTICES.md` are copied unchanged
  into the image at `/app/licenses/opentakeoff/`. No upstream source is committed to this repo.
- **Vula's code here:** `bridge.mjs`, a small HTTP wrapper around the engine's MCP stdio server.

## What it does

`POST /measure` (PDF body, `X-API-Key`), with optional `?scales={"<page>":"1:100"}`:

1. A fresh engine process starts for every request, so nothing is shared between plans or tenants.
   The PDF is written to a temp dir and deleted afterwards.
2. Each sheet's scale comes from the override label if given. Otherwise it uses the scale note the
   engine detected on the sheet. If neither exists, the sheet is reported **unscaled and not
   measured**. The engine's set scale is always `confirmed: false`, because confirming it is a human step.
3. `detect_rooms` runs on each scaled sheet: room labels, then vector or raster flood, then area and
   perimeter, each with the engine's own trace confidence. Anything skipped is counted under `withheld`.

The response uses the engine's own units (SF, LF, ft/px). `vula/takeoff/opentakeoff_client.py`
converts to metres and fills the existing `ExtractedProject`, so the rest of the takeoff is unchanged.

`detect_rooms` is gated upstream while its flood engine is re-validated, and the bridge enables it.
Vula owns that validation: the takeoff benchmark plus the review gate that runs before any pricing.

## Measured accuracy (CI, 31 Oct 2026)

Exact on clean geometry (synthetic plan +1.53%), and it correctly refuses a not-to-scale sheet.
**Automatic room detection on real plans is not good enough yet**: VA St Cloud rooms came out
20-36% under, and Porterville measured nothing. Do not feed `detect_rooms` results into pricing.
The details and next steps are in `docs/takeoff-audit.md`. CI re-scores on every change.

## Deploy (Railway)

1. New service from this repo. Set the root directory to `services/opentakeoff` and use its Dockerfile.
2. Set the variable `OPENTAKEOFF_API_KEY=<long random secret>`. Leave it public-networking off and use
   private networking only.
3. On the Vula backend service, set:
   - `OPENTAKEOFF_URL=http://<sidecar>.railway.internal:8790`
   - `OPENTAKEOFF_API_KEY=<same secret>`

   Then run `railway up` again, since env changes alone redeploy stale code.

With `OPENTAKEOFF_URL` unset, Vula behaves exactly as before (fail-open).
