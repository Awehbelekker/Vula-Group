/**
 * SendDocument.jsx — send one filed document to a client, a supplier, an email address or a WhatsApp
 * number (20 Nov 2026, Ian: "it would be nice if Vula can send this to a client when asked").
 * Preview first — the server says which file, to whom and how (vula/commerce/doc_send.py) — then Send.
 * Nothing leaves until Send. POST /v1/documents/{t}/filed/{id}/send {to, channel, note, confirm}.
 */
import { useState } from "react";
import { Send } from "lucide-react";
import { Button, Field, Input, Modal, Select, toast } from "../ui/index.jsx";
import { VULA_API } from "../../lib/authFetch";

async function post(tenantId, docId, body) {
  const r = await fetch(`${VULA_API}/v1/documents/${tenantId.trim()}/filed/${docId}/send`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "That didn't go through — please try again.");
  return d;
}

export default function SendDocument({ tenantId, doc }) {
  const [open, setOpen] = useState(false);
  const [to, setTo] = useState("");
  const [channel, setChannel] = useState("");
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const body = (confirm) => ({ to, channel: channel || null, note: note || null, confirm });
  const close = () => { setOpen(false); setPreview(null); setError(""); };
  const look = async () => {
    setBusy(true); setError("");
    try { setPreview(await post(tenantId, doc.id, body(false))); }
    catch (e) { setPreview(null); setError(e.message); }
    finally { setBusy(false); }
  };
  const send = async () => {
    setBusy(true); setError("");
    try {
      const d = await post(tenantId, doc.id, body(true));
      toast(d.reply_verbatim || "Sent.", d.sent ? "ok" : "warn");
      close();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  return (
    <>
      <Button size="sm" variant="secondary" Icon={Send} onClick={() => setOpen(true)} aria-label={`Send ${doc.filename}`}>Send</Button>
      <Modal open={open} title="Send this document" onClose={close} width={480}
        actions={<>
          <Button variant="ghost" onClick={close}>Cancel</Button>
          {preview
            ? <Button Icon={Send} onClick={send} disabled={busy}>{busy ? "Sending…" : "Send"}</Button>
            : <Button onClick={look} disabled={busy || !to.trim()}>{busy ? "Checking…" : "Preview"}</Button>}
        </>}>
        <p className="vdc-sub" style={{ marginTop: 0 }}>{doc.filename}</p>
        <Field style={{ marginBottom: 12 }} label="To" hint="A client or supplier's name, an email address or a WhatsApp number">
          <Input value={to} onChange={(e) => { setTo(e.target.value); setPreview(null); }} autoFocus />
        </Field>
        <Field style={{ marginBottom: 12 }} label="Send by">
          <Select value={channel} onChange={(e) => { setChannel(e.target.value); setPreview(null); }}>
            <option value="">Email if we have it, else WhatsApp</option>
            <option value="email">Email</option>
            <option value="whatsapp">WhatsApp</option>
          </Select>
        </Field>
        <Field label="Note (optional)">
          <Input value={note} onChange={(e) => { setNote(e.target.value); setPreview(null); }} />
        </Field>
        {preview?.message && <p role="status" className="vdc-send-preview">{preview.message}</p>}
        {error && <p role="alert" className="vdc-send-error">{error}</p>}
      </Modal>
    </>
  );
}
