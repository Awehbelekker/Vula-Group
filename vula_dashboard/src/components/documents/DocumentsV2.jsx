/**
 * DocumentsV2.jsx — the redesigned Documents screen (2026-10-19, UX redesign Phase F). Same data
 * and actions as the current screen (documents/useDocuments.js); the panels are grouped under four
 * tabs instead of one long page: Library (the filed documents), Add (upload to what Vula knows,
 * store media, Google Drive, your website), Tidy up (waiting for a project, health, re-read, learn)
 * and What customers see (who can see each document, a customer's-eye search, the KB files).
 */
import { useEffect, useRef, useState } from "react";
import {
  BookOpen, Cloud, Eye, FileUp, FolderInput, Globe, HeartPulse, Image as ImageIcon, RefreshCw, ShoppingBag, Upload, X,
} from "lucide-react";
import { Button, Card, EmptyState, Field, IconButton, Input, PageHeader, PillTabs, Select, StatusChip } from "../ui/index.jsx";
import FiledLibraryV2, { FileBadge } from "./FiledLibraryV2";
import {
  ACCEPT, AUDIENCE_BADGE, SORT_REASON, formatSize, num,
  useCustomerPreview, useDocumentsHealth, useDriveImport, useKbUpload, useKnowledgeVisibility, useLearn, useMediaUpload,
  useReread, useSortProjects, useWebsiteImport,
} from "./useDocuments";
import "./documents.css";

const TABS = [{ id: "library", label: "Library" }, { id: "add", label: "Add" }, { id: "tidy", label: "Tidy up" }, { id: "customers", label: "What customers see" }];
const AUDIENCE_TONE = { public: "ok", staff: "muted", owner: "info", mixed: "warn", untagged: "warn" };
const Audience = ({ audience }) => {
  const a = AUDIENCE_BADGE[audience] ? audience : "untagged";
  return <StatusChip tone={AUDIENCE_TONE[a]}>{AUDIENCE_BADGE[a].label}</StatusChip>;
};

function Panel({ Icon, title, children, className = "" }) {
  return (
    <Card className={`vdc-panel ${className}`}>
      <h3 className="vdc-h"><Icon size={18} aria-hidden="true" />{title}</h3>
      {children}
    </Card>
  );
}
const Note = ({ children, tone }) => (children ? <p className={`vdc-note${tone ? ` vdc-note--${tone}` : ""}`} role="status">{children}</p> : null);

export default function DocumentsV2({ tenantId = "default", defaultFiledBy }) {
  const [tab, setTab] = useState("library");
  const kb = useKbUpload(tenantId);
  useEffect(() => { if (kb.fromShare) setTab("add"); }, [kb.fromShare]);   // files shared from the phone wait here
  return (
    <div className="vdc">
      <PageHeader sub="Everything Vula has filed for you, and what it uses to answer questions."
        actions={tab !== "add" && <Button Icon={Upload} onClick={() => setTab("add")}>Add documents</Button>} />
      <PillTabs label="Documents" active={tab} onChange={setTab} tabs={TABS} />
      {tab === "library" && <FiledLibraryV2 key={kb.libraryKey} tenantId={tenantId} defaultFiledBy={defaultFiledBy} title="Filed documents & media" />}
      {tab === "add" && (
        <div className="vdc-stack">
          <KbUpload kb={kb} />
          <MediaUpload tenantId={tenantId} onUploaded={kb.bumpLibrary} />
          <DriveImport tenantId={tenantId} onImported={kb.bumpLibrary} />
          <WebsiteImport tenantId={tenantId} />
        </div>
      )}
      {tab === "tidy" && (
        <div className="vdc-stack">
          <SortIntoProjects tenantId={tenantId} />
          <DocumentsHealth tenantId={tenantId} />
          <RereadMissing tenantId={tenantId} />
          <LearnFromHistory tenantId={tenantId} />
        </div>
      )}
      {tab === "customers" && (
        <div className="vdc-stack">
          <KnowledgeVisibility tenantId={tenantId} refreshKey={kb.libraryKey} />
          <CustomerPreview tenantId={tenantId} />
          <KbFiles kb={kb} />
        </div>
      )}
    </div>
  );
}

