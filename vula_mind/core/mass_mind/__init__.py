"""core/mass_mind — the platform-wide layer above core/memory/reflection.py's per-tenant store.

Two rules hold everywhere in this package (see the Mass Mind design doc, shared separately):
only outcome/health telemetry ever crosses the tenant boundary here — never raw customer
content, prices, or names — and every write/read fails open. A Mass Mind signal is always a
secondary safety net, never something a tenant-facing request depends on to succeed.
"""
