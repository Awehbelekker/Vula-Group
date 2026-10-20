/**
 * RepCrmLookupV2.jsx — the redesigned My Work › CRM sync (2026-10-20, UX redesign Phase F). Same
 * calls as VulaRepCrmLookup / VulaDynamics365Connect (rep/useRepCrmLookup.js): connect Dynamics 365
 * (org URL, then Microsoft's approval window), then search its contacts, accounts and
 * opportunities.
 */
import { Building2, Link2, Search } from "lucide-react";
import { Button, Card, EmptyState, Field, Input, ListRow, PillTabs, Skeleton } from "../ui/index.jsx";
import { ConnectCard } from "../settings/connect/ConnectCard.jsx";
import { KINDS, useDynamics365Connect, useRepCrmLookup } from "./useRepCrmLookup";
import "./rep.css";

const TABS = KINDS.map(([id, label]) => ({ id, label }));

export default function RepCrmLookupV2({ tenantId }) {
  const x = useRepCrmLookup(tenantId);
  if (x.connected === null) return <Card><Skeleton lines={3} /></Card>;
  if (!x.connected) return <Connect tenantId={tenantId} />;
  return (
    <section className="vrp" aria-label="Dynamics 365">
      <p className="vrp-sub">Search your real CRM data in Dynamics 365.</p>
      <PillTabs label="Search in" tabs={TABS} active={x.kind} onChange={x.setKind} />
      <form className="vrp-row" role="search" onSubmit={(e) => { e.preventDefault(); x.search(); }}>
        <span className="vrp-grow"><Input type="search" value={x.query} onChange={(e) => x.setQuery(e.target.value)} aria-label="Search Dynamics 365" placeholder="Name, email or company" /></span>
        <Button type="submit" Icon={Search}>Search</Button>
      </form>
      {x.error && <p className="vrp-error" role="alert">{x.error}</p>}
      {x.results.length > 0
        ? <Card pad={4}>
            <ul className="vrp-list" aria-label="Results">
              {x.results.map((r, i) => (
                <li key={r.id || i}><ListRow Icon={Building2} title={r.name || r.fullname || r.subject || "—"}
                  detail={[r.emailaddress1, r.telephone1, r.jobtitle].filter(Boolean).join(" · ") || undefined} /></li>))}
            </ul>
          </Card>
        : !x.error && <EmptyState Icon={Search} title="No results yet">Search for a contact, account or opportunity.</EmptyState>}
    </section>
  );
}

function Connect({ tenantId }) {
  const d = useDynamics365Connect(tenantId);
  return (
    <section className="vrp" aria-label="Dynamics 365">
      <p className="vrp-sub">Connect your Dynamics 365 (Dataverse) org to search your real accounts, contacts and opportunities from here.</p>
      <ConnectCard status={d.status} error={d.error}>
        <form className="vrp-form" onSubmit={(e) => { e.preventDefault(); d.handleConnect(); }}>
          <Field label="Your Dynamics org URL" hint="e.g. https://yourorg.crm4.dynamics.com">
            <Input type="url" value={d.orgUrl} onChange={(e) => d.setOrgUrl(e.target.value)} />
          </Field>
          <div><Button type="submit" Icon={Link2} disabled={d.loading}>{d.loading ? "Opening Microsoft…" : "Connect Dynamics 365"}</Button></div>
          <p className="vrp-muted">A Microsoft window opens for you to approve. About 30 seconds.</p>
        </form>
      </ConnectCard>
    </section>
  );
}
