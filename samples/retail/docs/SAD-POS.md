<!-- build: docx -->
# POS: System Architecture Document

Rimal Retail, Retail Technology. Version 3.1, approved by the Architecture Review Board.

## 1. Purpose and scope

The Point of Sale system (POS) records every sale made in the chain's stores: the receipts printed at the tills, the products on them and the loyalty members who scanned their card. It also holds the store network, the product range and the loyalty programme. Online orders are handled by the E-commerce system, which shares loyalty members and products with POS.

## 2. System overview

| Item | Value |
| --- | --- |
| System code | pos |
| Database | PostgreSQL 16, database `pos`, schema `retail` |
| Business owner | Head of Store Operations |
| Technical owner | Store Systems team (IT) |
| Availability | Tills work offline and upload when connected; the central database is available 24 hours |
| Currency | Amounts are in Saudi riyals (SAR); prices exclude VAT |
| Data as of | The sample extract covers 2025 and is as of 31 December 2025 |

## 3. Modules

- **Store network**: the stores, their format and opening and closing dates. Table `stores`.
- **Product range**: categories and products with their list price and cost. Tables `categories` and `products`.
- **Loyalty programme**: members, their tier and marketing consent. Table `loyalty_customers`.
- **Sales**: receipts and the lines on them. Tables `sales_receipts` and `receipt_lines`.

## 4. Main tables

| Table | Kind | Rows (approx.) | Purpose |
| --- | --- | --- | --- |
| stores | Master | 12 | Stores in Riyadh, Jeddah, Dammam and Al Khobar. |
| categories | Reference | 10 | Product categories, with the three-letter code used in SKUs. |
| products | Master | 80 | One row per SKU, with list price and unit cost. |
| loyalty_customers | Master | 1,000 | Loyalty members, with personal data. |
| sales_receipts | Transactional | 1,500 per year | One row per till receipt, sale or return. |
| receipt_lines | Transactional | 3,000 per year | One row per product line on a receipt. |

Some relationships are enforced by the application only, not by foreign keys: `sales_receipts.store_id` points at `stores.store_id`, `sales_receipts.customer_id` at `loyalty_customers.customer_id`, `receipt_lines.product_id` at `products.product_id` and `loyalty_customers.home_store_id` at `stores.store_id`.

The view `v_loyalty_overview` lists every member with their tier, home store and the number and value of their purchases; the loyalty team works from it. The function `fn_store_net_sales(store_code, from, to)` returns a store's net sales in a date range; Store Operations uses it for the weekly store review.

## 5. Data flows

1. **Tills to the central database.** Each till uploads its receipts every 15 minutes while online and in full when it reconnects. A receipt is final once uploaded.
2. **Product catalogue.** POS is the master of the product range. Every night at 01:30 (job CAT-01) the active products are published to E-commerce, which sells about three in four of them online under the same SKU.
3. **Loyalty members.** Members enrol at the till or in the app of their home store. When a member links their loyalty card to an online account, E-commerce stores the loyalty number; name, email and mobile number are the same in both systems.
4. **Store reporting.** Every morning at 06:00 (job RPT-02) the previous day's sales per store go to Store Operations.

## 6. Business rules

### 6.1 Stores

- A store's `store_code` is its city prefix (RUH, JED, DMM or KHB) and a number. `format` is `hypermarket`, `supermarket` or `express`.
- A receipt is never dated before the store's `opened_on` or after its `closed_on`. The Obhur Express store closed on 31 August 2025 (`is_active` is false), and the Al Khobar Road store opened on 10 April 2025.

### 6.2 Products and prices

- A SKU is `SKU-`, the category code and a four-digit number, for example `SKU-GRC-0001`. The same SKU identifies the product in E-commerce.
- `list_price_sar` is the price per unit excluding VAT; `unit_cost_sar` is the purchase cost and is always below the price.
- A delisted product has `is_active = false` and is no longer sold, so it appears on no receipt of 2025.

### 6.3 Receipts and returns

- A line's net amount is `quantity x unit_price_sar - discount_sar`. A line discount, where given, is 10% of quantity x price. VAT is 15% of the net amount.
- A receipt's `subtotal_sar` is the sum of its net line amounts, `vat_sar` the sum of its line VAT and `total_sar` the sum of the two.
- `receipt_type` is `sale` or `return`. A return is a separate receipt whose quantities and amounts are all negative; it is not linked to the original sale. Net sales are therefore the sum of all lines, sales and returns together.
- Payment methods are `cash`, `mada`, `visa`, `apple_pay` and `stc_pay`.
- A receipt carries a `customer_id` only when the customer scanned a loyalty card, which is about four receipts in ten.

### 6.4 Loyalty programme

- A member's `loyalty_no` is `LY` followed by 7 digits. A member is never recorded as buying before `enrolled_on`.
- `tier` is `bronze`, `silver` or `gold`; most members are bronze and gold is the smallest group.
- A member with `marketing_consent = false` must not receive any marketing message. The email address is optional.

## 7. Personal data

POS holds personal data protected under the Personal Data Protection Law (PDPL): the names, mobile numbers, email addresses and dates of birth of loyalty members. Analytical use of member data must go through the Data Protection Officer; receipts of customers who did not scan a card are anonymous.
