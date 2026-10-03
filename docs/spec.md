# PRD: DAWAM — Data Analysis & Warehouse Architecture Modeler

| | |
|---|---|
| Name | **DAWAM**: Data Analysis & Warehouse Architecture Modeler (دوام, "continuity") |
| Status | Draft v3 (AI fully in scope, pluggable self-hosted or cloud LLMs; only DW implementation deferred), awaiting review |
| Date | 2026-10-03 |
| License | Open source (recommended: Apache-2.0) |
| Distribution | Self-hosted (Docker Compose), not sold |

---

## 1. Problem Statement

Data engineers, system analysts and data analysts building a data warehouse go through the same painful sequence on every project:

1. They analyse source systems by hand: connecting to databases, exploring tables, guessing relationships that are not declared as foreign keys, and reading scattered documentation (SAD documents, Confluence pages, Jira tickets, code repositories).
2. They design the warehouse (facts, dimensions, grain, SCD types, KPIs) in a mix of whiteboards, draw.io files and Word documents that quickly drift out of date.
3. They write source-to-target mapping sheets in Excel. These sheets are the contract between the analyst and the engineer, yet they have no versioning, no validation and no link to the actual source schema.
4. Lineage ("where does this KPI come from?") lives in people's heads.

Each step lives in a different tool, nothing is connected, and nothing tells the team whether the design is complete or sound. When the source schema changes, nobody knows which mappings and KPIs are affected.

On top of that, nobody systematically finds personal data in sources before it is copied into the warehouse, which matters under Saudi Arabia's PDPL. KPI definitions start from a blank page on every project. And answering simple questions about the project ("which tables feed revenue?") means digging through several tools.

## 2. Solution

A self-hosted web application where a team works through a data-warehouse project in connected stages, with every artifact traceable back to the real source schema.

| Stage | What happens | Key outputs |
|---|---|---|
| **1. Source System Analysis** | Connect to source databases (required), extract metadata, profile data, discover relationships, attach supporting material (repo links, Jira, Confluence, SAD documents). | Source catalog, data profile, relationship map, source documentation |
| **2. DW Design** | Design the target model (facts, dimensions, attributes, grain, SCD), document KPIs, map source to target, view lineage, and get a design-quality score. | Target model, KPI catalog, mapping specs, lineage graph, DW score, DDL |
| **3. DW Implementation** (next phase) | Turn mappings into implementation artifacts. | dbt project skeleton, SQL transforms, load-order plan |

Stages 1 and 2 and everything below are in scope for the first release. Stage 3 is the only part planned for the next phase.

Four capabilities run across all stages:

| Capability | What it does |
|---|---|
| **PII Detection** | Scans source column names and sampled values for personal data, tracks review decisions, and follows PII through lineage into the warehouse. |
| **KPI Suggestions** | Proposes KPIs from the target model, from a domain KPI library, and from the AI assistant. |
| **DW Schema Scoring** | Scores the warehouse schema per project, schema layer and table, with fix hints and an optional approval gate. |
| **AI Assistant** | An agentic chat in every project that answers questions, generates and updates files, and proposes model changes that a human approves. It runs on any pluggable model: self-hosted (vLLM, SGLang, Ollama) or a cloud API. |

Access is controlled at two levels. **System roles** (Admin, User) govern the installation. **Project roles** (Owner, Editor, Viewer) govern who can see and change each project. A project and its source systems are visible only to its members.

## 3. Glossary

| Term | Meaning |
|---|---|
| Project | A DW initiative, the top-level container. Holds sources, the target model, KPIs, mappings and members. |
| Source System | A logical system being analysed (e.g. "Core Banking", "CRM"). Has one or more connections and supporting artifacts. |
| Connection | Credentials and settings for reaching one source database. |
| Snapshot | A point-in-time capture of a source's metadata (schemas, tables, columns, keys). |
| Profile | Statistics about a source column (null %, distinct count, min/max, patterns). Aggregates only. |
| Inferred Relationship | A relationship between columns discovered by the tool rather than declared as a FK. |
| Target Model | The warehouse design: facts, dimensions, bridges and their attributes. |
| Grain | The precise meaning of one row in a fact table. |
| KPI | A business metric with a definition, formula and owner, linked to target columns. |
| Mapping | A rule describing how one target column is populated from one or more source columns. |
| Lineage | The graph linking source columns → mappings → target columns → KPIs. |
| DW Schema Score | An automated score of the warehouse schema's design quality, computed per project, per schema layer and per table. |
| PII Finding | A detection that a source column likely holds personal data, with category, confidence and evidence (never the values themselves). |
| KPI Suggestion | A proposed KPI from rules, a domain template or the assistant, waiting to be accepted or rejected. |
| Assistant | The project's AI agent. It answers questions, generates or updates files and proposes changes. |
| Change Set | A group of proposed changes shown as a diff and applied only after a human approves it. |
| Project File | A versioned file in the project's file area, generated by the tool or the assistant, or uploaded by a member. |
| LLM Provider | A configured model endpoint: self-hosted (vLLM, SGLang, Ollama or any OpenAI-compatible server) or a cloud API. |

## 4. Roles & Permissions

### 4.1 System roles

| Role | Description |
|---|---|
| **Admin** | Manages the installation: users, registration settings, system configuration. |
| **User** | Can create projects and be added to other people's projects. |

Admins are users too. An admin does **not** see project content unless they are a member of that project. Admins can see project metadata (name, owner, member count, created date) to support ownership recovery.

### 4.2 Project roles

| Role | Description |
|---|---|
| **Owner** | Full control including members, credentials, deletion. A project can have several owners. |
| **Editor** | Can change all analysis and design content. Cannot manage members or view and edit connection credentials. |
| **Viewer** | Read-only access to all project content and exports. |

### 4.3 Permission matrix

| Action | Admin (non-member) | Owner | Editor | Viewer |
|---|:-:|:-:|:-:|:-:|
| See project in list (metadata only) | ✅ | ✅ | ✅ | ✅ |
| Open project content | ❌ | ✅ | ✅ | ✅ |
| Rename / edit project details | ❌ | ✅ | ❌ | ❌ |
| Archive / delete project | ✅ | ✅ | ❌ | ❌ |
| Add / remove members, change roles | ❌ | ✅ | ❌ | ❌ |
| Transfer or reassign ownership | ✅ | ✅ | ❌ | ❌ |
| Create / delete source systems | ❌ | ✅ | ✅ | ❌ |
| Create connection, edit credentials | ❌ | ✅ | ❌ | ❌ |
| Run extraction / profiling on a connection | ❌ | ✅ | ✅ | ❌ |
| Edit source documentation & artifacts | ❌ | ✅ | ✅ | ❌ |
| Edit target model, KPIs, mappings | ❌ | ✅ | ✅ | ❌ |
| Comment | ❌ | ✅ | ✅ | ✅ |
| View lineage, scores, exports | ❌ | ✅ | ✅ | ✅ |
| View project activity log | ❌ | ✅ | ✅ | ✅ |
| Review PII findings, set PII handling | ❌ | ✅ | ✅ | ❌ |
| Manage project PII rules | ❌ | ✅ | ❌ | ❌ |
| Accept / reject KPI suggestions | ❌ | ✅ | ✅ | ❌ |
| Set minimum score gate, disable score checks | ❌ | ✅ | ❌ | ❌ |
| Ask the assistant questions | ❌ | ✅ | ✅ | ✅ |
| Let the assistant generate files and propose changes | ❌ | ✅ | ✅ | ❌ |
| Approve, apply and revert change sets | ❌ | ✅ | ✅ | ❌ |
| Enable AI for the project; choose its model, data-sharing level and internal-only restriction | ❌ | ✅ | ❌ | ❌ |
| Upload, edit, restore project files | ❌ | ✅ | ✅ | ❌ |
| Configure LLM providers, models, roles and budgets (system-wide) | ✅ | ❌ | ❌ | ❌ |

Authorization is enforced **on the server for every request**. The UI only hides controls for convenience.

---

## 5. User Stories

Actors: **Visitor** (not signed in), **User**, **Admin**, **Owner**, **Editor**, **Viewer**, **Member** (any project role).

### Epic A: Authentication

