# Retail sample: POS and E-commerce

A ready-to-run demo for the whole DAWAM flow. **Rimal Retail** is a made-up Saudi retail chain that sells in stores and online, with two source systems that share customers and products: **POS** on PostgreSQL (stores, products, loyalty customers, till receipts and their lines) and **E-commerce** on MySQL (online customers carrying their POS loyalty number, orders and items by SKU, payments, carriers and shipments). Both databases come seeded with a year of deterministic data (2025, as of 31 December 2025), a few undeclared relationships, Saudi personal data to detect, and documents to upload. Because the two systems share customers (by loyalty number or email) and products (by SKU), the warehouse calls for a conformed customer dimension and a conformed product dimension, each fed by one Mapping Branch per system.

All names, phone numbers and emails are generated. They pass the format checks DAWAM's PII scan uses, but belong to nobody.

| Path | What it is |
| --- | --- |
| `compose.yaml` | The two databases, as the standalone Compose project `dawam-sample-retail` |
| `databases/pos/init.sql` | POS: schema, data, a view, a function and the read-only user |
| `databases/shop/init.sql` | E-commerce: schema, data, a view, a procedure and the read-only user |
| `docs/` | Documents to upload: two System Architecture Documents (DOCX), a data dictionary (XLSX) and a glossary (Markdown). Each built file has its Markdown source next to it |
| `kpis.md` | Ten KPIs to enter by hand |

## Run, stop, reset

From the repository root, with Docker running:

```sh
docker compose -f samples/retail/compose.yaml up -d --wait   # start; the first start seeds the data
docker compose -f samples/retail/compose.yaml down           # stop, keep the data
docker compose -f samples/retail/compose.yaml down -v        # stop and delete the data (the next start seeds it again)
```

No `.env` is needed. The databases publish ports **5434** and **3308** on the host; free them first if something else uses them. They have nothing to do with DAWAM's own Compose project, so start DAWAM as usual (see the root `README.md`). The names and ports differ from the bank sample's, so both scenarios can run at the same time.

## Connection details

| | POS | E-commerce |
| --- | --- | --- |
| Engine | PostgreSQL | MySQL |
| Host | `host.docker.internal` | `host.docker.internal` |
| Port | 5434 | 3308 |
| Database | `pos` | `shop` |
| Username | `dawam_reader` | `dawam_reader` |
| Password | `dawam_reader` | `dawam_reader` |
| Allowed Database Schemas | `retail` | `shop` |

As SQLAlchemy URLs:

```text
postgresql+psycopg://dawam_reader:dawam_reader@host.docker.internal:5434/pos
mysql+pymysql://dawam_reader:dawam_reader@host.docker.internal:3308/shop
```

DAWAM runs in Docker, so `localhost` would point at DAWAM's own container. `host.docker.internal` reaches the host on Docker Desktop (Windows and macOS). On Linux, use the host's IP address or the Docker bridge address, usually `172.17.0.1`.

`dawam_reader` can only read: on PostgreSQL it has `SELECT` on the `retail` tables and its sessions are read-only (function definitions are readable by default); on MySQL it has `SELECT` and `SHOW VIEW` on `shop`, plus `SHOW_ROUTINE` so DAWAM can read the procedure's body. The connection test therefore shows no write warning. The admin users (`pos_admin` / `pos_admin`, `root` / `shop_admin`) are for poking at the data yourself, never for DAWAM. The passwords are sample values: don't expose these ports on a shared network.

## A model

