/**
 * DataHealth.jsx — what Vula found to check in the business's data (GET /admin/data-health,
 * vula/commerce/data_checks.py), on Home › Back house. Each list is something that, left alone,
 * would put a wrong figure in front of the owner or a client: a client link that could mislead, an
 * internal cost sheet a client would see, a bill on the wrong project, a date that can't be right, a
 * bill unpaid beside its payment, a supplier on file twice, a job payment with no project — and an
 * invoice for several jobs, whose split the owner confirms here (POST /admin/documents/{id}/site-split,
 * vula/commerce/site_split.py): its bank payment is then split across the jobs.
 * Nothing shows when there's nothing to check, or for a login without Money access.
 */
import { useCallback, useEffect, useState } from "react";
import { VULA_API } from "../lib/authFetch";
import { formatMoney } from "../lib/money";
import { Button, Card, ListRow, toast } from "./ui/index.jsx";

const small = { margin: "0 0 var(--space-3)", paddingLeft: "var(--space-4)", color: "var(--muted)", fontSize: "var(--fs-caption)" };

function SiteSplit({ tenantId, item, onDone }) {
  const [busy, setBusy] = useState(false);
  const [choices, setChoices] = useState(null);
  const apply = async (txnId) => {
    setBusy(true);
    try {
      const r = await fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/documents/${item.id}/site-split`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(txnId ? { txn_id: txnId } : {}),
      });
      const body = await r.json().catch(() => ({}));
      if (r.status === 409 && body.detail?.payments) { setChoices(body.detail.payments); return; }
      if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "The split didn't save.");
      toast(`Split: ${body.parts.map((p) => `${p.project} ${formatMoney(p.amount_cents)}`).join(" · ")}`);
      onDone();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{ margin: "var(--space-1) 0 var(--space-2)" }}>
      {item.parts.map((p) => (
        <div key={p.project} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
          <span>{p.project}</span><span className="vula-money">{formatMoney(p.amount_cents)}</span>
        </div>))}
      {choices ? (
        <div style={{ marginTop: "var(--space-2)" }}>
          <div>Which bank payment was this invoice's?</div>
          {choices.map((c) => (
            <Button key={c.id} size="sm" variant="secondary" disabled={busy} style={{ margin: "4px 8px 0 0" }}
              onClick={() => apply(c.id)}>
              {c.txn_date} · {formatMoney(c.amount_cents)}
            </Button>))}
        </div>
      ) : (
        <Button size="sm" disabled={busy} style={{ marginTop: "var(--space-2)" }} onClick={() => apply()}>
          {busy ? "Splitting…" : "Split the payment like this"}
        </Button>)}
    </div>
  );
}

export default function DataHealth({ tenantId }) {
  const [d, setD] = useState(null);
  const [open, setOpen] = useState(null);
  const load = useCallback(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/data-health`)
      .then((r) => (r.ok ? r.json() : null)).then(setD).catch(() => setD(null));
  }, [tenantId]);
  useEffect(load, [load]);
  if (!d || !d.sections || d.sections.length === 0) return null;
  return (
    <>
      <h2 style={{ font: "600 var(--fs-title)/var(--lh-title) var(--font-sans)", margin: "var(--space-5) 0 var(--space-2)" }}>
        To check before you rely on it · {d.issues}
      </h2>
      <Card pad={4}>
        {d.sections.map((s) => (
          <div key={s.code}>
            <ListRow title={s.title}
              detail={s.total_cents ? `${s.count} · ${formatMoney(s.total_cents)}` : `${s.count}`}
              trailing={open === s.code ? "Hide" : "Show"}
              onClick={() => setOpen(open === s.code ? null : s.code)} />
            {open === s.code && (
              <ul style={small}>
                {s.items.map((it, i) => (
                  <li key={it.id || i} style={{ overflowWrap: "anywhere", marginBottom: 4 }}>
                    {[it.project, it.filename || it.bill || it.supplier, it.detail].filter(Boolean).join(" — ")}
                    {it.action === "site_split" && <SiteSplit tenantId={tenantId} item={it} onDone={load} />}
                  </li>))}
                {s.count > s.items.length && <li>…and {s.count - s.items.length} more</li>}
              </ul>)}
          </div>))}
      </Card>
    </>
  );
}
