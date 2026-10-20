/**
 * RepContactsV2.jsx — the redesigned My Work › Contacts (2026-10-20, UX redesign Phase F). Same
 * calls as VulaRepCrmContacts (rep/useRepContacts.js): the rep's own contacts, saved on WhatsApp
 * (log_meeting, create_contact) or added here. Adding opens a Sheet.
 */
import { useState } from "react";
import { Check, Contact, Plus, Search } from "lucide-react";
import { Button, Card, EmptyState, Field, Input, ListRow, Sheet, Textarea } from "../ui/index.jsx";
import { emptyContact, useRepContacts } from "./useRepContacts";
import "./rep.css";

const initials = (name) => String(name || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

export default function RepContactsV2({ tenantId, repPhone }) {
  const x = useRepContacts(tenantId, repPhone);
  const [open, setOpen] = useState(false);
  const set = (k) => (e) => x.setForm((f) => ({ ...f, [k]: e.target.value }));
  const close = () => { setOpen(false); x.setMsg(""); x.setForm(emptyContact()); };
  const save = async () => { if (await x.save()) setOpen(false); };
  return (
    <section className="vrp" aria-label="My contacts">
      <p className="vrp-sub">Contacts you've saved on WhatsApp (after a meeting, or "save a contact") or added here.</p>
      <div className="vrp-row">
        <span className="vrp-grow"><Input type="search" value={x.search} onChange={(e) => x.setSearch(e.target.value)}
          aria-label="Search contacts" placeholder="Search by name…" /></span>
        <Button Icon={Plus} onClick={() => setOpen(true)}>Add contact</Button>
      </div>
      {x.contacts.length === 0
        ? <EmptyState Icon={x.search ? Search : Contact} title={x.search ? "No matches" : "No contacts yet"}>
            {x.search ? "Try another name." : "Log a meeting on WhatsApp, or add one here."}</EmptyState>
        : <Card pad={4}>
            <ul className="vrp-list" aria-label="Contacts">
              {x.contacts.map((c) => {
                const phone = c.phone && !c.phone.startsWith("nophone") ? c.phone : "";
                const line = [[c.title, c.company].filter(Boolean).join(" · "), phone, c.email].filter(Boolean).join(" · ");
                return (
                  <li key={c.id || c.phone}>
                    <ListRow initials={initials(c.name)} title={c.name}
                      detail={[line, c.notes].filter(Boolean).join(" — ") || undefined} />
                  </li>);
              })}
            </ul>
          </Card>}
      <Sheet open={open} onClose={close} title="Add a contact"
        footer={<>
          <Button variant="quiet" onClick={close}>Cancel</Button>
          <Button Icon={Check} onClick={save}>Save contact</Button>
        </>}>
        <form className="vrp-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <Field label="Name"><Input value={x.form.name} onChange={set("name")} required autoFocus /></Field>
          <div className="vrp-grid">
            <Field label="Phone"><Input type="tel" value={x.form.phone} onChange={set("phone")} /></Field>
            <Field label="Email"><Input type="email" value={x.form.email} onChange={set("email")} /></Field>
            <Field label="Company"><Input value={x.form.company} onChange={set("company")} /></Field>
            <Field label="Title"><Input value={x.form.title} onChange={set("title")} /></Field>
          </div>
          <Field label="Notes"><Textarea rows={2} value={x.form.notes} onChange={set("notes")} /></Field>
          {x.msg && <p className="vrp-error" role="alert">{x.msg}</p>}
        </form>
      </Sheet>
    </section>
  );
}
