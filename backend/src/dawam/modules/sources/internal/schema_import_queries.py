"""Helper queries for a Schema Import (spec §6.4, stories 47, 48).

For each engine there is one catalog query per template sheet; its result is exactly
that sheet's columns (``schema_import.SHEETS`` is the single source of truth, and a test
checks every query's select list against it), so it can be exported straight into the
sheet. The queries are written by DAWAM; a maintainer may replace them.

The PostgreSQL, MySQL, MariaDB, SQL Server and Oracle queries read what the Connectors
read, and are checked against them on the seeded containers. The Db2 (LUW, ``SYSCAT``)
and Teradata (``DBC``) queries are written from the vendors' documentation and are not
run in CI.

Conventions every query keeps:

- the final select list names its columns with *quoted* aliases, in sheet order; nothing
  else in a query quotes an alias (the shape test relies on it);
- a column list (``columns``, ``ref_columns``) is one comma-separated string, in key
  order, so a column name that itself contains a comma cannot be told apart;
- a true/false flag is the text ``true`` or ``false``;
- by default a query covers every schema except the engine's system ones; the download
  can name the schemas instead.
"""

from __future__ import annotations

import re

from .schema_import import SHEETS

ENGINE_LABELS: dict[str, str] = {
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "sqlserver": "SQL Server",
    "oracle": "Oracle",
    "db2": "IBM Db2 (LUW)",
    "teradata": "Teradata",
}
ENGINES: tuple[str, ...] = tuple(ENGINE_LABELS)

_NOTES: dict[str, str] = {
    "postgresql": (
        "PostgreSQL 12 or newer. View and default text is schema-qualified as far as your "
        "session's search_path demands; SET search_path to the schemas first to read it the "
        "way a live Connection does."
    ),
    "mysql": "MySQL 8.",
    "mariadb": "MariaDB 10.5 or newer.",
    "sqlserver": "SQL Server 2017 or newer (STRING_AGG).",
    "oracle": (
        "Oracle 12.2 or newer. A routine's source longer than 4000 bytes is cut short, and a "
        "function-based index is read through DBMS_XMLGEN."
    ),
    "db2": (
        "Db2 LUW 11.1 or newer, for the SYSCAT views. Written from the IBM documentation; "
        "DAWAM does not run it. Db2 shows a function-based index by its generated column."
    ),
    "teradata": (
        "Written from the Teradata documentation (DBC views); DAWAM does not run it. Row "
        "counts are left empty (optional). A foreign key's columns come in no guaranteed "
        "order."
    ),
}

# -- schema scope --------------------------------------------------------------------

_SYSTEM_SCOPE: dict[str, str] = {
    "postgresql": r"{c} <> 'information_schema' AND {c} NOT LIKE 'pg\_%'",
    "mysql": "{c} NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')",
    "sqlserver": "{c} NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest') AND {c} NOT LIKE 'db[_]%'",
    "oracle": "{c} IN (SELECT username FROM all_users WHERE oracle_maintained = 'N')",
    "db2": "{c} NOT LIKE 'SYS%' AND {c} NOT IN ('NULLID', 'SQLJ')",
    "teradata": (
        "{c} NOT IN ('DBC', 'SYSLIB', 'SYSUDTLIB', 'SYSBAR', 'SYSJDBC', 'SYSSPATIAL', 'SYSUIF', "
        "'SYSXML', 'SQLJ', 'TD_SYSFNLIB', 'TD_SYSXML', 'TD_SERVER_DB', 'TDSTATS', 'TDQCD', "
        "'Crashdumps', 'External_AP', 'LockLogShredder', 'SysAdmin', 'Sys_Calendar', 'SystemFe', "
        "'TDPUSER', 'TDMaps', 'TD_SYSGPL', 'dbcmngr', 'tdwm', 'All', 'Default', 'PUBLIC')"
    ),
}
_SYSTEM_SCOPE["mariadb"] = _SYSTEM_SCOPE["mysql"]
_SCOPE_MARK = re.compile(r"@scope\(([^)]+)\)")


def _scope_clause(engine: str, column: str, schemas: tuple[str, ...] | None) -> str:
    if schemas:
        names = ", ".join("'" + name.replace("'", "''") + "'" for name in schemas)
        return f"{column} IN ({names})"
    return "(" + _SYSTEM_SCOPE[engine].format(c=column) + ")"


# -- PostgreSQL ------------------------------------------------------------------------

_PG_RELKINDS = "('r', 'p', 'v', 'm', 'f')"

