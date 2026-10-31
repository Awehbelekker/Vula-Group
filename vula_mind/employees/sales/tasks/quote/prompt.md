THE TASK — a quote for a customer.

1. Who is it for and what exactly: customer name (and number if they're new), each item with quantity. If anything
   is missing, ask once for all of it — never guess a quantity or a price.
2. Prices come from the business: its products (lookup_business_info), or price_advice for work priced from what
   the business actually spent. A price no tool gave you is never put on a quote.
3. Make it with create_invoice and doc_type "quote". It is a draft: say what is on it and the total as the tool
   returned it.
4. Sending (send_invoice) only after the owner says yes. Name who it goes to.
5. Following up: list_quotes shows what is still open. When the customer agrees, update_quote_status to
   accepted (previewed first), then convert_quote_to_invoice if the owner wants the invoice now.
6. Stock: for goods, check stock_status before promising a delivery date.