1. As a visitor, I want to sign up with email, display name and password when self-registration is enabled, so that I can start using the tool.
2. As a visitor, I want to sign in with email and password, so that I can access my projects.
3. As a user, I want my session to stay signed in for a configurable period, so that I don't log in constantly.
4. As a user, I want to sign out, which ends my session on the server, so that a shared machine is safe.
5. As a user, I want to sign out of all my sessions at once, so that I can recover if a device is lost.
6. As a user, I want to reset a forgotten password through an emailed one-time link, so that I can regain access.
7. As a user, I want to change my password when I know the current one, so that I can rotate it.
8. As a user, I want to edit my display name, so that teammates recognise me.
9. As a user, I want to switch the interface between English and Arabic (with right-to-left layout), so that I can work in my preferred language.
10. As a visitor, I want login to slow down and temporarily lock after repeated failures, so that my account resists brute force.
11. As a visitor invited by email, I want to accept the invitation and set my password, so that I can join without open registration.
12. As a user, I want to sign in with my organisation's identity provider through OpenID Connect (e.g. Microsoft Entra ID, Google Workspace, Keycloak) when the admin has configured it, so that I can use my existing work account.
13. As a user, I want to turn on two-factor authentication with an authenticator app (TOTP) and receive recovery codes, so that my account stays safe even if my password leaks.

### Epic B: Administration

14. As the first person to install the tool, I want an admin account created from environment variables on first boot, so that the installation is never left without an admin.
15. As an admin, I want to list, search and filter all users, so that I can manage the installation.
16. As an admin, I want to invite a user by email, so that people can join while self-registration is off.
17. As an admin, I want to deactivate and reactivate a user, so that leavers lose access immediately without losing their history.
18. As an admin, I want to promote a user to admin or demote an admin, so that admin duties can be shared.
19. As an admin, I want the system to block demoting or deactivating the last active admin, so that the installation stays manageable.
20. As an admin, I want to force a password reset for a user, so that I can respond to a suspected compromise.
21. As an admin, I want to turn self-registration on or off and optionally restrict it to allowed email domains, so that I control who joins.
22. As an admin, I want to see a list of all projects with owner, member count and dates, without seeing their content, so that I can support users while respecting privacy.
23. As an admin, I want to reassign ownership of a project whose owners have all been deactivated, so that projects are never orphaned.
24. As an admin, I want to see a system audit log of security events (logins, failed logins, role changes, deactivations), so that I can investigate incidents.
25. As an admin, I want to configure SMTP settings, so that invitation and reset emails work.
26. As an admin, I want to configure OIDC single sign-on (issuer, client ID and secret, allowed email domains, automatic account creation) and optionally disable password login, so that access follows our company directory.
27. As an admin, I want to require two-factor authentication for all users or for admins only, so that accounts meet our security policy.

### Epic C: Projects & Sharing

28. As a user, I want to create a project with a name, description and business domain, so that I can start a DW initiative.
29. As a user, I want to see a list of projects I am a member of, with my role in each, so that I can navigate my work.
30. As an owner, I want to add an existing user to my project by email with a role, so that colleagues can collaborate.
31. As an owner, I want to invite someone who has no account yet by email, so that they join the installation and the project in one step (only if I am permitted to invite: admin, or self-registration enabled).
32. As an owner, I want to change a member's role, so that access matches responsibility.
33. As an owner, I want to remove a member, so that people who left the project lose access.
34. As a member, I want to leave a project, so that my list stays relevant; the last owner cannot leave without transferring ownership.
35. As an owner, I want to archive a project, which makes it read-only, so that finished work is preserved.
36. As an owner, I want to delete a project after typing its name to confirm, so that I can remove it permanently.
37. As a member, I want to see the project's stage progress (Source Analysis, DW Design, ETL), so that I know where the work stands.
38. As a member, I want a project activity feed (who changed what and when), so that I can follow the team's work.

### Epic D: Source Systems & Connections

39. As an editor, I want to create a source system with a name, description, business owner and technical owner, so that each source is documented.
40. As an owner, I want to add a database connection to a source system (PostgreSQL, MySQL/MariaDB, SQL Server, Oracle, Snowflake, BigQuery), so that metadata can be extracted.
41. As an owner, I want to test a connection before saving it, so that I catch configuration errors early.
42. As an owner, I want stored credentials to be encrypted and never shown again after saving, so that secrets are protected.
43. As an owner, I want the tool to warn me if the connection user has write privileges, so that I'm encouraged to use a read-only account.
44. As an owner, I want to restrict which schemas the tool may read, so that out-of-scope or sensitive schemas are never touched.
45. As an editor, I want to extract metadata from a connection into a new snapshot (schemas, tables, views, columns, data types, nullability, PKs, FKs, indexes, row-count estimates, table/column comments), so that I have an accurate source catalog.
46. As an editor, I want extraction to run in the background with visible progress, so that large databases don't block the UI.
47. As an editor, I want to re-extract and see a diff against the previous snapshot (added, removed and changed tables and columns), so that I can track source changes.
48. As a member, I want to be told which mappings and KPIs are affected when a new snapshot removes or changes a column, so that schema drift is caught early.

### Epic E: Source Analysis

49. As a member, I want to browse the source catalog by schema, table and column with search, so that I can explore quickly.
50. As an editor, I want to profile selected tables (row count, null %, distinct count, min/max, top-N value frequencies, length stats, detected patterns such as email or phone), so that I understand data quality.
51. As an owner, I want to choose per table whether top-N values may be stored, and to have columns flagged as sensitive never sampled, so that personal data is not copied into the tool.
52. As an editor, I want profiling to respect a configurable row-sample limit and query timeout, so that production sources aren't overloaded.
53. As a member, I want the tool to suggest inferred relationships based on name similarity, matching types and value overlap, each with a confidence score, so that undeclared joins are discovered.
54. As an editor, I want to accept or reject inferred relationships, so that the relationship map reflects reality.
55. As a member, I want an ER diagram of a source (declared and accepted relationships) that I can filter by schema or table, so that I can understand the structure visually.
56. As an editor, I want to add business descriptions, tags (e.g. `PII`, `master-data`, `transactional`) and a sensitivity flag to tables and columns, so that the catalog carries business meaning.
57. As an editor, I want to classify each source table (master, transactional, reference, log, staging), so that DW design decisions are informed.
58. As a member, I want to see a source summary dashboard (table count, documented %, profiled %, relationships found), so that I can gauge analysis completeness.

### Epic F: Supporting Artifacts (optional per source)

59. As an editor, I want to link a code repository URL to a source system, so that engineers can find the application code.
60. As an editor, I want to upload SAD and other documents (PDF, DOCX, MD, XLSX, images) to a source system, so that documentation lives with the analysis.
61. As an editor, I want to link Jira issues and Confluence pages by URL with a title and note, so that relevant context is one click away.
62. As an owner, I want to connect Jira and Confluence with an API token and import selected issues or pages (by JQL, project, space or page tree) as read-only artifacts that I can refresh on demand, so that their content is searchable and available to the assistant.
63. As an editor, I want to link any artifact to specific tables or columns, so that context appears where it's needed.
64. As a member, I want to download any uploaded document, so that I can read it offline.

### Epic G: DW Design: Target Model

65. As an editor, I want to create target schemas or layers (e.g. `staging`, `core`, `mart`), so that the warehouse is organised.
66. As an editor, I want to create fact tables with a mandatory grain statement and a fact type (transactional, periodic snapshot, accumulating snapshot, factless), so that facts are well-defined.
67. As an editor, I want to create dimension tables with an SCD type (0, 1, 2) per dimension and override it per attribute, so that history handling is explicit.
68. As an editor, I want to add attributes to target tables with name, data type, nullability, description and role (surrogate key, natural key, foreign key, measure, attribute, audit column), so that the model is complete.
69. As an editor, I want measures on facts to declare an additivity (additive, semi-additive, non-additive), so that BI usage is correct.
70. As an editor, I want to link fact foreign keys to dimensions, so that the star schema is captured.
71. As an editor, I want to mark a dimension as conformed and reuse it across facts, so that integration is enforced.
72. As an editor, I want to create a target table from a source table (copy columns as a starting point), so that I don't retype everything.
73. As a member, I want a visual star/snowflake diagram of the target model, so that I can review the design.
74. As an editor, I want a bus matrix (facts × conformed dimensions), so that the enterprise view is clear.
75. As an editor, I want naming conventions per project (prefixes such as `dim_`/`fact_`, case style) to be validated, so that the model is consistent.
76. As a member, I want to generate DDL for the target model in a chosen dialect (PostgreSQL, SQL Server, Snowflake, BigQuery), so that the design is deployable.

