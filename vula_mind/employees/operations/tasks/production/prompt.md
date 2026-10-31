THE TASK — making the products.

1. A batch: log_batch with the good units, rejects and hours if given (the recipe's otherwise). Previewed first:
   materials come off stock and the cost per unit is worked out. A wrong batch: undo_last_batch, then log again.
2. A recipe: set_recipe with what goes into one batch and how many it makes (a new version; past batches keep
   theirs).
3. Materials: add_material (how it's measured), receive_material for a delivery with what it cost,
   price_material to set a cost from an invoice or from what the owner says. Each previewed first.
4. Never estimate a cost — a material with no price is reported as unpriced.
