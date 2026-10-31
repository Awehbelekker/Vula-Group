THE TASK — the bank and money received.

1. bank_payments searches the statement lines by name and says up to which date the statements go. Quote that date,
   so the owner knows if a newer statement is needed (they upload it under Bank in the dashboard).
2. A customer payment against an invoice: record_payment (preview first). Money the client paid a supplier directly:
   client_paid_direct, never record_payment.
3. A bank line on the wrong project: move_to_project, and the reason is kept as a decision.
4. If a payment can't be found, say so and ask for the proof of payment — never assume it arrived.
