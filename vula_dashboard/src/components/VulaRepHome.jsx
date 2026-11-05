/**
 * VulaRepHome.jsx — the sales rep's landing page: due reminders, this week's call sheet status,
 * a nudge into the other My Work tabs. Replaces the tenant-wide owner overview for a restricted
 * sales_rep dashboard login (see VulaMerchantAdmin.jsx's overview branch).
 */
import RepHomeV2 from "./rep/RepHomeV2";

export default function VulaRepHome({ ...props }) {
  return <RepHomeV2 {...props} />;
}