### Epic H: KPI Documentation

77. As an editor, I want to document a KPI with name, business definition, formula (in words and in SQL), unit, aggregation, owner, refresh frequency and target values, so that metrics have one agreed definition.
78. As an editor, I want to link a KPI to the target measures and dimensions it uses, so that it's traceable.
79. As a member, I want to see each KPI's full lineage back to source columns, so that I can answer "where does this number come from?".
80. As an editor, I want KPI status (draft, in review, approved), so that sign-off is visible.

### Epic I: Source-to-Target Mapping

81. As an editor, I want to create a mapping for each target column specifying source column(s), a transformation rule in plain language and an optional SQL expression, so that the build contract is precise.
82. As an editor, I want to define, per target table, the source join path (driving table, joins and filters), so that the row set is unambiguous.
83. As an editor, I want to mark a target column as derived, constant, system-generated (surrogate key, audit column) or not yet mapped, so that coverage is honest.
84. As an editor, I want the tool to validate that mapped source columns exist in the latest snapshot and that the SQL expression parses, so that errors are caught early.
85. As an editor, I want a data-type compatibility warning when a source type may not fit the target type (e.g. truncation), so that load failures are prevented.
86. As a member, I want to see mapping coverage per target table and for the whole project, so that I know what's left.
87. As an editor, I want mapping status (draft, in review, approved) and to comment on individual mappings, so that analysts and engineers can review together.
88. As a member, I want to export mappings as XLSX and CSV in a familiar mapping-sheet layout, so that I can share them with people outside the tool.
89. As an editor, I want to import mappings from an XLSX in the same layout, with a validation report before anything is saved, so that existing Excel work can be migrated.

### Epic J: Lineage

90. As a member, I want a column-level lineage graph from source column through mapping to target column to KPI, so that dependencies are visible.
91. As a member, I want to start the lineage graph from any node and expand upstream or downstream, so that I can explore impact.
92. As a member, I want an impact report for a source column (all target columns and KPIs that depend on it), so that I can assess changes.

### Epic K: DW Schema Scoring

93. As a member, I want an overall DW schema score (0–100) and a letter grade, with a breakdown by category (completeness, dimensional modeling, consistency, traceability, documentation, performance readiness, privacy), so that I can see design quality at a glance.
94. As a member, I want a score for each target schema layer and each target table, so that I can find the weakest parts of the design.
95. As a member, I want a star-schema health card for each fact table (grain, linked dimensions, conformed dimensions, measure additivity, date dimension), so that I can review one star at a time.
96. As a member, I want each failed check to have a severity (error, warning, info), link to the object at fault and give a fix hint, so that improving the score is actionable.
97. As a member, I want the score recalculated automatically after every design change, so that it is always current.
98. As an owner, I want to disable individual checks for my project with a reason, so that the score reflects our deliberate decisions.
99. As an owner, I want to set a minimum score and block moving the design to "approved" while it has errors or is below that score, so that sign-off means something.
100. As a member, I want to see the score trend over time and compare the current score against a baseline, so that progress is visible.
101. As a member, I want to ask the assistant to explain a failed check and propose a fix as a change set, so that I can resolve issues faster.
102. As a member, I want to export the score report as Markdown or XLSX, so that I can share it in design reviews.

### Epic L: Collaboration & History

103. As a member, I want to comment on tables, columns, target objects, KPIs and mappings, with threads that can be resolved, so that review happens in context.
104. As a member, I want to @mention project members in comments, so that the right person is notified in-app.
105. As a member, I want to see the change history of a mapping, KPI or target object (who, when, old vs new), so that decisions are auditable.
106. As an editor, I want to create a named version (baseline) of the DW design, so that I can compare later changes against a signed-off state.

### Epic M: Exports & Documentation

107. As a member, I want to export a project documentation pack (source catalog, target model, KPIs, mappings, lineage summary) as Markdown and HTML, so that I can publish it.
108. As a member, I want to export the source data dictionary as XLSX, so that business users can review it.

### Epic N: DW Implementation (next phase)

109. As an editor, I want to generate a dbt project skeleton (sources.yml from snapshots, one model per target table with SELECT built from mappings), so that implementation starts from the agreed design.
110. As an editor, I want a suggested load order based on dependencies (dimensions before facts), so that pipelines are sequenced correctly.
111. As an editor, I want SCD2 dimensions to generate the matching snapshot/merge template, so that history handling is implemented consistently.

### Epic O: PII Detection

112. As an editor, I want every new snapshot scanned automatically for PII by column name, using a dictionary of English and Arabic-transliterated keywords (e.g. `national_id`, `iqama`, `hawiya`, `mobile`, `jawal`, `email`, `iban`, `birth_date`), so that obvious personal data is flagged immediately.
113. As an editor, I want to run a value-based PII scan on selected tables that checks sampled values in memory against patterns such as Saudi national ID and Iqama numbers (with checksum validation), Saudi mobile numbers, Saudi IBANs, emails, card numbers, commercial registration numbers and dates of birth, so that PII hidden behind vague column names is found.
114. As an owner, I want sampled values used for PII scanning to be discarded after the scan and never stored or shown, so that the scan itself doesn't leak personal data.
115. As a member, I want each finding to show its category (direct identifier, quasi-identifier, sensitive/special category, financial), its confidence and its evidence (which rule matched and on what share of sampled rows), so that I can judge it quickly.
116. As an editor, I want a PII review queue where I confirm or dismiss findings, so that the sensitive flags are trustworthy.
117. As an editor, I want a confirmed finding to set the column's sensitive flag and PII category, excluding it from top-N profiling and from anything sent to the AI assistant beyond its name, so that protection is automatic.
118. As an owner, I want to add custom PII rules for my project (name keywords, regex and category), so that organisation-specific identifiers such as employee or customer numbers are caught.
119. As a member, I want PII to propagate through lineage, so that any target column fed by a confirmed PII column is flagged as PII-derived.
120. As an editor, I want to record a handling decision for every PII-derived target column (keep, mask, hash, tokenise, generalise, drop) with a note, so that privacy is designed in rather than bolted on.
121. As a member, I want new suspected PII columns from a re-extraction highlighted in the snapshot diff, so that new personal data isn't missed.
122. As a member, I want to export a PII inventory (source and target columns, category, handling decision, reviewer) as XLSX or Markdown, so that I can support PDPL records of processing and audits.

### Epic P: KPI Suggestions

123. As an editor, I want the tool to suggest KPIs from the target model using rules (sums and averages of additive measures, distinct counts of dimension keys, ratios such as average order value, and period-over-period growth when a date dimension exists), with the formula SQL pre-filled, so that I don't start from a blank page.
124. As an editor, I want to pick a business domain (e.g. retail/e-commerce, banking, telecom, healthcare, HR, government services) and see KPIs from a built-in library that match my model, so that I benefit from industry-standard metrics.
125. As an editor, I want to tag target columns with a semantic type (amount, quantity, price, customer key, transaction date and so on), so that library KPIs can be matched to my model.
126. As a member, I want each suggestion to show its origin (rule, library or assistant), the reasoning behind it, and a feasibility status (computable now, needs mapping, needs new data), so that I can prioritise.
127. As an editor, I want to see KPI gaps, meaning library KPIs my model cannot compute yet, with the missing measures or dimensions listed, so that I know what to add to the design.
128. As an editor, I want to ask the assistant for KPI suggestions based on the project's domain, target model and uploaded documents, so that suggestions reflect the actual business.
129. As an editor, I want to accept a suggestion (optionally editing it first), which creates a draft KPI already linked to its measures and dimensions, so that adopting it takes one click.
130. As an editor, I want rejected suggestions to stay hidden unless I restore them, so that the list doesn't keep repeating itself.

### Epic Q: AI Assistant

