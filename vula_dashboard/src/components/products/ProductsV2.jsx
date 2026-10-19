/**
 * ProductsV2.jsx — the redesigned Products list (2026-10-18, UX redesign Phase F). VulaMerchantAdmin's
 * ProductsTab keeps all its state and actions; under v2 only the list is drawn here, through the
 * same functions (`api`). Products stay grouped by category; each row has the price (edited in
 * place, saved on blur / Enter, integer cents), the in-stock switch and the catch-of-the-day star.
 * "Edit" opens the full editor — the current ProductEditPanel, passed in — in a Sheet (it closes
 * on its own once a deleted product leaves the list).
 */
import { useMemo, useState } from "react";
import { Download, Package, Pencil, Plus, Star, Tags, Upload, X } from "lucide-react";
import { formatMoney } from "../../lib/money";
import {
  Button, Card, DataTable, EmptyState, Field, FilterBar, IconButton, Input, PageHeader, Select, Sheet, Skeleton, StatusChip, Textarea,
} from "../ui/index.jsx";
import "./products.css";

function Row({ p, api, onEdit }) {
  const busy = api.saving === p.id;
  const editing = api.editPrice[p.id] !== undefined;
  return (
    <div className={`vpr-row${p.in_stock ? "" : " vpr-row--out"}`} role="listitem">
      <span className="vpr-thumb" aria-hidden="true">
        {p.image_url ? <img src={p.image_url} alt="" loading="lazy" /> : (p.name || "?").charAt(0).toUpperCase()}
      </span>
      <span className="vpr-main">
        <span className="vpr-name">{p.name}</span>
        <span className="vpr-chips">
          {p.is_daily_catch && <StatusChip tone="highlight"><Star size={12} aria-hidden="true" />Catch of the day</StatusChip>}
          {!p.in_stock && <StatusChip tone="warm">Out of stock</StatusChip>}
          {p.stock_quantity != null && p.in_stock && <span className="vpr-sub">{p.stock_quantity} on hand</span>}
        </span>
      </span>
      <label className="vpr-price">
        <span className="vpr-sr">Price for {p.name}, in rand</span>
        <span aria-hidden="true" className="vpr-sub">R</span>
        <Input inputMode="decimal" value={editing ? api.editPrice[p.id] : (p.price_cents / 100).toFixed(2)}
          onChange={(e) => api.setEditPrice((prev) => ({ ...prev, [p.id]: e.target.value }))}
          onBlur={() => api.savePrice(p)} onKeyDown={(e) => e.key === "Enter" && api.savePrice(p)} />
        <span className={`vpr-sub${p.sold_by === "kg" ? "" : " vpr-unit"}`}>/{p.sold_by === "kg" ? "kg" : "pack"}</span>
      </label>
      <span className="vpr-actions">
        <button type="button" className="vpr-switch" role="switch" aria-checked={!!p.in_stock} disabled={busy}
          aria-label={`${p.name} in stock`} onClick={() => api.patch(p.id, { in_stock: !p.in_stock })}>
          <span className="vpr-switch-knob" aria-hidden="true" />
          <span className="vpr-switch-label">{busy ? "…" : p.in_stock ? "In stock" : "Out"}</span>
        </button>
        <IconButton Icon={Star} quiet label={p.is_daily_catch ? `Remove ${p.name} as catch of the day` : `Mark ${p.name} catch of the day`}
          aria-pressed={!!p.is_daily_catch} className="vpr-star" disabled={busy}
          onClick={() => api.patch(p.id, { is_daily_catch: !p.is_daily_catch })} />
        <Button size="sm" variant="secondary" Icon={Pencil} className="vpr-edit-btn" aria-label={`Edit ${p.name}`} onClick={() => onEdit(p.id)}><span className="vpr-edit-label">Edit</span></Button>
      </span>
    </div>
  );
}

