# Bank sample: Core Banking and CRM

A ready-to-run demo for the whole DAWAM flow. **Najd National Bank** is a made-up Saudi retail bank with two source systems that share customers: **Core Banking** on PostgreSQL (customers, accounts, transactions, branches) and **CRM** on MySQL (contacts carrying the Core Banking customer number, relationship managers, campaigns and their responses, complaints). Both databases come seeded with a year of deterministic data (2025, as of 31 December 2025), a few undeclared relationships, Saudi personal data to detect, and documents to upload. Because the two systems share customers, the warehouse calls for a conformed customer dimension fed by one Mapping Branch per system.

All names, IDs, phone numbers, emails and IBANs are generated. They pass the format and checksum checks DAWAM's PII scan uses, but belong to nobody.

| Path | What it is |
| --- | --- |
| `compose.yaml` | The two databases, as the standalone Compose project `dawam-sample-bank` |
| `databases/corebank/init.sql` | Core Banking: schema, data, a view, a function and the read-only user |
| `databases/crm/init.sql` | CRM: schema, data, a view, a procedure and the read-only user |
| `docs/` | Documents to upload: two System Architecture Documents (DOCX), a data dictionary (XLSX), an operations runbook (PDF) and a glossary (Markdown). Each built file has its Markdown source next to it |
| `kpis.md` | Nine KPIs to enter by hand |

## Run, stop, reset

From the repository root, with Docker running:

```sh
docker compose -f samples/bank/compose.yaml up -d --wait   # start; the first start seeds the data
docker compose -f samples/bank/compose.yaml down           # stop, keep the data
docker compose -f samples/bank/compose.yaml down -v        # stop and delete the data (the next start seeds it again)
```

No `.env` is needed. The databases publish ports **5433** and **3307** on the host; free them first if something else uses them. They have nothing to do with DAWAM's own Compose project, so start DAWAM as usual (see the root `README.md`).

## Connection details

| | Core Banking | CRM |
| --- | --- | --- |
| Engine | PostgreSQL | MySQL |
| Host | `host.docker.internal` | `host.docker.internal` |
| Port | 5433 | 3307 |
| Database | `corebank` | `crm` |
| Username | `dawam_reader` | `dawam_reader` |
| Password | `dawam_reader` | `dawam_reader` |
| Allowed Database Schemas | `banking` | `crm` |

As SQLAlchemy URLs:

```text
postgresql+psycopg://dawam_reader:dawam_reader@host.docker.internal:5433/corebank
mysql+pymysql://dawam_reader:dawam_reader@host.docker.internal:3307/crm
```

DAWAM runs in Docker, so `localhost` would point at DAWAM's own container. `host.docker.internal` reaches the host on Docker Desktop (Windows and macOS). On Linux, use the host's IP address or the Docker bridge address, usually `172.17.0.1`.

`dawam_reader` can only read: on PostgreSQL it has `SELECT` on the `banking` tables and its sessions are read-only (function definitions are readable by default); on MySQL it has `SELECT` and `SHOW VIEW` on `crm`, plus `SHOW_ROUTINE` so DAWAM can read the procedure's body. The connection test therefore shows no write warning. The admin users (`corebank_admin` / `corebank_admin`, `root` / `crm_admin`) are for poking at the data yourself, never for DAWAM. The passwords are sample values: don't expose these ports on a shared network.

## A model

The AI steps (descriptions, KPI suggestions, Core and Mart generation, mappings, evaluation, document questions) need a model. Configure any real model with an API key, or a local one, under **Admin > LLM** before creating the Workspace; see [`docs/install.md`](../../docs/install.md).

## Demo script

Each step says where it happens: in the **UI**, by asking the **Assistant** (the **Assistant** button at the top of every Workspace page), or through the **API** for the few features that have no button yet. Long-running work shows up under **Background jobs** in the header, and every AI change arrives as a **Change Set** you review with **Accept selected**, **Accept all**, **Reject selected** or **Reject all**.

**API steps.** Sign in, open the Workspace, and paste this once into the browser's developer console. It calls the API as you, with your session and CSRF cookie. `sys` is the Source System whose folder is open: open that system's folder first, and run the `sys = ...` line again when you switch systems.

```js
const ws = location.pathname.split("/")[2];
let sys = new URLSearchParams(location.search).get("folder")?.split("/")[1];
async function api(method, path, body) {
  const csrf = document.cookie.split("; ").find((c) => c.startsWith("dawam_csrf="))?.slice(11) ?? "";
  const res = await fetch(`/api/v1/workspaces/${ws}${path}`, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return res.status === 204 ? null : res.json();
}
```

