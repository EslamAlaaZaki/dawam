<!-- build: docx -->
# CRM: System Architecture Document

Najd National Bank (NNB), Customer Experience Technology. Version 2.4, approved by the Architecture Review Board.

## 1. Purpose and scope

The Customer Relationship Management system (CRM) holds everything NNB knows about the people it talks to, beyond their accounts: prospects, relationship managers, marketing campaigns and their responses, and customer complaints. Account and transaction data stays in Core Banking; CRM links to it through the Core Banking customer number.

## 2. System overview

| Item | Value |
| --- | --- |
| System code | crm |
| Database | MySQL 8, database `crm` |
| Business owner | Head of Customer Experience |
| Technical owner | CRM Applications team (IT) |
| Availability | 07:00 to 23:00 for users; nightly jobs between 01:00 and 06:00 Riyadh time |
| Currency | Amounts are in Saudi riyals (SAR) |
| Data as of | The sample extract is as of 31 December 2025 |

## 3. Modules

- **Contact management**: customers and prospects, with their contact details and marketing consent. Table `contacts`.
- **Relationship management**: relationship managers (RMs) and the contacts they look after. Table `relationship_managers`.
- **Campaign management**: marketing campaigns and every tracked response to them. Tables `campaigns` and `campaign_responses`.
- **Complaints management**: complaints from all channels, their category, SLA and resolution. Tables `complaints` and `complaint_categories`.

## 4. Main tables

| Table | Kind | Rows (approx.) | Purpose |
| --- | --- | --- | --- |
| contacts | Master | 1,000 | Customers (with `core_customer_no`) and prospects (without). |
| relationship_managers | Master | 15 | RMs, with their employee number and branch code. |
| campaigns | Master | 12 | Campaigns, the product each promotes, channel, dates and budget. |
| campaign_responses | Transactional | 2,500 | One row per tracked reaction of a contact to a campaign. |
| complaints | Transactional | 600 per year | Complaints, from opening to resolution. |
| complaint_categories | Reference | 8 | Complaint categories with their SLA in calendar days. |

Some relationships are enforced by the application only, not by foreign keys: `contacts.rm_id` points at `relationship_managers.rm_id`, `campaign_responses.contact_id` at `contacts.contact_id`, and `complaints.category_code` at `complaint_categories.category_code`.

The view `v_open_complaints` lists open and in-progress complaints with their category, SLA and age in days; the complaints desk works from it. The procedure `sp_campaign_results(campaign_code)` returns a campaign's responses, conversions and converted amount by contact segment; Marketing runs it for the post-campaign review.

## 5. Data flows

1. **Customer sync from Core Banking.** At 01:00 every night (job SYNC-01) CRM receives the customer master from Core Banking. A customer is matched to a contact on `core_customer_no`; if none matches, on the national ID; otherwise a new contact is created. Name, national ID, mobile, email, city and segment are overwritten from Core Banking.
2. **Prospects.** Leads from the website and branch walk-ins are created as contacts with `lifecycle_stage = prospect` and no customer number. When the person opens an account, the nightly sync sets the customer number and changes the stage to `customer`.
3. **Campaign responses.** Responses are imported every night at 03:00 (job CMP-03) from the SMS gateway, the email platform and the mobile app's event stream. Phone campaigns are logged by the call center.
4. **Complaints.** Complaints are logged by branches, the call center, the mobile app and email. Every morning at 06:00 (job CMPL-04) the SLA report goes to Compliance; on the 5th of every month the complaints of regulator-reportable categories are reported to the Saudi Central Bank (SAMA).

## 6. Business rules

### 6.1 Contacts and relationship managers

- `core_customer_no` is empty for prospects and is the only link to Core Banking.
- `id_number` holds the contact's Saudi national ID (10 digits).
- Mobile numbers are stored in local format, `05` followed by 8 digits (Core Banking stores the same number as `+9665...`).
- Only premium and private customers have a relationship manager (`rm_id`); retail customers and prospects have none. An RM who leaves (`is_active = 0`) keeps no contacts.
- **Marketing consent.** A contact with `marketing_consent = 0` must not be targeted by any campaign. Consent is captured at onboarding and changes only on the customer's written request.

### 6.2 Campaigns

- Campaign status is `planned`, `active` or `completed`. Responses are only recorded between `start_date` and `end_date`.
- Response types: `opened` (the message was opened), `clicked` (a link was followed), `applied` (an application was started), `converted` (the product was opened within the campaign window) and `opted_out` (the contact asked for no more messages about this campaign).
- `target_segment` is the segment the offer was designed for; empty means all segments.
- **Conversion.** A conversion is a response of type `converted`. `converted_amount_sar` is the amount deposited (savings, term deposits), financed (personal finance) or the credit limit granted (credit cards); it is empty for every other response type.
- **Campaign conversion rate** = conversions / distinct contacts that responded to the campaign.
- **Cost per conversion** = campaign `budget_sar` / conversions.

### 6.3 Complaints

- Complaint status is `open`, `in_progress`, `resolved` (a resolution was given to the customer) or `closed` (the customer confirmed, or 7 days passed after the resolution without a reply). Resolved and closed complaints both have `resolved_on` set.
- **SLA.** Each category has an SLA in calendar days (`complaint_categories.sla_days`), from 2 days for ATM complaints to 15 days for staff and branch service. A complaint **breaches its SLA** when `resolved_on - opened_on` is more than `sla_days`, or when it is still open and older than `sla_days` on the reporting date.
- Priority is `high`, `normal` or `low`. A high-priority complaint is escalated to the branch manager within 24 hours.
- Categories with `regulator_reportable = 1` are included in the monthly SAMA report.

## 7. Personal data

CRM holds personal data protected under the Personal Data Protection Law (PDPL): names, national IDs, mobile numbers and email addresses of contacts and of RMs. Marketing may only use contacts with consent; exports outside CRM need approval from the Data Protection Officer.
