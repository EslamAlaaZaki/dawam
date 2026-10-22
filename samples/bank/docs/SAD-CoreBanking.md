<!-- build: docx -->
# Core Banking System: System Architecture Document

Najd National Bank (NNB), Retail Banking Technology. Version 3.2, approved by the Architecture Review Board.

## 1. Purpose and scope

The Core Banking System (CBS) is the system of record for NNB's retail customers, their deposit accounts and every money movement on those accounts. This document describes its modules, main tables, data flows and the business rules that other systems and the planned data warehouse rely on. Lending, cards and treasury run on separate platforms and are out of scope.

## 2. System overview

| Item | Value |
| --- | --- |
| System code | cbs |
| Database | PostgreSQL 16, database `corebank`, schema `banking` |
| Business owner | Head of Retail Banking Operations |
| Technical owner | Core Systems team (IT) |
| Availability | 24x7; end-of-day batch window 23:30 to 03:00 Riyadh time |
| Currency | All amounts are in Saudi riyals (SAR) with 2 decimals |
| Data as of | The sample extract is as of 31 December 2025 |

## 3. Modules

- **Customer Information File (CIF)**: one record per individual customer, with identity, contact details, segment and KYC status. Table `customers`.
- **Branch management**: the branch network and its regions. Table `branches`.
- **Deposits and accounts**: account opening, status changes and balances. Tables `accounts` and `account_types`.
- **Payments and postings**: every posted transaction, from all channels. Tables `transactions` and `transaction_types`.

## 4. Main tables

| Table | Kind | Rows (approx.) | Purpose |
| --- | --- | --- | --- |
| customers | Master | 1,200 | Individual customers. Key `customer_id`; business key `customer_no` (CBnnnnnnn). |
| accounts | Master | 1,800 | Deposit accounts. Each belongs to one customer and is held at one branch. |
| transactions | Transactional | 3,000 per year in the extract | Posted money movements, one row per posting. |
| branches | Reference | 12 | Branches with city and region. |
| account_types | Reference | 6 | Deposit products: current, payroll, savings, youth savings, term deposits. |
| transaction_types | Reference | 8 | Posting types with their direction (credit or debit). |

Not every relationship is declared as a foreign key in the database. In particular `accounts.branch_id` and `customers.home_branch_id` both point at `branches.branch_id`, and `transactions.txn_type_code` points at `transaction_types.txn_type_code`, without a constraint (they were added after go-live and are enforced by the application).

The view `v_customer_portfolio` lists each customer with their home branch, number of open accounts and total balance. The function `fn_branch_deposits(branch_code, from, to)` returns the credits posted to a branch's accounts in a date range; Finance uses it for the monthly branch league table.

## 5. Data flows

1. **Onboarding.** A customer is created at a branch or through digital onboarding. The national ID is verified against the national identity service before the record is saved, so `national_id` is always a valid 10-digit Saudi ID. The customer gets a home branch: the branch that onboarded them, or the nearest branch for digital customers.
2. **Account opening.** An account is opened for an existing customer only. Its IBAN is generated from the bank code (77) and the 18-digit account number.
3. **Postings.** Channels post transactions in real time: the ATM switch (cash deposits and withdrawals), the card switch (card purchases), mobile and online banking (transfers), branches (cash), and internal batch jobs (salary credits, profit payments, fees).
4. **End of day.** The end-of-day batch closes the business date, pays profit, charges fees and flags dormant accounts. See the Operations Runbook for the schedule.
5. **Outbound to CRM.** Every night at 01:00 the customer master (customer number, name, national ID, mobile, email, city, segment) is extracted to CRM, which matches it to CRM contacts on the customer number.
6. **Outbound to the data warehouse.** Planned: a daily extract of all six tables into the warehouse's staging layer.

## 6. Business rules

### 6.1 Customers

- The customer number has the form `CB` followed by 7 digits and never changes. It is the key other systems use.
- Segments: `retail` (default), `premium` and `private`. Segment is assigned at onboarding and reviewed every quarter by Retail Banking; premium and private customers are looked after by a relationship manager, recorded in CRM.
- KYC status is `verified`, `pending` (documents requested, not yet received) or `expired` (ID or address proof older than 5 years). Customers with expired KYC are asked to refresh their documents at their next contact with the bank.
- Customer status is `active` or `inactive`. A customer is set to inactive when they ask to leave the bank; their remaining accounts are closed over the following months, and the record is kept for 10 years for regulatory reasons.
- The mobile number is stored in international format, `+9665` followed by 8 digits.

### 6.2 Accounts

- Account status is `active`, `dormant` or `closed`.
- **Dormant account.** An active current, payroll or savings account becomes dormant when it has had no customer-initiated transaction for the number of months in `account_types.dormancy_months` (12 months; 24 months for Youth Savings). Customer-initiated transactions are the types with `is_customer_initiated = true`: cash deposits, cash withdrawals, outgoing transfers and card purchases. Salary credits, incoming transfers, profit payments and fees do not keep an account active. The nightly job DORM-01 sets the status; a dormant account accepts credits but no debits, and is reactivated only when the customer visits a branch with valid ID.
- Term deposits (TD3, TD12) never become dormant; they mature into the customer's current account.
- A closed account has `closed_on` set and a balance of zero. Closed accounts are never reopened; a new account is opened instead.
- `last_activity_date` is the business date of the last posted transaction of any type.
- Savings accounts must keep a minimum balance of SAR 500 (`account_types.minimum_balance_sar`). The monthly job FEE-03 charges a service fee (type FEE) when the average balance of the month was below it; other service fees use the same type.
- Branch JED004 (Al Balad) no longer opens accounts (`branches.is_active = false`); its existing accounts are still serviced.

### 6.3 Transactions

- `amount_sar` is always positive. Whether a posting adds or removes money is given by `transaction_types.direction` (`credit` or `debit`).
- `transaction_date` is the business date the posting belongs to; `posted_at` is the exact time it was posted. A posting after the end-of-day cut-off (23:30) belongs to the next business date.
- Channels are `mobile`, `online`, `atm`, `branch`, `card` (card purchases) and `system` (batch postings).
- Card purchases carry a merchant category (Grocery, Restaurant, Fuel station, Pharmacy, Electronics, Clothing, Travel).
- Fee income is the sum of postings of type FEE. Profit paid to customers is the sum of postings of type PRF.

## 7. Personal data

The CBS holds personal data protected under the Personal Data Protection Law (PDPL): customer names, national IDs, dates of birth, gender, mobile numbers, email addresses, account numbers and IBANs. Access is limited to the roles that need it; extracts to other systems must be approved by the Data Protection Officer, and test environments use masked copies only.
