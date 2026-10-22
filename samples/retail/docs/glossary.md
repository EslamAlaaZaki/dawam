# Business glossary: Rimal Retail

Terms used across POS, E-commerce and the data warehouse, in alphabetical order.

**Average basket value.** Net sales of sale receipts divided by the number of sale receipts, excluding VAT. See Basket.

**Basket.** Everything a customer buys on one receipt in a store. Its value is the receipt's subtotal. Recorded in POS, table `sales_receipts` with its `receipt_lines`.

**Carrier.** A delivery company that takes online parcels from the warehouse to the customer (SMSA, Aramex, Naqel, Saudi Post, Zajil, J&T). Recorded in E-commerce, table `carriers`.

**Cash on delivery (COD).** An online payment method where the carrier collects the money at the door. Its payment stays pending until the parcel is delivered.

**Category.** A group of products (Grocery, Beverages, Household, Personal care, Electronics, Fashion, Home and kitchen, Toys, Sports, Stationery). Its three-letter code is part of every SKU.

**Coupon.** A code entered at checkout. The coupon `WELCOME10` gives 10% off every item of the order.

**Delivery option.** Standard (free for 200 SAR of goods or more, otherwise 20 SAR) or express (35 SAR).

**Gross margin.** Net sales minus the cost of the goods sold, as a share of net sales. The cost of a line is its quantity times the product's `unit_cost_sar`.

**Loyalty member.** A customer enrolled in the loyalty programme, identified by the loyalty number (LY followed by 7 digits). Members are recorded in POS and, when they have an online account, carry the same number in E-commerce.

**mada.** The Saudi national debit card network.

**Net sales.** The sum of the net line amounts (after discounts, excluding VAT) of sales minus returns. In stores, returns are receipts with negative amounts; online, an order that was cancelled or returned is not counted.

**Omnichannel customer.** A customer who is known in both channels: an online customer who is also a loyalty member in POS.

**On-time delivery.** A delivery whose time from shipping to delivery is not more than the carrier's SLA in days.

**Online-only customer.** A registered online customer without a loyalty number.

**PDPL.** The Saudi Personal Data Protection Law. Names, mobile numbers, emails, dates of birth and addresses are personal data under it.

**Return (store).** A customer bringing goods back to a store. It is recorded as a separate receipt of type `return` with negative quantities and amounts.

**Returned order (online).** An order whose goods were sent back after delivery. Its payment is refunded.

**SAR.** Saudi riyal, the currency of every amount in these systems.

**SKU (stock keeping unit).** The code of a product: SKU, the category code and a four-digit number, for example SKU-GRC-0001. The same SKU is used in POS and E-commerce.

**SLA (delivery).** The number of days a carrier has from shipping to delivery.

**Store format.** The size of a store: hypermarket, supermarket or express.

**Tier.** A loyalty member's level: bronze, silver or gold.

**VAT.** Value added tax, 15% in Saudi Arabia. Prices in these systems exclude VAT; receipts and orders show it separately.
