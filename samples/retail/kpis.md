# Retail sample: KPIs to enter by hand

Ten KPIs for the demo's KPI step. Enter each one with **Add KPI** in the KPIs folder named in "Enter under": a Source System's **KPIs** folder (under Systems) or **Data Warehouse > KPIs** for the ones that need both systems. Copy the fields as they are; leave **Formula SQL** empty, because it is written against the DW Schema once the warehouse exists (the assistant can propose it then).

Every KPI can be computed from the sample data. The data covers calendar year 2025 and is as of 31 December 2025. Amounts exclude VAT.

## POS

### 1. Net sales

- **Enter under:** Systems > POS > KPIs
- **Name:** Net sales
- **Business definition:** What the stores sold, after discounts and returns, excluding VAT.
- **Formula in words:** Sum of receipt_lines.line_net_sar over all receipts, sales and returns together (returns are negative), per month of sales_receipts.sold_at.
- **Unit:** SAR
- **Aggregation:** Sum
- **Owner:** Head of Store Operations
- **Refresh frequency:** Daily
- **Targets:** `FY2026: 900,000 SAR`

### 2. Gross margin

- **Enter under:** Systems > POS > KPIs
- **Name:** Gross margin
- **Business definition:** The share of net sales left after paying for the goods sold.
- **Formula in words:** Net sales minus the sum of receipt_lines.quantity times products.unit_cost_sar, divided by net sales, per month of sales_receipts.sold_at. Returns carry negative quantities, so they reduce both sales and cost. The cost is the product's current unit cost.
- **Unit:** %
- **Aggregation:** Ratio
- **Owner:** Chief Financial Officer
- **Refresh frequency:** Monthly
- **Targets:** `FY2026: 35%`

### 3. Average basket value

- **Enter under:** Systems > POS > KPIs
- **Name:** Average basket value
- **Business definition:** How much a customer spends on one visit to a store, excluding VAT.
- **Formula in words:** Sum of sales_receipts.subtotal_sar of receipts with receipt_type sale divided by the count of those receipts, per month of sold_at.
- **Unit:** SAR
- **Aggregation:** Average
- **Owner:** Head of Store Operations
- **Refresh frequency:** Weekly
- **Targets:** `FY2026: 600 SAR`

### 4. Return rate

- **Enter under:** Systems > POS > KPIs
- **Name:** Return rate
- **Business definition:** The share of till receipts that are returns, a measure of product and service quality.
- **Formula in words:** Count of sales_receipts with receipt_type return divided by the count of all sales_receipts, per month of sold_at.
- **Unit:** %
- **Aggregation:** Ratio
- **Owner:** Head of Store Operations
- **Refresh frequency:** Monthly
- **Targets:** `FY2026: 3%`

## E-commerce

### 5. Online net sales

- **Enter under:** Systems > E-commerce > KPIs
- **Name:** Online net sales
- **Business definition:** What the online shop sold, excluding VAT and delivery fees, not counting cancelled or returned orders.
- **Formula in words:** Sum of order_items.line_net_sar of orders whose order_status is not cancelled or returned, per month of orders.order_date.
- **Unit:** SAR
- **Aggregation:** Sum
- **Owner:** Head of Digital Commerce
- **Refresh frequency:** Daily
- **Targets:** `FY2026: 650,000 SAR`

### 6. Average order value

- **Enter under:** Systems > E-commerce > KPIs
- **Name:** Average order value
- **Business definition:** The average value of the goods in an online order, excluding VAT and delivery fees.
- **Formula in words:** Sum of orders.subtotal_sar divided by the count of orders whose order_status is not cancelled or returned, per month of order_date.
- **Unit:** SAR
- **Aggregation:** Average
- **Owner:** Head of Digital Commerce
- **Refresh frequency:** Weekly
- **Targets:** `FY2026: 300 SAR`

### 7. Order cancellation rate

- **Enter under:** Systems > E-commerce > KPIs
- **Name:** Order cancellation rate
- **Business definition:** The share of online orders that were cancelled before shipping.
- **Formula in words:** Count of orders with order_status cancelled divided by the count of all orders, per month of order_date.
- **Unit:** %
- **Aggregation:** Ratio
- **Owner:** Head of Digital Commerce
- **Refresh frequency:** Weekly
- **Targets:** `FY2026: 3%`

### 8. On-time delivery rate

- **Enter under:** Systems > E-commerce > KPIs
- **Name:** On-time delivery rate
- **Business definition:** The share of delivered parcels that reached the customer within the carrier's SLA.
- **Formula in words:** Count of shipments with delivered_on set and delivered_on minus shipped_on not more than carriers.sla_days, divided by the count of shipments with delivered_on set, per month of delivered_on. Shipments link to carriers through carrier_code.
- **Unit:** %
- **Aggregation:** Ratio
- **Owner:** Head of Logistics
- **Refresh frequency:** Daily
- **Targets:** `FY2026: 90%`

## Data Warehouse (both systems)

### 9. Omnichannel customer share

- **Enter under:** Data Warehouse > KPIs
- **Name:** Omnichannel customer share
- **Business definition:** The share of online customers who are also loyalty members in the stores, a measure of how many customers the chain can see across both channels.
- **Formula in words:** Count of distinct E-commerce customers with at least one order in the period whose loyalty_no equals a POS loyalty_customers.loyalty_no, divided by the count of distinct E-commerce customers with at least one order in the period. The email address is the fallback match for customers without a loyalty number.
- **Unit:** %
- **Aggregation:** Ratio
- **Owner:** Chief Marketing Officer
- **Refresh frequency:** Monthly
- **Targets:** `FY2026: 65%`

### 10. Online share of net sales

- **Enter under:** Data Warehouse > KPIs
- **Name:** Online share of net sales
- **Business definition:** The share of the chain's sales made online, a measure of the channel mix that needs both systems.
- **Formula in words:** Online net sales divided by the sum of POS net sales and online net sales, per month. POS net sales come from receipt_lines.line_net_sar by sold_at; online net sales from order_items.line_net_sar of orders that are not cancelled or returned, by order_date. Products are the same in both systems (SKU).
- **Unit:** %
- **Aggregation:** Ratio
- **Owner:** Chief Executive Officer
- **Refresh frequency:** Monthly
- **Targets:** `FY2026: 45%`
