/**
 * TakeoffV2.jsx — the redesigned Estimating › Takeoff (2026-10-20, UX redesign Phase F). Same calls
 * and the same maths as VulaTakeoff (estimating/useTakeoff.js): upload plans and Vula reads them
 * into a bill of quantities; set your own rate per line and a markup; see totals by trade, the
 * room schedule, an order sheet and the live SA rates. Demo data shows until real plans are read,
 * and says so. Figures are the estimator's own rand maths, shown through formatMoney.
 */
import { useState } from "react";
import { AlertTriangle, ClipboardCopy, Download, FileUp, RefreshCw, X } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, EmptyState, Field, Input, PillTabs, Skeleton, StatCard, StatusChip, confirmDialog, toast } from "../ui/index.jsx";
import { MOCK_TAKEOFF, TRADE_LABELS, copyOrderSheet, mid, net, orderGroups, useTakeoff, useTakeoffRates } from "./useTakeoff";
import "./estimating.css";

const rands = (n) => formatMoney(Math.round(Number(n || 0) * 100), { cents: "never" });
const TABS = [{ id: "boq", label: "Bill of quantities" }, { id: "plans", label: "Plans" }, { id: "rooms", label: "Rooms" },
  { id: "order", label: "Order sheet" }, { id: "rates", label: "Market rates" }];
const confTone = (v) => (v >= 90 ? "ok" : v >= 75 ? "warn" : "danger");
const words = (t) => String(t || "—").replace(/_/g, " ");

export default function TakeoffV2({ tenantId }) {
  const x = useTakeoff({ tenantId });
  const job = x.liveJob;
  const t = x.totals;
  const netTotal = t.total / (1 + x.markup / 100);
  return (
    <section className="ves" style={{ maxWidth: 1400 }} aria-label="Takeoff">
      <input ref={x.fileRef} type="file" accept=".pdf,.dxf,.dwg,.png,.jpg,.jpeg,.tiff" hidden onChange={x.handleFileSelected} aria-label="Plans file" />
      <div className="ves-row">
        <span className="ves-grow"><h2 className="ves-h">{x.projectName}
          {x.isLive ? <StatusChip tone="ok">Live</StatusChip> : <StatusChip tone="warn">Demo data</StatusChip>}</h2>
          <span className="ves-muted">{x.isLive ? `Read from ${job?.filename}` : "Upload real plans and Vula reads them into a bill of quantities."}</span></span>
        <Button Icon={FileUp} disabled={x.uploading} onClick={x.handleUploadClick}>{x.uploading ? "Uploading…" : "Upload plans"}</Button>
      </div>

      {job?.status === "needs_review" && <Review x={x} job={job} />}
      {job?.status === "complete" && <ToProject x={x} />}

      {job && !["complete", "needs_review"].includes(job.status) && (
        <p className={job.status === "failed" ? "ves-error" : "ves-note"} role="status">
          <span style={{ flex: 1 }}>{job.filename}: {x.statusLabel[job.status] || job.status}{job.error ? ` (${job.error})` : ""}</span>
          {["failed", "rejected"].includes(job.status) && <Button size="sm" variant="quiet" Icon={X} onClick={() => x.setLiveJob(null)}>Dismiss</Button>}
        </p>)}

      <Field label={`Markup: ${x.markup}%`}>
        <input type="range" min={0} max={40} step={0.5} value={x.markup} onChange={(e) => x.setMarkup(Number(e.target.value))} style={{ width: "100%", maxWidth: 360, accentColor: "var(--brand)" }} />
      </Field>

      <div className="ves-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 170px), 1fr))" }}>
        <StatCard variant="tile" label="Quote to client" value={rands(t.total)} sub={`incl. ${x.markup}% markup`} />
        <StatCard variant="tile" label="Your margin" value={rands(t.profit)} sub={`${x.markup}% on net cost`} />
        <StatCard variant="tile" label="Net cost" value={rands(netTotal)} sub="before markup" />
        <StatCard variant="tile" label="Cost per m²" value={x.areas.gross ? rands(t.total / x.areas.gross) : "—"} sub="all-in incl. markup" />
        <StatCard variant="tile" label="Line items" value={String(x.filtered.length)} sub={`${x.selectedTrades.length} trades on`} />
      </div>

      <div className="ves-row" role="group" aria-label="Trades">
        <Button size="sm" variant="quiet" onClick={() => x.setSelectedTrades(Object.keys(TRADE_LABELS))}>All trades</Button>
        {Object.entries(TRADE_LABELS).map(([key, label]) => {
          const on = x.selectedTrades.includes(key);
          return <Button key={key} size="sm" variant={on ? "secondary" : "quiet"} aria-pressed={on} onClick={() => x.toggleTrade(key)}>
            {label}{t.byTrade[key] ? ` · ${rands(t.byTrade[key])}` : ""}</Button>;
        })}
      </div>

      <PillTabs label="Takeoff view" tabs={TABS} active={x.tab} onChange={x.setTab} />
      {x.tab === "boq" && <Boq x={x} netTotal={netTotal} />}
      {x.tab === "plans" && <Plans drawings={job?.project
        ? (job.project.scale || []).map((s) => ({ sheet: s.sheet, title: s.sheet, scale: s.scale || "—", status: "read" }))
        : MOCK_TAKEOFF.drawings} areas={x.areas} onUpload={x.handleUploadClick} />}
      {x.tab === "rooms" && <Rooms rooms={x.rooms} />}
      {x.tab === "order" && <Order items={x.filtered} markup={x.markup} selectedTrades={x.selectedTrades} projectName={x.projectName} />}
      {x.tab === "rates" && <Rates apiHost={x.apiHost} tenantId={tenantId} />}

      <p className="ves-muted">{x.isLive
        ? `Live BOQ from ${job?.filename} · ${x.filtered.length} line items · rates: Vula AI and SA construction data`
        : `Demo data: ${MOCK_TAKEOFF.drawings.length} drawing sheets, AECOM 2025/26 rates. Upload real plans for a live BOQ.`}
        {" "}Confidence: high 90% and up, review 75–89%, specialist needed below 75%.</p>
    </section>
  );
}