_POSTGRESQL: dict[str, str] = {
    "schemas": """
SELECT n.nspname AS "schema"
FROM pg_namespace n
WHERE @scope(n.nspname)
ORDER BY n.nspname""",
    "tables": f"""
SELECT n.nspname AS "schema",
       c.relname AS "table",
       CASE WHEN c.relkind IN ('v', 'm') THEN 'view' ELSE 'table' END AS "kind",
       CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END AS "row_count",
       obj_description(c.oid, 'pg_class') AS "comment",
       CASE WHEN c.relkind IN ('v', 'm') THEN pg_get_viewdef(c.oid) END AS "definition"
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE @scope(n.nspname) AND c.relkind IN {_PG_RELKINDS}
ORDER BY n.nspname, c.relname""",
    "columns": f"""
SELECT n.nspname AS "schema",
       c.relname AS "table",
       a.attname AS "column",
       format_type(a.atttypid, a.atttypmod) AS "data_type",
       a.attnum AS "ordinal",
       CASE WHEN a.attnotnull THEN 'false' ELSE 'true' END AS "is_nullable",
       pg_get_expr(d.adbin, d.adrelid) AS "default",
       col_description(a.attrelid, a.attnum) AS "comment"
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE @scope(n.nspname) AND c.relkind IN {_PG_RELKINDS}
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY n.nspname, c.relname, a.attnum""",
    "constraints": """
SELECT n.nspname AS "schema",
       c.relname AS "table",
       con.conname AS "constraint",
       CASE con.contype WHEN 'p' THEN 'pk' WHEN 'f' THEN 'fk' ELSE 'unique' END AS "type",
       (SELECT string_agg(a.attname::text, ',' ORDER BY k.pos)
        FROM unnest(con.conkey) WITH ORDINALITY k(num, pos)
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.num) AS "columns",
       rn.nspname AS "ref_schema",
       rc.relname AS "ref_table",
       (SELECT string_agg(a.attname::text, ',' ORDER BY k.pos)
        FROM unnest(con.confkey) WITH ORDINALITY k(num, pos)
        JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.num) AS "ref_columns"
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_class rc ON rc.oid = con.confrelid
LEFT JOIN pg_namespace rn ON rn.oid = rc.relnamespace
WHERE @scope(n.nspname) AND con.contype IN ('p', 'f', 'u')
ORDER BY n.nspname, c.relname, con.conname""",
    "indexes": """
SELECT n.nspname AS "schema",
       c.relname AS "table",
       ic.relname AS "index",
       (SELECT string_agg(
                 CASE WHEN i.indkey[k - 1] = 0 THEN pg_get_indexdef(i.indexrelid, k, true)
                      ELSE (SELECT a.attname::text FROM pg_attribute a
                            WHERE a.attrelid = i.indrelid AND a.attnum = i.indkey[k - 1])
                 END, ',' ORDER BY k)
        FROM generate_series(1, i.indnkeyatts) k) AS "columns",
       CASE WHEN i.indisunique THEN 'true' ELSE 'false' END AS "is_unique"
FROM pg_index i
JOIN pg_class ic ON ic.oid = i.indexrelid
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE @scope(n.nspname)
ORDER BY n.nspname, c.relname, ic.relname""",
    "routines": """
SELECT n.nspname AS "schema",
       p.proname AS "name",
       CASE p.prokind WHEN 'p' THEN 'procedure' ELSE 'function' END AS "kind",
       pg_get_function_identity_arguments(p.oid) AS "signature",
       pg_get_functiondef(p.oid) AS "definition"
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE @scope(n.nspname) AND p.prokind IN ('f', 'p')
ORDER BY n.nspname, p.proname, p.oid""",
}

# -- MySQL and MariaDB -----------------------------------------------------------------

_MY_TABLE_TYPES = "('BASE TABLE', 'VIEW')"


def _mysql(*, mariadb: bool) -> dict[str, str]:
    # MariaDB spells "no default" as the text NULL, and has no functional indexes.
    default = (
        "CASE WHEN c.column_default = 'NULL' AND c.is_nullable = 'YES' THEN NULL "
        "ELSE c.column_default END"
        if mariadb
        else "c.column_default"
    )
    index_part = (
        "s.column_name" if mariadb else "COALESCE(s.column_name, REPLACE(s.expression, '`', ''))"
    )
    return {
        "schemas": """
SELECT s.schema_name AS `schema`
FROM information_schema.schemata s
WHERE @scope(s.schema_name)
ORDER BY s.schema_name""",
        "tables": f"""
SELECT t.table_schema AS `schema`,
       t.table_name AS `table`,
       CASE t.table_type WHEN 'VIEW' THEN 'view' ELSE 'table' END AS `kind`,
       CASE WHEN t.table_type = 'VIEW' THEN NULL ELSE t.table_rows END AS `row_count`,
       CASE WHEN t.table_type = 'VIEW' THEN NULL ELSE NULLIF(t.table_comment, '') END
         AS `comment`,
       CASE WHEN t.table_type = 'VIEW' THEN v.view_definition END AS `definition`
FROM information_schema.tables t
LEFT JOIN information_schema.views v
  ON v.table_schema = t.table_schema AND v.table_name = t.table_name
WHERE @scope(t.table_schema) AND t.table_type IN {_MY_TABLE_TYPES}
ORDER BY t.table_schema, t.table_name""",
        "columns": f"""
SELECT c.table_schema AS `schema`,
       c.table_name AS `table`,
       c.column_name AS `column`,
       c.column_type AS `data_type`,
       c.ordinal_position AS `ordinal`,
       CASE c.is_nullable WHEN 'YES' THEN 'true' ELSE 'false' END AS `is_nullable`,
       {default} AS `default`,
       NULLIF(c.column_comment, '') AS `comment`
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE @scope(c.table_schema) AND t.table_type IN {_MY_TABLE_TYPES}
ORDER BY c.table_schema, c.table_name, c.ordinal_position""",
        "constraints": """
SELECT tc.table_schema AS `schema`,
       tc.table_name AS `table`,
       tc.constraint_name AS `constraint`,
       CASE tc.constraint_type WHEN 'PRIMARY KEY' THEN 'pk' WHEN 'FOREIGN KEY' THEN 'fk'
            ELSE 'unique' END AS `type`,
       GROUP_CONCAT(k.column_name ORDER BY k.ordinal_position SEPARATOR ',') AS `columns`,
       MAX(k.referenced_table_schema) AS `ref_schema`,
       MAX(k.referenced_table_name) AS `ref_table`,
       GROUP_CONCAT(k.referenced_column_name ORDER BY k.ordinal_position SEPARATOR ',')
         AS `ref_columns`
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage k
  ON k.constraint_schema = tc.constraint_schema
 AND k.constraint_name = tc.constraint_name
 AND k.table_schema = tc.table_schema
 AND k.table_name = tc.table_name
WHERE @scope(tc.table_schema)
  AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY', 'UNIQUE')
GROUP BY tc.table_schema, tc.table_name, tc.constraint_name, tc.constraint_type
ORDER BY tc.table_schema, tc.table_name, tc.constraint_name""",
        "indexes": f"""
SELECT s.table_schema AS `schema`,
       s.table_name AS `table`,
       s.index_name AS `index`,
       GROUP_CONCAT({index_part} ORDER BY s.seq_in_index SEPARATOR ',') AS `columns`,
       CASE WHEN MIN(s.non_unique) = 0 THEN 'true' ELSE 'false' END AS `is_unique`
FROM information_schema.statistics s
WHERE @scope(s.table_schema)
GROUP BY s.table_schema, s.table_name, s.index_name
ORDER BY s.table_schema, s.table_name, s.index_name""",
        "routines": """
SELECT r.routine_schema AS `schema`,
       r.routine_name AS `name`,
       LOWER(r.routine_type) AS `kind`,
       (SELECT GROUP_CONCAT(
                 CONCAT(CASE WHEN p.parameter_mode IN ('OUT', 'INOUT')
                             THEN CONCAT(p.parameter_mode, ' ') ELSE '' END,
                        p.parameter_name, ' ', p.dtd_identifier)
                 ORDER BY p.ordinal_position SEPARATOR ', ')
        FROM information_schema.parameters p
        WHERE p.specific_schema = r.routine_schema AND p.specific_name = r.specific_name
          AND p.ordinal_position > 0) AS `signature`,
       r.routine_definition AS `definition`
FROM information_schema.routines r
WHERE @scope(r.routine_schema) AND r.routine_type IN ('FUNCTION', 'PROCEDURE')
ORDER BY r.routine_schema, r.routine_name""",
    }


