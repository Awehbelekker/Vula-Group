/**
 * VulaTakeoff.jsx — Estimating › Takeoff.
 *
 * 2026-10-31 (owner's call): the old page is replaced by TakeoffV2 for everyone. The old page had
 * no review step, so a takeoff held for review ("check these quantities before pricing") could
 * never be approved from it, and it still showed the items guessed from floor area that the
 * server no longer prices. One page, estimating/TakeoffV2.jsx, on estimating/useTakeoff.js.
 */
import TakeoffV2 from "./estimating/TakeoffV2";

// eslint-disable-next-line no-unused-vars
export default function VulaTakeoff({ v2 = false, ...props }) {
  return <TakeoffV2 {...props} />;
}