// ── Add ──
const STATUS = { pending: ["Pending", "muted"], uploading: ["Uploading…", "warn"], queued: ["Queued", "ok"], error: ["Error", "danger"] };

function KbUpload({ kb }) {
  const ref = useRef(null);
  const [dragging, setDragging] = useState(false);
  const take = (files) => { if (files?.length && !kb.uploading) kb.onFiles(Array.from(files)); };
  return (
    <Panel Icon={FileUp} title="Upload to what Vula knows">
      <p className="vdc-sub">Vula reads each file, files it, and uses it to answer questions. It takes a few minutes a document.</p>
      <div className={`vdc-drop${dragging ? " vdc-drop--on" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); take(e.dataTransfer.files); }}>
        <Upload size={24} aria-hidden="true" />
        <span>Drag files here, or</span>
        <Button variant="secondary" onClick={() => ref.current?.click()} disabled={kb.uploading}>Choose files</Button>
        <span className="vdc-sub">PDF, DOCX, XLSX, CSV, DXF, DWG, images — max 50 MB each</span>
        <input ref={ref} type="file" multiple accept={ACCEPT} hidden tabIndex={-1} aria-hidden="true"
          onChange={(e) => { take(e.target.files); e.target.value = ""; }} />
      </div>
      <label className="vdc-check">
        <input type="checkbox" checked={kb.forCustomers} onChange={(e) => kb.setForCustomers(e.target.checked)} />
        <span><strong>Customers can see these</strong> <span className="vdc-sub">— menus, FAQs, price lists for customers. Leave off for invoices, statements and anything internal.</span></span>
      </label>
      {kb.queue.length > 0 && (
        <ul className="vdc-queue" aria-label="Files to upload">
          {kb.queue.map((item, i) => (
            <li key={i} className="vdc-qrow">
              <FileBadge ext={item.ext} />
              <span className="vdc-qname">{item.name}</span>
              <span className="vdc-sub">{item.size}</span>
              <StatusChip tone={(STATUS[item.status] || STATUS.pending)[1]}>{(STATUS[item.status] || STATUS.pending)[0]}</StatusChip>
            </li>
          ))}
        </ul>
      )}
      <div className="vdc-row">
        {kb.pendingCount > 0 && (
          <Button Icon={Upload} onClick={kb.uploadAll} disabled={kb.uploading}>
            {kb.uploading ? "Uploading…" : `Upload ${kb.pendingCount} file${kb.pendingCount > 1 ? "s" : ""}`}
          </Button>)}
        {kb.queue.length > 0 && kb.queue.every((q) => q.status !== "uploading") && (
          <Button variant="quiet" Icon={X} onClick={kb.clearQueue}>Clear list</Button>)}
      </div>
    </Panel>
  );
}

function MediaUpload({ tenantId, onUploaded }) {
  const m = useMediaUpload(tenantId, onUploaded);
  const ref = useRef(null);
  return (
    <Panel Icon={ImageIcon} title="Store photos and files">
      <p className="vdc-sub">For anything with nothing to read — a photo, a signed form, anything you just want to keep. Stored straight into the library, no AI.</p>
      <div className="vdc-row">
        <Field label="For a customer (optional)">
          <Select value={m.customerPhone} onChange={(e) => m.setCustomerPhone(e.target.value)}>
            <option value="">No customer (general)</option>
            {m.customers.map((c) => <option key={c.phone} value={c.phone}>{c.name || c.phone}</option>)}
          </Select>
        </Field>
        <Button Icon={ImageIcon} onClick={() => ref.current?.click()} disabled={m.uploading}>{m.uploading ? "Storing…" : "Add photos / files"}</Button>
        <input ref={ref} type="file" multiple hidden tabIndex={-1} aria-hidden="true" onChange={(e) => { m.handle(e.target.files); e.target.value = ""; }} />
      </div>
      <Note tone="ok">{m.msg.replace(" ✓", "")}</Note>
    </Panel>
  );
}

function DriveImport({ tenantId, onImported }) {
  const d = useDriveImport(tenantId, onImported);
  if (d.status === null) return null;   // still checking — don't flash a "connect" prompt
  if (d.status !== "connected") {
    return (
      <Panel Icon={Cloud} title="Import from Google Drive">
        <p className="vdc-sub">Connect Google in Settings to import files from Drive into this library.</p>
      </Panel>
    );
  }
  return (
    <Panel Icon={Cloud} title="Import from Google Drive">
      <p className="vdc-sub">Search files Vula can see in your Drive and pull one into the library.</p>
      <div className="vdc-row">
        <Input className="vdc-grow" aria-label="Search Drive by filename" placeholder="Search Drive by filename…" value={d.q}
          onChange={(e) => d.setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && d.search()} />
        <Button onClick={d.search} disabled={d.searching}>{d.searching ? "Searching…" : "Search"}</Button>
      </div>
      {d.results !== null && d.results.length === 0 && (
        <p className="vdc-sub">No matching files Vula can see. Files you've opened via Vula before, or created with Vula, are searchable here.</p>)}
      {d.results?.length > 0 && (
        <ul className="vdc-list">
          {d.results.map((f) => (
            <li key={f.id} className="vdc-line">
              <span className="vdc-qname">{f.name}</span>
              <Button size="sm" variant="secondary" onClick={() => d.doImport(f)} disabled={d.importingId === f.id}>{d.importingId === f.id ? "Importing…" : "Import"}</Button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function WebsiteImport({ tenantId }) {
  const w = useWebsiteImport(tenantId);
  const st = w.st;
  return (
    <Panel Icon={Globe} title="Import from your website">
      <p className="vdc-sub">Your products (prices, options, photos) go into Products, and the pages customers ask about — about, FAQ, delivery, returns, care, blog — into what Vula knows. Running it again updates them. Nothing is sent to anyone.</p>
      {st?.running ? <p className="vdc-note" role="status">Working… {st.step}</p> : (
        <div className="vdc-row">
          <Input className="vdc-grow" type="url" aria-label="Your website address" placeholder="https://yourshop.co.za" value={w.url} onChange={(e) => w.setUrl(e.target.value)} />
          <Button onClick={w.start} disabled={!w.url.trim()}>Import</Button>
        </div>
      )}
      {st && !st.running && st.step === "done" && (
        <p className="vdc-sub">
          Last import from {st.url}: {st.products_found ? `${num(st.products_found)} products (${num(st.products_created)} new, ${num(st.products_updated)} updated${st.variants ? `, ${num(st.variants)} options` : ""}${st.products_on_request ? `, ${num(st.products_on_request)} priced on request — in the catalogue, not for sale online` : ""}), ` : "no product catalogue found, "}{num(st.kb_documents)} pages added to what Vula knows{st.skipped_pages ? ` (${num(st.skipped_pages)} too short to use)` : ""}.
          {st.errors?.length ? ` ${st.errors.length} couldn't be read.` : ""}
          {st.catalogue_note ? ` (Catalogue: ${st.catalogue_note}.)` : ""}
        </p>)}
      <Note tone="danger">{st?.error ? `Stopped: ${st.error}` : ""}</Note>
      <Note>{w.msg}</Note>
    </Panel>
  );
}

