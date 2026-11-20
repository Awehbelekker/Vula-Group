/**
 * useAccountant.js — the data behind the accountant's workspace (19 Nov 2026, Phase 3 of
 * Xero-quality books). Every call is a read the server allows an accountant's login
 * (vula_mind/vula/api/server.py _ACCOUNTANT_READS) and puts on the business's record. Plain fetch
 * (the installed auth patch adds the login). Every figure is the server's; nothing is worked out here.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../../lib/authFetch";
import { toast } from "../ui/index.jsx";

const admin = (t) => `${VULA_API}/v1/commerce/${t}/admin`;

async function getJson(url) {
  const r = await fetch(url).catch(() => null);
  if (!r) return { ok: false, status: 0, data: {} };
  return { ok: r.ok, status: r.status, data: await r.json().catch(() => ({})) };
}

/** Save a file the server makes (the login goes with it — the routes aren't public). */
export async function download(url, fallbackName) {
  try {
    const r = await fetch(url);
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(typeof d.detail === "string" ? d.detail : "Couldn't download that — please try again.");
    }
    const name = (r.headers.get("content-disposition") || "").match(/filename="([^"]+)"/)?.[1] || fallbackName;
    const href = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = href; a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  } catch (e) {
    toast(e.message, "danger");
  }
}

/** Open one filed document through the server, which notes it on the business's record. */
export async function openDocument(tenantId, docId) {
  const { ok, data } = await getJson(`${VULA_API}/v1/documents/${tenantId}/filed/${docId}/open`);
  if (!ok || !data.url) { toast(data.detail || "That document couldn't be opened.", "danger"); return; }
  window.open(data.url, "_blank", "noopener");
}

export function useClients(tenantId) {
  const [clients, setClients] = useState([]);
  const [name, setName] = useState("");
  useEffect(() => {
    getJson(`${VULA_API}/v1/accountant/clients`).then(({ data }) => setClients(data.clients || []));
  }, []);
  useEffect(() => {
    if (!tenantId) return;
    getJson(`${VULA_API}/v1/tenants/${tenantId}`).then(({ data }) => setName(data.display_name || data.name || ""));
  }, [tenantId]);
  return { clients, name };
}

export function useOverview(tenantId) {
  const [audit, setAudit] = useState(null);
  const [cover, setCover] = useState(null);
  const [state, setState] = useState("loading");
  useEffect(() => {
    if (!tenantId) return;
    setState("loading");
    Promise.all([getJson(`${admin(tenantId)}/books-audit/latest`), getJson(`${admin(tenantId)}/bank-statements/coverage`)])
      .then(([a, c]) => {
        if (a.status === 403 && c.status === 403) { setState("off"); return; }
        setAudit(a.ok ? a.data : null);
        setCover(c.ok ? c.data : null);
        setState("ready");
      });
  }, [tenantId]);
  return { audit, cover, state };
}

export function useReports(tenantId) {
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [basis, setBasis] = useState("accrual");
  const [d, setD] = useState({});
  const [state, setState] = useState("loading");
  const qs = useCallback((withBasis) => {
    const p = new URLSearchParams();
    if (since) p.set("since", since);
    if (until) p.set("until", until);
    if (withBasis) p.set("basis", basis);
    return p.toString();
  }, [since, until, basis]);
  useEffect(() => {
    if (!tenantId) return;
    setState("loading");
    const a = admin(tenantId);
    Promise.all([
      getJson(`${a}/reports/pnl?${qs(true)}`), getJson(`${a}/reports/vat?${qs(false)}`),
      getJson(`${a}/reports/trial-balance?${qs(true)}`), getJson(`${a}/reports/aged-creditors`),
      getJson(`${a}/finances/insights?days=3650`),
    ]).then(([pnl, vat, tb, cred, fin]) => {
      if (!pnl.ok) { setState(pnl.status === 403 ? "off" : "failed"); return; }
      setD({ pnl: pnl.data, vat: vat.data, tb: tb.ok ? tb.data : null, creditors: cred.ok ? cred.data : null,
        debtors: fin.ok ? fin.data.receivables : null });
      setState("ready");
    });
  }, [tenantId, qs]);
  const ledgerCsv = () => download(`${admin(tenantId)}/reports/ledger.csv?${qs(true)}`, "ledger.csv");
  const statementsPdf = () => download(`${admin(tenantId)}/reports/statements.pdf?${qs(true)}`, "financial-statements.pdf");
  return { since, setSince, until, setUntil, basis, setBasis, ...d, state, ledgerCsv, statementsPdf };
}

export function useBankLines(tenantId) {
  const [accounts, setAccounts] = useState([]);
  const [account, setAccount] = useState("");
  const [lines, setLines] = useState([]);
  const [more, setMore] = useState(false);
  const [state, setState] = useState("loading");
  useEffect(() => {
    if (!tenantId) return;
    getJson(`${admin(tenantId)}/bank/accounts`).then(({ data }) => setAccounts(data.accounts || []));
  }, [tenantId]);
  const load = useCallback(async (offset = 0) => {
    if (!tenantId) return;
    if (!offset) setState("loading");
    const p = new URLSearchParams({ limit: "200", offset: String(offset) });
    if (account) p.set("account_id", account);
    const { ok, status, data } = await getJson(`${admin(tenantId)}/bank/transactions?${p}`);
    if (!ok) { setState(status === 403 ? "off" : "failed"); return; }
    const rows = data.transactions || [];
    setLines((cur) => (offset ? [...cur, ...rows] : rows));
    setMore(rows.length === 200);
    setState("ready");
  }, [tenantId, account]);
  useEffect(() => { load(0); }, [load]);
  const csv = () => download(`${admin(tenantId)}/bank/transactions.csv${account ? `?account_id=${encodeURIComponent(account)}` : ""}`,
    "bank-lines.csv");
  return { accounts, account, setAccount, lines, more, loadMore: () => load(lines.length), state, csv };
}

export function useInvoiceList(tenantId) {
  const [direction, setDirection] = useState("outbound");
  const [rows, setRows] = useState([]);
  const [state, setState] = useState("loading");
  useEffect(() => {
    if (!tenantId) return;
    setState("loading");
    getJson(`${admin(tenantId)}/invoices?doc_type=invoice&direction=${direction}&limit=200`).then(({ ok, status, data }) => {
      if (!ok) { setState(status === 403 ? "off" : "failed"); return; }
      setRows(data.invoices || []);
      setState("ready");
    });
  }, [tenantId, direction]);
  const pdf = (inv) => download(`${admin(tenantId)}/invoices/${inv.id}/pdf`, `${inv.invoice_number || "invoice"}.pdf`);
  return { direction, setDirection, rows, state, pdf };
}