# -- SQL Server ------------------------------------------------------------------------


def _mssql_type(t: str, c: str) -> str:
    """The type as ``name(length)`` for sys.types ``t`` and sys.columns/parameters ``c``."""
    n = f"{t}.name"
    size = f"CASE WHEN {c}.max_length = -1 THEN 'max' ELSE CAST({c}.max_length AS varchar(10)) END"
    wide = (
        f"CASE WHEN {c}.max_length = -1 THEN 'max' ELSE CAST({c}.max_length / 2 AS varchar(10)) END"
    )
    return (
        f"CASE WHEN {n} IN ('char', 'varchar', 'binary', 'varbinary') "
        f"THEN {n} + '(' + {size} + ')' "
        f"WHEN {n} IN ('nchar', 'nvarchar') THEN {n} + '(' + {wide} + ')' "
        f"WHEN {n} IN ('decimal', 'numeric') THEN {n} + '(' + CAST({c}.precision AS varchar(10)) "
        f"+ ',' + CAST({c}.scale AS varchar(10)) + ')' "
        f"WHEN {n} IN ('datetime2', 'datetimeoffset', 'time') "
        f"THEN {n} + '(' + CAST({c}.scale AS varchar(10)) + ')' ELSE {n} END"
    )


# SQL Server stores a default in redundant parentheses: ((0)) -> 0, (getdate()) -> getdate().
_MSSQL_ONCE = (
    "CASE WHEN dc.definition LIKE '(%)' "
    "THEN SUBSTRING(dc.definition, 2, LEN(dc.definition) - 2) ELSE dc.definition END"
)
_MSSQL_DEFAULT = (
    f"CASE WHEN {_MSSQL_ONCE} LIKE '(%)' "
    f"AND SUBSTRING({_MSSQL_ONCE}, 2, LEN({_MSSQL_ONCE}) - 2) NOT LIKE '%[()]%' "
    f"THEN SUBSTRING({_MSSQL_ONCE}, 2, LEN({_MSSQL_ONCE}) - 2) ELSE {_MSSQL_ONCE} END"
)

