# Restaurant test: one sitting, about 15 minutes

A walk through what a restaurant on the light plan gets: table bookings, service at the table,
paying by tap, the waiter's app and the roster. Written on 24 November 2026. None of it had been
run with a real phone, payment or WhatsApp number when this was written, so expect to find things.

Note what happens at each step. For anything that goes wrong, the time and the step number are
enough to find it in the logs.

## What you need

- A test business of the type "Restaurant (light)" with its WhatsApp number connected.
- Two phones: one as the **customer**, one as the **waiter**. Your laptop is the **owner**.
- A team member for the waiter, with their WhatsApp number, and the staff app set up on the
  waiter's phone (Money › Tap to Pay gives the enrol code).
- A payment gateway in test mode on the business, so no real money moves.
- Two or three products with one price each (the menu).

## 1. Table bookings (owner, then customer)

1. Owner: Sell › Tables › settings. Add a sitting for today (for example "Dinner", 17:00 to 21:00),
   set how many people you seat at once, and save.
2. Customer phone: send "book a table" to the business's WhatsApp number.
3. Pick a day, how many people and a time from the lists. First-time customers are asked their name once.
4. **Expect:** a confirmation with a reference like `T-12345`, and the booking in Sell › Tables.
5. Customer: send "cancel my booking" and cancel it. **Expect:** it shows as cancelled on the owner's screen.
6. Owner: Sell › Specials, add a special for today. Customer: send "specials". **Expect:** today's special with a Book button.

## 2. Set up a table (owner)

1. Money › Tap to Pay › Tables: add "Table 1" and choose the waiter who serves it.
2. Turn on both switches: customers can call staff, and customers can order from the table.
3. Open the table's link on the customer phone (or scan its QR code).

## 3. Call the waiter (customer, then waiter)

1. Customer: on the table's page, ask for the waiter.
2. **Expect:** the waiter's phone gets a push and a WhatsApp message naming Table 1, and the staff app's floor shows the call.
3. Waiter: accept it, then mark it done.
4. Customer: ask for the waiter again and this time nobody accepts.
5. **Expect:** after about three minutes the owner gets "Table waiting".

## 4. Order from the table (customer, then waiter)

1. Customer: open the menu on the table's page, choose two items and send the order.
2. **Expect:** the waiter gets "New order, Table 1" with the items and the total.
3. Waiter: confirm it. **Expect:** the items are on Table 1's bill at the menu prices.
4. Waiter: add one more line by hand with a note (for example "no onions"), then remove one line.
5. Customer: send a second order and have the waiter reject it with a reason. **Expect:** the customer's page shows the reason.

## 5. Pay by tap (customer)

1. Customer: tap the table's tag (or open its pay link).
2. **Expect:** the pay page lists the bill's lines and the total.
3. Add a tip and pay with the gateway's test card.
4. **Expect:** the customer sees it paid, the waiter is told, and the table has no open bill.

## 6. The waiter's app (waiter)

1. Open the staff app's home. **Expect:** the business's logo and colour, "Earned today" with the tip, and a bar for today.
2. Set a target for yourself. **Expect:** a ring showing how far along you are.
3. Open the team board. **Expect:** your sale, the call you accepted and the order you confirmed counted, with points. Other people's tips are not shown.

## 7. The roster (owner, then waiter)

1. Owner: Admin › Shifts. Add a shift for the waiter tomorrow and one open shift with nobody on it.
2. Publish. **Expect:** the waiter gets one message listing the shift, by push and WhatsApp.
3. Waiter: in the staff app under My shifts, confirm the shift. **Expect:** the planner shows "Confirmed".
4. Waiter: send "sick" to the business's WhatsApp number. **Expect:** the shift is marked off sick only if it starts within 36 hours, and the owner is told at once.
5. Owner: on the shift that needs cover, press "Offer to the team". Waiter: take the open shift in the app.
6. Waiter: under "When I can work", add a weekly time and a day away. **Expect:** the planner shows them and warns about a shift outside them.
7. Owner: move to next week and press "Copy last week". **Expect:** the same shifts as drafts, and nobody is told until you publish.

## 8. Calls follow the roster (owner, customer)

1. Owner: add a shift for the waiter that covers right now, and publish it.
2. Owner: Money › Tap to Pay › Tables, add "Table 2" with nobody serving it.
3. Customer: open Table 2's page and ask for the waiter.
4. **Expect:** the waiter on shift gets the call, not the owner.

## What to send back

For each step that didn't do what "Expect" says: the step number, what you saw instead, and
roughly what time it was.