131. As a member, I want a chat panel on every page of a project that knows which object I'm looking at, so that I can ask about it without explaining the context.
132. As a member, I want to ask questions about the project in plain language (e.g. "which tables hold customer data?", "where does the Net Revenue KPI come from?", "what changed in the last snapshot?") and get answers that link to the objects they mention, so that I can navigate by asking.
133. As a member, I want to ask questions about uploaded documents (SAD documents, Markdown, DOCX, text-based PDFs) and get answers that cite the document and section, so that I don't have to read everything myself.
134. As a member, I want to see which tools the assistant used and what it looked at for each answer, so that I can verify it.
135. As an editor, I want to ask the assistant to generate files (mapping sheets, DDL, data dictionary, KPI documentation, dbt models, documentation pages), which are saved to the project's file area as new versioned files, so that deliverables are produced on request.
136. As an editor, I want to ask the assistant to update an existing file (e.g. "add the loyalty columns to the customer mapping sheet" or "rewrite section 3 of this Markdown document"), which saves a new version with a diff I can review, so that files evolve without manual editing.
137. As an editor, I want to ask the assistant to change the design itself (target tables and columns, mappings, KPIs, SCD settings, PII handling, or fixes for score issues), with every change presented as a change set showing a diff, so that nothing changes without my approval.
138. As an editor, I want to approve all, approve some, or reject the items in a change set, so that I stay in control.
139. As an editor, I want to revert an applied change set, so that mistakes are easy to undo.
140. As a viewer, I want to ask questions but not generate files or propose changes, so that read-only access stays read-only.
141. As a member, I want the assistant to act with my permissions only and never see connection credentials, so that it can't do anything I couldn't do myself.
142. As a member, I want my conversations saved in the project, private to me unless I share one with the project's members, so that I can come back to them.
143. As a member, I want to stop a response while it is running, so that I can redirect it quickly.
144. As an editor, I want large requests (e.g. "draft mappings for all 40 columns of fact_sales") to run as a background job with progress and end in one change set, so that big tasks don't time out.
145. As an admin, I want to register one or more LLM providers, either self-hosted (vLLM, SGLang, Ollama or any OpenAI-compatible server) or cloud (Anthropic, OpenAI, Azure OpenAI, Google Gemini, AWS Bedrock or any OpenAI-compatible API), with base URL, API key and model name, so that each installation uses the models it trusts.
146. As an admin, I want to test a provider and see whether its model supports tool calling, streaming and how large its context window is, so that I know it will work before anyone uses it.
147. As an admin, I want to assign models to roles (an agent model, an optional lighter model for small tasks, and an embedding model for document search), each from any registered provider, so that cost and quality are balanced.
148. As an admin, I want to mark each provider as internal (self-hosted in our network) or external, so that data exposure is always visible.
149. As an admin, I want to set a monthly token budget for the installation and optionally per project, so that usage and cost are controlled.
150. As an owner, I want to choose which approved model my project uses and restrict my project to internal providers only, so that sensitive projects never send data outside our network.
151. As a member, I want the assistant to reply in the language I write in (Arabic or English), so that the conversation feels natural.
152. As an owner, I want to turn the assistant on or off for my project and choose what it may see (metadata only; metadata and profile statistics; metadata, profiles and documents), so that data exposure matches our policy.
153. As an admin, I want to see AI usage per project and per user, so that I can manage the budget.

### Epic R: Project Files

154. As a member, I want a file area in each project listing generated and uploaded files with type, size, author and last update, so that all deliverables are in one place.
155. As an editor, I want every change to a file to create a new version recording who changed it, how (by hand, by export or by the assistant) and a note, so that history is never lost.
156. As a member, I want to compare two versions of a text file (Markdown, SQL, YAML, CSV) as a diff, so that I can see exactly what changed.
157. As an editor, I want to restore an older version as the current one, so that I can roll back.
158. As an editor, I want to edit Markdown, SQL and YAML files in the browser, so that small fixes don't need a download.
159. As a member, I want to download any file, or the whole file area as a zip, so that I can use the outputs elsewhere.

---

## 6. Functional Requirements by Module

### 6.1 Authentication

- **Password storage:** argon2id with per-password salt. Minimum length 10, checked against a common-password list.
- **Sessions:** server-side sessions stored in the database. The session ID is sent in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie. Idle timeout is 8 hours and absolute timeout 14 days, both configurable.
- **CSRF:** double-submit token required on all state-changing requests.
- **Rate limiting:** login attempts are limited per IP and per account. After 5 consecutive failures the account locks for 15 minutes (configurable). Locking is logged.
- **Password reset:** single-use token, valid 30 minutes, stored hashed. Using it invalidates all of the user's sessions. The response is identical whether or not the email exists.
- **Invitations:** single-use token, valid 7 days, bound to an email and optionally to a project and role.
- **Bootstrap admin:** `ADMIN_EMAIL` and `ADMIN_PASSWORD` env vars create the first admin on first boot only, if no admin exists.
- **Without SMTP:** invitation and reset links are shown to the admin to copy manually, so the tool works on air-gapped installs.
- **OIDC SSO:** authorization-code flow with PKCE against any OpenID Connect provider. Users are matched by verified email, and new users can be created automatically with the User role if the admin allows it. Admins can disable password login once SSO works, but the bootstrap admin keeps a password as break-glass access.
- **Two-factor (TOTP):** RFC 6238 authenticator apps, with 10 single-use recovery codes stored hashed. Admins can require it for everyone or for admins only. For SSO sign-ins, MFA is left to the identity provider.

### 6.2 Authorization

- One central policy function: `can(user, action, resource) -> bool`. Every endpoint calls it. No ad-hoc checks inside handlers.
- Project-scoped resources always resolve their `project_id` server-side from the resource itself, never from client input.
- Deactivated users are rejected at session validation, and their sessions are deleted on deactivation.
- A project must always have at least one owner. This is enforced in the service layer and backed by a database constraint or trigger.

### 6.3 Source connectivity

- **Engines:** PostgreSQL, MySQL/MariaDB, SQL Server, Oracle, Snowflake, BigQuery. Each engine is a connector plugin implementing one interface (`test`, `list_schemas`, `extract`, `profile`, `sample`), so more engines can be added without touching the rest.
- **Credentials:** encrypted with AES-256-GCM using a key from the `ENCRYPTION_KEY` env var. The key rotation procedure is documented. Credentials are never returned by the API; it returns `has_password: true` instead.
- Every source query runs with a statement timeout (default 30 s) and in a read-only transaction where the engine supports it.
- Extraction and profiling run as **background jobs** with status (`queued`, `running`, `succeeded`, `failed`, `cancelled`), progress percentage and a log.
- **Allowed-schemas list per connection:** anything outside it is never queried.
- **Jira and Confluence import** uses each product's REST API with a per-project API token, encrypted like connection credentials. Imported issues and pages are stored as read-only artifacts with their text extracted and indexed. A refresh job re-pulls them on demand.

### 6.4 Profiling & privacy

- Stores aggregates only: counts, null %, distinct count, min/max (except for sensitive columns), and length statistics.
- Top-N values are **off by default**. They can be enabled per table by an owner and are never captured for columns flagged sensitive.
- Sampling uses `TABLESAMPLE` or a `LIMIT`-based approach with a configurable cap (default 100 000 rows).
- Columns with confirmed PII findings (§6.9) are automatically excluded from top-N and min/max capture.

### 6.5 Relationship inference

Rule-based, no AI. Candidate pairs are scored on:

- Name similarity, e.g. `customer_id` ↔ `customers.id` or `cust_id`
- Exact type compatibility
- The target column being unique (PK or unique index, or profiled distinct = rows)
- Value-overlap ratio from a sample (only when profiling has been run)

The output is a confidence score from 0 to 1. Only candidates at or above a threshold (default 0.6) are shown.

### 6.6 DW Schema Scoring

Scoring is rule-based and deterministic. It recalculates after every design change (debounced) and stores a `ScoreRun`.

| Category | Check | Severity |
|---|---|---|
| Completeness | Every fact has a grain statement | Error |
| Completeness | Every target column has a mapping or is marked system-generated | Warning |
| Completeness | Every KPI links to at least one measure | Warning |
| Dimensional modeling | Every dimension has a surrogate key and a natural key | Error |
| Dimensional modeling | Every fact has at least one dimension FK | Error |
| Dimensional modeling | No fact-to-fact foreign keys | Error |
| Dimensional modeling | Facts with time-based measures link to a date dimension | Warning |
| Dimensional modeling | Every measure declares additivity | Warning |
| Dimensional modeling | Every dimension declares an SCD type | Warning |
| Dimensional modeling | Snowflaking deeper than one level | Info |
| Consistency | Naming conventions respected | Warning |
| Consistency | Same-named columns across tables share the same data type | Warning |
| Consistency | Mart-layer tables are sourced only from core-layer tables | Warning |
| Consistency | Conformed dimensions used by ≥ 2 facts | Info |
| Traceability | All mapped source columns exist in the latest snapshot | Error |
| Traceability | No mapping references a column flagged as removed | Error |
| Documentation | Descriptions present on all target tables, measures and KPIs | Info |
| Performance readiness | Surrogate keys use integer types | Warning |
| Performance readiness | Free-text attributes or text measures on fact tables | Warning |
| Performance readiness | Fact tables wider than 60 columns | Info |
| Privacy | Every PII-derived target column has a handling decision | Error |
| Privacy | No unmasked PII-derived columns in the mart layer | Warning |