export default function ProductsV2({ api, EditPanel }) {
  const [query, setQuery] = useState("");
  const [cat, setCat] = useState(null);
  const [editId, setEditId] = useState(null);
  const editing = api.products.find((p) => p.id === editId) || null;

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = {};
    for (const p of api.products) {
      if (q && !(p.name || "").toLowerCase().includes(q)) continue;
      const c = p.category || "extras";
      if (cat && c !== cat) continue;
      (out[c] = out[c] || []).push(p);
    }
    return Object.entries(out);
  }, [api.products, query, cat]);
  const usedCats = [...new Set(api.products.map((p) => p.category || "extras"))];

  const importCols = [
    { key: "n", label: "#", render: (r) => <span className="vpr-sub">{r.__i + 1}</span> },
    { key: "name", label: "Name", render: (r) => r.name || <span className="vpr-bad">missing</span> },
    { key: "price_cents", label: "Price (cents)", align: "right", render: (r) => r.price_cents || <span className="vpr-bad">missing</span> },
    { key: "category", label: "Category", render: (r) => <>{r.category || "extras"}{r.category && !api.catKeys.includes(r.category) && <span className="vpr-sub"> (new)</span>}</> },
    { key: "status", label: "Status", render: (r) => {
      const errs = api.validateImportRow(r);
      return errs.length ? <StatusChip tone="danger">{errs.join(", ")}</StatusChip> : <StatusChip tone="ok">OK</StatusChip>;
    } },
  ];

  return (
    <div>
      <PageHeader sub="Everything you sell, by category. Prices save as you leave the box."
        actions={<>
          <Button size="sm" variant="secondary" Icon={Tags} onClick={() => api.setShowCats((s) => !s)}>Categories</Button>
          <Button size="sm" variant="secondary" Icon={Download} onClick={api.exportCsv} disabled={!api.products.length}>Export CSV</Button>
          <Button size="sm" variant="secondary" Icon={Upload} onClick={() => api.fileInputRef.current?.click()}>Import CSV</Button>
          <input ref={api.fileInputRef} type="file" accept=".csv,text/csv" onChange={api.onImportFile} hidden tabIndex={-1} aria-hidden="true" />
          {!api.showAdd && <Button size="sm" Icon={Plus} onClick={() => api.setShowAdd(true)}>Add product</Button>}
        </>} />

      {api.showAdd && (
        <Card className="vpr-block">
          <form onSubmit={api.createProduct} className="vpr-form">
            <Field label="Product name"><Input required value={api.form.name} onChange={(e) => api.setForm({ ...api.form, name: e.target.value })} /></Field>
            <Field label="Price (R)"><Input required type="number" step="0.01" inputMode="decimal" value={api.form.price} onChange={(e) => api.setForm({ ...api.form, price: e.target.value })} /></Field>
            <Field label="Sold by">
              <Select value={api.form.sold_by} onChange={(e) => api.setForm({ ...api.form, sold_by: e.target.value })}>
                <option value="pack">per pack / item</option><option value="kg">per kg</option>
              </Select>
            </Field>
            <Field label="Category">
              <Select value={api.form.category} onChange={(e) => api.setForm({ ...api.form, category: e.target.value })}>
                {api.catKeys.map((c) => <option key={c} value={c}>{api.catLabel(c)}</option>)}
              </Select>
            </Field>
            <Field label="Description (optional)" style={{ gridColumn: "1 / -1" }}>
              <Textarea rows={2} value={api.form.description} onChange={(e) => api.setForm({ ...api.form, description: e.target.value })} />
            </Field>
            <div className="vpr-form-go">
              <Button type="submit" Icon={Plus} disabled={api.adding}>{api.adding ? "Adding…" : "Add product"}</Button>
              <Button type="button" variant="secondary" onClick={() => api.setShowAdd(false)}>Cancel</Button>
              <span className="vpr-sub">Add a photo afterwards, under Edit.</span>
            </div>
          </form>
        </Card>
      )}

      {api.showCats && (
        <Card className="vpr-block">
          <h2 className="vpr-h"><Tags size={18} aria-hidden="true" />Your categories</h2>
          <div className="vpr-cats">
            {(api.categories.length ? api.categories : api.catKeys.map((k) => ({ key: k, label: api.catLabel(k) }))).map((c) => (
              <span key={c.key} className="vpr-cat">{c.label}</span>
            ))}
          </div>
          <div className="vpr-cat-add">
            <Input placeholder="New category (e.g. Smoked Fish)" aria-label="New category" value={api.newCat}
              onChange={(e) => api.setNewCat(e.target.value)} onKeyDown={(e) => e.key === "Enter" && api.addCategory()} />
            <Button variant="secondary" onClick={api.addCategory}>Add</Button>
          </div>
        </Card>
      )}

      {api.importRows && (
        <Card className="vpr-block">
          <h2 className="vpr-h"><Upload size={18} aria-hidden="true" />Import preview — {api.importRows.length} row{api.importRows.length === 1 ? "" : "s"}</h2>
          <DataTable columns={importCols} rows={api.importRows.map((r, i) => ({ ...r, __i: i }))} rowKey="__i" caption="Import preview" />
          {api.importResults ? (
            <p className="vpr-result" role="status">
              {api.importResults.created} created · {api.importResults.updated} updated
              {api.importResults.errors > 0 && <span className="vpr-bad"> · {api.importResults.errors} failed</span>}
              {api.importResults.results?.filter((r) => r.status === "error").map((r, i) => (
                <span key={i} className="vpr-bad vpr-block-line">Row {r.row + 1} ({r.slug}): {r.error}</span>
              ))}
            </p>
          ) : null}
          <div className="vpr-form-go">
            {!api.importResults && (
              <Button onClick={api.commitImport} disabled={api.importing || api.importRows.every((r) => api.validateImportRow(r).length)}>
                {api.importing ? "Importing…" : `Import ${api.importRows.length} product${api.importRows.length === 1 ? "" : "s"}`}
              </Button>)}
            <Button variant="secondary" Icon={X} onClick={api.closeImport}>{api.importResults ? "Close" : "Cancel"}</Button>
          </div>
        </Card>
      )}

      <FilterBar query={query} onQuery={setQuery} placeholder="Search products" label="Category"
        chips={usedCats.length > 1 ? usedCats.map((c) => ({ id: c, label: api.catLabel(c) })) : []}
        selected={cat ? [cat] : []} onToggle={(id) => setCat(cat === id ? null : id)} />

      {api.loading ? <Skeleton lines={6} height={20} /> : api.products.length === 0 ? (
        <EmptyState Icon={Package} title="No products yet">Add your first product, or import a CSV.</EmptyState>
      ) : groups.length === 0 ? (
        <EmptyState Icon={Package} title="No products match">Try another search or clear the filter.</EmptyState>
      ) : groups.map(([c, items]) => (
        <section key={c} className="vpr-group" aria-label={api.catLabel(c)}>
          <h2 className="vpr-cat-h">{api.catLabel(c)} <span className="vpr-sub">{items.length}</span></h2>
          <Card pad={0} role="list">
            {items.map((p) => <Row key={p.id} p={p} api={api} onEdit={setEditId} />)}
          </Card>
        </section>
      ))}

      <Sheet open={!!editing} title={editing ? editing.name : ""} onClose={() => setEditId(null)}>
        {editing && (
          <div className="vpr-edit">
            <p className="vpr-sub">{formatMoney(editing.price_cents, { cents: "always" })} /{editing.sold_by === "kg" ? "kg" : "pack"} · {api.catLabel(editing.category || "extras")}</p>
            <EditPanel tenantId={api.tenantId} product={editing} patch={api.patch} saving={api.saving}
              deleteProduct={api.deleteProduct} catKeys={api.catKeys} catLabel={api.catLabel} />
          </div>
        )}
      </Sheet>
    </div>
  );
}
