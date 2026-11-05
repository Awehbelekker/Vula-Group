/**
 * VulaImport.jsx — 📥 historical onboarding: paste/upload old WhatsApp chats or emails, Vula
 * extracts the past orders, you review + edit, then commit them into the system.
 * Backend: /v1/commerce/{tenant}/admin/import/extract + /commit.
 */
import ImportV2 from "./import/ImportV2";

export default function VulaImport({ ...props }) {
  return <ImportV2 {...props} />;
}