**Formula.** Severity weights are error 10, warning 3 and info 1. A table's score is the weighted pass percentage of the checks that apply to it. A schema layer's score is the average of its tables' scores, weighted by column count. The project score is the weighted pass percentage across all checks. Any unresolved error caps the grade at C.

**Grades.** A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, F < 60.

**Gate.** Owners can set a minimum score. The design cannot move to `approved` while errors exist or the score is below the minimum.

Disabled checks are excluded from the formula and listed separately with their reasons.

### 6.7 Lineage

- Lineage edges come from mappings (source column → target column) and KPI links (target column → KPI).
- Source columns referenced inside a mapping's SQL expression are extracted with an SQL parser (sqlglot) and added as edges.
- Lineage is computed on read from the mapping tables (no separate lineage store). Graph queries use recursive CTEs.

### 6.8 Import / export formats

- **Mapping sheet (XLSX/CSV) columns:** `target_schema, target_table, target_column, target_type, source_system, source_schema, source_table, source_column(s), transformation_rule, sql_expression, mapping_type, status, notes`
- **DDL** generated through sqlglot transpilation into the chosen dialect
- **Documentation pack:** Markdown files zipped, plus a single-file HTML
- Every export can be saved into the project file area (§6.12) as well as downloaded.

### 6.9 PII detection

- **Name rules** use a keyword dictionary in English and Arabic transliteration, matched on normalised table and column names (lower case, separators removed). They run automatically on every new snapshot and put no load on the source.
- **Value rules** run on demand as a background job. Up to N rows per column are sampled (default 1 000) and tested in memory, and only the match ratio is recorded. Values are discarded when the job ends and are never logged.
- **Built-in value validators:**
  - Saudi national ID (starts with 1) and Iqama (starts with 2): 10 digits with checksum
  - Saudi mobile: `05xxxxxxxx` or `+9665xxxxxxxx`
  - Saudi IBAN: `SA` + 22 characters, mod-97 check
  - Email
  - Payment card (Luhn)
  - Commercial registration number
  - Date of birth (date columns where the implied age falls in a human range)
  - IPv4/IPv6 address
- **Confidence** combines the name match and the value match ratio. Findings at or above 0.5 enter the review queue.
- **Categories:** direct identifier, quasi-identifier, sensitive/special category (health, religion and similar), financial.
- **Propagation:** a target column is PII-derived if any upstream source column in its lineage is confirmed PII. This is recomputed whenever mappings change.
- **Handling decisions** are stored on target columns. Missing decisions feed the Privacy checks in §6.6.

### 6.10 KPI suggestions

- **Rule engine (deterministic).** For each fact, the engine proposes:
  - `SUM` and `AVG` of additive measures
  - `COUNT(DISTINCT)` of key dimension FKs
  - Simple ratios between measures of the same fact
  - Month-over-month and year-over-year growth when a date dimension is linked

  Formula SQL is generated against the target model.
- **Domain library.** Versioned YAML files in the repo (`kpi-library/*.yaml`), so the community can contribute. Each template lists the semantic types it requires (e.g. `amount`, `transaction_date`, `customer_key`) and a formula pattern. A template matches when the model has columns with those semantic types.
- **Assistant suggestions** come from the assistant's `suggest_kpis` tool (§6.11) and are stored as `KpiSuggestion` rows with origin `assistant` and a rationale.
- **Feasibility** has three values:
  - *computable*: all inputs exist and are mapped
  - *needs mapping*: the inputs exist but are unmapped
  - *needs data*: the inputs are missing from the model

### 6.11 AI assistant

- **Agent loop.** The backend runs a tool-use loop against the model the project is configured to use. The agent depends only on the LLM gateway interface (§6.13), never on a vendor SDK. Responses stream to the browser over Server-Sent Events.
- **Tools** call the same service layer as the REST API. Every call goes through `can(user, action, resource)` as the requesting user.

| Tool | Kind | Purpose |
|---|---|---|
| `search_catalog` | read | Find source/target tables and columns by name, tag or description |
| `get_object` | read | Read a table, column, KPI, mapping or file with its details |
| `get_lineage` | read | Upstream/downstream lineage for a node |
| `get_snapshot_diff` | read | Changes between snapshots |
| `get_score` | read | Current score, failed checks and fix hints |
| `get_pii_findings` | read | PII findings and handling status (never values) |
| `search_documents` | read | Full-text search over extracted document text, returning cited passages |
| `list_files` / `read_file` | read | Project file area |
| `run_validation` | read | Validate mappings and model, return problems |
| `suggest_kpis` | write (suggestion) | Create KPI suggestions |
| `propose_changes` | write (change set) | Propose creates, updates and deletes on the target model, mappings, KPIs and PII handling |
| `generate_file` | write (file) | Create a new project file (through the exporters for DDL, XLSX and dbt; free text for Markdown) |
| `update_file` | write (file) | Save a new version of an existing text file |

- **Human in the loop.** `propose_changes` never writes to the model. It creates a `ChangeSet` in `pending` state, shown as a diff in the chat. An editor approves all or some items, which applies them in one transaction and records them in the activity log. An applied change set can be reverted as long as the affected objects haven't changed since. File tools always create new versions, so they are reversible through file history.
- **Data minimisation.** The assistant never receives connection credentials, sampled values, top-N values or row data. Everything else it sees follows the project's data-sharing level (metadata only; plus profile statistics; plus documents). Columns confirmed as PII are sent by name and category only. When a project is restricted to internal providers, nothing leaves the organisation's network.
- **Prompt-injection posture.** Document text, comments and descriptions are passed as quoted data, and the system prompt tells the model to treat them as data. Permissions are enforced in the tool executor and every write needs human approval, so an injected instruction cannot escalate access or change anything silently.
- **Context.** The object on the current page is sent as context. The agent pulls everything else through tools instead of having the whole project placed in the prompt.
- **Document text** is extracted on upload (pypdf for text PDFs, python-docx for DOCX, raw for MD/TXT) and indexed with Postgres full-text search. Documents are also chunked and embedded with the configured embedding model into pgvector, and search combines full-text and vector results. Without an embedding model, search falls back to full-text only. Scanned PDFs without a text layer are marked "no text found".
- **Long tasks** that the agent judges to be large run as a `Job` and end in a single change set.
- **Limits.** Tool calls are capped per request (default 25), and the installation's monthly token budget is checked before each model call. Every run is logged with tokens, tools used and duration.

### 6.12 Project files

- Files live in the configured storage backend. Each change creates a `FileVersion`, and nothing is overwritten.
- Text formats (MD, SQL, YAML, CSV, JSON) support in-browser editing and line diffs.
- Binary formats (XLSX, DOCX, PDF) are versioned but not diffed. An exporter-produced XLSX can be regenerated from the current model.

### 6.13 LLM gateway (pluggable models)

Every AI feature (the assistant, assistant KPI suggestions, document embedding) goes through one internal gateway. Nothing else in the codebase imports a vendor SDK.

- **Interface:** `chat(messages, tools, stream) -> events`, `embed(texts) -> vectors` and `capabilities() -> {...}`. The gateway normalises messages, tool calls, streaming events, token usage and errors into one internal format.
- **Adapters:**

| Adapter | Covers | Configuration |
|---|---|---|
| `openai_compatible` | **vLLM, SGLang, Ollama**, LM Studio, llama.cpp server, OpenAI, OpenRouter, Groq, Together, Mistral, DeepSeek, and any server exposing `/v1/chat/completions` and `/v1/embeddings` | Base URL, optional API key, model name |
| `anthropic` | Claude via the Anthropic API | API key, model |
| `azure_openai` | Azure OpenAI deployments | Endpoint, deployment, API version, key or Entra ID |
| `gemini` | Google Gemini API and Vertex AI | API key or service account, model |
| `bedrock` | Models on AWS Bedrock | Region, IAM credentials or role, model ID |