1. **Workspace (UI).** On **Workspaces**, create a Workspace named "Najd National Bank" with business domain "Retail banking", and pick the models. *Point out:* the Workspace holds the source systems, the warehouse and everyone's work; the stepper at the top tracks Source Analysis, KPIs and DW Modeling.

2. **What the AI may see (API).** A new Workspace lets the AI see metadata only. For the document questions and profile-aware suggestions below, an owner raises it to documents (there is no button for it yet):

   ```js
   const ai = await api("GET", "/ai-settings");
   await api("PUT", "/ai-settings", {
     internal_only: ai.internal_only,
     data_sharing_level: "documents",
     agent_model_ids: ai.agent_models.map((m) => m.id),
     default_agent_model_id: ai.agent_models.find((m) => m.is_default)?.id ?? null,
     light_model_ids: ai.light_models.map((m) => m.id),
     default_light_model_id: ai.light_models.find((m) => m.is_default)?.id ?? null,
     embedding_model_id: ai.embedding_model?.id ?? null,
   });
   ```

   *Point out:* the level (metadata, profiles, documents, sample rows) applies to every model role, so data exposure is a deliberate choice.

3. **Source Systems (UI).** Open **Systems** and use **Add Source System** twice: "Core Banking" with System Code `cbs`, and "CRM" with System Code `crm`. *Point out:* the System Code is what generated names use (`stg_cbs_banking_accounts`).

4. **Connections and test (UI).** In each system's **Connection | Schema Import** folder, on **Live connection**, enter the details above, press **Test connection**, then **Save Connection**. *Point out:* the test shows the server version and no write warning: DAWAM only reads, through a read-only user.

5. **Extraction (UI).** In **Source Schema**, press **Extract metadata**. *Point out:* the Snapshot captures tables, columns, keys, indexes, views and routines (`v_customer_portfolio` and `fn_branch_deposits`; `v_open_complaints` and `sp_campaign_results`), and the system's folder now shows its Source summary.

6. **Browse and ER diagram (UI).** Open a table in **Source Schema** (or use **Search by name**), then open **ER diagram** and **Focus on a table**. *Point out:* only the declared foreign keys are drawn so far: `accounts` to `customers` and `account_types`, `transactions` to `accounts`, `campaign_responses` to `campaigns` and `complaints` to `contacts`.

7. **Profiling (UI).** In **Source Schema**, open `customers`, `accounts` and `transactions` and press **Profile this table** on each. Tick **Keep the most frequent values of this table** and profile again to capture top values. *Point out:* null % (`email`, `closed_on`, `merchant_category`), min/max dates and amounts, and the skew in `segment` and `status`; personal-data columns never get top values or min/max.

8. **Relationship inference (API, then Assistant).** With a system's folder open, start inference and, once the job has finished, list the candidates:

   ```js
   await api("POST", `/systems/${sys}/relationship-inference`, {});
   (await api("GET", `/systems/${sys}/relationships?status=suggested`)).items;
   ```

   Then ask the Assistant: "Review the suggested relationships of Core Banking: accept the ones that hold and reject any that don't", and review the Change Set. *Point out:* three undeclared relationships per system, each with its evidence (matching names and types, and the JOINs in the view and routine): `accounts.branch_id`, `customers.home_branch_id` and `transactions.txn_type_code` in Core Banking; `contacts.rm_id`, `campaign_responses.contact_id` and `complaints.category_code` in CRM. `customers.home_branch_id` is found only because the view joins it. Accepted relationships appear in the **ER diagram** as "inferred, accepted".

9. **PII review (API).** Name rules flagged the obvious columns at extraction. Run the value scan on every table of the open system, then list the review queue:

   ```js
   const schema = await api("GET", `/systems/${sys}/schema`);
   const tableIds = schema.tables.filter((t) => t.kind === "table").map((t) => t.id);
   await api("POST", `/systems/${sys}/pii-scans`, { table_ids: tableIds });
   (await api("GET", `/systems/${sys}/pii-findings?status=suggested`)).items;
   ```

   Confirm or dismiss each finding by its `id`:

   ```js
   await api("POST", `/systems/${sys}/pii-findings/${id}/confirm`);   // or .../dismiss
   ```

   *Point out:* national IDs, IBANs, `+966` and `05` mobile numbers, emails, names and dates of birth are found with their category and the share of sampled rows that matched, and CRM's vaguely named `contacts.id_number` is caught by its values alone. Sampled values are never stored, and the ER diagram marks tables with protected columns.

