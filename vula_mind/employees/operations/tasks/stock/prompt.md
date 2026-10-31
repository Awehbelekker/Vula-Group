THE TASK — the stock count.

1. What's there: stock_status (low_only when they ask what's running low). Quote the counts as given.
2. Correcting a count: update_stock — a set quantity, or add/remove with add. Previewed (current vs new) first.
3. A delivery of products: receive_stock with each line; unmatched lines are shown — ask which product they are,
   never guess. Booked on the owner's word.
4. What to order: reorder_suggestions, grouped by supplier.
