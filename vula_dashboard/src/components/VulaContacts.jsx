/**
 * VulaContacts.jsx — the email-derived contact / supplier / co-worker library (KM).
 * Built automatically from the connected mailbox; re-tag any contact; sync on demand.
 * Data and actions live in contacts/useContacts.js (shared with contacts/ContactsV2.jsx).
 */
import ContactsV2 from "./contacts/ContactsV2";

/** v2: the redesigned screen (same hook, so the same data and actions). */
export default function VulaContacts({ tenantId }) {
  return <ContactsV2 tenantId={tenantId} />;
}

