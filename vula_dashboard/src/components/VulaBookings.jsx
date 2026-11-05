/**
 * VulaBookings.jsx — appointments/scheduling for health/wellness/services tenants.
 * Day view of bookings, quick-book into a free slot, service types, and availability rules.
 * Backend: /v1/bookings/{tenant} (migration 050).
 * Data and actions live in bookings/useBookings.js (shared with bookings/BookingsV2.jsx).
 */
import BookingsV2 from "./bookings/BookingsV2";

/** v2: the redesigned screen (same hook, so the same data and actions). */
export default function VulaBookings({ tenantId }) {
  return <BookingsV2 tenantId={tenantId} />;
}

