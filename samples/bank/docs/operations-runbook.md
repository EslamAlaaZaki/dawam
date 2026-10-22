<!-- build: pdf -->
# Operations Runbook: Core Banking and CRM

Najd National Bank (NNB), IT Operations. Version 5.1. All times are Riyadh time (UTC+3).

## 1. Purpose

This runbook tells the operations team how the Core Banking System (CBS) and CRM are run day to day: the nightly batch schedule, what to check, what to do when a job fails, and who to call. It complements the two System Architecture Documents.

## 2. Systems and owners

| System | Database | Business owner | Technical owner | Support hours |
| --- | --- | --- | --- | --- |
| Core Banking (cbs) | PostgreSQL 16, corebank | Head of Retail Banking Operations | Core Systems team | 24x7 |
| CRM (crm) | MySQL 8, crm | Head of Customer Experience | CRM Applications team | 07:00-23:00, on call at night |

## 3. Nightly batch schedule

The batch window opens at 23:30, when the business date is closed, and must finish by 06:30, before branches and the call center open.

| Job | System | Start | Frequency | What it does | Depends on |
| --- | --- | --- | --- | --- | --- |
| EOD-01 | CBS | 23:30 | Daily | Closes the business date: no more postings for it; later postings go to the next business date. Updates current_balance and last_activity_date. | - |
| INT-02 | CBS | 00:15 | Daily | Accrues profit on savings and term deposits; on the last day of the month posts profit payments (type PRF). | EOD-01 |
| FEE-03 | CBS | 00:45 | Monthly, 1st | Charges the monthly service fee (type FEE) on savings accounts whose average balance last month was below the product minimum (SAR 500). | INT-02 |
| SYNC-01 | CBS to CRM | 01:00 | Daily | Extracts the customer master (customer number, name, national ID, mobile, email, city, segment) and loads it into CRM contacts. | EOD-01 |
| DORM-01 | CBS | 02:30 | Daily | Flags dormant accounts (see section 4) and sends the list to Retail Banking. | EOD-01 |
| CMP-03 | CRM | 03:00 | Daily | Imports campaign responses from the SMS gateway, the email platform and the mobile app. | - |
| BKP-01 | CBS | 04:00 | Daily | Full backup of corebank; WAL archiving runs continuously. | DORM-01 |
| BKP-02 | CRM | 04:30 | Daily | Full backup of crm. | CMP-03 |
| CMPL-04 | CRM | 06:00 | Daily | Sends the complaints SLA report (open complaints, breaches of the day) to Compliance. | SYNC-01 |
| SAMA-05 | CRM | 09:00 | Monthly, 5th | Builds the monthly complaints report for SAMA from the regulator-reportable categories. | CMPL-04 |

## 4. The dormancy job (DORM-01)

DORM-01 sets an account's status to dormant when all of these hold:

- the account is active and its product is a current, payroll or savings account (CUR, PAY, SAV, YTH);
- it has had no customer-initiated transaction (cash deposit, cash withdrawal, outgoing transfer or card purchase) for the product's dormancy period: 12 months, or 24 months for Youth Savings.

Salary credits, incoming transfers, profit payments and fees do not count. Term deposits are never flagged. The job writes the list of newly dormant accounts to Retail Banking's shared folder; branch staff contact those customers within 30 days. A dormant account is reactivated only at a branch, with the customer present and a valid ID.

If DORM-01 fails, re-run it the same morning. It is safe to re-run: an account already dormant is skipped.

## 5. Daily checks

1. By 06:30, confirm in the scheduler that every job of the night finished with status OK.
2. Check that SYNC-01 loaded the same number of customers as Core Banking has (the job log prints both counts). A difference of more than 10 needs a ticket to the CRM Applications team.
3. Check the CMPL-04 report was delivered to Compliance.
4. Check both backups completed and were copied off site.

## 6. When a job fails

- **EOD-01 fails:** this blocks the whole night. Call the Core Systems on-call engineer immediately. Do not start the dependent jobs by hand.
- **INT-02 or FEE-03 fails:** re-run once. If it fails again, raise a P2 incident; Finance must be told before 09:00 because profit and fee postings will be late.
- **SYNC-01 fails:** CRM keeps yesterday's data. Re-run once; if it fails again raise a P3 incident. Campaign selections that day must use yesterday's data.
- **CMP-03 fails:** re-run after 07:00 when the gateways are less busy. Responses are not lost; the gateways keep them for 7 days.
- **CMPL-04 or SAMA-05 fails:** raise a P2 incident and tell Compliance; the SAMA report has a hard deadline on the 10th of the month.

## 7. Incident priorities

| Priority | Meaning | Response time |
| --- | --- | --- |
| P1 | Customers cannot transact (CBS down or EOD-01 not finished at 06:30) | 15 minutes, 24x7 |
| P2 | A regulatory or financial deadline is at risk | 1 hour |
| P3 | Degraded service, a workaround exists | Next business day |

## 8. Month-end and year-end

- No changes to CBS or CRM from the 28th of the month until the 3rd of the next month (month-end freeze).
- On 31 December the business date is closed only after Finance confirms the year-end profit run.

## 9. Access and personal data

Operators use named accounts only. Read-only reporting users (for example the data warehouse and analysis tools) get SELECT on the business tables and nothing else. Never copy production data that contains national IDs, mobile numbers, emails or IBANs to a test system; use the masked copies.