_SQLSERVER: dict[str, str] = {
    "schemas": """
SELECT s.name AS [schema]
FROM sys.schemas s
WHERE @scope(s.name)
ORDER BY s.name""",
    "tables": """
SELECT s.name AS [schema],
       o.name AS [table],
       CASE o.type WHEN 'V' THEN 'view' ELSE 'table' END AS [kind],
       (SELECT SUM(p.rows) FROM sys.partitions p
        WHERE p.object_id = o.object_id AND p.index_id IN (0, 1)) AS [row_count],
       CAST(ep.value AS nvarchar(max)) AS [comment],
       CASE WHEN o.type = 'V' THEN OBJECT_DEFINITION(o.object_id) END AS [definition]
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.extended_properties ep
  ON ep.class = 1 AND ep.major_id = o.object_id AND ep.minor_id = 0
 AND ep.name = 'MS_Description'
WHERE @scope(s.name) AND o.type IN ('U', 'V')
ORDER BY s.name, o.name""",
    "columns": f"""
SELECT s.name AS [schema],
       o.name AS [table],
       c.name AS [column],
       {_mssql_type("t", "c")} AS [data_type],
       c.column_id AS [ordinal],
       CASE c.is_nullable WHEN 1 THEN 'true' ELSE 'false' END AS [is_nullable],
       {_MSSQL_DEFAULT} AS [default],
       CAST(ep.value AS nvarchar(max)) AS [comment]
FROM sys.columns c
JOIN sys.objects o ON o.object_id = c.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.types t ON t.user_type_id = c.user_type_id
LEFT JOIN sys.default_constraints dc
  ON dc.parent_object_id = c.object_id AND dc.parent_column_id = c.column_id
LEFT JOIN sys.extended_properties ep
  ON ep.class = 1 AND ep.major_id = c.object_id AND ep.minor_id = c.column_id
 AND ep.name = 'MS_Description'
WHERE @scope(s.name) AND o.type IN ('U', 'V')
ORDER BY s.name, o.name, c.column_id""",
    "constraints": """
SELECT s.name AS [schema],
       o.name AS [table],
       kc.name AS [constraint],
       CASE kc.type WHEN 'PK' THEN 'pk' ELSE 'unique' END AS [type],
       (SELECT STRING_AGG(c.name, ',') WITHIN GROUP (ORDER BY ic.key_ordinal)
        FROM sys.index_columns ic
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
          AND ic.is_included_column = 0) AS [columns],
       CAST(NULL AS nvarchar(128)) AS [ref_schema],
       CAST(NULL AS nvarchar(128)) AS [ref_table],
       CAST(NULL AS nvarchar(max)) AS [ref_columns]
FROM sys.key_constraints kc
JOIN sys.objects o ON o.object_id = kc.parent_object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE @scope(s.name) AND kc.type IN ('PK', 'UQ') AND o.type IN ('U', 'V')
UNION ALL
SELECT s.name, o.name, fk.name, 'fk',
       (SELECT STRING_AGG(pc.name, ',') WITHIN GROUP (ORDER BY fkc.constraint_column_id)
        FROM sys.foreign_key_columns fkc
        JOIN sys.columns pc
          ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
        WHERE fkc.constraint_object_id = fk.object_id),
       rs.name, ro.name,
       (SELECT STRING_AGG(rc.name, ',') WITHIN GROUP (ORDER BY fkc.constraint_column_id)
        FROM sys.foreign_key_columns fkc
        JOIN sys.columns rc
          ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
        WHERE fkc.constraint_object_id = fk.object_id)
FROM sys.foreign_keys fk
JOIN sys.objects o ON o.object_id = fk.parent_object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.objects ro ON ro.object_id = fk.referenced_object_id
JOIN sys.schemas rs ON rs.schema_id = ro.schema_id
WHERE @scope(s.name) AND o.type IN ('U', 'V')
ORDER BY 1, 2, 3""",
    "indexes": """
SELECT s.name AS [schema],
       o.name AS [table],
       i.name AS [index],
       (SELECT STRING_AGG(c.name, ',') WITHIN GROUP (ORDER BY ic.key_ordinal)
        FROM sys.index_columns ic
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id
          AND ic.is_included_column = 0 AND ic.key_ordinal > 0) AS [columns],
       CASE WHEN i.is_unique = 1 THEN 'true' ELSE 'false' END AS [is_unique]
FROM sys.indexes i
JOIN sys.objects o ON o.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE @scope(s.name) AND o.type IN ('U', 'V') AND i.index_id > 0 AND i.name IS NOT NULL
ORDER BY s.name, o.name, i.name""",
    "routines": f"""
SELECT s.name AS [schema],
       o.name AS [name],
       CASE o.type WHEN 'P' THEN 'procedure' ELSE 'function' END AS [kind],
       (SELECT STRING_AGG(p.name + ' ' + {_mssql_type("pt", "p")}, ', ')
               WITHIN GROUP (ORDER BY p.parameter_id)
        FROM sys.parameters p
        JOIN sys.types pt ON pt.user_type_id = p.user_type_id
        WHERE p.object_id = o.object_id AND p.parameter_id > 0) AS [signature],
       m.definition AS [definition]
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
WHERE @scope(s.name) AND o.type IN ('P', 'FN', 'IF', 'TF')
ORDER BY s.name, o.name""",
}

# -- Oracle ----------------------------------------------------------------------------


def _ora_tables(owner: str, table: str) -> str:
    """Joins that keep only the tables and views the Connector reads (not recycle-bin,
    nested or materialised-view tables): ``all_tab_comments`` as ``c``, ``all_tables`` as
    ``t``. Pair with ``_ORA_REAL_TABLE`` in the WHERE clause."""
    return (
        f"JOIN all_tab_comments c ON c.owner = {owner} AND c.table_name = {table}\n"
        "LEFT JOIN all_tables t ON t.owner = c.owner AND t.table_name = c.table_name"
    )


_ORA_REAL_TABLE = """c.table_type IN ('TABLE', 'VIEW') AND c.table_name NOT LIKE 'BIN$%'
  AND (t.table_name IS NULL OR (t.dropped = 'NO' AND t.nested = 'NO'))
  AND NOT EXISTS (SELECT 1 FROM all_mviews m
                  WHERE m.owner = c.owner AND m.mview_name = c.table_name
                    AND c.table_type = 'TABLE')"""

