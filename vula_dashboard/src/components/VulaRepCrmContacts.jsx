/**
 * VulaRepCrmContacts.jsx — a sales rep's own contacts (commerce_contacts, created_by-scoped).
 * Distinct from VulaContacts.jsx, which is an unrelated email-derived contact directory.
 */
import RepContactsV2 from "./rep/RepContactsV2";

export default function VulaRepCrmContacts({ ...props }) {
  return <RepContactsV2 {...props} />;
}