// ── Tidy up ──
function SortIntoProjects({ tenantId }) {
  const s = useSortProjects(tenantId);
  if (!s.show) return null;
  const info = s.info;
  return (
    <Panel Icon={FolderInput} title="Waiting for a project">
      <p className="vdc-sub">
        {info.waiting} waiting. Vula can place {info.would_file} itself
        {Object.keys(info.by_reason || {}).length ? " — " + Object.entries(info.by_reason).map(([k, v]) => `${v} ${SORT_REASON[k] || k}`).join(", ") : ""}.
        {info.still_ask ? ` ${info.still_ask} have no clear clue and stay for you to answer.` : ""}
      </p>
      {Object.keys(info.by_project || {}).length > 0 && <p className="vdc-body">{Object.entries(info.by_project).map(([p, n]) => `${p}: ${n}`).join(" · ")}</p>}
      {info.would_file > 0 && <div className="vdc-row"><Button disabled={s.busy} onClick={() => s.apply(false)}>{s.busy ? "Filing…" : `File these ${info.would_file}`}</Button></div>}
      {Object.keys(info.suggested_by_month || {}).length > 0 && (
        <div className="vdc-row">
          <p className="vdc-sub vdc-grow">No clue in the document itself, but most of that month's project spend went to one project: {Object.entries(info.suggested_by_month).map(([p, n]) => `${n} → ${p}`).join(" · ")}.</p>
          <Button size="sm" variant="secondary" disabled={s.busy} onClick={() => s.apply(true)}>File those too</Button>
        </div>)}
      <Note>{s.msg}</Note>
    </Panel>
  );
}