10. **Descriptions and classification (Assistant).** Ask: "Propose descriptions and a master, transactional or reference classification for every Core Banking table and column", then the same for CRM, and accept the Change Sets. *Point out:* `customers`, `accounts` and `contacts` are master data, `transactions`, `campaign_responses` and `complaints` transactional, and the code tables reference data; nothing changes until you accept.

11. **Documents (UI, API and Assistant).** In each system's **Documents** folder, use **Upload a document**: `SAD-CoreBanking.docx`, `data-dictionary.xlsx`, `operations-runbook.pdf` and `glossary.md` under Core Banking, `SAD-CRM.docx` under CRM. The **Text** column shows the text was extracted. To link a document to a table (there is no button yet), take `fileId` from the file list and `tableId` from the schema call above:

    ```js
    (await api("GET", `/systems/${sys}/files`));
    await api("POST", `/files/${fileId}/object-links`, { object_type: "table", object_id: tableId });
    ```

    Then ask the Assistant: "What counts as a dormant account?", "When does the dormancy job run?" and "What is the SLA for ATM complaints?". *Point out:* the answers come from the documents (12 months without a customer-initiated transaction, 24 for Youth Savings; DORM-01 at 02:30; 2 days).

12. **KPIs (UI and Assistant).** Enter the KPIs in [`kpis.md`](kpis.md) with **Add KPI** in each system's **KPIs** folder and in **Data Warehouse > KPIs**. Then ask the Assistant: "Suggest KPIs for CRM". *Point out:* suggestions arrive as drafts marked **AI-generated** with a rationale; until the warehouse exists a KPI has only its formula in words.

13. **Set up Data Warehouse (UI).** Open **Data Warehouse** and fill in the setup dialog: a **Target platform**, **Date dimension: first year** 2024 and **last year** 2026, the weekend days, and optionally **Include Hijri calendar attributes**. Press **Set up Data Warehouse**. *Point out:* the Staging, Core, Mart, Lineage, Score, Files and DDL folders appear.

14. **Staging (API).** Generate the Staging Layer, then open **Data Warehouse > Staging > Model**:

    ```js
    await api("POST", "/data-warehouse/staging/generate");
    ```

    *Point out:* one Staging Table per source table (`stg_cbs_banking_customers`, `stg_crm_crm_contacts`, ...) with translated types and audit columns, generated by software, not AI.

15. **Core and Mart generation (Assistant).** Ask: "Generate the Core and Mart layers from the staging tables and our KPIs". It runs as a background job and ends in one Change Set; review and accept it. *Point out:* look for a conformed customer dimension fed by both systems, facts for transactions, campaign responses and complaints, and the date dimension.

16. **Mappings (Assistant).** Ask: "Draft the mappings for all Core tables", accept the Change Set, then open a table in **Core > Model** and press **Mappings**. *Point out:* the customer dimension gets one Mapping Branch per system, matched on the customer number (`contacts.core_customer_no` = `customers.customer_no`).

17. **Scoring and AI evaluation (UI and Assistant).** Open **Data Warehouse > Score**: each failed check has **Explain** and **Propose fix**. Ask the Assistant: "Evaluate the Core layer". *Point out:* the rule-based score and the advisory AI evaluation are separate; AI findings never change the score.

18. **Lineage (UI).** Open **Data Warehouse > Lineage**, start from a KPI (for example Total deposits) or a column, and use **Expand upstream**, **Expand downstream** and **Impact report**. *Point out:* every DW column traces back to its source column, and PII follows the lineage.

19. **Exports (UI and browser).** The data dictionary is **Download data dictionary (XLSX)** in each **Source Schema**. The DDL, the documentation pack and the mapping sheet are downloads: open these addresses in the browser while signed in, with the Workspace's id from the address bar in place of `<workspace>`:

    ```text
    /api/v1/workspaces/<workspace>/data-warehouse/ddl?layer=core
    /api/v1/workspaces/<workspace>/documentation-pack?format=html
    /api/v1/workspaces/<workspace>/data-warehouse/mapping-sheet?format=xlsx
    ```

    The Assistant can also save the DDL or the mapping sheet into **Files** ("Save the Core DDL to the files"). *Point out:* every export is built from the same design, so they always agree.

## Rebuilding the documents

The DOCX, XLSX and PDF files in `docs/` are built from the Markdown file of the same name, whose first line says the format (`<!-- build: docx -->`). After editing a source, rebuild from the repository root with [uv](https://docs.astral.sh/uv/) and commit both files:

```sh
uv run samples/build_docs.py samples/bank
```

The script declares its own dependencies (python-docx, openpyxl, fpdf2), so it needs nothing from DAWAM's environment. The output is deterministic: rebuilding unchanged sources gives identical files. `glossary.md` ships as Markdown and is not built.
