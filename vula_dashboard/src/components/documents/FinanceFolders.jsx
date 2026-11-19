/**
 * FinanceFolders.jsx — Documents › For your accountant (19 Nov 2026; vula/commerce/finance_folders.py,
 * statement_files.py). The year's money documents in the folders an accountant expects — bank statements
 * per account (and the months still missing), sales invoices, supplier invoices by supplier, proofs of
 * payment, settlements, statements, contracts — worked out on the server from what each document is and
 * its own date. Download a folder or the whole year as a zip with an index. Owners and managers only.
 * Every figure is the server's; this file only formats it.
 */
import { useCallback, useEffect, useState } from "react";
import { ChevronRight, Download, FolderOpen, Landmark } from "lucide-react";
import { VULA_API } from "../../lib/authFetch";
import { formatMoney } from "../../lib/money";
import { Button, Card, EmptyState, ErrorCard, Select, Skeleton, StatusChip, toast } from "../ui/index.jsx";
import { openDocument } from "../accountant/useAccountant";

const money = (c) => formatMoney(c || 0, { cents: "always" });
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");
const monthName = (ym) => { const [y, m] = String(ym).split("-"); return `${MONTHS[Number(m) - 1] || m} ${y}`; };

/** The financial years to offer: this one (from 1 March) and the two before it. */
function years(today = new Date()) {
  const y = today.getMonth() >= 2 ? today.getFullYear() : today.getFullYear() - 1;
  return [0, 1, 2].map((n) => ({ id: `${y - n}-03-01`, label: `March ${y - n} – February ${y - n + 1}` }));
}

export function useFinanceFolders(tenantId) {
  const options = years();
  const [year, setYear] = useState(options[0].id);
  const [tree, setTree] = useState(null);
  const [state, setState] = useState("loading");      // loading | ready | off | failed
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    if (!tenantId) return;
    setState("loading");
    const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/finance-folders?year=${year}`).catch(() => null);
    if (r && r.status === 403) { setState("off"); return; }
    if (!r || !r.ok) { setState("failed"); return; }
    setTree(await r.json().catch(() => null));
    setState("ready");
  }, [tenantId, year]);
  useEffect(() => { load(); }, [load]);

  // fetched (not a plain link) so the signed-in token goes with it
  const download = async (folder) => {
    setBusy(folder || "all");
    try {
      const q = new URLSearchParams({ year }); if (folder) q.set("folder", folder);
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/finance-folders.zip?${q}`);
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(typeof d.detail === "string" ? d.detail : "Couldn't make the download — please try again.");
      }
      const name = (r.headers.get("content-disposition") || "").match(/filename="([^"]+)"/)?.[1] || "Documents.zip";
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url; a.download = name; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      const failed = Number(r.headers.get("x-files-failed") || 0);
      if (failed) toast(`${failed} file${failed === 1 ? "" : "s"} couldn't be fetched — the index lists them.`, "warn");
    } catch (e) {
      toast(e.message, "danger");
    } finally {
      setBusy("");
    }
  };

  return { options, year, setYear, tree, state, busy, reload: load, download };
}

