"""scripts/sync_meta_catalog.py — push a tenant's products into its connected WhatsApp (Meta
Commerce Manager) catalogue from the command line. The work is vula/commerce/meta_catalog.py, which
Master › tenant › WhatsApp catalogue and every website import / Shopify sync also use.

Usage:
    railway run python scripts/sync_meta_catalog.py <tenant_id> [catalog_id]
"""
from __future__ import annotations

import asyncio
import sys


async def _main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    from vula.commerce.meta_catalog import sync
    print(await sync(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))


if __name__ == "__main__":
    asyncio.run(_main())