- **Plugins:** adapters register through a Python entry point (`dawam.llm_providers`), so a new provider can ship as a separate package without changing the core.
- **Self-hosting notes:** vLLM and SGLang must be started with tool calling enabled and a tool-call parser that matches the model (vLLM: `--enable-auto-tool-choice --tool-call-parser <parser>`; SGLang: `--tool-call-parser <parser>`). Ollama is reached through its OpenAI-compatible `/v1` endpoint, and supports tools for models that advertise them. The setup docs include a tested recipe for each server.
- **Capability detection:** "Test connection" sends a short prompt and a dummy tool call, then records tool support, streaming support, JSON-schema output support, context window (from server metadata or admin input) and, for embedding models, the vector dimension.
- **Fallback for models without native tool calling:** tools are described in the system prompt, and the model answers with a JSON tool call that is validated against the tool's JSON Schema and retried up to 2 times if invalid. These models are shown as "limited" in the UI. Write tools still go through change sets.
- **Model roles:** `agent` (required for the assistant), `light` (optional; conversation titles, short summaries, KPI rationales) and `embedding` (optional; document search). Each role points to any provider and model. Owners can override the agent model per project from an admin-approved list.
- **Internal vs external:** every provider is flagged internal or external. For projects set to internal-only, the gateway refuses requests to external providers.
- **Context management:** the gateway knows each model's context window. The agent trims or summarises older turns and large tool results to fit, which matters for smaller self-hosted models.
- **Reliability:** provider errors map to common codes (auth, rate limit, context overflow, unavailable). Rate limits and 5xx errors are retried with backoff. Timeouts are configurable per provider.
- **Usage:** token counts come from the provider when it reports them and are estimated otherwise. They feed installation and project budgets.
- **Secrets:** API keys are encrypted like connection credentials and never returned by the API.
- **Embedding changes:** switching the embedding model, or a model whose vector dimension differs, triggers a re-indexing job.
- **Local quick start:** Docker Compose includes an optional `ollama` profile (`docker compose --profile ollama up`) for a fully local setup with no external calls.

---

## 7. Domain Model

```
User(id, email UNIQUE, display_name, password_hash, system_role[admin|user],
     is_active, failed_login_count, locked_until, created_at, last_login_at)
Session(id, user_id, created_at, last_seen_at, expires_at, ip, user_agent)
Invitation(id, email, token_hash, invited_by, project_id?, project_role?, expires_at, accepted_at)
PasswordReset(id, user_id, token_hash, expires_at, used_at)
SystemSetting(key, value)                         -- registration, allowed domains, SMTP
AuditEvent(id, actor_id?, event_type, target_type, target_id, metadata JSON, ip, created_at)

Project(id, name, description, domain, status[active|archived], naming_rules JSON,
        created_by, created_at, updated_at)
ProjectMember(project_id, user_id, role[owner|editor|viewer], added_by, added_at)  PK(project_id,user_id)
ActivityEvent(id, project_id, actor_id, verb, object_type, object_id, diff JSON, created_at)

SourceSystem(id, project_id, name, description, business_owner, technical_owner, repo_url?)
Connection(id, source_system_id, engine, host, port, database, username,
           secret_encrypted, options JSON, allowed_schemas[], last_tested_at, created_by)
Job(id, project_id, type[extract|profile|infer_relationships|export], status,
    progress, params JSON, log TEXT, started_at, finished_at, created_by)
Snapshot(id, connection_id, job_id, taken_at, is_latest)
SrcSchema(id, snapshot_id, name)
SrcTable(id, schema_id, name, kind[table|view], row_estimate, comment,
         classification?, description?, tags[], is_sensitive)
SrcColumn(id, table_id, name, ordinal, data_type, is_nullable, is_pk, default,
          comment, description?, tags[], is_sensitive, pii_category?, status[present|removed])
SrcConstraint(id, table_id, type[pk|fk|unique], columns[], ref_table_id?, ref_columns[])
ColumnProfile(id, column_id, job_id, row_count, null_pct, distinct_count, min, max,
              avg_len, max_len, top_values JSON?, patterns[], profiled_at)
Relationship(id, from_column_id, to_column_id, origin[declared|inferred|manual],
             confidence, status[suggested|accepted|rejected])
Artifact(id, source_system_id, kind[file|repo|jira|confluence|link], title, url?,
         storage_key?, mime?, size?, note, uploaded_by,
         extracted_text?, text_status[none|extracted|no_text_found])
ArtifactLink(artifact_id, object_type[table|column], object_id)

TgtSchema(id, project_id, name, layer[staging|core|mart|other])
TgtTable(id, schema_id, name, kind[fact|dimension|bridge|other], fact_type?, grain?,
         scd_type?, is_conformed, description, status)
TgtColumn(id, table_id, name, ordinal, data_type, is_nullable, role[sk|nk|fk|measure|attribute|audit],
          additivity?, scd_type_override?, references_table_id?, description,
          semantic_type?, is_pii_derived)
Kpi(id, project_id, name, definition, formula_text, formula_sql, unit, aggregation,
    owner, refresh_frequency, targets JSON, status)
KpiLink(kpi_id, tgt_column_id)
TableMapping(id, tgt_table_id, driving_src_table_id, joins JSON, filters TEXT, notes)
ColumnMapping(id, tgt_column_id, mapping_type[direct|derived|constant|system|unmapped],
              rule_text, sql_expression, status, validation JSON, updated_by, updated_at)
ColumnMappingSource(column_mapping_id, src_column_id)
Comment(id, project_id, object_type, object_id, parent_id?, author_id, body, resolved_at?, created_at)
Baseline(id, project_id, name, created_by, created_at, payload JSON)   -- frozen design copy
ScoreRun(id, project_id, score, grade, breakdown JSON, per_layer JSON, per_table JSON, created_at)
ScoreCheckResult(score_run_id, check_code, severity, object_type, object_id, passed)
DisabledCheck(project_id, check_code, reason, disabled_by)
ScoreGate(project_id, min_score, block_on_errors)

PiiRule(id, project_id?, name, category, name_keywords[], value_regex?, validator?, enabled)
                                                  -- project_id null = built-in rule
PiiFinding(id, src_column_id, rule_id, category, confidence, evidence JSON,
           status[suggested|confirmed|dismissed], reviewed_by?, reviewed_at?)
PiiHandling(tgt_column_id, method[keep|mask|hash|tokenise|generalise|drop], note, decided_by)

KpiTemplate(id, domain, name, definition, formula_pattern, required_semantic_types[], library_version)
KpiSuggestion(id, project_id, origin[rule|template|assistant], payload JSON, rationale,
              feasibility[computable|needs_mapping|needs_data], status[suggested|accepted|rejected])

ProjectFile(id, project_id, path, kind[generated|uploaded], mime, current_version_id)
FileVersion(id, file_id, version_no, storage_key, size, created_by,
            created_via[user|export|assistant], change_note, created_at)

LlmProvider(id, name, adapter[openai_compatible|anthropic|azure_openai|gemini|bedrock|<plugin>],
            base_url?, api_key_encrypted?, extra_config JSON, is_internal, enabled, last_tested_at)
LlmModel(id, provider_id, model_name, display_name, kind[chat|embedding], supports_tools,
         supports_streaming, supports_json_schema, context_window, embedding_dim?, enabled)
ModelRoleAssignment(role[agent|light|embedding], model_id)               -- installation defaults
AiBudget(scope[installation|project], project_id?, monthly_token_limit)
ProjectAiSettings(project_id, enabled, data_level[metadata|metadata_profiles|metadata_profiles_documents],
                  agent_model_id?, internal_providers_only)
DocumentChunk(id, source_type[artifact|file_version], source_id, ordinal, text, tsv, embedding vector?)
UserMfa(user_id, totp_secret_encrypted, recovery_codes_hashed[], enabled_at)
UserIdentity(user_id, provider, subject, email)                          -- OIDC links
Conversation(id, project_id, user_id, title, shared_with_project, created_at)
Message(id, conversation_id, role[user|assistant|tool], content JSON, created_at)
AssistantRun(id, message_id, tool_calls JSON, input_tokens, output_tokens, duration_ms, status)
ChangeSet(id, project_id, conversation_id?, created_by, items JSON,
          status[pending|applied|partially_applied|rejected|reverted], applied_by?, applied_at?)
```