_ORACLE: dict[str, str] = {
    "schemas": """
SELECT u.username AS "schema"
FROM all_users u
WHERE @scope(u.username)
ORDER BY u.username""",
    "tables": f"""
SELECT c.owner AS "schema",
       c.table_name AS "table",
       CASE c.table_type WHEN 'VIEW' THEN 'view' ELSE 'table' END AS "kind",
       t.num_rows AS "row_count",
       c.comments AS "comment",
       v.text AS "definition"
FROM all_tab_comments c
LEFT JOIN all_tables t ON t.owner = c.owner AND t.table_name = c.table_name
LEFT JOIN all_views v
  ON v.owner = c.owner AND v.view_name = c.table_name AND c.table_type = 'VIEW'
WHERE @scope(c.owner) AND {_ORA_REAL_TABLE}
ORDER BY c.owner, c.table_name""",
    "columns": f"""
SELECT col.owner AS "schema",
       col.table_name AS "table",
       col.column_name AS "column",
       CASE WHEN col.data_type IN ('VARCHAR2', 'NVARCHAR2', 'CHAR', 'NCHAR')
                 AND col.char_length > 0
            THEN col.data_type || '(' || col.char_length || ')'
            WHEN col.data_type = 'NUMBER' AND col.data_precision IS NOT NULL
            THEN CASE WHEN col.data_scale <> 0
                      THEN 'NUMBER(' || col.data_precision || ',' || col.data_scale || ')'
                      ELSE 'NUMBER(' || col.data_precision || ')' END
            ELSE col.data_type END AS "data_type",
       col.column_id AS "ordinal",
       CASE col.nullable WHEN 'Y' THEN 'true' ELSE 'false' END AS "is_nullable",
       col.data_default AS "default",
       m.comments AS "comment"
FROM all_tab_columns col
{_ora_tables("col.owner", "col.table_name")}
LEFT JOIN all_col_comments m
  ON m.owner = col.owner AND m.table_name = col.table_name AND m.column_name = col.column_name
WHERE @scope(col.owner) AND {_ORA_REAL_TABLE}
ORDER BY col.owner, col.table_name, col.column_id""",
    "constraints": f"""
SELECT k.owner AS "schema",
       k.table_name AS "table",
       k.constraint_name AS "constraint",
       CASE k.constraint_type WHEN 'P' THEN 'pk' WHEN 'R' THEN 'fk' ELSE 'unique' END AS "type",
       (SELECT LISTAGG(cc.column_name, ',') WITHIN GROUP (ORDER BY cc.position)
        FROM all_cons_columns cc
        WHERE cc.owner = k.owner AND cc.constraint_name = k.constraint_name) AS "columns",
       CASE WHEN k.constraint_type = 'R' THEN k.r_owner END AS "ref_schema",
       (SELECT MAX(rc.table_name) FROM all_cons_columns rc
        WHERE rc.owner = k.r_owner AND rc.constraint_name = k.r_constraint_name) AS "ref_table",
       (SELECT LISTAGG(rc.column_name, ',') WITHIN GROUP (ORDER BY rc.position)
        FROM all_cons_columns rc
        WHERE rc.owner = k.r_owner AND rc.constraint_name = k.r_constraint_name) AS "ref_columns"
FROM all_constraints k
{_ora_tables("k.owner", "k.table_name")}
WHERE @scope(k.owner) AND k.constraint_type IN ('P', 'R', 'U') AND {_ORA_REAL_TABLE}
ORDER BY k.owner, k.table_name, k.constraint_name""",
    # all_ind_expressions.column_expression is a LONG, which plain SQL cannot read: it goes
    # through DBMS_XMLGEN. Only a function-based index's generated column needs it.
    "indexes": f"""
SELECT x.sch AS "schema",
       x.tbl AS "table",
       x.idx AS "index",
       LISTAGG(x.col, ',') WITHIN GROUP (ORDER BY x.pos) AS "columns",
       MAX(x.uniq) AS "is_unique"
FROM (
  SELECT i.table_owner AS sch, i.table_name AS tbl, i.index_name AS idx,
         ic.column_position AS pos,
         CASE i.uniqueness WHEN 'UNIQUE' THEN 'true' ELSE 'false' END AS uniq,
         NVL(CASE WHEN ic.column_name LIKE 'SYS\\_NC%' ESCAPE '\\' THEN
                XMLCAST(XMLQUERY('/ROWSET/ROW/COLUMN_EXPRESSION/text()' PASSING
                  DBMS_XMLGEN.GETXMLTYPE(
                    'SELECT column_expression FROM all_ind_expressions WHERE index_owner = '''
                    || ic.index_owner || ''' AND index_name = ''' || ic.index_name
                    || ''' AND column_position = ' || ic.column_position)
                  RETURNING CONTENT) AS VARCHAR2(4000)) END, ic.column_name) AS col
  FROM all_indexes i
  JOIN all_ind_columns ic ON ic.index_owner = i.owner AND ic.index_name = i.index_name
  {_ora_tables("i.table_owner", "i.table_name")}
  WHERE @scope(i.table_owner) AND i.index_type <> 'LOB' AND {_ORA_REAL_TABLE}
) x
GROUP BY x.sch, x.tbl, x.idx
ORDER BY x.sch, x.tbl, x.idx""",
    "routines": """
SELECT o.owner AS "schema",
       o.object_name AS "name",
       CASE o.object_type WHEN 'PROCEDURE' THEN 'procedure' ELSE 'function' END AS "kind",
       (SELECT LISTAGG(a.argument_name || ' '
                       || CASE WHEN a.in_out = 'IN' THEN NULL
                               ELSE REPLACE(a.in_out, '/', ' ') || ' ' END
                       || a.data_type, ', ') WITHIN GROUP (ORDER BY a.position)
        FROM all_arguments a
        WHERE a.owner = o.owner AND a.object_name = o.object_name AND a.package_name IS NULL
          AND a.argument_name IS NOT NULL AND a.data_level = 0) AS "signature",
       (SELECT CASE WHEN COUNT(*) > 0 THEN 'CREATE OR REPLACE '
                    || LISTAGG(s.text ON OVERFLOW TRUNCATE '...' WITHOUT COUNT)
                       WITHIN GROUP (ORDER BY s.line) END
        FROM all_source s
        WHERE s.owner = o.owner AND s.name = o.object_name AND s.type = o.object_type)
         AS "definition"
FROM all_objects o
WHERE @scope(o.owner) AND o.object_type IN ('FUNCTION', 'PROCEDURE')
ORDER BY o.owner, o.object_name""",
}

