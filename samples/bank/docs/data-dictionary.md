<!-- build: xlsx -->
# Data dictionary: Core Banking and CRM

One sheet per system, one row per column.

## Core Banking

| Table | Column | Data type | Nullable | Key | Business description |
| --- | --- | --- | --- | --- | --- |
| branches | branch_id | integer | No | PK | Internal identifier of the branch. |
| branches | branch_code | varchar(6) | No | Unique | Branch code used across systems: city prefix (RUH, JED, DMM, KHB, MKK) and a number. CRM stores it for each relationship manager. |
| branches | branch_name | varchar(100) | No | | Name of the branch as shown to customers. |
| branches | city | varchar(50) | No | | City the branch is in. |
| branches | region | varchar(20) | No | | Region the branch reports to: Central, Western or Eastern. |
| branches | opened_on | date | No | | Date the branch opened. |
| branches | is_active | boolean | No | | False when the branch no longer opens accounts; existing accounts are still serviced. |
| account_types | account_type_code | varchar(10) | No | PK | Product code: CUR, PAY, SAV, YTH, TD3, TD12. |
| account_types | type_name | varchar(60) | No | | Product name. |
| account_types | product_category | varchar(20) | No | | current, savings or term_deposit. |
| account_types | minimum_balance_sar | numeric(18,2) | No | | Minimum balance in SAR; below it a monthly fee is charged (savings) or the account cannot be opened (term deposits). |
| account_types | dormancy_months | integer | Yes | | Months without a customer-initiated transaction after which an account becomes dormant. Empty for term deposits, which never become dormant. |
| account_types | profit_rate_pct | numeric(5,2) | Yes | | Annual profit rate in percent; empty for current accounts. |
| account_types | is_active | boolean | No | | Whether the product can still be opened. |
| transaction_types | txn_type_code | varchar(10) | No | PK | Posting type code: DEP, WDL, TRF_IN, TRF_OUT, POS, SAL, FEE, PRF. |
| transaction_types | type_name | varchar(60) | No | | Posting type name. |
| transaction_types | direction | varchar(6) | No | | credit (money in) or debit (money out). |
| transaction_types | is_fee | boolean | No | | True for fee postings (fee income). |
| transaction_types | is_customer_initiated | boolean | No | | True when the customer started the transaction; only these keep an account from becoming dormant. |
| customers | customer_id | integer | No | PK | Internal identifier of the customer. |
| customers | customer_no | varchar(12) | No | Unique | Customer number, CB followed by 7 digits. The key other systems (CRM) use. |
| customers | full_name | varchar(150) | No | | First name, father's name and family name. Personal data. |
| customers | national_id | varchar(10) | No | Unique | Saudi national ID, 10 digits starting with 1, verified at onboarding. Personal data. |
| customers | date_of_birth | date | No | | Date of birth. Personal data. |
| customers | gender | char(1) | No | | M or F. Personal data. |
| customers | mobile_number | varchar(13) | No | | Mobile number in international format (+9665 and 8 digits). Personal data. |
| customers | email | varchar(150) | Yes | | Email address, when the customer gave one. Personal data. |
| customers | city | varchar(50) | No | | City of residence (the home branch's city). |
| customers | home_branch_id | integer | No | | The customer's home branch (branches.branch_id). No foreign key is declared. |
| customers | segment | varchar(10) | No | | retail, premium or private. Reviewed every quarter. |
| customers | kyc_status | varchar(10) | No | | verified, pending or expired. |
| customers | status | varchar(10) | No | | active or inactive (asked to leave the bank). |
| customers | onboarded_on | date | No | | Date the customer was onboarded. |
| accounts | account_id | integer | No | PK | Internal identifier of the account. |
| accounts | account_number | varchar(18) | No | Unique | 18-digit account number. Personal data. |
| accounts | iban | varchar(24) | No | Unique | Saudi IBAN: SA, 2 check digits, bank code 77 and the account number. Personal data. |
| accounts | customer_id | integer | No | FK | The account holder (customers.customer_id). |
| accounts | account_type_code | varchar(10) | No | FK | The product (account_types.account_type_code). |
| accounts | branch_id | integer | No | | The branch that holds the account (branches.branch_id). No foreign key is declared. |
| accounts | currency_code | char(3) | No | | Always SAR. |
| accounts | opened_on | date | No | | Date the account was opened. |
| accounts | closed_on | date | Yes | | Date the account was closed; empty while it is open. |
| accounts | status | varchar(10) | No | | active, dormant or closed. See the dormancy rule in the Core Banking SAD. |
| accounts | current_balance | numeric(18,2) | No | | Balance in SAR at the end of the last business date. Zero for closed accounts. |
| accounts | last_activity_date | date | Yes | | Business date of the last posted transaction of any type. Empty for term deposits with no postings. |
| transactions | transaction_id | bigint | No | PK | Internal identifier of the posting. |
| transactions | reference_no | varchar(12) | No | Unique | Posting reference shown on statements (TXN and 9 digits). |
| transactions | account_id | integer | No | FK | The account posted to (accounts.account_id). |
| transactions | txn_type_code | varchar(10) | No | | Posting type (transaction_types.txn_type_code). No foreign key is declared. |
| transactions | channel | varchar(10) | No | | mobile, online, atm, branch, card or system. |
| transactions | transaction_date | date | No | | Business date of the posting. |
| transactions | posted_at | timestamp | No | | Exact time the posting was made. |
| transactions | amount_sar | numeric(18,2) | No | | Amount in SAR, always positive; the type's direction says whether it is a credit or a debit. |
| transactions | merchant_category | varchar(30) | Yes | | Merchant category of a card purchase; empty for other types. |

## CRM

| Table | Column | Data type | Nullable | Key | Business description |
| --- | --- | --- | --- | --- | --- |
| relationship_managers | rm_id | int | No | PK | Internal identifier of the relationship manager. |
| relationship_managers | employee_no | varchar(10) | No | Unique | HR employee number. |
| relationship_managers | full_name | varchar(100) | No | | Name of the RM. Personal data. |
| relationship_managers | email | varchar(150) | No | | Work email address. Personal data. |
| relationship_managers | mobile_number | varchar(10) | No | | Work mobile number (05 and 8 digits). Personal data. |
| relationship_managers | branch_code | varchar(6) | No | | Branch the RM works at; the Core Banking branch code. |
| relationship_managers | hired_on | date | No | | Date the RM joined the bank. |
| relationship_managers | is_active | tinyint(1) | No | | 0 when the RM has left; their contacts are reassigned. |
| complaint_categories | category_code | varchar(10) | No | PK | Category code: CARD, FEES, TRANSFER, ATM, APP, ACCOUNT, SERVICE, LOAN. |
| complaint_categories | category_name | varchar(60) | No | | Category name. |
| complaint_categories | sla_days | int | No | | Calendar days within which a complaint of this category must be resolved. |
| complaint_categories | regulator_reportable | tinyint(1) | No | | 1 when complaints of this category are reported to SAMA every month. |
| campaigns | campaign_id | int | No | PK | Internal identifier of the campaign. |
| campaigns | campaign_code | varchar(20) | No | Unique | Campaign code, CMP, year and a number. |
| campaigns | campaign_name | varchar(100) | No | | Campaign name. |
| campaigns | product | varchar(30) | No | | Product promoted: savings, credit_card, payroll_account, term_deposit or personal_finance. |
| campaigns | channel | varchar(10) | No | | sms, email, phone or app. |
| campaigns | target_segment | varchar(10) | Yes | | Segment the offer was designed for; empty means all segments. |
| campaigns | start_date | date | No | | First day of the campaign. |
| campaigns | end_date | date | No | | Last day of the campaign. |
| campaigns | budget_sar | decimal(14,2) | No | | Approved budget in SAR. |
| campaigns | status | varchar(10) | No | | planned, active or completed. |
| contacts | contact_id | int | No | PK | Internal identifier of the contact. |
| contacts | core_customer_no | varchar(12) | Yes | | Core Banking customer number (customers.customer_no); empty for prospects. |
| contacts | full_name | varchar(150) | No | | Name of the contact. Personal data. |
| contacts | id_number | varchar(10) | No | | Saudi national ID of the contact. Personal data. |
| contacts | mobile_number | varchar(10) | No | | Mobile number in local format (05 and 8 digits). Personal data. |
| contacts | email | varchar(150) | Yes | | Email address, when known. Personal data. |
| contacts | city | varchar(50) | No | | City of residence. |
| contacts | segment | varchar(10) | No | | retail, premium or private, as in Core Banking. Prospects are retail. |
| contacts | lifecycle_stage | varchar(10) | No | | customer or prospect. |
| contacts | preferred_channel | varchar(10) | No | | sms, email, phone or app. |
| contacts | marketing_consent | tinyint(1) | No | | 1 when the contact agreed to receive marketing; 0 contacts must not be targeted. |
| contacts | rm_id | int | Yes | | The contact's relationship manager (relationship_managers.rm_id); only premium and private customers have one. No foreign key is declared. |
| contacts | created_at | datetime | No | | When the contact was created in CRM. |
| campaign_responses | response_id | int | No | PK | Internal identifier of the response. |
| campaign_responses | campaign_id | int | No | FK | The campaign (campaigns.campaign_id). |
| campaign_responses | contact_id | int | No | | The contact who responded (contacts.contact_id). No foreign key is declared. |
| campaign_responses | response_date | date | No | | Date of the response, within the campaign's dates. |
| campaign_responses | response_type | varchar(10) | No | | opened, clicked, applied, converted or opted_out. |
| campaign_responses | channel | varchar(10) | No | | Channel the response came through (the campaign's channel). |
| campaign_responses | converted_amount_sar | decimal(14,2) | Yes | | For conversions: amount deposited, financed or credit limit granted, in SAR. Empty otherwise. |
| complaints | complaint_id | int | No | PK | Complaint number. |
| complaints | contact_id | int | No | FK | The complaining contact (contacts.contact_id). |
| complaints | category_code | varchar(10) | No | | The category (complaint_categories.category_code). No foreign key is declared. |
| complaints | channel | varchar(12) | No | | branch, call_center, mobile_app or email. |
| complaints | priority | varchar(6) | No | | high, normal or low. |
| complaints | opened_on | date | No | | Date the complaint was logged. |
| complaints | resolved_on | date | Yes | | Date a resolution was given; empty while open or in progress. |
| complaints | status | varchar(12) | No | | open, in_progress, resolved or closed. |
