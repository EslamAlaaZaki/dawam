# Bank sample: KPIs to enter by hand

Nine KPIs for the demo's KPI step. Enter each one with **Add KPI** in the KPIs folder named in "Enter under": a Source System's **KPIs** folder (under Systems) or **Data Warehouse > KPIs** for the ones that need both systems. Copy the fields as they are; leave **Formula SQL** empty, because it is written against the DW Schema once the warehouse exists (the assistant can propose it then).

Every KPI can be computed from the sample data. The data covers calendar year 2025 and is as of 31 December 2025.

## Core Banking

### 1. Total deposits

- **Enter under:** Systems > Core Banking > KPIs
- **Name:** Total deposits
- **Business definition:** The money customers hold with the bank: the sum of the balances of all accounts that are not closed.
- **Formula in words:** Sum of accounts.current_balance where accounts.status is not closed.
- **Unit:** SAR
- **Aggregation:** Sum
- **Owner:** Head of Retail Banking Operations
- **Refresh frequency:** Daily
- **Targets:** `FY2026: 125,000,000 SAR`

### 2. Dormant account ratio

- **Enter under:** Systems > Core Banking > KPIs
- **Name:** Dormant account ratio
- **Business definition:** The share of open accounts that are dormant (no customer-initiated transaction for the product's dormancy period).
- **Formula in words:** Count of accounts with status dormant divided by the count of accounts with status active or dormant.
- **Unit:** %
- **Aggregation:** Ratio
- **Owner:** Head of Retail Banking Operations
- **Refresh frequency:** Monthly
- **Targets:** `FY2026: 6%`

### 3. Digital transaction share

- **Enter under:** Systems > Core Banking > KPIs
- **Name:** Digital transaction share
- **Business definition:** The share of customer-initiated transactions made through the mobile app or online banking.
- **Formula in words:** Count of transactions with channel mobile or online divided by the count of transactions whose transaction type is customer-initiated (transaction_types.is_customer_initiated), per month of transaction_date.
- **Unit:** %
- **Aggregation:** Ratio
- **Owner:** Head of Digital Banking
- **Refresh frequency:** Monthly
- **Targets:** `FY2026: 35%`

### 4. Fee income

- **Enter under:** Systems > Core Banking > KPIs
- **Name:** Fee income
- **Business definition:** Income from service fees charged to customers' accounts.
- **Formula in words:** Sum of transactions.amount_sar for transactions of type FEE (transaction_types.is_fee), per month of transaction_date.
- **Unit:** SAR
- **Aggregation:** Sum
- **Owner:** Chief Financial Officer
- **Refresh frequency:** Monthly

### 5. Card spend per active account

- **Enter under:** Systems > Core Banking > KPIs
- **Name:** Card spend per active account
- **Business definition:** Average card purchase spend per account that made at least one card purchase in the month.
- **Formula in words:** Sum of transactions.amount_sar of type POS divided by the number of distinct accounts with a POS transaction, per month of transaction_date.
- **Unit:** SAR
- **Aggregation:** Average
- **Owner:** Head of Cards
- **Refresh frequency:** Monthly

## CRM

### 6. Campaign conversion rate

- **Enter under:** Systems > CRM > KPIs
- **Name:** Campaign conversion rate
- **Business definition:** The share of contacts who responded to a campaign and then opened the promoted product within the campaign window.
- **Formula in words:** Count of campaign_responses with response_type converted divided by the count of distinct contacts with any response, per campaign.
- **Unit:** %
- **Aggregation:** Ratio
- **Owner:** Head of Marketing
- **Refresh frequency:** Weekly
- **Targets:** `FY2026: 12%`

### 7. Complaint SLA compliance

- **Enter under:** Systems > CRM > KPIs
- **Name:** Complaint SLA compliance
- **Business definition:** The share of resolved complaints that were resolved within their category's SLA.
- **Formula in words:** Count of complaints with resolved_on set and resolved_on minus opened_on not more than complaint_categories.sla_days, divided by the count of complaints with resolved_on set, per month of resolved_on.
- **Unit:** %
- **Aggregation:** Ratio
- **Owner:** Head of Customer Experience
- **Refresh frequency:** Daily
- **Targets:** `FY2026: 95%`

## Data Warehouse (both systems)

### 8. Complaints per 1,000 customers

- **Enter under:** Data Warehouse > KPIs
- **Name:** Complaints per 1,000 customers
- **Business definition:** How many complaints the bank receives for every thousand active customers, a measure of service quality that needs both systems.
- **Formula in words:** Count of CRM complaints opened in the period divided by the count of Core Banking customers with status active, times 1,000. Complaints link to customers through contacts.core_customer_no = customers.customer_no.
- **Unit:** Complaints per 1,000 customers
- **Aggregation:** Ratio
- **Owner:** Head of Customer Experience
- **Refresh frequency:** Monthly

### 9. Deposits per relationship manager

- **Enter under:** Data Warehouse > KPIs
- **Name:** Deposits per relationship manager
- **Business definition:** The total deposits of the customers each relationship manager looks after.
- **Formula in words:** Sum of Core Banking accounts.current_balance of accounts that are not closed, for customers whose CRM contact (contacts.core_customer_no = customers.customer_no) has a relationship manager, grouped by relationship_managers.rm_id.
- **Unit:** SAR
- **Aggregation:** Sum
- **Owner:** Head of Private and Premium Banking
- **Refresh frequency:** Monthly