# -- IBM Db2 (LUW) ---------------------------------------------------------------------


def _db2_tables(a: str) -> str:
    return f"JOIN SYSCAT.TABLES t ON t.TABSCHEMA = {a}.TABSCHEMA AND t.TABNAME = {a}.TABNAME"


_DB2: dict[str, str] = {
    "schemas": """
SELECT s.SCHEMANAME AS "schema"
FROM SYSCAT.SCHEMATA s
WHERE @scope(s.SCHEMANAME)
ORDER BY s.SCHEMANAME""",
    "tables": """
SELECT t.TABSCHEMA AS "schema",
       t.TABNAME AS "table",
       CASE t.TYPE WHEN 'V' THEN 'view' ELSE 'table' END AS "kind",
       CASE WHEN t.TYPE = 'T' AND t.CARD >= 0 THEN t.CARD END AS "row_count",
       t.REMARKS AS "comment",
       CASE WHEN t.TYPE = 'V' THEN v.TEXT END AS "definition"
FROM SYSCAT.TABLES t
LEFT JOIN SYSCAT.VIEWS v ON v.VIEWSCHEMA = t.TABSCHEMA AND v.VIEWNAME = t.TABNAME
WHERE @scope(t.TABSCHEMA) AND t.TYPE IN ('T', 'V')
ORDER BY t.TABSCHEMA, t.TABNAME""",
    "columns": f"""
SELECT c.TABSCHEMA AS "schema",
       c.TABNAME AS "table",
       c.COLNAME AS "column",
       CASE WHEN c.TYPENAME IN ('CHARACTER', 'VARCHAR', 'GRAPHIC', 'VARGRAPHIC', 'BINARY',
                                'VARBINARY')
            THEN c.TYPENAME || '(' || RTRIM(CHAR(c.LENGTH)) || ')'
            WHEN c.TYPENAME IN ('DECIMAL', 'NUMERIC')
            THEN c.TYPENAME || '(' || RTRIM(CHAR(c.LENGTH)) || ',' || RTRIM(CHAR(c.SCALE)) || ')'
            WHEN c.TYPENAME = 'TIMESTAMP' THEN 'TIMESTAMP(' || RTRIM(CHAR(c.SCALE)) || ')'
            ELSE c.TYPENAME END AS "data_type",
       c.COLNO + 1 AS "ordinal",
       CASE c.NULLS WHEN 'Y' THEN 'true' ELSE 'false' END AS "is_nullable",
       c."DEFAULT" AS "default",
       c.REMARKS AS "comment"
FROM SYSCAT.COLUMNS c
{_db2_tables("c")}
WHERE @scope(c.TABSCHEMA) AND t.TYPE IN ('T', 'V')
ORDER BY c.TABSCHEMA, c.TABNAME, c.COLNO""",
    "constraints": """
SELECT tc.TABSCHEMA AS "schema",
       tc.TABNAME AS "table",
       tc.CONSTNAME AS "constraint",
       CASE tc.TYPE WHEN 'P' THEN 'pk' WHEN 'F' THEN 'fk' ELSE 'unique' END AS "type",
       (SELECT LISTAGG(k.COLNAME, ',') WITHIN GROUP (ORDER BY k.COLSEQ)
        FROM SYSCAT.KEYCOLUSE k
        WHERE k.TABSCHEMA = tc.TABSCHEMA AND k.TABNAME = tc.TABNAME
          AND k.CONSTNAME = tc.CONSTNAME) AS "columns",
       r.REFTABSCHEMA AS "ref_schema",
       r.REFTABNAME AS "ref_table",
       (SELECT LISTAGG(rk.COLNAME, ',') WITHIN GROUP (ORDER BY rk.COLSEQ)
        FROM SYSCAT.KEYCOLUSE rk
        WHERE rk.TABSCHEMA = r.REFTABSCHEMA AND rk.TABNAME = r.REFTABNAME
          AND rk.CONSTNAME = r.REFKEYNAME) AS "ref_columns"
FROM SYSCAT.TABCONST tc
LEFT JOIN SYSCAT.REFERENCES r
  ON r.TABSCHEMA = tc.TABSCHEMA AND r.TABNAME = tc.TABNAME AND r.CONSTNAME = tc.CONSTNAME
WHERE @scope(tc.TABSCHEMA) AND tc.TYPE IN ('P', 'F', 'U')
ORDER BY tc.TABSCHEMA, tc.TABNAME, tc.CONSTNAME""",
    "indexes": f"""
SELECT i.TABSCHEMA AS "schema",
       i.TABNAME AS "table",
       i.INDNAME AS "index",
       (SELECT LISTAGG(ic.COLNAME, ',') WITHIN GROUP (ORDER BY ic.COLSEQ)
        FROM SYSCAT.INDEXCOLUSE ic
        WHERE ic.INDSCHEMA = i.INDSCHEMA AND ic.INDNAME = i.INDNAME
          AND ic.COLORDER <> 'I') AS "columns",
       CASE WHEN i.UNIQUERULE IN ('U', 'P') THEN 'true' ELSE 'false' END AS "is_unique"
FROM SYSCAT.INDEXES i
{_db2_tables("i")}
WHERE @scope(i.TABSCHEMA) AND t.TYPE IN ('T', 'V')
ORDER BY i.TABSCHEMA, i.TABNAME, i.INDNAME""",
    "routines": """
SELECT r.ROUTINESCHEMA AS "schema",
       r.ROUTINENAME AS "name",
       CASE r.ROUTINETYPE WHEN 'P' THEN 'procedure' ELSE 'function' END AS "kind",
       (SELECT LISTAGG(p.PARMNAME || ' ' || p.TYPENAME, ', ') WITHIN GROUP (ORDER BY p.ORDINAL)
        FROM SYSCAT.ROUTINEPARMS p
        WHERE p.ROUTINESCHEMA = r.ROUTINESCHEMA AND p.SPECIFICNAME = r.SPECIFICNAME
          AND p.ROWTYPE IN ('P', 'O', 'B')) AS "signature",
       r.TEXT AS "definition"
FROM SYSCAT.ROUTINES r
WHERE @scope(r.ROUTINESCHEMA) AND r.ROUTINETYPE IN ('F', 'P')
ORDER BY r.ROUTINESCHEMA, r.ROUTINENAME, r.SPECIFICNAME""",
}

