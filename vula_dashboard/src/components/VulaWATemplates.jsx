/**
 * VulaWATemplates.jsx — 📨 WhatsApp template manager: create, submit, and track approval status
 * for the templates Vula needs to message people outside the 24h conversation window (proactive
 * notifications, opt-in campaigns). Backend: /v1/commerce/{tenant}/admin/wa-templates.
 */
import WATemplatesV2 from "./marketing/WATemplatesV2";

export default function VulaWATemplates({ ...props }) {
  return <WATemplatesV2 {...props} />;
}

