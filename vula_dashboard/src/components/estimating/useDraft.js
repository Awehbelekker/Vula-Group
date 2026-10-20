/**
 * useDraft.js — Vula Draft (shared by VulaDraft and DraftV2): generate a construction document
 * (fee proposal, scope of works, minutes…) from a brief and the tenant's knowledge base. Moved
 * verbatim out of VulaDraft.jsx.
 */
import { useState } from "react";
import { VULA_API as API } from "../../lib/authFetch";

export const DOC_TYPES = [
  { id: "fee_proposal", label: "Fee Proposal" },
  { id: "scope_of_works", label: "Scope of Works" },
  { id: "appointment_letter", label: "Letter of Appointment" },
  { id: "site_meeting_minutes", label: "Site Meeting Minutes" },
  { id: "tender_invitation", label: "Tender Invitation" },
  { id: "project_programme", label: "Project Programme" },
  { id: "payment_certificate", label: "Payment Certificate" },
];

export function useDraft({ tenantId: tenantIdProp }) {
  const [tenantIdInput, setTenantIdInput] = useState("");
  const tenantId = tenantIdProp || tenantIdInput;
  const [docType, setDocType] = useState("fee_proposal");
  const [brief, setBrief] = useState("");
  const [projectName, setProjectName] = useState("");
  const [clientName, setClientName] = useState("");
  const [projectValue, setProjectValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function generate() {
    if (!brief.trim() || !tenantId) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch(`${API}/v1/draft/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tenant_id: tenantId,
          document_type: docType,
          brief,
          project_name: projectName || undefined,
          client_name: clientName || undefined,
          project_value: projectValue ? Number(projectValue) : undefined,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Generation failed");
      setResult(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function copyToClipboard() {
    if (result?.content) navigator.clipboard.writeText(result.content);
  }
  return { tenantIdInput, setTenantIdInput, tenantId, docType, setDocType, brief, setBrief, projectName, setProjectName, clientName, setClientName, projectValue, setProjectValue, loading, setLoading, result, setResult, error, setError, generate, copyToClipboard };
}