Source objects are tied to a **snapshot**. Mappings reference `SrcColumn` rows from the latest snapshot. On re-extraction, columns are matched by `(schema, table, column)` name, existing IDs are kept for unchanged columns, and missing ones are marked `status=removed` rather than deleted. This keeps mappings and drift detection intact.

---

## 8. Implementation Decisions

These are recommended defaults. Any of them can be changed during review.

### 8.1 Architecture

- **Modular monolith.** One backend service, one frontend, one Postgres database, one background worker. This fits a solo maintainer and keeps self-hosting simple.
- **Deep modules with narrow interfaces**, each owning its tables and exposing a service API: `auth`, `admin`, `projects` (incl. membership & authorization policy), `sources` (connections, extraction, snapshots), `analysis` (profiling, inference), `artifacts`, `design` (target model, KPIs), `mapping`, `lineage`, `scoring`, `collaboration` (comments, activity, baselines), `exports`, `jobs`, `pii`, `kpi_suggestions`, `files`, `assistant` (agent loop, tools, change sets), `llm_gateway` (provider adapters, capability detection, budgets).
- Modules talk only through service interfaces, never by reading another module's tables directly.

### 8.2 Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | Python 3.12 + FastAPI | Best ecosystem for DB introspection and SQL parsing |
| DB introspection | SQLAlchemy `inspect()` + engine drivers (psycopg, PyMySQL, pyodbc/pymssql) | One API across many engines |
| SQL parsing / DDL | sqlglot | Parses and transpiles many dialects; used for lineage and DDL |
| Metadata store | PostgreSQL 16 | JSONB, recursive CTEs, robust |
| Migrations | Alembic | Standard with SQLAlchemy |
| Background jobs | Postgres-backed queue (`SELECT … FOR UPDATE SKIP LOCKED`) worker | No extra infrastructure (no Redis needed) |
| File storage | Local volume by default, S3-compatible optional | Self-host friendly |
| Frontend | React + TypeScript + Vite, TanStack Query, React Flow (diagrams & lineage) | Mature graph tooling |
| XLSX | openpyxl | Import/export |
| Packaging | Docker Compose (`app`, `worker`, `db`, optional `ollama` profile) | One-command install; fully local AI optional |
| LLM access | Own thin gateway: the `openai` SDK for every OpenAI-compatible server (vLLM, SGLang, Ollama, many cloud APIs), plus Anthropic, Azure OpenAI, Gemini and Bedrock adapters | One internal interface; self-hosted and cloud models are interchangeable |
| Chat streaming | Server-Sent Events | Simple one-way streaming |
| Document text | pypdf, python-docx | Text extraction for assistant search (no OCR) |
| Document search | Postgres full-text search + pgvector | Hybrid search with no extra infrastructure |
| SSO / MFA | Authlib (OIDC), pyotp (TOTP) | Standard, well-maintained libraries |

### 8.3 API

- REST + JSON under `/api/v1`, with an OpenAPI spec generated by FastAPI. The frontend client is generated from the OpenAPI spec.
- Errors use a consistent shape: `{ "error": { "code", "message", "details" } }`.
- List endpoints use cursor pagination.
- Optimistic concurrency on editable objects via a `version` field. A stale write returns `409`.

Key endpoint groups:

```
POST   /auth/register | /auth/login | /auth/logout | /auth/logout-all
POST   /auth/password/forgot | /auth/password/reset | /auth/password/change
POST   /auth/invitations/{token}/accept
GET    /me

GET    /admin/users            POST /admin/users/invite
PATCH  /admin/users/{id}       (role, is_active)   POST /admin/users/{id}/force-reset
GET    /admin/projects         POST /admin/projects/{id}/reassign-owner
GET|PUT /admin/settings        GET /admin/audit

GET|POST /projects             GET|PATCH|DELETE /projects/{id}
GET|POST /projects/{id}/members  PATCH|DELETE /projects/{id}/members/{userId}
GET    /projects/{id}/activity

…/projects/{id}/sources, /sources/{id}/connections, /connections/{id}/test,
/connections/{id}/extract, /snapshots/{id}/diff, /tables, /columns,
/profile, /relationships, /artifacts

…/projects/{id}/target/schemas|tables|columns, /kpis, /mappings,
/mappings/import, /mappings/export, /lineage?node=…&direction=…&depth=…,
/score, /baselines, /comments, /exports/ddl?dialect=…, /exports/docs

…/projects/{id}/pii/findings, /pii/rules, /pii/handling, /pii/inventory/export
…/projects/{id}/kpi-suggestions (GET; POST generate; PATCH accept | reject)
…/projects/{id}/score/history, /score/gate, /score/report
…/projects/{id}/files, /files/{fid}/versions, /files/{fid}/versions/{v}/diff?against=…, /files/{fid}/restore
…/projects/{id}/assistant/conversations, /conversations/{cid}/messages (POST → SSE stream), /conversations/{cid}/stop
…/projects/{id}/change-sets/{csid} (GET), /apply, /reject, /revert
GET|POST /admin/llm/providers   PATCH|DELETE /admin/llm/providers/{id}   POST /admin/llm/providers/{id}/test
GET|POST /admin/llm/models      PUT /admin/llm/roles    GET|PUT /admin/llm/budgets    GET /admin/ai-usage
GET|PUT  /projects/{id}/ai-settings
GET|PUT  /admin/sso            GET /auth/oidc/login     GET /auth/oidc/callback
POST     /auth/mfa/enroll | /auth/mfa/verify | /auth/mfa/disable

GET    /jobs/{id}              POST /jobs/{id}/cancel
```

### 8.4 Security baseline

- OWASP ASVS Level 1 as a minimum
- Security headers: CSP, HSTS (when behind TLS), `X-Content-Type-Options`, `frame-ancestors 'none'`
- Uploads: 25 MB limit by default, MIME sniffing with an allow-list, stored under random keys, served with `Content-Disposition: attachment`
- Secrets only from env vars, never in the database except encrypted connection credentials
- No source data rows are ever persisted, except opt-in top-N values

---

## 9. Non-Functional Requirements

| Area | Requirement |
|---|---|
| Performance | Extract metadata for a 2 000-table database in < 5 min. Catalog search < 300 ms. Lineage graph of 500 nodes renders < 2 s. |
| Source safety | No source query runs longer than the configured timeout. Profiling is sampled. Everything is read-only. |
| Concurrency | Several editors on the same project. Conflicting edits are detected via the `version` field. |
| Availability | Single-node self-hosted. Backups via documented `pg_dump` + file-volume copy. |
| i18n | UI strings externalised from day one. English and Arabic (RTL) in the first release. |
| Accessibility | Keyboard-navigable core flows, WCAG AA contrast. |
| Observability | Structured JSON logs, `/healthz` and `/readyz`, job logs visible in the UI. |
| Install | `docker compose up` with a documented `.env.example`. Works offline after image pull. With a self-hosted LLM (vLLM, SGLang or Ollama), the whole tool, AI included, runs fully offline and air-gapped. Cloud models need outbound access to the chosen provider only. |
| Assistant | First streamed token in < 3 s for simple questions. |
| Scoring | Recalculation in < 2 s for a 200-table target model. |
| AI data exposure | Only what the project's data-sharing level allows is sent to the provider. Credentials, sampled values and row data are never sent. With internal providers, nothing leaves the network. |
| LLM portability | Full agent mode works with any model that has native tool calling and a context window of at least 32k tokens. Other models run in limited mode. |

---

## 10. Testing Decisions

