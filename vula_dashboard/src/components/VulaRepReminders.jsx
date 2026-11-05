/**
 * VulaRepReminders.jsx — a sales rep's own reminders (vula_reminders, created_by-scoped).
 * First dashboard surface for vula_reminders at all — previously WhatsApp-only.
 */
import RepRemindersV2 from "./rep/RepRemindersV2";

export default function VulaRepReminders({ ...props }) {
  return <RepRemindersV2 {...props} />;
}
