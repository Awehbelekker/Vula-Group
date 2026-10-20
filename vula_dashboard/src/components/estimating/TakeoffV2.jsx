/**
 * TakeoffV2.jsx — the redesigned Estimating › Takeoff (2026-10-20, UX redesign Phase F). Same calls
 * and the same maths as VulaTakeoff (estimating/useTakeoff.js): upload plans and Vula reads them
 * into a bill of quantities; set your own rate per line and a markup; see totals by trade, the
 * room schedule, an order sheet and the live SA rates. Demo data shows until real plans are read,
 * and says so. Figures are the estimator's own rand maths, shown through formatMoney.
 */
import { AlertTriangle, ClipboardCopy, Download, FileUp, RefreshCw, X } from "lucide-react";
import { formatMoney } from "../../lib/money";
import { Button, Card, EmptyState, Field, Input, PillTabs, Skeleton, StatCard, StatusChip } from "../ui/index.jsx";
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

      {job && job.status !== "complete" && (
        <p className={job.status === "failed" ? "ves-error" : "ves-note"} role="status">
          <span style={{ flex: 1 }}>{job.filename}: {x.statusLabel[job.status] || job.status}{job.error ? ` (${job.error})` : ""}</span>
          {job.status === "failed" && <Button size="sm" variant="quiet" Icon={X} onClick={() => x.setLiveJob(null)}>Dismiss</Button>}
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
      {x.tab === "plans" && <Plans drawings={job?.project?.sheets ? [] : MOCK_TAKEOFF.drawings} areas={x.areas} onUpload={x.handleUploadClick} />}
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