export default function FinanceFolders({ tenantId, accountant = false }) {
  const x = useFinanceFolders(tenantId);
  return (
    <div className="vdc-stack vff">
      <Card className="vdc-panel">
        <h3 className="vdc-h"><FolderOpen size={18} aria-hidden="true" />{accountant ? "The year's documents" : "For your accountant"}</h3>
        <p className="vdc-sub">{accountant ? "The business's" : "Your"} money documents for the year, in the folders an accountant expects — sorted by what each document is and its own date. Nothing is moved.</p>
        <div className="vdc-row">
          <label className="vdc-grow"><span className="vdc-sr">Financial year</span>
            <Select aria-label="Financial year" value={x.year} onChange={(e) => x.setYear(e.target.value)}>
              {x.options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </Select>
          </label>
          <Button Icon={Download} disabled={!!x.busy || x.state !== "ready"} onClick={() => x.download()}>
            {x.busy === "all" ? "Making the zip…" : "Download the whole year"}
          </Button>
        </div>
      </Card>
      {x.state === "loading" && <Card><Skeleton lines={5} /></Card>}
      {x.state === "failed" && <ErrorCard what="your folders" onRetry={x.reload} />}
      {x.state === "off" && (
        <EmptyState Icon={FolderOpen} title={accountant ? "This business hasn't given you access right now" : "Only an owner or manager can see these folders"}>
          {accountant ? "Ask the business to check your access under Team › Accountant." : "They hold the business's bank statements and money documents."}
        </EmptyState>)}
      {x.state === "ready" && x.tree && (
        <>
          <Coverage cover={x.tree.coverage} />
          {x.tree.folders.map((f) => <Folder key={f.key} f={f} busy={x.busy} onDownload={() => x.download(f.key)}
            onOpen={(id) => openDocument(tenantId, id)} />)}
        </>)}
    </div>
  );
}

function Coverage({ cover }) {
  if (!cover) return null;
  const gaps = (cover.accounts || []).filter((a) => a.missing.length);
  return (
    <Card className="vdc-panel">
      <h3 className="vdc-h"><Landmark size={18} aria-hidden="true" />Bank statements on file</h3>
      {(cover.accounts || []).length === 0
        ? <p className="vdc-sub">No bank accounts yet — they appear as Vula reads your statements.</p>
        : (
          <ul className="vff-cover">
            {cover.accounts.map((a) => (
              <li key={a.id} className="vff-cover-row">
                <span className="vff-name">{a.account}</span>
                {a.missing.length === 0
                  ? <StatusChip tone="ok">Every month on file</StatusChip>
                  : <span className="vff-missing">Missing: {a.missing.map(monthName).join(", ")}</span>}
              </li>))}
          </ul>)}
      {gaps.length > 0 && <p className="vdc-sub">Download these from your bank's website (the official monthly statement) and upload each in Money › Books review › Statements — it's filed here under its account.</p>}
      {cover.lines_on_no_account > 0 && <p className="vdc-sub">{cover.lines_on_no_account} bank line{cover.lines_on_no_account === 1 ? " doesn't" : "s don't"} say which account {cover.lines_on_no_account === 1 ? "it's" : "they're"} from yet — the official statements settle that.</p>}
    </Card>
  );
}

function Folder({ f, busy, onDownload, onOpen }) {
  const [open, setOpen] = useState(false);
  return (
    <Card pad={0} className="vff-folder">
      <div className="vff-head">
        <button type="button" className="vff-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <ChevronRight size={18} className="vff-chev" aria-hidden="true" />
          <span className="vff-name">{f.title}</span>
          <span className="vdc-sub">{f.count} document{f.count === 1 ? "" : "s"}</span>
        </button>
        {f.count > 0 && (
          <Button size="sm" variant="secondary" Icon={Download} disabled={!!busy} onClick={onDownload}
            aria-label={`Download ${f.title}`}>{busy === f.key ? "Making…" : "Download"}</Button>)}
      </div>
      {open && (f.groups.length === 0
        ? <p className="vdc-sub vff-pad">Nothing in this folder for the year.</p>
        : f.groups.map((g) => (
          <div key={g.name} className="vff-group">
            <h4 className="vff-group-h">{g.name} <span className="vdc-sub">· {g.count}</span></h4>
            <ul className="vff-items">
              {g.items.map((it) => (
                <li key={it.id} className="vff-item">
                  <span className="vff-date">{it.date}</span>
                  {it.file_url
                    ? <a className="vff-label" href={it.file_url} target="_blank" rel="noreferrer">{it.label}</a>
                    : it.open
                      ? <button type="button" className="vff-label vff-open" onClick={() => onOpen(it.id)}>{it.label}</button>
                      : <span className="vff-label">{it.label}</span>}
                  {it.official && <StatusChip tone="ok">Official</StatusChip>}
                  {it.amount_cents != null && <span className="vula-money">{money(it.amount_cents)}</span>}
                </li>))}
            </ul>
          </div>)))}
    </Card>
  );
}