/** A priced takeoff becomes the project's working BOQ — one bill, edited and used as the budget there. */
function ToProject({ x }) {
  const [project, setProject] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async (replace) => {
    setBusy(true);
    try {
      const r = await x.toProject(project, replace);
      if (r?.exists) {
        if (await confirmDialog(`${r.detail} Replace that project's BOQ with this takeoff?`)) await send(true);
        return;
      }
      if (r) toast(`Sent ${r.lines} lines to ${r.project}'s BOQ. Open Projects › BOQ to edit, price and use it as the budget.`);
    } catch (e) {
      toast(`Not sent: ${e.message}`, "danger");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="ves-row" role="group" aria-label="Send to a project">
      <Field label="Project">
        <Input value={project} onChange={(e) => setProject(e.target.value)} placeholder="Project name" aria-label="Project name" />
      </Field>
      <Button variant="secondary" disabled={busy || !project.trim()} onClick={() => send(false)}>Make this the project's BOQ</Button>
    </div>
  );
}

/** Held before pricing: why, the two readings side by side, and approve / correct / reject. */
const FINISHES = ["", "lvt", "vinyl_lvt", "carpet", "porcelain_tile", "tile", "epoxy", "screed", "concrete"];

function Review({ x, job }) {
  const p = job.project || {};
  const [finish, setFinish] = useState({});
  const [wallHeight, setWallHeight] = useState("");
  const [extWalls, setExtWalls] = useState(p.external_walls_m ? String(p.external_walls_m) : "");
  const reasons = job.review?.reasons || [];
  const measured = p.measured_rooms || [];
  const ai = p.ai_rooms || [];
  const names = [...new Set([...ai.map((r) => r.name), ...measured.map((r) => r.name)])];
  const area = (list, n) => list.find((r) => r.name === n)?.area;
  const dxfUnitsUnsure = (p.scale || []).some((s) => s.method === "inferred_from_extent");
  // The rooms as they'll be priced: the chosen reading's areas, with any finishes picked here.
  const roomsFrom = (useMeasured) => {
    const src = useMeasured && measured.length ? measured : (p.room_list || []).map((r) => ({ name: r.name, area: r.area }));
    return src.map((r) => ({ name: r.name, area: r.area,
      floor_finish: finish[r.name] ?? (p.room_list || []).find((x) => x.name === r.name)?.floor ?? "" }));
  };
  const edited = Object.keys(finish).length > 0;
  const num = (v) => (String(v).trim() === "" ? undefined : Number(String(v).replace(",", ".")));
  const approve = (useMeasured) => x.submitReview("approve", {
    scale_confirmed: dxfUnitsUnsure,
    ...(num(wallHeight) ? { wall_height: num(wallHeight) } : {}),
    ...(num(extWalls) ? { external_walls_m: num(extWalls) } : {}),
    ...(edited ? { rooms: roomsFrom(useMeasured) } : { use_measured: useMeasured }) });
  return (
    <Card className="ves-card" role="region" aria-labelledby="ves-review">
      <h3 id="ves-review" className="ves-h"><AlertTriangle size={16} aria-hidden="true" /> Check these quantities before Vula prices them</h3>
      <ul>{reasons.map((r) => <li key={r}>{r}</li>)}</ul>
      <p className="ves-muted">Measured confidence {p.confidence ?? "—"}%. Nothing is priced or ordered until you approve.</p>
      {names.length > 0 && (
        <table className="ves-table">
          <thead><tr><th>Room</th><th className="ves-num">AI reading (m²)</th><th className="ves-num">Measured / printed (m²)</th><th>Floor finish</th></tr></thead>
          <tbody>{names.map((n) => {
            const current = finish[n] ?? (p.room_list || []).find((r) => r.name === n)?.floor ?? "";
            return (
              <tr key={n}><td>{n}</td><td className="ves-num">{area(ai, n) ?? "—"}</td><td className="ves-num">{area(measured, n) ?? "—"}</td>
                <td><select aria-label={`Floor finish for ${n}`} value={current} onChange={(e) => setFinish((f) => ({ ...f, [n]: e.target.value }))}>
                  {FINISHES.map((v) => <option key={v} value={v}>{v ? words(v) : "None (not priced)"}</option>)}</select></td></tr>);
          })}
          </tbody>
        </table>)}
      <div className="ves-row">
        <Field label="Wall height (m), floor to ceiling">
          <Input inputMode="decimal" value={wallHeight} placeholder="2.7 if left empty" onChange={(e) => setWallHeight(e.target.value)} aria-label="Wall height in metres" />
        </Field>
        <Field label="External wall length (m)">
          <Input inputMode="decimal" value={extWalls} placeholder="for partition walls" onChange={(e) => setExtWalls(e.target.value)} aria-label="External wall length in metres" />
        </Field>
      </div>
      <div className="ves-row">
        {measured.length > 0 && <Button disabled={x.reviewing} onClick={() => approve(true)}>Use measured areas and price</Button>}
        {ai.length > 0 && <Button variant="secondary" disabled={x.reviewing} onClick={() => approve(false)}>Use the AI reading</Button>}
        {!measured.length && !ai.length && <Button variant="secondary" disabled={x.reviewing} onClick={() => approve(false)}>Approve as read</Button>}
        <Button variant="quiet" Icon={X} disabled={x.reviewing} onClick={() => x.submitReview("reject")}>Reject</Button>
      </div>
      {dxfUnitsUnsure && <p className="ves-muted">The drawing doesn't state its units. Approving records that you checked one known dimension.</p>}
    </Card>
  );
}

function Boq({ x, netTotal }) {
  return (
    <Card className="ves-card" role="group" aria-labelledby="ves-boq">
      <div className="ves-row">
        <h3 className="ves-h" id="ves-boq" style={{ flex: 1 }}>Bill of quantities{x.isLive ? "" : " (demo)"}</h3>
        {x.isLive && x.liveJob?.job_id && <Button size="sm" variant="secondary" Icon={Download} onClick={x.downloadExcel}>Excel</Button>}
        <Button size="sm" variant="secondary" Icon={ClipboardCopy} onClick={x.copyTSV}>Copy for Excel</Button>
      </div>
      <div className="ves-scroll" tabIndex={0} role="region" aria-label="Bill of quantities table">
        <table className="ves-table">
          <thead><tr>
            <th>Item</th><th className="ves-num">Qty</th><th>Unit</th><th className="ves-num">Market range</th>
            <th className="ves-num">Your rate (R)</th><th className="ves-num">Line</th><th className="ves-num">Charge-out</th><th>Confidence</th>
          </tr></thead>
          <tbody>
            {x.filtered.map((item) => {
              const rate = parseFloat(x.itemRates[item.id]) || mid(item);
              const lineNet = rate * item.qty;
              const over = rate > item.rateRange[1] * 1.1;
              return (
                <tr key={item.id}>
                  <td style={{ minWidth: 220 }}><span className="ves-grow"><span>{item.description}</span>
                    <span className="ves-muted">{TRADE_LABELS[item.trade] || item.trade}{item.notes ? ` · ${item.notes}` : ""}</span></span></td>
                  <td className="ves-num">{item.qty}</td>
                  <td>{item.unit}</td>
                  <td className="ves-num ves-muted">{rands(item.rateRange[0])}–{rands(item.rateRange[1])}</td>
                  <td className="ves-num">
                    <Input type="number" value={x.itemRates[item.id] || ""} aria-label={`Your rate for ${item.description}`}
                      aria-describedby={over ? `ves-over-${item.id}` : undefined}
                      onChange={(e) => x.setItemRates((p) => ({ ...p, [item.id]: e.target.value }))}
                      style={{ width: 104, textAlign: "right", borderColor: over ? "var(--danger)" : undefined }} />
                    {over && <span id={`ves-over-${item.id}`} className="ves-muted ves-danger" style={{ display: "block" }}>over market</span>}
                  </td>
                  <td className="ves-num">{rands(lineNet)}</td>
                  <td className="ves-num ves-money">{rands(lineNet * (1 + x.markup / 100))}</td>
                  <td><StatusChip tone={confTone(item.confidence)}>{item.confidence}%</StatusChip></td>
                </tr>);
            })}
          </tbody>
        </table>
      </div>
      <div style={{ maxWidth: 420, marginLeft: "auto", width: "100%" }}>
        <div className="ves-kv"><span>Net cost</span><span className="ves-money">{rands(netTotal)}</span></div>
        <div className="ves-kv"><span>Your margin ({x.markup}%)</span><span className="ves-money">{rands(x.totals.profit)}</span></div>
        <div className="ves-kv ves-kv--total"><span>Quote to client</span><span className="ves-money">{rands(x.totals.total)}</span></div>
      </div>
    </Card>
  );
}

function Plans({ drawings, areas, onUpload }) {
  return (
    <Card className="ves-card" role="group" aria-labelledby="ves-plans">
      <div className="ves-row"><h3 className="ves-h" id="ves-plans" style={{ flex: 1 }}>Drawing register</h3>
        <Button size="sm" variant="secondary" Icon={FileUp} onClick={onUpload}>Upload plans</Button></div>
      <div className="ves-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 150px), 1fr))" }}>
        <StatCard variant="tile" label="Gross floor area" value={`${areas.gross} m²`} />
        <StatCard variant="tile" label="Net usable" value={`${areas.net} m²`} />
        <StatCard variant="tile" label="Mezzanine" value={typeof areas.mezzanine === "number" ? `${areas.mezzanine} m²` : "—"} />
      </div>
      {drawings.length === 0 ? <p className="ves-muted">The sheets in these plans aren't listed separately; the rooms and areas above were read from them.</p> : (
        <ul className="ves-list" aria-label="Drawings">
          {drawings.map((d) => (
            <li key={d.sheet} className="ves-item" style={{ paddingLeft: 0, paddingRight: 0 }}>
              <span className="ves-grow"><b>{d.sheet} · {d.title}</b><span className="ves-muted">Scale {d.scale}</span></span>
              <StatusChip tone={d.status === "read" ? "ok" : "warn"}>{d.status === "read" ? "Read" : "Pending"}</StatusChip>
            </li>))}
        </ul>)}
    </Card>
  );
}

