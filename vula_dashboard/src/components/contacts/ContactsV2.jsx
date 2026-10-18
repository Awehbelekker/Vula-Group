/**
 * ContactsV2.jsx — the redesigned email-contact library (2026-10-18, UX redesign Phase F). Same
 * data and actions as the current screen (contacts/useContacts.js): search and kind chips filter
 * the loaded list; each contact's kind is re-tagged in place. A table from 768px, rows on a phone.
 */
import { Download, RefreshCw, Upload, Users } from "lucide-react";
import { Button, DataTable, EmptyState, FilterBar, PageHeader, Select } from "../ui/index.jsx";
import { KIND_LABEL, KINDS, useContacts } from "./useContacts";
import "./contacts.css";

const initial = (c) => (c.name || c.email || "?").charAt(0).toUpperCase();
const nameOf = (c) => c.name || (c.email || "").split("@")[0];

function Kind({ c, x }) {
  return (
    <Select value={c.kind} onChange={(e) => x.retag(c.id, e.target.value)} aria-label={`Kind for ${nameOf(c)}`} className="vco-kind"
      onClick={(e) => e.stopPropagation()}>
      {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
    </Select>
  );
}

export default function ContactsV2({ tenantId }) {
  const x = useContacts(tenantId);
  const chips = KINDS.map((k) => ({ id: k, label: `${KIND_LABEL[k]}s`, count: x.counts[k] || 0 }));
  const columns = [
    { key: "name", label: "Name", sortable: true, sortValue: nameOf, render: (c) => (
      <span className="vco-who"><span className="vui-disc" aria-hidden="true">{initial(c)}</span><span className="vco-name">{nameOf(c)}</span></span>) },
    { key: "email", label: "Email", sortable: true, render: (c) => <span className="vco-sub">{c.email}</span> },
    { key: "message_count", label: "Messages", align: "right", sortable: true },
    { key: "kind", label: "Kind", render: (c) => <Kind c={c} x={x} /> },
  ];
  return (
    <div>
      {/* the shell's top bar says "People"; this sub-tab is the email directory */}
      <PageHeader sub="Your email directory — co-workers, clients and suppliers. Buyers who order or message you are in Customers."
        actions={<>
          <Button size="sm" variant="secondary" Icon={Download} onClick={x.exportCsv} disabled={!x.contacts.length}>Export CSV</Button>
          <input ref={x.fileRef} type="file" accept=".csv" onChange={(e) => x.importCsv(e.target.files?.[0])} hidden aria-hidden="true" tabIndex={-1} />
          <Button size="sm" variant="secondary" Icon={Upload} onClick={() => x.fileRef.current?.click()} disabled={x.importing}>{x.importing ? "Importing…" : "Import CSV"}</Button>
          <Button size="sm" Icon={RefreshCw} onClick={x.syncNow} disabled={x.syncing}>{x.syncing ? "Syncing…" : "Sync now"}</Button>
        </>} />
      {x.msg && <p className="vco-msg" role="status">{x.msg}</p>}
      <p className="vco-sub vco-hint">CSV columns: name, email, kind (internal, client, supplier or external — optional, defaults to external). Synced every 15 minutes.</p>
      <FilterBar query={x.q} onQuery={x.setQ} placeholder="Search name or email" label="Kind"
        chips={chips} selected={x.tab === "all" ? [] : [x.tab]} onToggle={(id) => x.setTab(x.tab === id ? "all" : id)} />
      {x.rows.length === 0 ? (
        <EmptyState Icon={Users} title={x.contacts.length ? "No contacts match" : "No contacts yet"}>
          {x.contacts.length ? "Try another search or clear the filter." : "Connect a mailbox in Settings, then Sync now."}
        </EmptyState>
      ) : (
        <DataTable columns={columns} rows={x.rows} caption="Contacts"
          stack={{ initials: initial, title: nameOf, detail: (c) => `${c.email} · ${c.message_count} msg`, trailing: (c) => <Kind c={c} x={x} /> }} />
      )}
    </div>
  );
}
