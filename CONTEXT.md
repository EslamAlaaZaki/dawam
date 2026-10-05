# DAWAM

DAWAM (Data Analysis & Warehouse Architecture Modeler) helps teams analyse source systems and design a data warehouse from them, with every design artifact traceable back to the real source schema.

## Language

### Containers

**Workspace**:
The top-level container a team works in. Holds one or more Source Systems and one Data Warehouse, plus the members and everything produced along the way.
_Avoid_: Project

**Archived**:
A Workspace state in which it is read-only: reads and exports still work, every change is refused until an owner or admin unarchives it. Distinct from deleted, which is permanent.
_Avoid_: Closed, frozen

**Notification**:
A message addressed to one user (one row per recipient), shown as unread in the header until read, e.g. that a Workspace's ownership was reassigned. Distinct from the activity feed and the security-event log.
_Avoid_: Alert, toast

**Source System**:
A logical system being analysed (e.g. "Core Banking", "CRM"). Backed by exactly one database for now.
_Avoid_: Source, application

**System Code**:
A short, identifier-safe code for a Source System (e.g. `cbs`, `crm`), used in generated names instead of its display name.
_Avoid_: Prefix, alias

**Data Warehouse**:
The area of a Workspace where the warehouse is designed from the Workspace's Source Systems.
_Avoid_: Target, DW project

### Analysing a source

**Connection**:
Live, read-only access settings for a Source System's database.
_Avoid_: Datasource, link

**Schema Import**:
Loading a Source System's metadata from filled-in template files instead of a live Connection.
_Avoid_: Upload, manual schema

**Source Object**:
The stable identity of a database schema, table, column or routine in a Source System, kept across Snapshots. Descriptions, PII, relationships and mappings attach to it, not to a Snapshot.
_Avoid_: Snapshot column, catalog entry

**Snapshot**:
An immutable, point-in-time capture of a Source System's metadata (tables, columns, keys, indexes, procedures, functions, views), produced by either a Connection extraction or a Schema Import. Its frozen rows point at Source Objects.
_Avoid_: Version, scan

**Source Schema**:
The enhanced picture of a Source System's structure: the latest Snapshot plus discovered relationships, descriptions and labels added by users and the AI.
_Avoid_: Catalog, source model

**Database Schema**:
A namespace inside a source database (e.g. `dbo`, `sales`).
_Avoid_: Bare "schema" (ambiguous with Source Schema and DW Schema)

### Designing the warehouse

**DW Modeling**:
The activity of designing the warehouse's tables from the Source Schemas and KPIs.
_Avoid_: Target design

**DW Schema**:
The result of DW Modeling: the warehouse's facts, dimensions, attributes and relationships, across all Layers.
_Avoid_: DW Model, target model, target schema

**Layer**:
A tier of the Data Warehouse: `staging`, `core` or `mart`. Each Layer has its own part of the DW Schema, its own mappings from the Layer below, and its own evaluation.
_Avoid_: Target schema, zone

**Staging Table**:
A DW Schema table in the `staging` Layer that mirrors one source base table (or a source view the user opted in), named `stg_<system code>_<database schema>_<table>`.
_Avoid_: Landing table, raw table

**Generated Table**:
A DW Schema table DAWAM builds without any source input, such as the date dimension (with optional Hijri and fiscal attributes).
_Avoid_: Static table, seed

**Unknown Member**:
The row every dimension carries (surrogate key `-1`) for facts whose dimension key is missing or not yet known.
_Avoid_: Default row, dummy row

**Mapping Branch**:
One row-set inside a table's mapping, with its own driving table, joins, filters and column expressions; a table's branches are combined with UNION ALL, typically one per Source System.
_Avoid_: Sub-mapping, source path

**Lookup**:
How a Core fact's foreign key is filled: finding the matching dimension row by natural key (and date, for SCD2), falling back to the Unknown Member.
_Avoid_: Join (a join belongs to a Mapping Branch), FK mapping

**KPI**:
A business metric with an agreed definition and formula, entered by a user or suggested by the AI (and labelled as AI-generated). Belongs to a Source System, or to the Data Warehouse when it spans systems.
_Avoid_: Metric, measure (a measure is a DW Schema column)

### Changing things

**Asset**:
Anything in a Workspace that a user or the AI can edit: the Source Schema's enhancements, a KPI, a DW Schema table, a Layer's table mapping, a document or file. Changes are forward-only; there is no revert.
_Avoid_: Object, artifact, item

**Change Set**:
A group of proposed changes to Assets, shown as a diff and applied only after a user accepts some or all of it. Produced by the AI assistant, by regeneration, by Snapshot sync, by mapping import and by propagating a change to impacted Assets.
_Avoid_: Patch, proposal, suggestion

**Protected Column**:
A source column flagged sensitive, or with a suggested or confirmed PII finding. Its values never reach the AI or DAWAM's storage.
_Avoid_: PII column (narrower), sensitive column (narrower)

**Layer Approval**:
The sign-off state (`draft`, `in_review`, `approved`) of the Core or Mart Layer as a whole; mappings have no approval of their own.
_Avoid_: Mapping status, design approval

**Tombstone**:
The record DAWAM keeps of a deleted generated object or Staging Table, so regeneration and sync never propose it again.
_Avoid_: Deleted flag, blacklist

**Propagation**:
Carrying a structural change (rename, type change, delete…) through lineage to every impacted Asset in one Change Set.
_Avoid_: Cascade, sync (sync means following a new Snapshot)
