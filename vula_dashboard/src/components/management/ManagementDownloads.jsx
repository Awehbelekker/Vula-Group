/**
 * ManagementDownloads.jsx — the month's management accounts as PDF or Excel (23 Nov 2026), built and
 * read back on the server (vula_mind/vula/commerce/management_pack.py); a refusal's reason is shown.
 */
import { useState } from "react";
import { FileSpreadsheet, FileText } from "lucide-react";
import { saveFrom } from "../documents/FinanceFolders.jsx";
import { Button } from "../ui/index.jsx";
import { reportUrl } from "./useManagementAccounts.js";
import "./management.css";

/** The pack as files, for one month (used here and in the accountant's workspace). */
export function ManagementDownloads({ tenantId, month }) {
  const [busy, setBusy] = useState("");
  const get = async (kind) => {
    setBusy(kind);
    await saveFrom(reportUrl(tenantId, kind, month), `Management_accounts_${month || "month"}.${kind}`);
    setBusy("");
  };
  return (
    <div className="vma-actions">
      <Button Icon={FileText} disabled={!!busy} onClick={() => get("pdf")}>
        {busy === "pdf" ? "Making the PDF…" : "Download PDF"}
      </Button>
      <Button Icon={FileSpreadsheet} variant="secondary" disabled={!!busy} onClick={() => get("xlsx")}>
        {busy === "xlsx" ? "Making the workbook…" : "Download Excel"}
      </Button>
    </div>
  );
}
