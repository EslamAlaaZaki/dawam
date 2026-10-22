<!-- build: docx -->
# E-commerce: System Architecture Document

Rimal Retail, Digital Commerce Technology. Version 2.2, approved by the Architecture Review Board.

## 1. Purpose and scope

The E-commerce system runs the chain's online shop: the customer accounts, the orders placed on the website and in the mobile apps, their payments and their delivery by carriers. It does not hold a product table: the range is owned by POS, and every order item carries the SKU and name of the product it was sold as. Customers who are also loyalty members carry their POS loyalty number.

## 2. System overview

| Item | Value |
| --- | --- |
| System code | ecom |
| Database | MySQL 8, database `shop` |
| Business owner | Head of Digital Commerce |
| Technical owner | E-commerce Platform team (IT) |
| Availability | The shop is available 24 hours; the nightly jobs run between 01:00 and 04:00 Riyadh time |
| Currency | Amounts are in Saudi riyals (SAR); prices exclude VAT |
| Data as of | The sample extract covers 2025 and is as of 31 December 2025 |

## 3. Modules

- **Customer accounts**: registration, addresses and marketing opt-in. Table `customers`.
- **Orders**: orders and the items on them. Tables `orders` and `order_items`.
- **Payments**: one payment per order. Table `payments`.
- **Fulfilment**: carriers and the shipment of each order. Tables `carriers` and `shipments`.

## 4. Main tables

| Table | Kind | Rows (approx.) | Purpose |
| --- | --- | --- | --- |
| customers | Master | 800 | Registered online shoppers, with personal data. |
| carriers | Reference | 6 | Delivery companies, each with its delivery SLA in days. |
| orders | Transactional | 2,000 per year | One row per online order, with its totals and status. |
| order_items | Transactional | 3,000 per year | One row per product on an order, by SKU. |
| payments | Transactional | 2,000 per year | One payment per order. |
| shipments | Transactional | about 1,900 per year | One row per order that was shipped. |

Some relationships are enforced by the application only, not by foreign keys: `payments.order_id` and `shipments.order_id` point at `orders.order_id`, and `shipments.carrier_code` at `carriers.carrier_code`.

The view `v_orders_to_ship` lists the orders waiting to be dispatched with their customer and payment; the warehouse works from it. The procedure `sp_carrier_performance(carrier_code)` returns a carrier's shipments, deliveries and on-time deliveries by delivery option; Logistics runs it for the monthly carrier review.

## 5. Data flows

1. **Product range from POS.** Every night at 01:30 (job CAT-01) E-commerce receives the active products of POS. Items are sold under the POS SKU and price.
2. **Loyalty link.** When a customer registers with a loyalty card, the loyalty number is stored in `customers.loyalty_no` and the name, email and mobile number are taken from the loyalty account. Online-only customers have no loyalty number. Customers are matched to POS on the loyalty number; the email address is the fallback.
3. **Payments.** Card and wallet payments are captured by the payment gateway when the order is placed. Cash on delivery is collected by the carrier and confirmed to E-commerce at delivery.
4. **Fulfilment.** The warehouse hands parcels to the carrier every day; the carrier reports the delivery date back by API.

## 6. Business rules

### 6.1 Customers

- `loyalty_no` is empty for customers who are not loyalty members. Mobile numbers are stored in local format, `05` followed by 8 digits (POS stores the same number as `+9665...`).
- A customer is never recorded as ordering before `registered_on`.
- A customer with `marketing_opt_in = 0` must not receive any marketing message.

### 6.2 Orders and prices

- `order_status` is `processing` (placed, not yet dispatched), `shipped` (with the carrier), `delivered`, `returned` (the whole order was sent back after delivery) or `cancelled` (stopped before shipping). Orders that are `processing` or `shipped` are open.
- An item's net amount is `quantity x unit_price_sar - discount_sar`. The coupon `WELCOME10` gives 10% off every item of its order; other orders have no discount.
- `subtotal_sar` is the sum of the order's net item amounts. Delivery is free for a standard order with a subtotal of 200 SAR or more and otherwise 20 SAR; express delivery is always 35 SAR. VAT is 15% of subtotal plus delivery, and `total_sar` is the sum of the three.
- A cancelled order keeps its totals, has no shipment and a voided payment.

### 6.3 Payments

- An order has exactly one payment, for the order's total. Methods are `mada`, `card`, `apple_pay`, `stc_pay` and `cod` (cash on delivery).
- `payment_status` is `captured`, `pending` (cash on delivery, not yet delivered), `voided` (cancelled order) or `refunded` (returned order). `paid_at` is empty for voided and pending payments.

### 6.4 Delivery

- Each shipped order has one shipment with a carrier. `shipment_status` is `in_transit`, `delivered` or `returned`; `delivered_on` is empty while in transit.
- Express parcels usually arrive 1 to 2 days after shipping, standard parcels in 2 to 5 days.
- **On-time delivery.** A delivery is on time when `delivered_on - shipped_on` is not more than the carrier's `sla_days` (3 to 5 days, depending on the carrier).

## 7. Personal data

E-commerce holds personal data protected under the Personal Data Protection Law (PDPL): names, email addresses, mobile numbers and delivery addresses of customers and recipients. Delivery addresses are shared with carriers only for the shipment; analytical use needs approval from the Data Protection Officer.
