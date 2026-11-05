/**
 * VulaRepCallSheet.jsx — view/edit/configure the rep's persistent weekly call sheet
 * (vula/commerce/call_sheet.py). Thin wrapper — reuses the same compose/edit logic the
 * WhatsApp configure_call_sheet/view_call_sheet/update_call_sheet tools already use.
 */
import RepCallSheetV2 from "./rep/RepCallSheetV2";

export default function VulaRepCallSheet({ ...props }) {
  return <RepCallSheetV2 {...props} />;
}