- **Test behaviour through module public interfaces**, not internals. A good test calls the service or HTTP API and asserts on outputs and side effects.
- **Authorization gets a table-driven test:** every endpoint × every role (anonymous, user non-member, viewer, editor, owner, admin non-member), asserting allow/deny against the permission matrix in §4.3. This is the single most important test suite.
- **Auth flows:** registration toggle, lockout, reset-token single use and expiry, session invalidation on password change and deactivation, last-admin and last-owner protections.
- **Connectors:** integration tests against real PostgreSQL, MySQL and SQL Server containers (Testcontainers) seeded with a sample schema that includes undeclared relationships, so inference can be verified.
- **Snapshot diff & drift:** re-extract after `ALTER TABLE` changes. Assert that IDs are preserved, removed columns are flagged and affected mappings are reported.
- **Lineage & scoring:** fixture-based tests with small hand-built projects and known expected graphs and scores.
- **Import/export:** round-trip export → import and assert no changes. A malformed XLSX produces a validation report and writes nothing.
- **Frontend:** component tests for complex editors (mapping grid, model editor), plus a small Playwright suite covering sign in → create project → add member → extract → map → view lineage.
- **PII detection:** unit tests per validator (valid and invalid Saudi IDs, Iqamas, IBANs, mobiles, cards) and name-rule tests including Arabic transliterations. One test asserts that no sampled value appears in the database, logs or job output after a scan.
- **Scoring:** pass and fail fixtures for every check. The formula, grade cap and approval gate are tested end to end.
- **KPI suggestions:** fixture models with known expected suggestions and feasibility. Library YAML is validated in CI.
- **Assistant:** a fake LLM provider replays scripted tool calls, so the agent loop, change-set creation, partial approval and revert are tested deterministically. A table-driven test confirms every tool refuses actions the user's role can't perform, and that viewers can't trigger write tools. A small set of real questions against a sample project is run against the real model before each release.
- **Files:** version creation, diff and restore. Assistant updates always create a new version.
- **LLM gateway:** a contract test suite every adapter must pass (streaming, tool-call round trip, error mapping, usage reporting), run against recorded fixtures. A CI job runs the assistant against Ollama with a small tool-capable model. Before each release the evaluation set runs against vLLM, SGLang and at least one cloud model. The prompted-tool fallback is tested with malformed and invalid JSON.
- **SSO and MFA:** the OIDC flow is tested against a Keycloak container. TOTP enrolment, verification, recovery codes and enforcement policies are covered.
- **Connectors:** Oracle runs in a container like the others. Snowflake and BigQuery are tested against recorded fixtures, with an optional live job when credentials are available.

---

## 11. Delivery Phases

**Release 1 contains everything in this document except DW implementation (Epic N).** Phases 0–5 are the build order within Release 1, and each is shippable on its own. Use this ordering when turning the PRD into GitHub issues.

**Phase 0: Foundations.** Repo, CI, Docker Compose, DB migrations, module skeletons, error format, OpenAPI client generation.

**Phase 1: Auth, Admin & Projects** (Epics A, B, C). Bootstrap admin, sessions, invitations, password reset, OIDC SSO, TOTP two-factor, admin console, projects, membership, authorization policy with the full permission test suite, activity log, English/Arabic UI shell with RTL.

**Phase 2: Source Analysis & PII** (Epics D, E, F, and scanning and review from O). Connector plugin interface with PostgreSQL, MySQL/MariaDB and SQL Server first, then Oracle, Snowflake and BigQuery. Encrypted credentials, background jobs, extraction, snapshots & diff, catalog, profiling, relationship inference, ER diagram, documentation/tags, artifacts with text extraction, Jira/Confluence import, PII scans, review queue and custom rules.

**Phase 3: LLM Gateway & Assistant foundations** (Epic Q). Provider registry and adapters (OpenAI-compatible for vLLM, SGLang, Ollama and cloud APIs; Anthropic; Azure OpenAI; Gemini; Bedrock), capability detection, prompted-tool fallback, model roles, budgets, internal-only projects, document chunking and hybrid search, and the agent loop with read tools for project and document Q&A. AI comes this early so every later feature ships with its assistant tools.

**Phase 4: DW Design core & Files** (Epics G, H, I, R, P). Target model editor & diagram, bus matrix, semantic types, KPIs, KPI suggestions from rules, library and assistant, mappings, validation, coverage, XLSX import/export, DDL export, project file area with versions. The assistant gains file generation and update tools, and change sets with approve and revert.

**Phase 5: Insight, Scoring & Collaboration** (Epics J, K, L, M, and propagation and handling from O). Lineage graph & impact report, DW schema scoring with gate, PII propagation and handling, PII inventory export, comments & mentions, history, baselines, documentation pack. The assistant gains lineage, score-explanation and fix tools, and long-running assistant jobs.

**Next phase: DW Implementation** (Epic N). dbt project generation, load-order planning, SCD2 templates, and assistant tools to generate and update them. Whether the tool also runs pipelines and loads data into the warehouse is decided when this phase is specified.

---

### 11.1 Estimated timeline

These estimates assume **Claude Code writes the code and one person reviews, tests and steers it**. At this pace, review is the bottleneck, not code generation: every phase needs time to read diffs, run the app, test against real databases and models, and send fixes back. "Full-time" means about 35–40 review hours per week; "part-time" means about 15.

| Phase | Scope | Full-time | Part-time |
|---|---|---|---|
| 0 | Foundations: repo, CI, Docker Compose, migrations, module skeletons | 1 week | 2 weeks |
| 1 | Auth, Admin & Projects, incl. SSO, MFA and the Arabic/RTL shell | 2–3 weeks | 5–6 weeks |
| 2 | Source Analysis & PII, incl. six connectors and Jira/Confluence import | 4–5 weeks | 8–10 weeks |
| 3 | LLM Gateway & Assistant foundations | 3–4 weeks | 6–8 weeks |
| 4 | DW Design core & Files (model editor, diagrams, mapping grid: the heaviest UI) | 5–6 weeks | 10–12 weeks |
| 5 | Insight, Scoring & Collaboration | 3–4 weeks | 6–8 weeks |
| Release | Hardening: security review, install docs, LLM server recipes, evaluation set, bug fixing | 2 weeks | 4 weeks |
| **Total** | **Release 1** | **≈ 20–25 weeks (5–6 months)** | **≈ 41–50 weeks (10–12 months)** |

**Lean MVP option.** A first public version with PostgreSQL/MySQL/SQL Server connectors, extraction and catalog, name-based PII detection, target model, mappings with XLSX export, DDL export, and an assistant on the OpenAI-compatible adapter (Q&A plus file generation) takes about **8–10 weeks full-time** or **4–5 months part-time**. The remaining stories then become follow-up releases.

**Main schedule risks:**

- **Review throughput.** If review falls behind, generated code piles up unverified. Keep issues small and merge often.
- **Visual editors.** The model diagram, lineage graph and mapping grid usually take the most iteration to feel right.
- **Cloud connectors.** Snowflake and BigQuery need test accounts, and Oracle containers are slow and heavy.
- **Self-hosted model quality.** Tool calling on smaller vLLM, SGLang or Ollama models needs real testing and prompt tuning.
- **Arabic/RTL.** Supporting RTL throughout adds friction to every UI story, not just one.

## 12. Out of Scope

Not planned for any phase. DW implementation is not listed here because it is the next phase (§11).

- OCR of scanned documents (text-based PDFs are supported)
- The assistant applying changes without human approval, or acting on its own schedule
- Fine-tuning models, or managing model servers (the tool connects to vLLM, SGLang, Ollama or cloud APIs that you run or subscribe to; the optional Ollama Compose profile is a convenience)
- Storing or browsing raw source data rows
- Real-time co-editing with live cursors (optimistic locking only)
- Multi-tenant SaaS hosting, billing or organisations above projects
- Non-relational sources (MongoDB, APIs, files as sources)

## 13. Assumptions to Confirm

1. **DW schema scoring** scores the *target* warehouse schema (§6.6). A separate score for how DW-ready the *source* tables are (data quality, key stability) is not included, but could be added in Phase 5.
2. **Admins cannot read project content** unless they are members. This favours privacy, but some teams may prefer admin oversight.
3. **Self-hosted only** for an open-source, not-for-sale tool. No hosted multi-tenant version is planned.
4. **Python/FastAPI backend.** Chosen for the data-tooling ecosystem. If you prefer another backend stack you know well, only §8.2 and parts of §8.3 change.
5. **Source engines** in the first release are PostgreSQL, MySQL/MariaDB, SQL Server, Oracle, Snowflake and BigQuery.
6. **"Project (source system) shared with users I add"** is implemented as project-level membership: adding someone to a project gives access to all its source systems. Per-source-system permissions are not included.
7. **No AI provider is bundled by default.** Each installation registers its own, either a self-hosted model (vLLM, SGLang, Ollama) or a cloud API key, and pays for its own usage if any.
8. **The assistant's default data-sharing level** is *metadata only*. Owners opt in to sending profile statistics or documents.
9. **Initial KPI library domains** are retail/e-commerce, banking, telecom, healthcare, HR and government services, starting with roughly 15–25 KPIs each.
10. **Minimum model for full agent mode** is native tool calling and a context window of at least 32k tokens. Weaker models run in limited mode (prompted tools, shorter history).
