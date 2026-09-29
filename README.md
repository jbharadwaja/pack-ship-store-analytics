# Pack & Ship Store Analytics (Simulated Data)

> **Everything in this dataset is simulated.** It is not real sales data from The UPS Store, UPS, or any other business, and this project is not affiliated with or endorsed by UPS. Carrier service names such as UPS Ground and 2nd Day Air only describe the kind of shipments a pack-and-ship store sells.

Real store sales data is confidential, so this dataset simulates two years of transactions at a fictional pack-and-ship retail store: shipping, packing, printing, mailbox rentals, notary and other counter services. It is built to support the kind of analysis a store owner would actually act on.

## What's inside

| File | Contents |
|---|---|
| `data/*.csv` | The seven tables below, one CSV each |
| `pack_ship_store.db` | The same tables in a SQLite database, with primary and foreign keys |
| `store_analysis.ipynb` | Analysis notebook: SQL queries and a chart for each question below |
| `generate_data.py` | The script that produces all of it. The random seed is fixed, so it always produces the same data. |

## The store

- **Period:** September 1, 2024 to August 31, 2026 (24 months). The store was closed September 1–2, 2024 (Sunday and Labor Day), so the first transaction is on September 3.
- **Hours:** Monday–Friday 8am–7pm, Saturday 9am–5pm, closed Sundays and major holidays, early close on December 24 and 31.
- **Size:** 45,542 transactions, 78,099 line items and 29,366 packages shipped.
- **Customers:** walk-ins, 352 mailbox holders and 36 business accounts.
- **Staff:** a store manager, full-time and part-time associates, and a seasonal helper from mid-November to December 23.
- **Events a manager would know about:**
  - 15% off all printing during June 2025.
  - A part-time associate left in February 2025 and a replacement started in March 2025.

## Tables

### `services` (34 rows): the menu of everything the store sells

| Column | Description |
|---|---|
| `service_id` | Item code, e.g. `SHP-GND`, `PRT-CLR`, `NOT-SIG` |
| `service_name` | Item name |
| `category` | Shipping, Returns, Packing supplies, Packing services, Printing, Mailbox, Notary, Other services, Retail |
| `list_price_2026` | Current menu price. Empty for shipping, which is priced per package by weight and zone. |
| `unit_cost_2026` | Current cost per unit. Empty for shipping; each shipping line has its own cost. |
| `taxable` | 1 if sales tax applies |
| `labor_min_per_line` | Estimated staff minutes each time the item is rung up |
| `labor_min_per_unit` | Estimated staff minutes for each unit (per page, per package, per signature) |

### `employees` (7 rows)

| Column | Description |
|---|---|
| `employee_id` | E01–E07 |
| `first_name`, `role`, `employment_type` | Who they are and whether they are full-time, part-time or seasonal |
| `hire_date`, `termination_date` | Employment dates. Empty termination date means still employed. |
| `hourly_wage` | Hourly pay |

### `shifts` (2,101 rows): who was scheduled, and when

| Column | Description |
|---|---|
| `shift_id`, `employee_id`, `shift_date` | One row per person per day worked |
| `start_time`, `end_time` | Shift times, 24-hour clock |
| `hours` | Scheduled hours |

### `customers` (388 rows): business accounts and mailbox holders

Walk-in customers are anonymous and do not appear here.

| Column | Description |
|---|---|
| `customer_id` | `B####` for business accounts, `M####` for mailbox holders |
| `customer_type` | Business account or Mailbox holder |
| `industry` | Business accounts only |
| `account_open_date` | Account opened or mailbox first rented. Can be before the data starts. |
| `mailbox_size`, `mailbox_term_months` | Mailbox holders only: Small, Medium or Large, and a 6- or 12-month rental term |
| `status`, `close_date` | Active or Closed as of August 31, 2026, and when it closed |

### `transactions` (45,542 rows): one row per register transaction

| Column | Description |
|---|---|
| `transaction_id` | T0000001, in time order |
| `transaction_datetime` | Local date and time |
| `employee_id` | Who rang it up. Occasionally empty. |
| `customer_id` | Empty for walk-ins |
| `customer_type` | Walk-in, Mailbox holder or Business account |
| `payment_method` | Card, Cash, Mobile wallet, On account (business accounts) or No charge ($0 visits) |
| `status` | Completed, Voided or Refund |
| `refund_of_transaction_id` | For refunds, the original transaction |
| `subtotal`, `discount_total`, `sales_tax`, `total` | Register totals. `total` includes sales tax. |

### `transaction_items` (78,099 rows): one row per item on a transaction

| Column | Description |
|---|---|
| `line_id` | L00000001, in time order |
| `transaction_id` | The transaction this line belongs to |
| `service_id` | What was sold |
| `quantity` | Units: pages, packages, signatures, feet of bubble wrap and so on. Negative on refunds. |
| `unit_price` | Price actually charged per unit. Changes each January. |
| `discount_amount` | Discount on this line |
| `line_revenue` | `quantity × unit_price − discount_amount`, before tax |
| `line_cost` | Cost of the goods, or the carrier cost for shipping |

