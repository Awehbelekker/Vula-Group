THE TASK — buying from a supplier.

1. What's needed: reorder_suggestions, grouped by supplier.
2. A purchase order: create_purchase_order with the supplier and each line — a draft, previewed first.
3. Sending it commits real spend: send_purchase_order only after the owner has seen it and said yes, by email or
   WhatsApp as they choose.
4. When it arrives: update_po_status to received (previewed) — that adds the lines to stock.
5. The supplier must exist (list_suppliers); if not, ask for their details first.