# -- Teradata --------------------------------------------------------------------------


def _td_type(c: str) -> str:
    """The type of a ``DBC.ColumnsV`` row ``c`` as Teradata spells it."""
    code = f"TRIM({c}.ColumnType)"
    length = f"CAST({c}.ColumnLength AS VARCHAR(10))"
    digits = f"CAST({c}.DecimalTotalDigits AS VARCHAR(10))"
    fraction = f"CAST({c}.DecimalFractionalDigits AS VARCHAR(10))"
    names = {
        "I": "INTEGER",
        "I1": "BYTEINT",
        "I2": "SMALLINT",
        "I8": "BIGINT",
        "F": "FLOAT",
        "DA": "DATE",
        "AT": "TIME",
        "TS": "TIMESTAMP",
        "TZ": "TIME WITH TIME ZONE",
        "SZ": "TIMESTAMP WITH TIME ZONE",
        "BO": "BLOB",
        "CO": "CLOB",
        "JN": "JSON",
        "XM": "XML",
        "N": "NUMBER",
    }
    cases = "".join(f"WHEN '{k}' THEN '{v}' " for k, v in names.items())
    return (
        f"CASE {code} WHEN 'CV' THEN 'VARCHAR(' || {length} || ')' "
        f"WHEN 'CF' THEN 'CHAR(' || {length} || ')' "
        f"WHEN 'BV' THEN 'VARBYTE(' || {length} || ')' "
        f"WHEN 'BF' THEN 'BYTE(' || {length} || ')' "
        f"WHEN 'D' THEN 'DECIMAL(' || {digits} || ',' || {fraction} || ')' "
        f"{cases}ELSE {code} END"
    )


def _td_list(value: str, order: str) -> str:
    """Teradata has no LISTAGG: the XMLAGG idiom joins values with commas."""
    return (
        f"TRIM(TRAILING ',' FROM CAST(XMLAGG(TRIM({value}) || ',' ORDER BY {order}) "
        "AS VARCHAR(4000)))"
    )


_TD_PARAMETER = (
    "CASE WHEN p.ParameterType = 'O' THEN 'OUT ' WHEN p.ParameterType = 'B' THEN 'INOUT ' "
    "ELSE '' END || p.ColumnName || ' ' || " + _td_type("p")
)

