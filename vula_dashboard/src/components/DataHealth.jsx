/**
 * DataHealth.jsx — what Vula found to check in the business's data (GET /admin/data-health,
 * vula/commerce/data_checks.py), on Home › Back house. Each list is something that, left alone,
 * would put a wrong figure in front of the owner or a client: a client link that could mislead, an
 * internal cost sheet a client would see, a bill on the wrong project, a date that can't be right, a
 * bill unpaid beside its payment, a supplier on file twice, a job payment with no project.
 * Nothing shows when there's nothing to check, or for a login without Money access.
 */
import { useEffect, useState } from "react";
import { VULA_API } from "../lib/authFetch";
import { formatMoney } from "../lib/money";
import { Card, ListRow } from "./ui/index.jsx";

export default function DataHealth({ tenantId }) {
  const [d, setD] = useState(null);
  const [open, setOpen] = useState(null);
  useEffect(() => {
    fetch(`${VULA_API}/v1/commerce/${tenantId}/admin/data-health`)
      .then((r) => (r.ok ? r.json() : null)).then(setD).catch(() => setD(null));
  }, [tenantId]);
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
              <ul style={{ margin: "0 0 var(--space-3)", paddingLeft: "var(--space-4)", color: "var(--muted)", fontSize: "var(--fs-caption)" }}>
                {s.items.map((it, i) => (
                  <li key={it.id || i} style={{ overflowWrap: "anywhere", marginBottom: 4 }}>
                    {[it.project, it.filename || it.bill || it.supplier, it.detail].filter(Boolean).join(" — ")}
                  </li>))}
                {s.count > s.items.length && <li>…and {s.count - s.items.length} more</li>}
              </ul>)}
          </div>))}
      </Card>
    </>
  );
}