The AI steps (descriptions, KPI suggestions, Core and Mart generation, mappings, evaluation, document questions) need a model. Configure any real model with an API key, or a local one, under **AI > Language models** in the admin sidebar before creating the Workspace; see [`docs/install.md`](../../docs/install.md).

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
  const data = res.status === 204 ? null : await res.json();
  if (!res.ok) throw new Error(`${res.status}: ${JSON.stringify(data)}`);
  return data;
}
```

1. **Workspace (UI).** On **Workspaces**, create a Workspace named "Rimal Retail" with business domain "Retail", and pick the models. *Point out:* the Workspace holds the source systems, the warehouse and everyone's work; the stepper at the top tracks Source Analysis, KPIs and DW Modeling.

2. **What the AI may see (UI).** A new Workspace lets the AI see metadata only. For the document questions and profile-aware suggestions below, open **Workspace settings > AI privacy**, turn on **Documents** and **Profile statistics** (each asks you to confirm), and press **Save**. Only an owner can change them.

   *Point out:* metadata is always shared, and profile statistics, documents and sample rows are three separate opt-ins that apply to every model role, so data exposure is a deliberate choice.

3. **Source Systems (UI).** Open **Systems** and use **Add Source System** twice: "POS" with System Code `pos`, and "E-commerce" with System Code `ecom`. *Point out:* the System Code is what generated names use (`stg_pos_retail_products`).

4. **Connections and test (UI).** In each system's **Connection** folder, choose the **Live connection** card, enter the details above, press **Test connection**, then **Save connection**. *Point out:* the result box shows the server version and no write warning: DAWAM only reads, through a read-only user. Other schemas on the server appear as "+" chips you could add to the allowed list. (A system DAWAM can't reach uses **Import from template** instead: run the helper queries, fill in the template, then upload it.)

5. **Extraction (UI).** In **Source schema**, press **Extract metadata**. *Point out:* the Snapshot captures tables, columns, keys, indexes, views and routines (`v_loyalty_overview` and `fn_store_net_sales`; `v_orders_to_ship` and `sp_carrier_performance`), the latest snapshot's counts now show under the heading, and the system's folder shows its Source summary.

6. **Browse and ER diagram (UI).** Open a table in the **Source schema** tree (Database Schema > Tables, Views, and Programmability > Functions, Procedures), or use the **Search by name** box, then open **ER diagram** and tick a few tables in the **Tables** list. *Point out:* the Relationships list under the diagram and its count narrow to the same filter, and only the declared foreign keys are drawn so far: `products` to `categories` and `receipt_lines` to `sales_receipts` in POS, `orders` to `customers` and `order_items` to `orders` in E-commerce.

7. **Profiling (UI).** In **Source schema**, press **Profile all tables** in the header (or open `loyalty_customers`, `sales_receipts` and `receipt_lines` and press the re-profile button next to \"Not profiled yet\"). Then, in a table's **Profile** tab, an owner turns on **Keep top values**, which starts profiling to collect them. *Point out:* null % (`email`, `customer_id` for anonymous purchases), min/max dates and amounts, negative quantities on returns, and the skew in `tier`, `receipt_type` and `payment_method`; the bars show null shares and the **Unique** and **Constant** tags mark such columns; personal-data columns show a red shield and "Hidden" instead of top values or min/max.

   *Across the system:* open the system's **Profiling** folder. The cards show how much is profiled, what is out of date (profiled before the latest Snapshot), the findings and the growth. **Tables** lists each table's status, its sampled rows (**Capped** when the sample hit its limit) and its findings count; tick tables and press **Profile selected**, or use **Profile all tables** and **Profile out-of-date tables** in the header. **Findings** lists every all-null, mostly-null, constant and unique-but-not-a-key column across the system, and **Open profile** and **Open column** jump to that table's **Profile** tab. *Point out:* protected columns get only the null findings, so no value of theirs is shown. **Data growth** needs two Snapshots: press **Extract metadata** again later for a second one. The sample's data doesn't change, so expect little or no growth.

8. **Relationships (UI, or Assistant).** In each system's **Source schema** folder, press **Find relationships** and wait for the background job to finish. It opens the **Relationships** tab on **Suggested** (the tab's badge says how many are to review). Press **Accept** or **Reject** on each row, or per table in its **Keys and relationships** tab; or ask the Assistant: "Review the suggested relationships of POS: accept the ones that hold and reject any that don't", and review the Change Set. *Point out:* undeclared relationships in each system, each with its evidence: what found it (**rules**, **AI** or **both**: the rules match names and types and read the JOINs in the views and routines, the AI reads the tables' metadata, never their data), and the counts the job ran on the source database (how many rows match, the orphan rows and the distinct values; counts only, each query cancelled after 45 s). The confidence comes from those counts, so these pairs score high: `sales_receipts.store_id`, `sales_receipts.customer_id`, `receipt_lines.product_id` and `loyalty_customers.home_store_id` in POS; `payments.order_id`, `shipments.order_id` and `shipments.carrier_code` in E-commerce. `loyalty_customers.home_store_id` is found only because the view joins it. Accepted relationships move to the **Current** list and appear on the **ER diagram** as dashed lines, listed as "Inferred, accepted"; the ER diagram's header links back to the review. Every list shows a relationship as `[FK] table.column → [PK] table.column` with its cardinality. Without an agent model the job still runs on the rules alone (its log says why), and a pair the AI suggests that no rule finds shows as **AI**.

   *Fix by hand:* in POS, **Reject** `loyalty_customers.home_store_id`, then open the **Rejected** tab and press **Accept** to bring it back. Or delete an accepted one from **Current** (it moves to **Rejected** and is never suggested again), then press **Add relationship**, search for the table and column on each side, and add it back: it shows as **Manually added**, has no confidence, and later **Find relationships** runs leave it alone. Declared foreign keys show a lock and can't be deleted.

9. **PII review (UI).** Name rules flagged the obvious columns at extraction. In each system's **PII** folder, press **Detect PII** (every table is ticked; untick any to narrow it) and **Start detection**. That starts the value scan and the AI review together (only the AI review when the system has no Connection). When the jobs finish, the **Suggested** tab lists one row per column; **Matched rules** shows each rule under its source (Name, Values, AI or Manual) with its own confidence, most confident first: pick the category and **Accept**, or **Reject**, and watch the column move to the **PII** and **Accepted** tabs or to **Rejected**. On the **PII** tab, **Add PII column** flags a column by hand, and the delete icon removes PII (blocked while staging hashes the column).

   *Point out:* `+966` and `05` mobile numbers, emails and dates of birth are found with their category and the share of sampled rows that matched, while names, genders (POS) and delivery addresses and postal codes (E-commerce) are flagged by their column names alone. Sampled values are never stored, and the ER diagram marks tables with protected columns.

10. **Descriptions and classification (Assistant).** Ask: "Propose descriptions and a master, transactional or reference classification for every POS table and column", then the same for E-commerce, and accept the Change Sets. *Point out:* `products`, `loyalty_customers` and `customers` are master data, `sales_receipts`, `receipt_lines`, `orders`, `order_items`, `payments` and `shipments` transactional, and `categories` and `carriers` reference data; nothing changes until you accept.

11. **Documents (UI, API and Assistant).** In each system's **Documents** folder, use **Upload a document**: `SAD-POS.docx`, `data-dictionary.xlsx` and `glossary.md` under POS, `SAD-Ecommerce.docx` under E-commerce. The **Text** column shows the text was extracted. To link a document to a table (there is no button yet), with POS's folder open:

    ```js
    var files = (await api("GET", `/systems/${sys}/files`)).items;
    var tables = (await api("GET", `/systems/${sys}/schema`)).tables;
    var fileId = files.find((f) => f.name === "SAD-POS.docx").id;
    var tableId = tables.find((t) => t.name === "sales_receipts").id;
    await api("POST", `/files/${fileId}/object-links`, { object_type: "table", object_id: tableId });
    ```

    Then ask the Assistant: "How is a return recorded at the till?", "When is delivery free?" and "When is a delivery on time?". *Point out:* the answers come from the documents (a separate receipt with negative amounts; a standard order of 200 SAR or more, otherwise 20 SAR, and express is 35 SAR; delivered no later than the carrier's SLA days after shipping).

12. **KPIs (UI and Assistant).** Enter the KPIs in [`kpis.md`](kpis.md): **+ Add KPI** in each system's **KPIs** folder, and **+ Add cross-system KPI** in **Data Warehouse > KPIs**. Then press **Suggest KPIs** on the E-commerce **KPIs** page (or ask the Assistant: "Suggest KPIs for E-commerce"), and **Suggest cross-system KPIs** on **Data Warehouse > KPIs**. *Point out:* suggestions wait in the **Suggested** tab with the AI's rationale and the tables they are **based on**; accept or reject each one. Deleting an accepted suggestion moves it to **Rejected**. Only the **KPIs** tab (added by hand plus accepted) feeds the warehouse design. **Data Warehouse > KPIs** lists every KPI with where it is documented. Until the warehouse exists a KPI has only its formula in words.

13. **Set up Data Warehouse (UI).** Open **Data Warehouse** and fill in the setup dialog: a **Target platform**, **Date dimension: first year** 2024 and **last year** 2026, the weekend days, and optionally **Include Hijri calendar attributes**. Press **Set up Data Warehouse**. *Point out:* the Staging, Core, Mart, Lineage, Score, Files and DDL folders appear.

14. **Staging (API).** Generate the Staging Layer, then open **Data Warehouse > Staging > Model**:

    ```js
    await api("POST", "/data-warehouse/staging/generate");
    ```

    *Point out:* one Staging Table per source table (`stg_pos_retail_loyalty_customers`, `stg_ecom_shop_customers`, ...) with translated types and audit columns, generated by software, not AI.

15. **Core and Mart generation (Assistant).** Ask: "Generate the Core and Mart layers from the staging tables and our KPIs". It runs as a background job and ends in one Change Set; review and accept it. *Point out:* look for a conformed customer dimension and a conformed product dimension fed by both systems, facts for receipt lines, order items, payments and shipments, a store dimension and the date dimension. Then open **Data Warehouse > KPIs**: rule suggestions built from the new facts appear in **Suggested** with formula SQL and **Feasibility**.

16. **Mappings (Assistant).** Ask: "Draft the mappings for all Core tables", accept the Change Set, then open a table in **Core > Model** and press **Mappings**. *Point out:* the customer dimension gets one Mapping Branch per system, matched on the loyalty number (`customers.loyalty_no` = `loyalty_customers.loyalty_no`), and the product dimension is matched on the SKU (`order_items.sku` = `products.sku`).

17. **Scoring and AI evaluation (UI and Assistant).** Open **Data Warehouse > Score**: each failed check has **Explain** and **Propose fix**. Ask the Assistant: "Evaluate the Core layer". *Point out:* the rule-based score and the advisory AI evaluation are separate; AI findings never change the score.

18. **Lineage (UI).** Open **Data Warehouse > Lineage**, start from a KPI (for example Net sales) or a column, and use **Expand upstream**, **Expand downstream** and **Impact report**. *Point out:* every DW column traces back to its source column, and PII follows the lineage.

19. **Exports (UI and browser).** The data dictionary is **Data dictionary ▾ > Download (XLSX)** in each **Source schema**. The DDL, the documentation pack and the mapping sheet are downloads: open these addresses in the browser while signed in, with the Workspace's id from the address bar in place of `<workspace>`:

    ```text
    /api/v1/workspaces/<workspace>/data-warehouse/ddl?layer=core
    /api/v1/workspaces/<workspace>/documentation-pack?format=html
    /api/v1/workspaces/<workspace>/data-warehouse/mapping-sheet?format=xlsx
    ```

    The Assistant can also save the DDL or the mapping sheet into **Files** ("Save the Core DDL to the files"). *Point out:* every export is built from the same design, so they always agree.

## Rebuilding the documents

The DOCX and XLSX files in `docs/` are built from the Markdown file of the same name, whose first line says the format (`<!-- build: docx -->`). After editing a source, rebuild from the repository root with [uv](https://docs.astral.sh/uv/) and commit both files:

```sh
uv run samples/build_docs.py samples/retail
```

The script declares its own dependencies (python-docx, openpyxl, fpdf2), so it needs nothing from DAWAM's environment. The output is deterministic: rebuilding unchanged sources gives identical files. `glossary.md` ships as Markdown and is not built; this scenario has no PDF.
