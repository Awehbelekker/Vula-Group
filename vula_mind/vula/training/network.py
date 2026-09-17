"""
vula/training/network.py

Cross-tenant "network" knowledge collection — same synthetic-tenant-id trick as
vula/training/content.py (TRAINING_TENANT_ID) and vula/training/business_content.py
(BUSINESS_TRAINING_TENANT_ID): VulaIngestionPipeline(tenant_id=NETWORK_TENANT_ID) resolves to
its own Qdrant collection, read by every tenant's reasoning.py/architecture_planning.py the
same way the developer-authored training corpora already are.

Unlike those two, this collection has NO static seed content — it grows only from content a
tenant explicitly chose to share. Per the product owner (voice, 2026-09-17): "being able to
learn and share knowledge between businesses on Vula... if the client or tenant is willing to
share, could be great." Explicitly gated on that tenant's own opt-in
(vula_tenant_config.share_knowledge_with_network, migration 164) — a tenant's approved learned
answer only ever lands here via vula/api/master.py's promote endpoint with target="network",
which checks that flag before ingesting. No tenant content reaches another tenant without the
source tenant having turned this on.
"""
from __future__ import annotations

NETWORK_TENANT_ID = "vula_network"