function Rooms({ rooms }) {
  return (
    <Card className="ves-card" role="group" aria-labelledby="ves-rooms">
      <h3 className="ves-h" id="ves-rooms">Room schedule</h3>
      <p className="ves-muted">Read from the plans by Vula.</p>
      {rooms.length === 0 ? <p className="ves-muted">No rooms could be read from these plans; the BOQ uses the gross floor area instead.</p> : (
        <div className="ves-scroll" tabIndex={0} role="region" aria-label="Room schedule table">
          <table className="ves-table">
            <thead><tr><th>Room</th><th className="ves-num">Area m²</th><th>Floor</th><th>Ceiling</th><th>Walls</th></tr></thead>
            <tbody>{rooms.map((r, i) => (
              <tr key={i}><td>{r.name}</td><td className="ves-num">{r.area}</td><td>{words(r.floor)}</td><td>{words(r.ceiling)}</td><td>{words(r.walls)}</td></tr>))}</tbody>
          </table>
        </div>)}
    </Card>
  );
}

function Order({ items, markup, selectedTrades, projectName }) {
  const groups = orderGroups(items, selectedTrades);
  return (
    <Card className="ves-card" role="group" aria-labelledby="ves-order">
      <div className="ves-row"><h3 className="ves-h" id="ves-order" style={{ flex: 1 }}>Material order list</h3>
        <Button size="sm" variant="secondary" Icon={ClipboardCopy} onClick={() => copyOrderSheet(groups, markup, projectName)}>Copy order sheet</Button></div>
      <p className="ves-muted">Priced at the middle of each market range, plus markup.</p>
      {groups.map((g) => (
        <div key={g.key}>
          <h4 className="ves-overline">{g.label} · {g.items.length} items · {rands(g.items.reduce((s, i) => s + net(i, markup), 0))}</h4>
          {g.items.map((item) => (
            <div key={item.id} className="ves-kv"><span>{item.description}<span className="ves-muted">{item.qty} {item.unit}</span></span><span className="ves-money">{rands(net(item, markup))}</span></div>))}
        </div>))}
    </Card>
  );
}

