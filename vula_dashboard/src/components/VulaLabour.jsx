/**
 * VulaLabour.jsx — 👷 casual-labour register + labour-paid report.
 * Load day/week workers (name/ID/bank/rate/project); Vula recognises their payments off the bank
 * statement and files them as casual labour. Backend: /v1/commerce/{tenant}/admin/workers + reports/labour.
 */
import LabourV2 from "./construction/LabourV2";

export default function VulaLabour({ ...props }) {
  return <LabourV2 {...props} />;
}