### `shipments` (29,366 rows): one row per package shipped

| Column | Description |
|---|---|
| `shipment_id` | S0000001, in time order |
| `line_id` | The shipping line in `transaction_items` |
| `service_level` | Ground, 3 Day Select, 2nd Day Air or Next Day Air |
| `billable_weight_lb` | Billable weight in pounds |
| `zone` | Shipping zone, 2 (nearby) to 8 (farthest) |
| `destination_type` | Residential or Commercial |
| `package_type` | Envelope or Box |

### How the tables connect

```
employees ──< shifts
employees ──< transactions >── customers
transactions ──< transaction_items >── services
transaction_items ──o shipments        (shipping lines only)
transactions ──o transactions          (refund → original sale)
```

## Counting revenue correctly

- **Revenue** is `SUM(transaction_items.line_revenue)`. It is already net of discounts and excludes sales tax.
- **Leave out voided transactions** (`status = 'Voided'`).
- **Keep refunds** (`status = 'Refund'`). Their amounts are negative, so they cancel out the original sale.
- **Gross profit** is `line_revenue − line_cost`.
- **Don't use `transactions.total` for revenue.** It includes sales tax.

## Data quirks to clean

These are intentional, to reflect what a real register export looks like:

- **181 voided transactions** still have their line items.
- **113 refunds** are separate transactions with negative amounts, linked to the original sale by `refund_of_transaction_id`.
- **227 transactions have no `employee_id`**, from a register left logged out.
- **Roughly 3 in 10 transactions are return drop-offs**, and most of them are $0. They pull down any average-per-transaction figure.
- **Prices change every January.** Use `unit_price` on each line, not the menu price in `services`.

## How the numbers were built

- **Store size.** Sales for calendar 2025 come to about $725,000. That is close to the roughly $720,000 average adjusted gross sales that The UPS Store's franchise disclosure document reports for its traditional locations in 2024. The disclosure does not break sales down by service, so the service mix here is an assumption.
- **Shipping prices.** Based on UPS's published 2026 retail rates for 2nd Day Air (U.S. 48 states). Ground, 3 Day Select and Next Day Air prices are scaled from that table using assumed ratios. Prices for 2024 and 2025 are set about 5.9% lower per year. Real store prices vary by location.
- **Shipping cost.** Assumed to be 71–75% of the retail price.
- **Other costs.** Assumed per item, e.g. about $0.04 per black-and-white page. Notary and packing service fees have no product cost; their cost is staff time.
- **Price changes.** Menu prices rise about 3% each January and supply costs about 2%.
- **Sales tax.** 8% on supplies, printing and retail items. Shipping and services are not taxed.
- **Business accounts** get 8% off shipping.
- **Seasonality.** A holiday shipping peak in November and December, a surge in return drop-offs after the holidays, tax-season printing and shredding from February to April, and back-to-school printing in August.
- **Growth.** Store traffic grows modestly, while return drop-offs grow faster than paid visits.
- **Labor minutes** in `services` are rough estimates of staff time. For mailbox rentals they include sorting the box's mail over the rental term.

## Questions to explore

1. Which services bring in the most revenue, and which bring in the most gross profit? Are they the same ones?
2. How much do business accounts contribute, and at what margin compared with walk-in customers?
3. What share of visits are no-charge return drop-offs, and how often does one turn into a sale?
4. How often do shipping customers also buy packing supplies, and does it depend on who is at the register?
5. Did the June 2025 printing promotion pay off?
6. What is the mailbox renewal rate, and which box sizes and terms are cancelled most?
7. Which days and hours are busiest compared with how many staff are scheduled?
8. Which services earn the most gross profit per minute of staff time?

## Using the data

- **Notebook:** open `store_analysis.ipynb` in Jupyter, VS Code or Google Colab and choose Run All.
- **SQL:** open `pack_ship_store.db` in [DB Browser for SQLite](https://sqlitebrowser.org/) or query it from Python with `sqlite3`.
- **Excel, Power BI or Tableau:** import the CSV files from `data/`.
- **Regenerate:** `pip install numpy pandas`, then `python generate_data.py`.

## Sources

- The UPS Store franchise disclosure figures, as reported in [The UPS Store Franchise Review 2025 (Franchise Chatter)](https://www.franchisechatter.com/2025/11/11/the-ups-store-franchise-review-2025-costs-fees-news-average-revenues-and-or-profits/)
- UPS 2nd Day Air retail rates: [2026 UPS Rates – Retail](https://assets.ups.com/adobe/assets/urn:aaid:aem:5e7a9caa-cf5d-4f88-aa30-1b4c3241b40d/original/as/preview-retail-rates-us-en.pdf)