function Rates({ apiHost, tenantId }) {
  const x = useTakeoffRates({ apiHost, tenantId });
  return (
    <Card className="ves-card" role="group" aria-labelledby="ves-rates">
      <div className="ves-row"><h3 className="ves-h" id="ves-rates" style={{ flex: 1 }}>SA construction rates</h3>
        <label className="ves-row ves-muted" style={{ minHeight: 44 }}><input type="checkbox" checked={x.changedOnly} onChange={(e) => x.setChangedOnly(e.target.checked)} /> Changed only</label>
        <Button size="sm" variant="secondary" Icon={RefreshCw} disabled={x.loading} onClick={x.fetchRates}>Refresh</Button></div>
      {x.error && <p className="ves-error" role="alert"><AlertTriangle size={16} aria-hidden="true" /> Couldn't load rates ({x.error}).</p>}
      {x.loading && !x.rates.length && <Skeleton lines={4} />}
      {!x.error && !x.loading && x.rates.length === 0 && <EmptyState Icon={RefreshCw} title="No rates yet">The rates database hasn't been filled yet.</EmptyState>}
      {x.rates.length > 0 && (
        <div className="ves-scroll" tabIndex={0} role="region" aria-label="Rates table">
          <table className="ves-table">
            <thead><tr><th>Item</th><th>Unit</th><th className="ves-num">Low</th><th className="ves-num">Mid</th><th className="ves-num">High</th><th>Source</th><th>Updated</th></tr></thead>
            <tbody>{x.rates.map((r, i) => (
              <tr key={i}>
                <td style={{ minWidth: 200 }}><span className="ves-grow"><span>{r.item || r.description}</span><span className="ves-muted">{TRADE_LABELS[r.trade || "concrete"] || r.trade}</span></span></td>
                <td>{r.unit}</td>
                <td className="ves-num">{r.low ? rands(r.low) : "—"}</td>
                <td className="ves-num ves-money">{r.mid ? rands(r.mid) : (r.rate ? rands(r.rate) : "—")}</td>
                <td className="ves-num">{r.high ? rands(r.high) : "—"}</td>
                <td>{r.source || "—"}</td>
                <td>{(r.updated_at || r.scraped_at || "")?.slice(0, 10) || "—"}</td>
              </tr>))}</tbody>
          </table>
        </div>)}
    </Card>
  );
}
