/**
 * VulaPipeline.jsx — the deals pipeline board (2026-11-10, `pipeline` module), any business type.
 * Data and actions live in pipeline/usePipeline.js; the screen is pipeline/PipelineV2.jsx.
 */
import PipelineV2 from "./pipeline/PipelineV2";

export default function VulaPipeline({ tenantId }) {
  return <PipelineV2 tenantId={tenantId} />;
}
