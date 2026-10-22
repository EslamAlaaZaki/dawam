<!-- build: xlsx -->
# Data dictionary: POS and E-commerce

One sheet per system, one row per column.

## POS

| Table | Column | Data type | Nullable | Key | Business description |
| --- | --- | --- | --- | --- | --- |
| categories | category_id | integer | No | PK | Internal identifier of the category. |
| categories | category_code | varchar(3) | No | Unique | Three-letter category code that is part of every SKU in the category (GRC, BEV, HOU, PRC, ELC, FSH, HOM, TOY, SPT, STA). |
| categories | category_name | varchar(50) | No |  | Name of the category. |
| loyalty_customers | customer_id | integer | No | PK | Internal identifier of the loyalty member. |
| loyalty_customers | loyalty_no | varchar(9) | No | Unique | Loyalty number, LY followed by 7 digits. The same number identifies the customer in E-commerce (customers.loyalty_no). Personal data. |
| loyalty_customers | full_name | varchar(150) | No |  | Member's full name: first name, father's name and family name. Personal data. |
| loyalty_customers | mobile_number | varchar(13) | No |  | Mobile number in international format (+9665 and 8 digits). Personal data. |
| loyalty_customers | email | varchar(150) | Yes |  | Email address; empty when the member gave none. Personal data. |
| loyalty_customers | date_of_birth | date | No |  | Date of birth. Personal data. |
| loyalty_customers | gender | char(1) | No |  | M or F. |
| loyalty_customers | city | varchar(50) | No |  | City of the member's home store. |
| loyalty_customers | home_store_id | integer | No |  | The store where the member enrolled (stores.store_id). No foreign key is declared. |
| loyalty_customers | tier | varchar(10) | No |  | Loyalty tier: bronze, silver or gold. |
| loyalty_customers | marketing_consent | boolean | No |  | True when the member agreed to receive marketing messages. |
| loyalty_customers | enrolled_on | date | No |  | Date the member joined the programme. No receipt of the member is dated before it. |
| products | product_id | integer | No | PK | Internal identifier of the product. |
| products | sku | varchar(12) | No | Unique | Stock keeping unit: SKU, the category code and a four-digit number. The same SKU identifies the product in E-commerce (order_items.sku). |
| products | product_name | varchar(100) | No |  | Name of the product as printed on the receipt and shown online. |
| products | category_id | integer | No | FK | The product's category (categories.category_id). |
| products | list_price_sar | numeric(10,2) | No |  | Selling price per unit in SAR, excluding VAT. |
| products | unit_cost_sar | numeric(10,2) | No |  | What the chain pays per unit in SAR, excluding VAT; always below the list price. Used for gross margin. |
| products | is_active | boolean | No |  | False for delisted products: they are no longer sold, so they appear on no 2025 receipt. |
| receipt_lines | line_id | integer | No | PK | Internal identifier of the line. |
| receipt_lines | receipt_id | integer | No | FK | The receipt the line belongs to (sales_receipts.receipt_id). |
| receipt_lines | line_no | smallint | No |  | Position of the line on the receipt, from 1. |
| receipt_lines | product_id | integer | No |  | The product sold (products.product_id). No foreign key is declared. |
| receipt_lines | quantity | integer | No |  | Units sold, 1 to 4; negative on a return. |
| receipt_lines | unit_price_sar | numeric(10,2) | No |  | Price per unit in SAR, excluding VAT (the product's list price). |
| receipt_lines | discount_sar | numeric(10,2) | No |  | Discount on the line in SAR, 10% of quantity x price where there is one; negative on a return. |
| receipt_lines | line_net_sar | numeric(12,2) | No |  | Quantity x unit price - discount, in SAR, excluding VAT. Net sales are the sum of this column. |
| receipt_lines | vat_sar | numeric(12,2) | No |  | VAT on the line: 15% of the net amount. |
| sales_receipts | receipt_id | integer | No | PK | Internal identifier of the receipt. |
| sales_receipts | receipt_no | varchar(10) | No | Unique | Receipt number printed on the till slip: RC and 8 digits. |
| sales_receipts | store_id | integer | No |  | The store that issued the receipt (stores.store_id). No foreign key is declared. |
| sales_receipts | customer_id | integer | Yes |  | The loyalty member who scanned their card (loyalty_customers.customer_id); empty for anonymous purchases. No foreign key is declared. |
| sales_receipts | receipt_type | varchar(6) | No |  | sale, or return for a customer bringing goods back (all its lines and amounts are negative). |
| sales_receipts | sold_at | timestamp | No |  | Date and time of the sale (store time), within 2025. |
| sales_receipts | payment_method | varchar(10) | No |  | How the customer paid: cash, mada, visa, apple_pay or stc_pay. |
| sales_receipts | cashier_code | varchar(4) | No |  | Code of the cashier who served the customer. |
| sales_receipts | subtotal_sar | numeric(12,2) | No |  | Sum of the receipt's net line amounts in SAR, excluding VAT. Negative on a return. |
| sales_receipts | vat_sar | numeric(12,2) | No |  | Sum of the receipt's VAT (15%) in SAR. Negative on a return. |
| sales_receipts | total_sar | numeric(12,2) | No |  | Subtotal plus VAT: what the customer paid (or got back) in SAR. |
| stores | store_id | integer | No | PK | Internal identifier of the store. |
| stores | store_code | varchar(6) | No | Unique | Store code used across systems: city prefix (RUH, JED, DMM, KHB) and a number. |
| stores | store_name | varchar(100) | No |  | Name of the store as shown to customers. |
| stores | city | varchar(50) | No |  | City the store is in. |
| stores | region | varchar(20) | No |  | Region the store reports to: Central, Western or Eastern. |
| stores | format | varchar(12) | No |  | hypermarket, supermarket or express. |
| stores | opened_on | date | No |  | Date the store opened. No receipt is dated before it. |
| stores | closed_on | date | Yes |  | Date the store closed; empty while it trades. No receipt is dated after it. |
| stores | is_active | boolean | No |  | False once the store has closed. |

## E-commerce

| Table | Column | Data type | Nullable | Key | Business description |
| --- | --- | --- | --- | --- | --- |
| carriers | carrier_code | varchar(10) | No | PK | Carrier code: SMSA, ARAMEX, NAQEL, SPL, ZAJIL or JT. |
| carriers | carrier_name | varchar(60) | No |  | Name of the delivery company. |
| carriers | sla_days | int | No |  | Days the carrier has from shipping to delivery; a delivery taking longer is late. |
| carriers | is_active | tinyint(1) | No |  | False when the chain no longer sends orders with the carrier. |
| customers | customer_id | int | No | PK | Internal identifier of the online customer. |
| customers | loyalty_no | varchar(9) | Yes |  | The customer's loyalty number (loyalty_customers.loyalty_no in POS); empty for customers who are not members. Personal data. |
| customers | full_name | varchar(150) | No |  | Customer's full name; the same as the loyalty member's. Personal data. |
| customers | email | varchar(150) | No |  | Email address used to log in; the same as the loyalty member's. Personal data. |
| customers | mobile_number | varchar(10) | No |  | Mobile number in local format (05 and 8 digits). Personal data. |
| customers | city | varchar(50) | No |  | City of the customer's address. |
| customers | address_line | varchar(150) | No |  | Street address for deliveries: building, street and district. Personal data. |
| customers | postal_code | varchar(5) | No |  | Five-digit postal code of the address. |
| customers | registered_on | date | No |  | Date the customer registered. No order is dated before it. |
| customers | marketing_opt_in | tinyint(1) | No |  | True when the customer agreed to receive marketing messages. |
| order_items | item_id | int | No | PK | Internal identifier of the item. |
| order_items | order_id | int | No | FK | The order the item belongs to (orders.order_id). |
| order_items | sku | varchar(12) | No |  | SKU of the product ordered; the same code as products.sku in POS. Products are not a table in this system. |
| order_items | product_name | varchar(100) | No |  | Name of the product at the time of the order. |
| order_items | quantity | int | No |  | Units ordered, 1 to 3. |
| order_items | unit_price_sar | decimal(10,2) | No |  | Price per unit in SAR, excluding VAT. |
| order_items | discount_sar | decimal(10,2) | No |  | Coupon discount on the item in SAR: 10% of quantity x price when the order has a coupon, otherwise 0. |
| order_items | line_net_sar | decimal(12,2) | No |  | Quantity x unit price - discount, in SAR, excluding VAT. Net online sales are the sum of this column. |
| orders | order_id | int | No | PK | Internal identifier of the order. |
| orders | order_no | varchar(12) | No | Unique | Order number shown to the customer: WEB and 8 digits. |
| orders | customer_id | int | No | FK | The customer who ordered (customers.customer_id). |
| orders | order_date | date | No |  | Date the order was placed, within 2025. |
| orders | order_status | varchar(12) | No |  | processing, shipped, delivered, returned or cancelled. |
| orders | order_source | varchar(10) | No |  | Where the order was placed: web, ios or android. |
| orders | delivery_option | varchar(10) | No |  | standard (free from 200 SAR of goods, otherwise 20 SAR) or express (35 SAR). |
| orders | coupon_code | varchar(12) | Yes |  | Coupon used, if any; WELCOME10 gives 10% off every item. |
| orders | ship_city | varchar(50) | No |  | City the order is delivered to. |
| orders | subtotal_sar | decimal(12,2) | No |  | Sum of the order's net item amounts in SAR, excluding VAT and delivery. |
| orders | shipping_fee_sar | decimal(10,2) | No |  | Delivery fee in SAR, excluding VAT. |
| orders | vat_sar | decimal(12,2) | No |  | VAT (15%) on goods and delivery in SAR. |
| orders | total_sar | decimal(12,2) | No |  | Subtotal + delivery fee + VAT: what the customer pays, in SAR. Cancelled orders keep their total. |
| payments | payment_id | int | No | PK | Internal identifier of the payment. |
| payments | order_id | int | No |  | The order paid (orders.order_id). No foreign key is declared. |
| payments | payment_method | varchar(10) | No |  | mada, card, apple_pay, stc_pay or cod (cash on delivery). |
| payments | payment_status | varchar(10) | No |  | captured, pending (cash on delivery not yet collected), voided (cancelled order) or refunded (returned order). |
| payments | amount_sar | decimal(12,2) | No |  | Amount in SAR, equal to the order's total. |
| payments | paid_at | datetime | Yes |  | When the money was collected: at ordering, or at delivery for cash on delivery. Empty for voided and pending payments. |
| shipments | shipment_id | int | No | PK | Internal identifier of the shipment. |
| shipments | order_id | int | No |  | The order shipped (orders.order_id). No foreign key is declared. |
| shipments | carrier_code | varchar(10) | No |  | The delivery company (carriers.carrier_code). No foreign key is declared. |
| shipments | tracking_no | varchar(12) | No |  | Tracking number: TRK and 9 digits. |
| shipments | shipment_status | varchar(10) | No |  | in_transit, delivered or returned. |
| shipments | shipped_on | date | No |  | Date the carrier collected the parcel; never before the order date. |
| shipments | delivered_on | date | Yes |  | Date the parcel was delivered; empty while in transit. Delivery is on time when delivered_on - shipped_on is at most the carrier's sla_days. |
| shipments | recipient_name | varchar(150) | No |  | Name of the person receiving the parcel. Personal data. |
| shipments | delivery_address | varchar(200) | No |  | Street address the parcel was sent to. Personal data. |
| shipments | delivery_city | varchar(50) | No |  | City the parcel was sent to. |
