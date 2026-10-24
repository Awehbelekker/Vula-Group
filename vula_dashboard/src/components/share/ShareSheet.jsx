/**
 * ShareSheet — what to do with files shared into Vula from the phone's Share sheet (2026-10-24).
 *
 * public/sw.js keeps the shared photos / PDFs on the device and opens #/share; this asks what they
 * are and hands them to the screen that deals with them. The Scanner reads a photo and still asks
 * before anything is booked; Documents queues the files and still waits for Upload. Only screens
 * this login may use are offered.
 */
import { useEffect, useState } from "react";
import { FileText, FolderUp, ScanLine } from "lucide-react";
import { EmptyState, ListRow, Sheet } from "../ui/index.jsx";
import { handOff, readSharedFiles } from "../../lib/sharedFiles";
import "./share.css";

const sizeOf = (b) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

export default function ShareSheet({ canOpen, open, onClose }) {
  const [files, setFiles] = useState(null);
  useEffect(() => { readSharedFiles().then(setFiles); }, []);

  const go = (to, page, list) => { handOff(to, list); open(page); onClose(); };
  const photos = (files || []).filter((f) => f.type.startsWith("image/"));
  const choices = [
    photos.length === 1 && canOpen("scanner") && { id: "scan", Icon: ScanLine, title: "Scan it",
      detail: "A receipt, supplier bill or delivery note: Vula reads it, you check it before it's booked",
      onClick: () => go("scan", "scanner", photos) },
    canOpen("documents") && { id: "docs", Icon: FolderUp, title: "Save to Documents",
      detail: "Ready to upload, so Vula can file it and answer questions from it",
      onClick: () => go("documents", "documents", files) },
  ].filter(Boolean);

  return (
    <Sheet open title="Shared with Vula" onClose={onClose}>
      {files === null ? <p className="vsh-muted">Opening what you shared…</p>
        : !files.length ? (
          <EmptyState Icon={FileText} title="Nothing came through">
            Try sharing it again. You can also forward it to your business's WhatsApp number.
          </EmptyState>
        ) : (
          <>
            <ul className="vsh-files" aria-label="Shared files">
              {files.map((f, i) => <li key={i}><span className="vsh-name">{f.name}</span><span className="vsh-muted">{sizeOf(f.size)}</span></li>)}
            </ul>
            {choices.length
              ? choices.map((c) => <ListRow key={c.id} Icon={c.Icon} title={c.title} detail={c.detail} onClick={c.onClick} />)
              : <p className="vsh-muted">This login can't scan or file documents. Forward it to your business's WhatsApp number instead.</p>}
          </>
        )}
    </Sheet>
  );
}
