/**
 * FiledLibraryV2.jsx — the redesigned filed-documents library (2026-10-19, UX redesign Phase F).
 * Same data and actions as VulaDocuments' FiledLibrary (documents/useDocuments.js useFiledLibrary):
 * search + category / project / customer / date filters, a grid of tiles (photos open in a Sheet,
 * other files in a new tab), "File under…" for documents waiting on a project, and Load more.
 * Used by Documents, a project's Documents tab and a customer's page under the redesign.
 */
import { CalendarDays, FileText, FolderInput, ListChecks, User } from "lucide-react";
import { Button, Card, FilterBar, Input, Select, Sheet, Skeleton, StatusChip } from "../ui/index.jsx";
import { EXT_ICON, extOf, isImageDoc, needsProject, useFiledLibrary } from "./useDocuments";
import "./documents.css";

const EXT_TONE = { PDF: "danger", DOC: "info", XLS: "ok", CSV: "ok" };

export function FileBadge({ ext }) {
  const label = EXT_ICON[ext] || "FILE";
  return <span className={`vdc-badge vdc-badge--${EXT_TONE[label] || "muted"}`}>{label}</span>;
}

export default function FiledLibraryV2({ tenantId, customerPhone, defaultFiledBy, project, title = "Documents & media" }) {
  const x = useFiledLibrary({ tenantId, customerPhone, defaultFiledBy, project });
  const { docs, total, loading, filters, setFilter } = x;
  return (
    <Card pad={0} className="vdc-lib">
      <div className="vdc-lib-head">
        <h3 className="vdc-h">{title}</h3>
        <span className="vdc-sub" aria-live="polite">{loading && !docs.length ? "Loading…" : `${total} ${total === 1 ? "document" : "documents"}`}</span>
      </div>

      {!customerPhone && (
        <div className="vdc-lib-filters">
          <FilterBar query={filters.search} onQuery={(v) => setFilter("search", v)} placeholder="Search name, supplier, number, project…">
            <div className="vdc-selects">
              <Select aria-label="Category" value={filters.category} onChange={(e) => setFilter("category", e.target.value)}>
                <option value="">All categories</option>
                {x.categories.map(([c, n]) => <option key={c} value={c}>{c} ({n})</option>)}
              </Select>
              {!project && (
                <Select aria-label="Project" value={filters.project} onChange={(e) => setFilter("project", e.target.value)}>
                  <option value="">All projects</option>
                  {x.projects.map((p) => <option key={p.label} value={p.label}>{p.label}</option>)}
                </Select>
              )}
              <Select className="vdc-span" aria-label="Customer" value={filters.customer_phone} onChange={(e) => setFilter("customer_phone", e.target.value)}>
                <option value="">All customers</option>
                {x.customers.map((c) => <option key={c.phone} value={c.phone}>{c.name || c.phone}</option>)}
              </Select>
              <label className="vdc-date"><CalendarDays size={16} aria-hidden="true" /><span>From</span>
                <Input type="date" value={filters.since} onChange={(e) => setFilter("since", e.target.value)} /></label>
              <label className="vdc-date"><CalendarDays size={16} aria-hidden="true" /><span>To</span>
                <Input type="date" value={filters.until} onChange={(e) => setFilter("until", e.target.value)} /></label>
            </div>
          </FilterBar>
        </div>
      )}

      {loading && !docs.length && <div className="vdc-pad"><Skeleton lines={3} /></div>}
      {!loading && docs.length === 0 && <p className="vdc-empty">No documents match{customerPhone ? " this customer" : ""} yet.</p>}

      {docs.length > 0 && (
        <ul className="vdc-grid" aria-label={title}>
          {docs.map((doc) => <DocTile key={doc.id} doc={doc} projects={x.projects} onAssign={x.assign} onOpenImage={x.setLightboxDoc} />)}
        </ul>
      )}

      {docs.length < total && (
        <div className="vdc-more">
          <Button variant="secondary" onClick={x.loadMore} disabled={loading}>{loading ? "Loading…" : `Load more (${total - docs.length} left)`}</Button>
        </div>
      )}

      <Sheet open={!!x.lightboxDoc} title={x.lightboxDoc?.filename || ""} onClose={() => x.setLightboxDoc(null)}>
        {x.lightboxDoc && <img className="vdc-photo" src={x.lightboxDoc.file_url} alt={x.lightboxDoc.filename} />}
      </Sheet>
    </Card>
  );
}

function DocTile({ doc, projects, onAssign, onOpenImage }) {
  const ext = extOf(doc.filename);
  const isImage = isImageDoc(doc);
  const open = () => (isImage ? onOpenImage(doc) : doc.file_url && window.open(doc.file_url, "_blank"));
  const canOpen = !!doc.file_url;
  return (
    <li className="vdc-tile">
      <button type="button" className="vdc-open" onClick={open} disabled={!canOpen}
        aria-label={canOpen ? `Open ${doc.filename}` : `${doc.filename} (no copy stored)`}>
        {isImage && doc.file_url
          ? <img className="vdc-thumb" src={doc.file_url} alt="" loading="lazy" />
          : <span className="vdc-icon"><FileText size={28} strokeWidth={1.5} aria-hidden="true" /><FileBadge ext={ext} /></span>}
        <span className="vdc-name">{doc.filename}</span>
      </button>
      <div className="vdc-meta">
        {(doc.fields?.labels || []).map((l) => <StatusChip key={l} tone="warn">{l}</StatusChip>)}
        {doc.category && <StatusChip>{doc.category}</StatusChip>}
        {doc.customer_phone && <span className="vdc-tag" title="Linked to a customer"><User size={14} aria-hidden="true" />{doc.customer_phone}</span>}
        {doc.clickup_task_id && <span className="vdc-tag" title="Attached in ClickUp"><ListChecks size={14} aria-hidden="true" /><span className="vdc-sr">Attached in ClickUp</span></span>}
      </div>
      {doc.summary && <p className="vdc-summary">{doc.summary}</p>}
      {needsProject(doc) && (
        <label className="vdc-assign"><FolderInput size={16} aria-hidden="true" />
          <Select defaultValue="" aria-label={`File ${doc.filename} under a project`} onChange={(e) => onAssign(doc.id, e.target.value)}>
            <option value="" disabled>File under…</option>
            {projects.map((p) => <option key={p.label} value={p.label}>{p.label}</option>)}
          </Select>
        </label>
      )}
    </li>
  );
}