_TERADATA: dict[str, str] = {
    "schemas": """
SELECT d.DatabaseName AS "schema"
FROM DBC.DatabasesV d
WHERE @scope(d.DatabaseName)
ORDER BY d.DatabaseName""",
    "tables": """
SELECT t.DatabaseName AS "schema",
       t.TableName AS "table",
       CASE t.TableKind WHEN 'V' THEN 'view' ELSE 'table' END AS "kind",
       CAST(NULL AS BIGINT) AS "row_count",
       t.CommentString AS "comment",
       CASE WHEN t.TableKind = 'V' THEN t.RequestText END AS "definition"
FROM DBC.TablesV t
WHERE @scope(t.DatabaseName) AND t.TableKind IN ('T', 'O', 'V')
ORDER BY t.DatabaseName, t.TableName""",
    "columns": f"""
SELECT c.DatabaseName AS "schema",
       c.TableName AS "table",
       c.ColumnName AS "column",
       {_td_type("c")} AS "data_type",
       ROW_NUMBER() OVER (PARTITION BY c.DatabaseName, c.TableName ORDER BY c.ColumnId)
         AS "ordinal",
       CASE c.Nullable WHEN 'Y' THEN 'true' ELSE 'false' END AS "is_nullable",
       c.DefaultValue AS "default",
       c.CommentString AS "comment"
FROM DBC.ColumnsV c
JOIN DBC.TablesV t ON t.DatabaseName = c.DatabaseName AND t.TableName = c.TableName
WHERE @scope(c.DatabaseName) AND t.TableKind IN ('T', 'O', 'V')
ORDER BY c.DatabaseName, c.TableName, c.ColumnId""",
    # Primary keys and unique constraints are in DBC.IndicesV; foreign keys in the
    # referential-integrity view, which carries no column position.
    "constraints": f"""
SELECT i.DatabaseName AS "schema",
       i.TableName AS "table",
       COALESCE(i.IndexName, 'constraint_' || CAST(i.IndexNumber AS VARCHAR(10)))
         AS "constraint",
       CASE i.IndexType WHEN 'K' THEN 'pk' ELSE 'unique' END AS "type",
       {_td_list("i.ColumnName", "i.ColumnPosition")} AS "columns",
       CAST(NULL AS VARCHAR(128)) AS "ref_schema",
       CAST(NULL AS VARCHAR(128)) AS "ref_table",
       CAST(NULL AS VARCHAR(4000)) AS "ref_columns"
FROM DBC.IndicesV i
WHERE @scope(i.DatabaseName) AND i.IndexType IN ('K', 'U')
GROUP BY i.DatabaseName, i.TableName, i.IndexName, i.IndexNumber, i.IndexType
UNION ALL
SELECT r.ChildDB, r.ChildTable, r.IndexName, 'fk',
       {_td_list("r.ChildKeyColumn", "r.ChildKeyColumn")},
       MAX(r.ParentDB), MAX(r.ParentTable),
       {_td_list("r.ParentKeyColumn", "r.ChildKeyColumn")}
FROM DBC.All_RI_ChildrenV r
WHERE @scope(r.ChildDB)
GROUP BY r.ChildDB, r.ChildTable, r.IndexName
ORDER BY 1, 2, 3""",
    "indexes": f"""
SELECT i.DatabaseName AS "schema",
       i.TableName AS "table",
       COALESCE(i.IndexName, 'index_' || CAST(i.IndexNumber AS VARCHAR(10))) AS "index",
       {_td_list("i.ColumnName", "i.ColumnPosition")} AS "columns",
       CASE WHEN MAX(i.UniqueFlag) = 'Y' THEN 'true' ELSE 'false' END AS "is_unique"
FROM DBC.IndicesV i
WHERE @scope(i.DatabaseName) AND i.IndexType IN ('P', 'Q', 'S', 'U', 'K')
GROUP BY i.DatabaseName, i.TableName, i.IndexName, i.IndexNumber
ORDER BY 1, 2, 3""",
    "routines": f"""
SELECT t.DatabaseName AS "schema",
       t.TableName AS "name",
       CASE WHEN t.TableKind IN ('P', 'E') THEN 'procedure' ELSE 'function' END AS "kind",
       (SELECT {_td_list(_TD_PARAMETER, "p.ColumnId")}
        FROM DBC.ColumnsV p
        WHERE p.DatabaseName = t.DatabaseName AND p.TableName = t.TableName) AS "signature",
       t.RequestText AS "definition"
FROM DBC.TablesV t
WHERE @scope(t.DatabaseName) AND t.TableKind IN ('P', 'E', 'F', 'R')
ORDER BY t.DatabaseName, t.TableName""",
}

_QUERIES: dict[str, dict[str, str]] = {
    "postgresql": _POSTGRESQL,
    "mysql": _mysql(mariadb=False),
    "mariadb": _mysql(mariadb=True),
    "sqlserver": _SQLSERVER,
    "oracle": _ORACLE,
    "db2": _DB2,
    "teradata": _TERADATA,
}

SHEET_NAMES: tuple[str, ...] = tuple(sheet.name for sheet in SHEETS)


def helper_query(engine: str, sheet: str, schemas: tuple[str, ...] | None = None) -> str:
    """The query for one sheet. ``schemas`` limits it to those Database Schemas; without
    them it covers every non-system schema. ``KeyError`` for an unknown engine or sheet."""
    text = _QUERIES[engine][sheet].strip()
    return _SCOPE_MARK.sub(lambda m: _scope_clause(engine, m.group(1), schemas), text)


def helper_script(engine: str, schemas: tuple[str, ...] | None = None) -> str:
    """Every sheet's query for an engine as one commented SQL script."""
    scope = (
        "Limited to the schemas: " + ", ".join(schemas) + "."
        if schemas
        else "Covers every schema except the system ones; to narrow it, edit the schema "
        "condition in the WHERE clause."
    )
    lines = [
        f"-- DAWAM Schema Import helper queries for {ENGINE_LABELS[engine]}.",
        f"-- {_NOTES[engine]}",
        "-- Run each query on the source database and save its result, header row included,",
        "-- on the sheet of the Schema Import template with the same name (or as a CSV named",
        "-- like the sheet, e.g. columns.csv). Read-only: nothing here changes the database.",
        f"-- {scope}",
        "",
    ]
    for sheet in SHEET_NAMES:
        lines += [f"-- sheet: {sheet}", helper_query(engine, sheet, schemas) + ";", ""]
    return "\n".join(lines)


_ALIAS = re.compile(r'\bAS\s+(?:"([^"]+)"|`([^`]+)`|\[([^\]]+)\])', re.IGNORECASE)


def output_columns(query: str) -> tuple[str, ...]:
    """The quoted aliases of a helper query's select list, in order (by convention only
    the final select list quotes its aliases)."""
    return tuple(a or b or c for a, b, c in _ALIAS.findall(query))