function DocumentsHealth({ tenantId }) {
  const x = useDocumentsHealth(tenantId);
  const h = x.h;
  if (!h || !h.total) return null;
  const unreg = Object.entries(h.unregistered_projects || {});
  const stat = (n, label, warn) => (
    <div className="vdc-stat"><span className={`vdc-stat-n${warn && n ? " vdc-stat-n--warn" : ""}`}>{n}</span><span className="vdc-sub">{label}</span></div>
  );
  return (
    <Panel Icon={HeartPulse} title="Documents health">
      <div className="vdc-stats">
        {stat(h.total, "documents")}
        {stat(h.missing_details, "missing a detail", true)}
        {stat(h.pending_project, "waiting on a project", true)}
        {stat(h.catch_all, "not yet sorted into a type", true)}
      </div>
      {(h.samples?.missing || []).length > 0 && (
        <>
          <p className="vdc-sub">Vula couldn't read these details — they don't count in supplier spend or job costing until filled in:</p>
          <ul className="vdc-list">
            {h.samples.missing.map((d) => <li key={d.id} className="vdc-line"><span className="vdc-qname">{d.filename}</span><span className="vdc-sub">no {(d.missing || []).join(", ")}</span></li>)}
          </ul>
        </>
      )}
      {unreg.length > 0 && (
        <>
          <p className="vdc-sub">Filed under names that aren't on your project list:</p>
          <ul className="vdc-list">
            {unreg.map(([name, n]) => (
              <li key={name} className="vdc-line vdc-line--wrap">
                <span className="vdc-qname">{name} <span className="vdc-sub">— {n} document{n === 1 ? "" : "s"}</span></span>
                <span className="vdc-row">
                  <Select aria-label={`Make ${name} another name for…`} value={x.pick[name] || ""} onChange={(e) => x.setPick((p) => ({ ...p, [name]: e.target.value }))}>
                    <option value="">Another name for…</option>
                    {x.register.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </Select>
                  <Button size="sm" variant="secondary" onClick={() => x.addAlias(name)} disabled={!x.pick[name]}>Link</Button>
                  <Button size="sm" variant="secondary" onClick={() => x.createFrom(name)}>Make it a project</Button>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <Note>{x.msg}</Note>
    </Panel>
  );
}

function RereadMissing({ tenantId }) {
  const r = useReread(tenantId);
  if (!r.show) return null;
  const { info, st } = r;
  return (
    <Panel Icon={RefreshCw} title="Documents missing data">
      <p className="vdc-sub">
        {info.candidates} filed PDF{info.candidates === 1 ? "" : "s"} couldn't be read properly the first time (no type, or a missing supplier, date or amount). Vula also re-reads new ones once a day by itself.
        Vula can read them again — it fills in the details; nothing is booked.
      </p>
      {st.running ? <p className="vdc-note" role="status">Reading… {st.done}/{st.total}</p>
        : <div className="vdc-row"><Button variant="secondary" Icon={RefreshCw} onClick={r.start} disabled={!info.candidates}>Re-read them</Button></div>}
      {!st.running && st.total > 0 && (
        <p className="vdc-sub">
          Last run: {st.fixed} of {st.total} filled in{r.cats ? ` (${r.cats})` : ""}{st.stock_sheets ? `, ${st.stock_sheets} stock sheet${st.stock_sheets === 1 ? "" : "s"}` : ""}{st.failed ? `, ${st.failed} couldn't be opened` : ""}.
        </p>)}
      <Note>{r.msg}</Note>
    </Panel>
  );
}

function LearnFromHistory({ tenantId }) {
  const l = useLearn(tenantId);
  if (!l.info) return null;
  const { st, book } = l;
  return (
    <Panel Icon={BookOpen} title="Learn from your documents">
      <p className="vdc-sub">Every priced line on your invoices, quotes, BOQs and supplier price lists, and your workers' day rates, become rates you can price from (QS Rates, Quick Cost, QS Pro, Takeoff). Your own rates are never changed. Nothing is booked.</p>
      {book.items > 0 && (
        <p className="vdc-body">Price book: <strong>{num(book.items)}</strong> items from {num(book.priced_lines)} priced lines · {num(book.suppliers)} suppliers · {num(book.labour_rates)} labour rates · {num(book.projects)} projects</p>)}
      {st.running ? <p className="vdc-note" role="status">Working… {st.step}</p>
        : <div className="vdc-row"><Button variant="secondary" Icon={BookOpen} onClick={l.start}>Learn from history</Button></div>}
      {!st.running && st.step === "done" && (
        <p className="vdc-sub">
          Last run: {num(st.documents)} documents read, {num(st.priced_lines)} priced lines{st.boqs_completed ? `, ${st.boqs_completed} BOQ${st.boqs_completed === 1 ? "" : "s"} read in full` : ""}{st.price_lists_read ? `, ${st.price_lists_read} price list${st.price_lists_read === 1 ? "" : "s"} itemised` : ""}{st.stock_sheets ? `, ${st.stock_sheets} stock sheet${st.stock_sheets === 1 ? "" : "s"} stored` : ""}{st.reread_fixed ? `, ${st.reread_fixed} re-read and filled in` : ""}{st.still_unread ? `, ${st.still_unread} still unreadable` : ""}.
        </p>)}
      <Note tone="danger">{st.error ? `Stopped: ${st.error}` : ""}</Note>
      <Note>{l.msg}</Note>
    </Panel>
  );
}

// ── What customers see ──
function KnowledgeVisibility({ tenantId, refreshKey }) {
  const k = useKnowledgeVisibility(tenantId, refreshKey);
  if (!k.docs || !k.docs.length) return null;
  const docs = k.docs;
  const pub = docs.filter((d) => d.audience === "public").length;
  const unset = docs.filter((d) => d.audience === "untagged" || d.audience === "mixed").length;
  const shown = k.showAll ? docs : docs.slice(0, 15);
  return (
    <Panel Icon={Eye} title="Who can see your documents">
      <p className="vdc-sub">
        Customers chatting on WhatsApp and your public web page only use documents marked “Customers can see”. Invoices, statements and staff documents stay with your team.
        {" "}{pub} of {docs.length} are visible to customers.
        {unset > 0 && !k.strict && (unset === 1 ? " 1 isn't set yet and is still visible to customers until it's sorted." : ` ${unset} aren't set yet and are still visible to customers until they're sorted.`)}
      </p>
      <ul className="vdc-list">
        {shown.map((d) => (
          <li key={d.doc_id} className="vdc-line vdc-line--wrap">
            <span className="vdc-qname">{d.filename}</span>
            <span className="vdc-row">
              <Audience audience={d.audience} />
              {d.audience === "public"
                ? <Button size="sm" variant="secondary" disabled={k.busy === d.doc_id} onClick={() => k.setAudience(d, "staff")}>Team only</Button>
                : <Button size="sm" variant="secondary" disabled={k.busy === d.doc_id} onClick={() => k.setAudience(d, "public")}>Let customers see it</Button>}
            </span>
          </li>
        ))}
      </ul>
      {docs.length > 15 && <div className="vdc-row"><Button size="sm" variant="quiet" onClick={() => k.setShowAll((v) => !v)}>{k.showAll ? "Show fewer" : `Show all ${docs.length}`}</Button></div>}
      <Note>{k.msg}</Note>
    </Panel>
  );
}

function CustomerPreview({ tenantId }) {
  const c = useCustomerPreview(tenantId);
  const res = c.res;
  return (
    <Panel Icon={ShoppingBag} title="Ask as a customer">
      <div className="vdc-row">
        <Input className="vdc-grow" aria-label="A customer's question" placeholder="e.g. do you deliver on Saturdays?" value={c.q}
          onChange={(e) => c.setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && c.run()} />
        <Button variant="secondary" onClick={c.run} disabled={c.loading}>{c.loading ? "Checking…" : c.q.trim() ? "Search as a customer" : "List what they can see"}</Button>
      </div>
      {res && (
        <>
          <p className="vdc-sub">
            {res.visible_documents.length} document{res.visible_documents.length === 1 ? "" : "s"} visible to customers
            {!res.strict && res.visible_documents.some((d) => d.audience !== "public") && " (including some not set yet)"}:
          </p>
          <ul className="vdc-list">
            {res.visible_documents.map((d) => <li key={d.doc_id} className="vdc-line"><span className="vdc-qname">{d.filename}</span><Audience audience={d.audience} /></li>)}
          </ul>
          {c.q.trim() && (
            <>
              <p className="vdc-sub">{res.results.length ? "A customer asking that would get:" : "A customer asking that would get nothing from your documents."}</p>
              {res.results.map((h, i) => (
                <div key={i} className="vdc-hit">
                  <div className="vdc-row"><strong className="vdc-qname">{h.filename}</strong><Audience audience={h.audience} /></div>
                  <p className="vdc-excerpt">{h.excerpt}</p>
                </div>
              ))}
            </>
          )}
        </>
      )}
    </Panel>
  );
}

function KbFiles({ kb }) {
  return (
    <Card pad={0} className="vdc-lib">
      <div className="vdc-lib-head">
        <h3 className="vdc-h">Knowledge base files</h3>
        <span className="vdc-row">
          <span className="vdc-sub">{kb.docs.length} {kb.docs.length === 1 ? "file" : "files"}</span>
          <IconButton Icon={RefreshCw} quiet label="Refresh" onClick={kb.load} disabled={kb.loading} />
        </span>
      </div>
      {kb.error && <p className="vdc-empty vdc-note--danger">Couldn't load the files ({kb.error}).</p>}
      {!kb.error && !kb.loading && kb.docs.length === 0 && (
        <EmptyState Icon={FileUp} title="Nothing here yet">Nothing has been read into what Vula knows yet — add documents under Add.</EmptyState>)}
      {kb.docs.length > 0 && (
        <ul className="vdc-list">
          {kb.docs.map((d) => (
            <li key={d.filename} className="vdc-line vdc-line--pad">
              <FileBadge ext={d.type} />
              <span className="vdc-qname vdc-grow">{d.filename}</span>
              <span className="vdc-sub vdc-mono">{formatSize(d.size_kb)}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
