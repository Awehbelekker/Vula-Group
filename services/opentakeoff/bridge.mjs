// Vula ↔ OpenTakeoff bridge. Vula's own code (not upstream), Apache-2.0-compatible.
//
// OpenTakeoff's engine speaks MCP over stdio. Vula's backend is Python on another Railway service,
// so this exposes ONE fixed, deterministic measurement pass over HTTP:
//
//   POST /measure   body: the plan PDF (application/pdf), header X-API-Key
//                   optional ?scales={"1":"1:100","3":"1:50"}  (page → scale label override)
//   GET  /health    liveness, plus the pinned engine tag/commit
//
// Per request: a FRESH engine process (no state shared between plans or tenants) → load_plan →
// per sheet set_scale (override label, else the scale note the engine detected on the sheet, else
// the sheet is reported unscaled and NOT measured) → detect_rooms → the engine's own numbers back,
// in its own units (SF / LF / feet). vula/takeoff/opentakeoff_client.py converts to metres. No
// agent, no LLM, and nothing is guessed. An unscaled sheet is a question, never a measurement.

import { createServer } from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { timingSafeEqual } from "node:crypto";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const PORT = Number(process.env.PORT || 8790);
const API_KEY = process.env.OPENTAKEOFF_API_KEY || "";
const MAX_BYTES = 100 * 1024 * 1024;          // same cap as Vula's /takeoff/upload
const TIMEOUT_MS = Number(process.env.OPENTAKEOFF_TIMEOUT_MS || 240000);
const MAX_CONCURRENT = Number(process.env.OPENTAKEOFF_MAX_CONCURRENT || 2);
const ENGINE = { tag: process.env.OPENTAKEOFF_TAG || "", commit: process.env.OPENTAKEOFF_COMMIT || "" };

let running = 0;

function keyOk(given) {
  if (!API_KEY || !given) return false;        // unset key = locked, never open by accident
  const a = Buffer.from(String(given)), b = Buffer.from(API_KEY);
  return a.length === b.length && timingSafeEqual(a, b);
}

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []; let size = 0;
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BYTES) { reject(Object.assign(new Error("Plan too large (max 100MB)"), { status: 413 })); req.destroy(); return; }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

async function call(client, name, args) {
  const r = await client.callTool({ name, arguments: args });
  const text = (r.content || []).map((c) => c.text || "").join("\n");
  if (r.isError) throw Object.assign(new Error(text || `${name} failed`), { tool: name });
  if (r.structuredContent) return r.structuredContent;
  try { return JSON.parse(text); } catch { return { text }; }
}

async function measure(pdfPath, scales, holder) {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: ["dist/server.js"],
    // detect_rooms is gated upstream while its flood engine is re-validated. Vula enables it and
    // owns the validation: the takeoff benchmark plus the review gate before any pricing.
    env: { ...process.env, OPENTAKEOFF_ONE_CLICK: "1" },
    stderr: "ignore",
  });
  const client = new Client({ name: "vula-takeoff-bridge", version: "1.0.0" });
  holder.client = client;                       // so a timeout can kill the engine process
  await client.connect(transport);
  try {
    const loaded = await call(client, "load_plan", { path: pdfPath });
    const sheets = [];
    for (const s of loaded.sheets || []) {
      const out = { page: s.page, sheet: s.sheet, sheet_number: s.sheet_number ?? null,
                    detected_scale: s.detected_scale ?? null, scale: null, rooms: [], withheld: null, error: null };
      const override = scales[String(s.page)];
      try {
        let set = null;
        if (override) set = await call(client, "set_scale", { sheet: s.sheet, label: override });
        else if (s.detected_scale) set = await call(client, "set_scale", { sheet: s.sheet, use_detected: true });
        if (set) {
          out.scale = { label: set.label ?? null, source: override ? "user_label" : "detected",
                        engine_source: set.source, confirmed: !!set.confirmed, upp_ft: set.upp,
                        warning: set.warning ?? null };
          const det = await call(client, "detect_rooms", { sheet: s.sheet, condition: "floor" });
          out.withheld = det.withheld ?? null;
          out.rooms = (det.rooms || []).filter((r) => typeof r.area_sf === "number").map((r) => ({
            label: r.label, merged_labels: r.merged_labels ?? [], area_sf: r.area_sf,
            perimeter_lf: r.perimeter_lf ?? null, confidence: r.confidence ?? null,
            confidence_factors: r.confidence_factors ?? [], raster_traced: !!r.raster_traced,
          }));
        }
      } catch (e) {
        out.error = String(e.message || e).slice(0, 500);   // one bad sheet never sinks the set
      }
      sheets.push(out);
    }
    return { engine: ENGINE, units: { area: "sf", length: "lf", upp: "ft_per_px" }, sheets };
  } finally {
    await client.close().catch(() => {});
  }
}

createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  if (req.method === "GET" && url.pathname === "/health") return send(res, 200, { ok: true, engine: ENGINE });
  if (req.method !== "POST" || url.pathname !== "/measure") return send(res, 404, { error: "not found" });
  if (!keyOk(req.headers["x-api-key"])) return send(res, 401, { error: "missing or invalid X-API-Key" });
  if (running >= MAX_CONCURRENT) return send(res, 503, { error: "busy" });

  let scales = {};
  try { scales = url.searchParams.get("scales") ? JSON.parse(url.searchParams.get("scales")) : {}; }
  catch { return send(res, 400, { error: "scales must be JSON {page: label}" }); }

  running++;
  const dir = await mkdtemp(join(tmpdir(), "vula-ot-"));
  let timer;
  const holder = {};
  try {
    const body = await readBody(req);
    if (body.subarray(0, 5).toString() !== "%PDF-") return send(res, 400, { error: "body is not a PDF" });
    const pdf = join(dir, "plan.pdf");                  // fixed name: no caller-controlled paths
    await writeFile(pdf, body);
    const result = await Promise.race([
      measure(pdf, scales, holder),
      new Promise((_, rej) => { timer = setTimeout(() => rej(Object.assign(new Error("timed out"), { status: 504 })), TIMEOUT_MS); }),
    ]);
    send(res, 200, result);
  } catch (e) {
    send(res, e.status || 500, { error: String(e.message || e).slice(0, 500) });
  } finally {
    clearTimeout(timer);
    if (holder.client) await holder.client.close().catch(() => {});
    running--;
    await rm(dir, { recursive: true, force: true });   // plans are never kept (POPIA)
  }
}).listen(PORT, () => console.log(`opentakeoff bridge on :${PORT} engine=${ENGINE.tag}@${ENGINE.commit.slice(0, 8)}`));
