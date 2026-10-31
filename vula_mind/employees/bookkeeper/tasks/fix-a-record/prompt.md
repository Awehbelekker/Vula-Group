THE TASK — fixing a record that was booked wrongly.

1. Find exactly which record (find_document, bank_payments) before changing anything; if two could match, ask.
2. What can be fixed here: the project (move_to_project — its document, expense and prices move with it), money the
   client paid directly (client_paid_direct), a customer invoice (credit_note). Each previews first.
3. Something these tools can't change (an expense's account, cancelling a duplicate): say exactly what is wrong and
   where the owner changes it in the dashboard — never claim it was fixed.
4. Keep the owner's reason with record_decision, so the same mistake isn't made again and the figure can be explained.
