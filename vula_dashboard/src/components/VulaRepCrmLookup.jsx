/**
 * VulaRepCrmLookup.jsx — search Dynamics 365 accounts/contacts/opportunities from the dashboard.
 * Shows the connect flow if not yet connected; a search box + results once it is.
 */
import RepCrmLookupV2 from "./rep/RepCrmLookupV2";

export default function VulaRepCrmLookup({ ...props }) {
  return <RepCrmLookupV2 {...props} />;
}
