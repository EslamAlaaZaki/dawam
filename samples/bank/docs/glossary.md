# Business glossary: Najd National Bank

Terms used across Core Banking, CRM and the data warehouse, in alphabetical order.

**Account.** A deposit account at the bank (current, payroll, savings, youth savings or term deposit). Held by one customer at one branch. Recorded in Core Banking, table `accounts`.

**Branch.** A physical branch of the bank, identified by its branch code (for example RUH001). Every account is held at a branch and every customer has a home branch.

**Business date.** The banking day a posting belongs to. The end-of-day batch closes the business date at 23:30 Riyadh time; later postings belong to the next business date.

**Campaign.** A marketing action that promotes one product through one channel (SMS, email, phone or app) between a start and an end date. Recorded in CRM.

**Complaint.** An expression of dissatisfaction by a customer that needs a response. Each complaint has one category, which sets its SLA.

**Contact.** A person recorded in CRM: either a customer (with a Core Banking customer number) or a prospect.

**Conversion.** A campaign response of type `converted`: the contact opened the promoted product within the campaign window.

**Conversion rate.** Conversions divided by the number of distinct contacts that responded to the campaign.

**Customer.** A person who holds at least one account, or held one in the past. Identified by the customer number (CB followed by 7 digits) in every system.

**Customer-initiated transaction.** A transaction the customer started: cash deposit, cash withdrawal, outgoing transfer or card purchase. Salary credits, incoming transfers, profit payments and fees are not customer-initiated.

**Digital channels.** The mobile app and online banking.

**Dormant account.** An active current, payroll or savings account with no customer-initiated transaction for 12 months (24 months for Youth Savings). A dormant account accepts credits but no debits until the customer reactivates it at a branch. Term deposits never become dormant.

**Fee income.** The total of postings of type FEE (service fees) in a period.

**IBAN.** International Bank Account Number. A Saudi IBAN is SA, two check digits, the two-digit bank code (77 for NNB) and the 18-digit account number.

**KYC (Know Your Customer).** The identity and address checks a customer must pass. Status is verified, pending or expired; documents expire after 5 years.

**National ID.** The 10-digit Saudi national identity number, starting with 1 for citizens. Personal data.

**PDPL.** The Saudi Personal Data Protection Law. Names, national IDs, dates of birth, mobile numbers, emails, account numbers and IBANs are personal data under it.

**Prospect.** A contact who is not yet a customer: a lead from the website, a branch walk-in or a campaign.

**Relationship manager (RM).** A bank employee who looks after premium and private customers.

**SAMA.** The Saudi Central Bank, the regulator. The bank reports complaints of regulator-reportable categories to SAMA every month.

**SAR.** Saudi riyal, the currency of every amount in these systems.

**Segment.** The customer's value tier: retail, premium or private. Premium and private customers have a relationship manager.

**SLA (complaints).** The number of calendar days within which a complaint of a category must be resolved. A complaint breaches its SLA when it is resolved later than that, or is still open after it.

**Total deposits.** The sum of the current balances of all accounts that are not closed, in SAR.
