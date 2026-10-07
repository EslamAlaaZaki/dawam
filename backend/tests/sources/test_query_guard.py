# ruff: noqa: E501
"""The AI source query guard (ADR 0002, spec §6.8, story 68), tested at its one interface:
``check_query(sql, engine=, catalog=)`` returns ``Rejected`` or a ``SafeQuery``. Pure unit
tests over a hand-built Snapshot (``GuardCatalog``); ``test_query_guard_snapshot.py`` does one
end-to-end pass over a real extracted Snapshot.

The suite is table-driven. Every row runs on all four engines unless it names one. The
catalog has protected columns ``customers.national_id``, ``.email``, ``.salary`` and
``accounts.iban``.
"""

from __future__ import annotations

import pytest

from dawam.modules.sources.internal.query_guard import (
    GuardCatalog,
    GuardColumn,
    GuardTable,
    Rejected,
    SafeQuery,
    check_query,
    untraceable_views,
)

ENGINES = ("postgresql", "sqlserver", "mysql", "oracle")
PROTECTED = {"national_id", "email", "salary", "iban"}


def table(schema, name, cols, kind="table", definition=None):
    return (schema, name, tuple(cols), kind, definition)


SPEC = [
    table(
        "core",
        "customers",
        ["cust_no", "full_name", "national_id", "email", "salary", "branch_code", "age"],
    ),
    table("core", "accounts", ["acct_no", "cust_no", "iban", "balance"]),
    table("core", "branches", ["branch_code", "branch_name"]),
    table("core", "notes", ["note_id", "body"]),
    table("crm", "notes", ["note_id", "cust_no"]),
    table("crm", "leads", ["lead_id", "cust_no"]),
    table("restricted", "secrets", ["id", "v"]),
    table("core", "cust_syn", ["cust_no"], kind="synonym"),
    table(
        "core",
        "v_balances",
        ["cust_no", "total"],
        "view",
        "select c.cust_no, sum(a.balance) as total from core.customers c "
        "join core.accounts a on a.cust_no = c.cust_no group by c.cust_no",
    ),
    table(
        "core",
        "v_pii",
        ["cust_no", "prefix"],
        "view",
        "select cust_no, substring(national_id, 1, 3) as prefix from core.customers",
    ),
    table(
        "core",
        "v_ids",
        ["nid", "cust_no"],
        "view",
        "select national_id as nid, cust_no from core.customers",
    ),
    table("core", "v_star", ["branch_code", "branch_name"], "view", "select * from core.branches"),
    table("core", "v_chain", ["cust_no", "prefix"], "view", "select * from core.v_pii"),
    table(
        "core",
        "v_paren",
        ["cust_no", "acct_no"],
        "view",
        "SELECT c.cust_no, a.acct_no FROM (core.customers c JOIN core.accounts a ON ((a.cust_no = c.cust_no)))",
    ),
    table(
        "core",
        "v_filtered",
        ["v"],
        "view",
        "select branch_code as v from core.branches where branch_code = 'abc'",
    ),
    table("core", "v_bad", ["x"], "view", "select from where"),
    table("core", "v_empty", ["x"], "view", None),
    table("core", "v_multi", ["x"], "view", "select 1 as x; select 2 as x"),
    table("core", "v_cross", ["v"], "view", "select v from restricted.secrets"),
    table("core", "v_unknown", ["g"], "view", "select 1 as g from core.nothing"),
    table("core", "v_cycle_a", ["x"], "view", "select * from core.v_cycle_b"),
    table("core", "v_cycle_b", ["x"], "view", "select * from core.v_cycle_a"),
    table("core", "v_fn", ["g"], "view", "select g from generate_series(1, 3) as g"),
]
TRACEABLE = {"v_filtered", "v_balances", "v_pii", "v_ids", "v_star", "v_chain", "v_paren"}
UNTRACEABLE = {
    "v_bad",
    "v_empty",
    "v_multi",
    "v_cross",
    "v_unknown",
    "v_cycle_a",
    "v_cycle_b",
    "v_fn",
}


def catalog(engine: str, *, database: str = "bank") -> GuardCatalog:
    up = (lambda s: s.upper()) if engine == "oracle" else (lambda s: s)
    tables = tuple(
        GuardTable(
            up(schema),
            up(name),
            tuple(GuardColumn(up(c), c in PROTECTED) for c in cols),
            kind,
            definition,
        )
        for schema, name, cols, kind, definition in SPEC
    )
    return GuardCatalog(database, (up("core"), up("crm")), tables)


def run(sql: str, engine: str) -> SafeQuery | Rejected:
    return check_query(sql, engine=engine, catalog=catalog(engine))


def accepted(sql: str, engine: str) -> SafeQuery:
    result = run(sql, engine)
    assert isinstance(result, SafeQuery), f"{engine}: {sql!r} was rejected: {result}"
    return result


def rejected(sql: str, engine: str, fragment: str | None = None) -> Rejected:
    result = run(sql, engine)
    assert isinstance(result, Rejected), (
        f"{engine}: {sql!r} was accepted as {getattr(result, 'sql', None)!r}"
    )
    if fragment:
        assert fragment.lower() in result.reason.lower(), result.reason
    return result


def each_engine(rows):
    """Expand ``(sql, ...)`` rows to one pytest param per engine."""
    return [
        pytest.param(engine, *row, id=f"{engine}:{row[0][:60]}")
        for row in rows
        for engine in ENGINES
    ]


# --- accepted queries, and which output columns are masked -----------------------------------------

ACCEPTED = [
    # (sql, masked output names)
    ("select cust_no, branch_code from core.customers", ()),
    ("select count(*) from core.customers", ()),
    ("select * from core.branches", ()),
    ("select c.* from core.branches c", ()),
    ("select 1", ()),
    ("select 1;", ()),
    ("SeLeCt cust_no FrOm core.customers", ()),
    ("select/**/cust_no/**/from/**/core.customers", ()),
    ("select /* note */ cust_no from core.customers -- trailing", ()),
    ("select cust_no from core.customers where full_name = 'a; drop table x -- y'", ()),
    ("select cust_no from core.customers where branch_code in ('a', 'b') and age > 18", ()),
    ("select cust_no from core.customers where age between 18 and 65 order by cust_no", ()),
    ("select cust_no, count(*) as n from core.customers group by cust_no having count(*) > 1", ()),
    ("select branch_code, count(*) from core.customers group by branch_code order by 2", ()),
    (
        "select c.cust_no, a.balance from core.customers c join core.accounts a on a.cust_no = c.cust_no",
        (),
    ),
    (
        "select c.cust_no from core.customers c left join core.accounts a on a.cust_no = c.cust_no where a.acct_no is null",
        (),
    ),
    ("select c.cust_no from core.customers c, core.accounts a where a.cust_no = c.cust_no", ()),
    ("select count(case when branch_code = 'a' then 1 end) from core.customers", ()),
    ("select row_number() over (order by cust_no) from core.customers", ()),
    (
        "select cust_no from core.customers c where exists (select 1 from core.accounts a where a.cust_no = c.cust_no)",
        (),
    ),
    ("select count(*) from core.customers where salary = age", ()),
    ("select cust_no from core.customers where cust_no in (select cust_no from core.accounts)", ()),
    ("select x.n from (select count(*) as n from core.customers) x", ()),
    ("with t as (select cust_no from core.customers) select * from t", ()),
    (
        "with t as (select cust_no from core.customers), u as (select cust_no from t) select count(*) from u",
        (),
    ),
    ("select cust_no from core.customers union all select cust_no from core.accounts", ()),
    (
        "select cust_no from core.customers where cust_no = 1 union select cust_no from core.accounts order by 1",
        (),
    ),
    ("select * from core.v_balances", ()),
    ("select cust_no, total from core.v_balances where total > 100", ()),
    ("select * from core.v_star", ()),
    ("select * from core.v_paren", ()),
    # a name resolves when it is unique among the allowed schemas
    ("select * from branches", ()),
    ("select * from leads", ()),
    # counting and joining on protected columns
    ("select count(national_id) from core.customers", ()),
    ("select count(distinct national_id) from core.customers", ()),
    (
        "select count(distinct c.national_id) from core.customers c join core.accounts a on a.cust_no = c.cust_no",
        (),
    ),
    (
        "select count(*) from core.customers c join core.customers d on c.national_id = d.national_id",
        (),
    ),
    ("select count(*) from core.customers c join core.branches b on b.branch_code = c.email", ()),
    (
        "select count(*) from core.customers c, core.customers d where c.national_id = d.national_id",
        (),
    ),
    ("select count(*) from core.customers where national_id is null", ()),
    ("select count(*) from core.customers where national_id is not null", ()),
    (
        "select count(*) from core.branches where branch_code in (select national_id from core.customers)",
        (),
    ),
    ("select count(*), count(email) from core.customers where cust_no > 5", ()),
    ("select count(salary) over (partition by branch_code) from core.customers", ()),
    (
        "select branch_code, count(distinct national_id) from core.customers group by branch_code",
        (),
    ),
    # derived from protected columns: returned, masked
    ("select * from core.customers", ("national_id", "email", "salary")),
    ("select c.* from core.customers c", ("national_id", "email", "salary")),
    ("select substring(national_id, 1, 3) as p from core.customers", ("p",)),
    ("select cust_no, concat(national_id, email) as j from core.customers", ("j",)),
    ("select cast(national_id as varchar(20)) as c from core.customers", ("c",)),
    ("select sum(salary) as s from core.customers", ("s",)),
    ("select avg(salary), count(*) from core.customers", ("col1",)),
    ("select lower(email) as e, branch_code from core.customers", ("e",)),
    ("select upper(email) from core.customers", ("col1",)),
    ("select p from (select substring(national_id, 1, 3) as p from core.customers) t", ("p",)),
    ("with t as (select upper(email) as e from core.customers) select e from t", ("e",)),
    (
        "select sum(c.salary) as s from core.customers c join core.accounts a on a.cust_no = c.cust_no",
        ("s",),
    ),
    (
        "select substring(national_id, 1, 2) as p from core.customers union all select branch_code from core.branches",
        ("p",),
    ),
    (
        "select (select substring(national_id, 1, 1) from core.customers where cust_no = 1) as s",
        ("s",),
    ),
    ("select cust_no, prefix from core.v_pii", ("prefix",)),
    ("select * from core.v_pii", ("prefix",)),
    (
        "select count(*) from (select substring(national_id, 1, 1) as p from core.customers union all select '1') t",
        (),
    ),
    ("select prefix from core.v_chain", ("prefix",)),
    ("select * from core.v_ids", ("nid",)),
    # a column joined to a protected one holds its values: returned masked, never compared
    (
        "select b.branch_code from core.customers a join core.branches b on a.national_id = b.branch_code",
        ("branch_code",),
    ),
    (
        "select b.branch_name, count(*) from core.customers a join core.branches b on a.national_id = b.branch_code group by b.branch_name",
        (),
    ),
    ("select * from (select * from core.customers) t", ("national_id", "email", "salary")),
]


@pytest.mark.parametrize(("engine", "sql", "masked"), each_engine(ACCEPTED))
def test_accepted_queries_and_the_columns_to_mask(engine, sql, masked):
    result = accepted(sql, engine)

    # Oracle folds unquoted names to upper case; generated names (col1) are not Snapshot names.
    assert [m.lower() for m in result.masked_columns] == list(masked)
    assert [i for i, c in enumerate(result.columns) if c.masked] == list(result.masked_ordinals)


# --- rejected queries -----------------------------------------------------------------------------

REJECTED = [
    # (sql, reason fragment or None)
    # multiple statements and statement types
    ("select 1; select 2", "one select"),
    ("select 1; drop table core.branches", None),
    ("select cust_no from core.customers; delete from core.customers", None),
    ("select 1 -- x\n; drop table core.branches", None),
    ("insert into core.branches values ('a', 'b')", "select"),
    ("update core.branches set branch_name = 'x'", "select"),
    ("delete from core.branches", "select"),
    ("drop table core.branches", None),
    ("truncate table core.branches", None),
    ("create table core.x (a int)", None),
    ("create table core.x as select 1", None),
    ("alter table core.branches add c int", None),
    ("grant select on core.branches to public", None),
    (
        "merge into core.branches b using core.branches c on (b.branch_code = c.branch_code) when matched then delete",
        None,
    ),
    ("call do_something()", None),
    ("explain select 1", None),
    ("values (1)", None),
    ("set role admin", None),
    ("begin", None),
    ("commit", None),
    # writes in CTEs
    ("with d as (delete from core.branches returning *) select * from d", None),
    ("with i as (insert into core.branches values ('a', 'b') returning *) select * from i", None),
    ("with u as (update core.branches set branch_name = 'x' returning *) select * from u", None),
    ("with t as (select 1) insert into core.branches select * from t", None),
    ("with t as (select 1) delete from core.branches", None),
    # SELECT INTO
    ("select * into core.copy from core.branches", "into"),
    ("select cust_no into backup_t from core.customers", "into"),
    ("select cust_no into #tmp from core.customers", None),
    # locking, hints, modifiers
    ("select * from core.branches for update", None),
    ("select * from core.branches for share", None),
    ("select * from core.branches for update nowait", None),
    ("select * from core.branches lock in share mode", None),
    ("select * from core.branches with (updlock)", None),
    ("select * from core.branches with (nolock)", None),
    ("select /*+ full(b) */ * from core.branches b", None),
    ("select /*+ leading */ 1", None),
    ("select /*!50000 sleep(5) */ 1", "comment"),
    ("select 1 /*!50000 union select 2 */", "comment"),
    ("select distinct on (cust_no) cust_no from core.customers", None),
    ("select cust_no from core.customers window w as (order by cust_no)", None),
    ("select row_number() over w from core.customers window w as (order by cust_no)", None),
    ("select count(*) filter (where cust_no > 1) from core.customers", None),
    ("select cust_no from core.customers fetch first 5 rows with ties", None),
    ("select cust_no from core.customers limit (select 1)", None),
    ("select cust_no from core.customers limit 5 offset (select 1)", None),
    ("select cust_no from core.customers offset -1", None),
    ("select top 5 percent cust_no from core.customers", None),
    (
        "with recursive r as (select 1 as n union all select n + 1 from r) select * from r",
        "recursive",
    ),
    ("select * from core.branches natural join core.notes", None),
    ("select * from core.branches b join core.notes n using (note_id)", None),
    ("select * from core.branches b, lateral (select 1) x", None),
    ("select * from core.branches b cross apply (select 1) x", None),
    ("select * from core.branches pivot (count(*) for branch_code in ('a'))", None),
    ("select * from core.branches tablesample (10)", None),
    # parse failures and junk
    ("", "empty"),
    ("   ", "empty"),
    ("-- nothing but a comment", None),
    ("/* nothing */", None),
    ("select", None),
    ("selec 1", None),
    ("select from", None),
    ("select 1 from", None),
    ("select 'unterminated", None),
    ("select (1", None),
    ("select 1 )", None),
    ("select 1\x00", "nul"),
    ("select $1", None),
    ("select :p from core.branches", None),
    ("select ? from core.branches", None),
    ("select @v", None),
    # functions with side effects, and user-defined ones
    ("select core.my_fn(1)", None),
    ("select my_fn(cust_no) from core.customers", "allow-list"),
    ("select lpad(branch_code, 1000000000, 'x') from core.customers", "allow-list"),
    ("select repeat(branch_code, 1000000000) from core.customers", "allow-list"),
    ("select nextval_x(1)", "allow-list"),
    ("select count(*) from core.customers where my_udf(cust_no) = 1", "allow-list"),
    ("select sum(my_agg(salary)) from core.customers", "allow-list"),
    ("select cust_no from core.customers order by random_thing()", None),
    # tables: resolution
    ("select * from core.nothing", "snapshot"),
    ("select * from nothing", "snapshot"),
    ("select * from core.cust_syn", "synonym"),
    ("select * from cust_syn", "synonym"),
    ("select * from restricted.secrets", "allowed"),
    ("select * from core.v_cross", "untraceable"),
    ("select * from notes", "ambiguous"),
    ("select * from core.branches b join restricted.secrets s on s.id = 1", "allowed"),
    ("select * from (select * from restricted.secrets) t", "allowed"),
    ("with t as (select * from restricted.secrets) select 1", "allowed"),
    ("select (select count(*) from restricted.secrets)", "allowed"),
    (
        "select * from core.branches where branch_code in (select v from restricted.secrets)",
        "allowed",
    ),
    (
        "select cust_no from core.customers where exists (select 1 from restricted.secrets)",
        "allowed",
    ),
    ("select * from core.branches union select v, v from restricted.secrets", "allowed"),
    ("select * from other_db.core.branches", None),
    ("select * from bank.restricted.secrets", None),
    ("select * from remote_srv.bank.core.branches", None),
    ("select * from core.branches@dblink", None),
    ("select * from information_schema.tables", None),
    ("select * from pg_catalog.pg_user", None),
    ("select * from sys.objects", None),
    ("select * from dual", None),
    ("select * from mysql.user", None),
    ("select * from all_tables", None),
    ("select * from v$session", None),
    ("select * from generate_series(1, 3)", None),
    ("select * from unnest(array[1, 2])", None),
    ("select * from openrowset('a', 'b', 'c')", None),
    ("select * from openquery(srv, 'select 1')", None),
    ("select * from table(fn())", None),
    ("select * from core.fn(1)", None),
    # columns
    ("select nothing from core.customers", "does not exist"),
    ("select x.cust_no from core.customers", "not in this query"),
    (
        "select cust_no from core.customers c join core.accounts a on a.cust_no = c.cust_no",
        "ambiguous",
    ),
    ("select core.customers.cust_no from core.customers", None),
    ("select bank.core.customers.cust_no from core.customers", None),
    ("select * from core.customers c join core.customers c on 1 = 1", "same name"),
    ("select (select cust_no, branch_code from core.customers)", "one column"),
    ("select 1 union select 1, 2", "column"),
    # views that cannot be traced
    ("select * from core.v_bad", "untraceable"),
    ("select * from core.v_empty", "untraceable"),
    ("select * from core.v_multi", "untraceable"),
    ("select * from core.v_unknown", "untraceable"),
    ("select * from core.v_cycle_a", "untraceable"),
    ("select * from core.v_fn", "untraceable"),
]


@pytest.mark.parametrize(("engine", "sql", "fragment"), each_engine(REJECTED))
def test_rejected_queries(engine, sql, fragment):
    rejected(sql, engine, fragment)


# --- protected columns ----------------------------------------------------------------------------

PROTECTED_REJECTED = [
    # projection, in every disguise
    "select national_id from core.customers",
    "select c.national_id from core.customers c",
    "select national_id as x from core.customers",
    "select (national_id) from core.customers",
    "select cust_no, email from core.customers",
    "select salary from core.customers",
    "select iban from core.accounts",
    "select x from (select national_id as x from core.customers) t",
    "select national_id from (select * from core.customers) t",
    "select x from (select national_id x from core.customers) t",
    "with t as (select email as e from core.customers) select e from t",
    "with t as (select * from core.customers) select email from t",
    "select nid from core.v_ids",
    "select v.nid from core.v_ids v",
    "select national_id from core.customers union select branch_code from core.branches",
    "select branch_code from core.branches union all select national_id from core.customers",
    # GROUP BY
    "select count(*) from core.customers group by national_id",
    "select count(*) from core.customers group by email, branch_code",
    "select count(*) from core.customers group by substring(national_id, 1, 1)",
    "select substring(national_id, 1, 1) as s, count(*) from core.customers group by s",
    "select substring(national_id, 1, 1) as s, count(*) from core.customers group by 1",
    "select count(*) from core.customers c group by c.national_id",
    # MIN, MAX and other value-emitting aggregates
    "select min(national_id) from core.customers",
    "select max(email) from core.customers",
    "select max(salary) from core.customers",
    "select min(c.iban) from core.accounts c",
    "select least(national_id, email) from core.customers",
    "select greatest(salary, 0) from core.customers",
    "select (select max(national_id) from core.customers)",
    "select x from (select min(national_id) as x from core.customers) t",
    "select max(salary) over () from core.customers",
    "select first_value(national_id) over (order by cust_no) from core.customers",
    "select lag(email) over (order by cust_no) from core.customers",
    "select lead(salary) over (order by cust_no) from core.customers",
    # comparisons with anything but another column
    "select count(*) from core.customers where national_id = '1012345678'",
    "select count(*) from core.customers where '1012345678' = national_id",
    "select count(*) from core.customers where national_id <> '1'",
    "select count(*) from core.customers where national_id > '1'",
    "select count(*) from core.customers where national_id like '10%'",
    "select count(*) from core.customers where national_id in ('1', '2')",
    "select count(*) from core.customers where national_id between '1' and '2'",
    "select count(*) from core.customers where substring(national_id, 1, 1) = '1'",
    "select count(*) from core.customers where length(national_id) = 10",
    "select count(*) from core.customers where upper(email) = lower(email)",
    "select count(*) from core.customers where not national_id = '1'",
    "select count(*) from core.customers where salary > 5000",
    "select count(*) from core.customers where national_id = (select '1')",
    "select count(*) from core.customers where national_id = cast('1' as varchar(10))",
    "select count(*) from core.customers where national_id in (select '1')",
    "select count(*) from core.customers where national_id in (select v from (select '1' as v) x)",
    "select count(*) from core.customers where national_id in (select branch_code from core.branches where 1 = 1 union select '5')",
    "select count(*) from core.branches where '1012345678' in (select national_id from core.customers)",
    "select count(*) from core.customers where coalesce(national_id, 'x') = 'x'",
    "select count(*) from core.customers where case when national_id = '1' then 1 else 0 end = 1",
    "select count(*) from core.customers group by branch_code having max(salary) > 1",
    "select count(case when national_id = '1' then 1 end) from core.customers",
    "select count(case when salary > 1 then 1 end) from core.customers",
    "select sum(case when email like 'a%' then 1 else 0 end) from core.customers",
    "select national_id = branch_code as same from core.customers c",
    "select count(*) from core.customers where national_id = branch_code or age = 1 or email = 'x'",
    # joins with literals, expressions or constructed values
    "select count(*) from core.customers c join core.branches b on c.national_id = '1'",
    "select count(*) from core.customers c join core.branches b on c.national_id like '1%'",
    "select count(*) from core.customers c join core.branches b on substring(c.national_id, 1, 2) = b.branch_code",
    "select count(*) from core.customers c join core.branches b on b.branch_code = lower(c.email)",
    "select count(*) from core.customers c join (select '1012345678' as v from core.branches) t on t.v = c.national_id",
    "select count(*) from core.customers c join (select case when 1 = 1 then '5' end as v from core.branches) t on c.national_id = t.v",
    "select count(*) from core.customers c join (select branch_code as v from core.branches union all select '1' as v) t on c.national_id = t.v",
    "select count(*) from core.customers c join core.branches b on c.national_id > b.branch_code",
    "select count(*) from core.customers c join core.branches b on c.national_id = b.branch_code and c.email = 'a@b.c'",
    "select count(*) from core.customers c where c.national_id in (select 1 as v from core.branches)",
    "with k as (select '1012345678' as v) select count(*) from core.customers c join k on k.v = c.national_id",
    # counting or comparing a transformed value leaks through NULLs, DISTINCT and row comparison
    "select count(case national_id when '1' then 1 end) from core.customers",
    "select count(nullif(national_id, '1')) from core.customers",
    "select count(substring(national_id, 20)) from core.customers",
    "select count(x) from (select nullif(national_id, '1') as x from core.customers) t",
    "select count(distinct p) from (select substring(national_id, 1, 1) as p from core.customers) t",
    "select count(*) from (select substring(national_id, 1, 1) as p from core.customers) t where p is null",
    "select count(*) from (select substring(national_id, 1, 1) as p from core.customers) t join core.branches b on b.branch_code = t.p",
    "select count(*) from (select distinct substring(national_id, 1, 1) as p from core.customers) t",
    "select count(*) from (select substring(national_id, 1, 1) as p from core.customers union select '1') t",
    "select substring(national_id, 1, 1) from core.customers intersect select '1'",
    "select substring(national_id, 1, 1) from core.customers except select '1'",
    "select count(prefix) from core.v_pii",
    "select count(*) from core.v_pii p join core.branches b on b.branch_code = p.prefix",
    "select count(*) from core.customers where (select sum(case national_id when '1' then 1 end) from core.customers) > 0",
    # boolean oracle: a bare expression on a protected value as a condition, or inside CASE
    "select count(*) from core.customers where ascii(substring(national_id, 1, 1)) - 65",
    "select count(*) from core.customers where cast(ascii(substring(national_id, 1, 1)) - 65 as boolean)",
    "select count(*) from core.customers where upper(national_id)",
    "select count(*) from core.customers where national_id",
    "select count(*) from core.customers where cust_no = 1 and substring(national_id, 1, 1)",
    "select count(*) from core.customers where cust_no = 1 or national_id is null",
    "select count(*) from core.customers where not (cust_no = 1 or national_id is null)",
    "select case when ascii(substring(national_id, 1, 1)) > 65 then 1 end from core.customers",
    "select case when cust_no > 1 then salary else 0 end as s from core.customers",
    "select case when upper(national_id) is null then 1 end from core.customers",
    "select coalesce(email, 'none') as e from core.customers",
    # error oracle: arithmetic, casts to non-text types, any other function on a protected value
    "select 1 / (ascii(substring(national_id, 1, 1)) - 65) from core.customers where cust_no = 5",
    "select cast(national_id as int) from core.customers",
    "select cast(national_id as bigint) from core.customers where cust_no = 5",
    "select try_cast(national_id as int) from core.customers",
    "select salary * 12 as yearly from core.customers",
    "select -salary from core.customers",
    "select salary + 1 from core.customers",
    "select abs(salary) from core.customers",
    "select sqrt(salary) from core.customers",
    "select power(salary, 2) from core.customers",
    "select round(salary) from core.customers",
    "select length(email) from core.customers",
    "select ascii(national_id) from core.customers",
    "select replace(email, 'a', 'b') from core.customers",
    "select sum(salary * 2) from core.customers",
    "select sum(distinct salary) from core.customers",
    "select avg(abs(salary)) from core.customers",
    "select substring('abcdef', cast(national_id as varchar(5))) from core.customers",
    "select substring(email, length(email) - 1) from core.customers",
    "select concat(upper(national_id), ascii(email)) from core.customers",
    # constants laundered through aggregates and window functions can pin a protected column
    "select count(*) from core.customers a join (select count(1) + count(1) as x from core.accounts) b on a.national_id = b.x",
    "select count(*) from core.customers a join (select count(*) as x from core.accounts) b on a.national_id = b.x",
    "select count(*) from core.customers a join (select row_number() over (order by acct_no) as x from core.accounts) b on a.national_id = b.x",
    "select count(*) from core.customers a join (select rank() over (order by acct_no) as x from core.accounts) b on a.salary = b.x",
    "select count(*) from core.customers where national_id in (select count(*) from core.accounts)",
    "select count(*) from core.customers where national_id = (select count(*) from core.accounts)",
    # a literal filter inside a subquery, derived table, CTE or view pins the joined column
    "select count(*) from core.customers where national_id in (select branch_code from core.branches where branch_code = 'abc')",
    "select count(*) from core.customers a join (select branch_code as v from core.branches where branch_code = 'abc') d on a.national_id = d.v",
    "select count(*) from core.customers a join (select branch_code as v from core.branches where branch_code like 'a%') d on a.national_id = d.v",
    "select count(*) from core.customers a join (select branch_code as v from core.branches where branch_code in ('abc')) d on a.national_id = d.v",
    "with d as (select branch_code as v from core.branches where branch_code = 'abc') select count(*) from core.customers a join d on a.national_id = d.v",
    "select count(*) from core.customers a join core.v_filtered d on a.national_id = d.v",
    "select count(*) from core.customers a join core.v_filtered d on a.email = d.v",
    "select count(*) from core.customers where national_id in (select v from core.v_filtered)",
    # transitivity: whatever is equated with a protected column is protected too
    "select count(*) from core.customers a join core.branches b on a.national_id = b.branch_code where b.branch_code = 'x'",
    "select count(*) from core.customers a join core.branches b on a.national_id = b.branch_code where b.branch_code in ('x', 'y')",
    "select count(*) from core.customers a join core.branches b on a.national_id = b.branch_code where b.branch_code like '1%'",
    "select count(*) from core.customers a join core.branches b on a.national_id = b.branch_code group by b.branch_code",
    "select count(*) from core.customers a join core.branches b on a.national_id = b.branch_code order by b.branch_code",
    "select max(b.branch_code) from core.customers a join core.branches b on a.national_id = b.branch_code",
    "select 1 / (ascii(b.branch_code) - 65) from core.customers a join core.branches b on a.national_id = b.branch_code",
    "select upper(b.branch_code) from core.customers a join core.branches b on a.national_id = b.branch_code",
    "select count(*) from core.customers a join core.branches b on a.national_id = b.branch_code join core.notes n on n.body = b.branch_code where n.body = 'x'",
    "select count(*) from core.customers a join core.branches b on a.national_id = b.branch_code where b.branch_code = a.branch_code and a.branch_code = 'x'",
    "select count(*) from core.customers a, core.branches b where a.email = b.branch_name and b.branch_name = 'x'",
    "select count(*) from core.customers a join (select branch_code as v from core.branches) d on a.national_id = d.v where d.v = 'x'",
    "select count(*) from core.customers a join (select branch_code as v from core.branches) d on a.national_id = d.v where length(d.v) > 3",
    "select cust_no from core.customers order by national_id",
    "select cust_no from core.customers order by email desc limit 1",
    "select cust_no from core.customers order by salary",
    "select cust_no from core.customers order by upper(national_id)",
    "select substring(national_id, 1, 3) as p from core.customers order by 1",
    "select substring(national_id, 1, 3) as p from core.customers order by p",
    "select cust_no, salary * 2 as d from core.customers order by d",
    "select cust_no from core.customers union select cust_no from core.accounts order by (select max(salary) from core.customers)",
    "select branch_code as national_id, count(*) from core.customers group by national_id",
    "select substring(national_id, 1, 1) as cust_no from core.customers order by cust_no",
    "select count(*) over (partition by national_id) from core.customers",
    "select count(*) over (order by email) from core.customers",
    "select row_number() over (order by national_id) from core.customers",
    "select rank() over (partition by branch_code order by salary desc) from core.customers",
    "select ntile(4) over (order by salary) from core.customers",
    "select sum(balance) over (partition by iban) from core.accounts",
    "select cust_no from (select cust_no, row_number() over (order by national_id) as rn from core.customers) t where rn = 1",
    # through a view
    "select count(*) from core.v_ids group by nid",
    "select count(*) from core.v_ids where nid = '1'",
    "select count(*) from core.v_ids where nid like '1%'",
    "select min(nid) from core.v_ids",
    "select cust_no from core.v_ids order by nid",
    "select count(*) from core.v_pii group by prefix",
    "select count(*) from core.v_pii where prefix = '101'",
    "select max(prefix) from core.v_chain",
    "select cust_no from core.v_chain order by prefix",
    "select count(*) from core.v_pii p join core.branches b on p.prefix = '1'",
]


@pytest.mark.parametrize(("engine", "sql"), each_engine([(q,) for q in PROTECTED_REJECTED]))
def test_misuse_of_protected_columns_is_rejected(engine, sql):
    rejected(sql, engine)


def test_the_reason_names_the_protected_column():
    result = rejected("select national_id from core.customers", "postgresql")

    assert "core.customers.national_id" in result.reason
    assert "count" in result.reason.lower()


def test_masking_is_by_position_so_repeated_names_are_safe():
    result = accepted(
        "select cust_no, substring(national_id, 1, 1), cust_no, upper(email) from core.customers",
        "postgresql",
    )

    assert [c.name for c in result.columns] == ["cust_no", "col2", "cust_no", "col4"]
    assert result.masked_ordinals == (1, 3)


def test_the_output_columns_follow_the_select_list():
    result = accepted(
        "select cust_no as id, count(*) as n, upper(branch_code) from core.customers group by cust_no, branch_code",
        "postgresql",
    )

    assert [(c.name, c.masked) for c in result.columns] == [
        ("id", False),
        ("n", False),
        ("col3", False),
    ]


# --- the SQL that comes out -----------------------------------------------------------------------

QUOTES = {"postgresql": '"', "sqlserver": ("[", "]"), "mysql": "`", "oracle": '"'}


def q(engine: str, name: str) -> str:
    if engine == "oracle":
        name = name.upper()
    mark = QUOTES[engine]
    return mark[0] + name + mark[-1]


@pytest.mark.parametrize("engine", ENGINES)
def test_every_table_comes_out_fully_qualified_and_aliased(engine):
    result = accepted("select branch_code from branches", engine)

    assert f"{q(engine, 'core')}.{q(engine, 'branches')}" in result.sql
    assert (
        f"{q(engine, 'branches')} " in result.sql
    )  # aliased (Oracle has no AS before a table alias)
    assert result.sql.count(q(engine, "core")) == 1


@pytest.mark.parametrize("engine", ENGINES)
def test_the_input_is_never_what_runs(engine):
    result = accepted("select /* hello */ cust_no from core.customers -- bye", engine)

    assert "hello" not in result.sql
    assert "bye" not in result.sql
    assert "--" not in result.sql


@pytest.mark.parametrize("engine", ENGINES)
def test_a_star_is_expanded_to_the_snapshots_columns(engine):
    result = accepted("select * from core.branches", engine)

    assert "*" not in result.sql
    assert [c.name.lower() for c in result.columns] == ["branch_code", "branch_name"]


@pytest.mark.parametrize("engine", ENGINES)
def test_a_safe_query_passes_the_guard_again_unchanged(engine):
    for sql in [
        "select c.cust_no, count(*) as n from core.customers c join core.accounts a on a.cust_no = c.cust_no group by c.cust_no order by 2",
        "with t as (select cust_no from core.customers) select x.cust_no from (select * from t) x where x.cust_no in (select cust_no from core.accounts)",
        "select cust_no from core.customers union all select cust_no from core.accounts",
        "select count(distinct national_id), sum(salary) from core.customers",
        "select * from core.v_balances",
    ]:
        first = accepted(sql, engine)
        second = accepted(first.sql, engine)
        assert second.sql == first.sql
        assert second.columns == first.columns


# --- identifiers, quoting and engine specifics ----------------------------------------------------


@pytest.mark.parametrize(
    ("engine", "sql", "masked"),
    [
        ("postgresql", 'select "cust_no" from "core"."customers"', ()),
        ("postgresql", "select cust_no::text, national_id::text as n from core.customers", ("n",)),
        ("postgresql", "select cust_no from core.customers where full_name ilike 'a%'", ()),
        ("postgresql", "select cust_no from core.customers limit 5 offset 10", ()),
        ("postgresql", "select string_agg(branch_name, ',') from core.branches", ()),
        ("postgresql", "select date_trunc('month', now()), extract(year from current_date)", ()),
        ("postgresql", "select 'a' || branch_code from core.branches", ()),
        ("postgresql", "select CUST_NO from core.customers", ()),
        ("sqlserver", "select top 5 cust_no from core.customers", ()),
        ("sqlserver", "select [cust_no] from [core].[customers]", ()),
        ("sqlserver", "select CUST_NO from CORE.CUSTOMERS", ()),
        ("sqlserver", "select cust_no from bank.core.customers", ()),
        (
            "sqlserver",
            "select len(full_name), isnull(branch_code, ''), getdate() from core.customers",
            (),
        ),
        ("sqlserver", "select string_agg(branch_name, ',') from core.branches", ()),
        (
            "sqlserver",
            "select cust_no from core.customers order by cust_no offset 5 rows fetch next 5 rows only",
            (),
        ),
        ("mysql", "select cust_no from core.customers limit 5, 10", ()),
        ("mysql", "select `cust_no` from `core`.`customers`", ()),
        ("mysql", "select CUST_NO from CORE.Customers", ()),
        ("mysql", "select group_concat(branch_name) from core.branches", ()),
        ("mysql", "select ifnull(branch_code, ''), now(), curdate() from core.branches", ()),
        ("oracle", "select cust_no from core.customers fetch first 5 rows only", ()),
        ("oracle", 'select "CUST_NO" from "CORE"."CUSTOMERS"', ()),
        (
            "oracle",
            "select nvl(branch_code, 'x'), to_char(sysdate), listagg(branch_name, ',') within group (order by branch_name) from core.branches group by branch_code",
            (),
        ),
        ("oracle", "select substr(national_id, 1, 3) as p from core.customers", ("P",)),
    ],
    ids=lambda v: v if isinstance(v, str) and len(v) < 70 else None,
)
def test_engine_specific_syntax_that_is_accepted(engine, sql, masked):
    assert accepted(sql, engine).masked_columns == masked


@pytest.mark.parametrize(
    ("engine", "sql"),
    [
        # protected-column oracles named in the security review
        (
            "mysql",
            "select count(*) from core.customers where ascii(substring(national_id, 1, 1)) - 65",
        ),
        (
            "mysql",
            "select count(*) from core.customers where 1 / (ascii(substring(national_id, 1, 1)) - 65)",
        ),
        ("sqlserver", "select count(*) from core.customers where convert(int, national_id) = 1"),
        ("sqlserver", "select convert(int, national_id) from core.customers"),
        ("sqlserver", "select count(*) from core.customers where len(national_id) + 1"),
        ("oracle", "select count(*) from core.customers where to_number(national_id) > 1"),
        ("oracle", "select count(*) from core.customers where decode(national_id, '1', 1, 0) = 1"),
        ("oracle", "select count(*) from core.customers where nvl2(national_id, 1, 0) = 1"),
        ("mysql", "select count(*) from core.customers where ifnull(national_id, 'x') = 'x'"),
        (
            "postgresql",
            "select count(*) from core.customers where cast(ascii(substring(national_id, 1, 1)) - 65 as boolean)",
        ),
        # postgresql
        ("postgresql", "select pg_sleep(10)"),
        ("postgresql", "select pg_terminate_backend(123)"),
        ("postgresql", "select nextval('core.s')"),
        ("postgresql", "select setval('core.s', 1)"),
        ("postgresql", "select lastval()"),
        ("postgresql", "select dblink('host=x', 'select 1')"),
        ("postgresql", "select * from dblink('host=x', 'select 1') as t(a int)"),
        ("postgresql", "select lo_import('/etc/passwd')"),
        ("postgresql", "select pg_read_file('/etc/passwd')"),
        ("postgresql", "select set_config('role', 'postgres', false)"),
        ("postgresql", "select current_setting('server_version')"),
        ("postgresql", "select pg_advisory_lock(1)"),
        ("postgresql", "select txid_current()"),
        ("postgresql", "select version()"),
        ("postgresql", "select current_user"),
        ("postgresql", "select pg_catalog.pg_sleep(1)"),
        ("postgresql", "select cust_no from core.customers where pg_sleep(1) is not null"),
        ("postgresql", "select cust_no from core.customers order by pg_sleep(1)"),
        ("postgresql", "select random()"),
        ("postgresql", "select 1 from core.branches where exists (select pg_sleep(1))"),
        ("postgresql", "select E'\\x41'"),
        ("postgresql", "select $$; drop table x; $$ as s"),
        ("postgresql", "select array[1, 2]"),
        ("postgresql", "select a->'b' from core.branches"),
        ("postgresql", "select cust_no from core.customers where national_id ~ '^1'"),
        ("postgresql", "select cust_no from core.customers where email similar to 'a%'"),
        ("postgresql", "select cust_no from core.customers where email ilike 'a%'"),
        ("postgresql", "select cust_no from core.customers where national_id::text = '1'"),
        ("postgresql", 'select * from "CORE"."customers"'),
        ("postgresql", 'select "CUST_NO" from core.customers'),
        ("postgresql", "select * from core.customers tablesample bernoulli (10)"),
        ("postgresql", "copy core.branches to program 'id'"),
        ("postgresql", "do $$ begin perform 1; end $$"),
        # sqlserver
        ("sqlserver", "exec xp_cmdshell 'dir'"),
        ("sqlserver", "select xp_cmdshell('dir')"),
        ("sqlserver", "select * from openrowset('SQLNCLI', 'x', 'select 1')"),
        ("sqlserver", "select * from openquery(srv, 'select 1')"),
        ("sqlserver", "select * from srv.bank.core.branches"),
        ("sqlserver", "select * from [srv].[bank].[core].[branches]"),
        ("sqlserver", "select * from otherdb.core.branches"),
        ("sqlserver", "select * from otherdb..branches"),
        ("sqlserver", "select * from sys.objects"),
        ("sqlserver", "select newid()"),
        ("sqlserver", "select @@version"),
        ("sqlserver", "select db_name()"),
        ("sqlserver", "select object_id('core.branches')"),
        ("sqlserver", "select suser_sname()"),
        ("sqlserver", "select user_name()"),
        ("sqlserver", "select * from core.branches with (nolock)"),
        ("sqlserver", "select * into core.copy from core.branches"),
        ("sqlserver", "select top 1 with ties cust_no from core.customers order by cust_no"),
        ("sqlserver", "select cust_no from core.customers option (maxdop 1)"),
        ("sqlserver", "waitfor delay '00:00:10'"),
        ("sqlserver", "select cust_no from core.customers; waitfor delay '00:00:10'"),
        ("sqlserver", "select * from core.branches for xml auto"),
        ("sqlserver", "use master"),
        # mysql
        ("mysql", "select sleep(10)"),
        ("mysql", "select benchmark(1000000, md5('a'))"),
        ("mysql", "select load_file('/etc/passwd')"),
        ("mysql", "select get_lock('a', 10)"),
        ("mysql", "select uuid()"),
        ("mysql", "select last_insert_id()"),
        ("mysql", "select @@version"),
        ("mysql", "select @x := 1"),
        ("mysql", "select user()"),
        ("mysql", "select database()"),
        ("mysql", "select * from core.branches into outfile '/tmp/x'"),
        ("mysql", "select * from core.branches into dumpfile '/tmp/x'"),
        ("mysql", "select 'a\\' from core.branches"),
        ("mysql", "select * from mysql.user"),
        ("mysql", "select * from information_schema.tables"),
        ("mysql", "select * from core.branches procedure analyse()"),
        ("mysql", "show tables"),
        ("mysql", "select cust_no from core.customers where national_id regexp '^1'"),
        # oracle
        ("oracle", "select dbms_lock.sleep(10) from core.branches"),
        ("oracle", "select utl_http.request('http://x') from core.branches"),
        ("oracle", "select seq.nextval from core.branches"),
        ("oracle", "select core.seq.nextval from core.branches"),
        ("oracle", "select sys_context('userenv', 'session_user') from core.branches"),
        ("oracle", "select dbms_random.value from core.branches"),
        ("oracle", "select user from core.branches"),
        ("oracle", "select sys_guid() from core.branches"),
        ("oracle", "select xmltype('<a/>') from core.branches"),
        ("oracle", "select rownum from core.branches"),
        ("oracle", "select * from core.branches@remote"),
        ("oracle", "select * from core.branches where rownum < 3"),
        ("oracle", "select * from dual"),
        ("oracle", "select * from all_tables"),
        ("oracle", "select * from v$session"),
        ("oracle", "select * from core.branches for update"),
        ("oracle", "select * from core.branches connect by level < 5"),
        ("oracle", "select * from core.branches b, core.notes n where b.branch_code = n.body(+)"),
        ("oracle", "select * from core.branches sample (10)"),
        ("oracle", "select * from core.branches as of timestamp sysdate"),
        ("oracle", 'select * from "core"."branches"'),
    ],
)
def test_engine_specific_dangers_are_rejected(engine, sql):
    rejected(sql, engine)


@pytest.mark.parametrize("engine", ("sqlserver", "mysql"))
def test_a_case_insensitive_engine_matches_names_loosely_but_emits_the_snapshots(engine):
    result = accepted("SELECT CUST_NO FROM CORE.CUSTOMERS", engine)

    assert q(engine, "customers") in result.sql
    assert q(engine, "cust_no") in result.sql


def test_a_case_sensitive_engine_matches_exactly():
    rejected('select * from "Core"."customers"', "postgresql")
    rejected('select "Cust_No" from core.customers', "postgresql")


def test_the_same_database_name_is_accepted_only_where_the_engine_has_one():
    accepted("select * from bank.core.branches", "postgresql")
    accepted("select * from BANK.core.branches", "sqlserver")
    rejected("select * from bank2.core.branches", "postgresql", "other databases")
    rejected("select * from bank2.core.branches", "sqlserver", "other databases")
    rejected("select * from bank.core.branches", "mysql")
    rejected("select * from bank.core.branches", "oracle")


# --- views ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_untraceable_views_are_listed(engine):
    found = untraceable_views(engine=engine, catalog=catalog(engine))

    names = {v.name.lower() for v in found}
    assert names == UNTRACEABLE
    assert all(v.reason for v in found)


@pytest.mark.parametrize("engine", ENGINES)
def test_traceable_views_are_queryable(engine):
    for name in TRACEABLE:
        accepted(f"select count(*) from core.{name}", engine)


def test_a_view_over_a_protected_column_is_protected_through_the_view():
    result = accepted("select cust_no, prefix from core.v_chain", "postgresql")

    assert result.masked_columns == ("prefix",)
    rejected("select count(*) from core.v_chain group by prefix", "postgresql", "group by")


def test_a_view_column_flagged_protected_itself_is_protected():
    base = catalog("postgresql")
    flagged = GuardCatalog(
        base.database,
        base.allowed_schemas,
        tuple(
            GuardTable(
                t.schema,
                t.name,
                tuple(
                    GuardColumn(
                        c.name, c.protected or (t.name == "v_balances" and c.name == "total")
                    )
                    for c in t.columns
                ),
                t.kind,
                t.definition,
            )
            for t in base.tables
        ),
    )

    assert isinstance(
        check_query(
            "select count(total) from core.v_balances", engine="postgresql", catalog=flagged
        ),
        SafeQuery,
    )
    assert isinstance(
        check_query("select total from core.v_balances", engine="postgresql", catalog=flagged),
        Rejected,
    )
    assert isinstance(
        check_query("select max(total) from core.v_balances", engine="postgresql", catalog=flagged),
        Rejected,
    )


def test_a_view_reading_a_disallowed_schema_is_untraceable_not_just_rejected_at_query_time():
    [cross] = [
        v
        for v in untraceable_views(engine="postgresql", catalog=catalog("postgresql"))
        if v.name == "v_cross"
    ]

    assert "allowed" in cross.reason.lower()


# --- the catalog itself ---------------------------------------------------------------------------


def test_a_table_in_the_catalog_outside_the_allowed_schemas_does_not_resolve():
    rejected("select * from restricted.secrets", "postgresql", "allowed")
    rejected("select * from secrets", "postgresql", "snapshot")


def test_an_unknown_engine_is_rejected():
    rejected("select 1", "db2", "engine")


def test_an_empty_catalog_resolves_nothing_but_constants():
    empty = GuardCatalog("bank", ("core",), ())

    assert isinstance(check_query("select 1", engine="postgresql", catalog=empty), SafeQuery)
    assert isinstance(
        check_query("select * from core.branches", engine="postgresql", catalog=empty), Rejected
    )


def test_duplicate_names_that_differ_only_by_case_are_ambiguous_on_a_case_insensitive_engine():
    ambiguous = GuardCatalog(
        "bank",
        ("core",),
        (
            GuardTable("core", "T", (GuardColumn("a"),)),
            GuardTable("core", "t", (GuardColumn("a"),)),
        ),
    )

    assert isinstance(
        check_query("select a from core.t", engine="sqlserver", catalog=ambiguous), Rejected
    )
    assert isinstance(
        check_query('select a from core."t"', engine="postgresql", catalog=ambiguous), SafeQuery
    )


# --- robustness -----------------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_oversized_and_pathological_input_is_rejected_not_crashed(engine):
    for sql in [
        "select " + "1," * 30000 + "1",
        "select " + "(" * 3000 + "1" + ")" * 3000,
        "select 1 " + "union select 1 " * 3000,
        "select " + "not " * 5000 + "true",
        "select a from core.branches where " + " and ".join(["branch_code = branch_code"] * 3000),
        "select 'é' || chr(0)",
        "﻿select 1",
        "select 1 from core.branches where branch_code = '" + "x" * 30000 + "'",
    ]:
        result = run(sql, engine)
        assert isinstance(result, (Rejected, SafeQuery))


@pytest.mark.parametrize("bad", [None, 1, b"select 1", ["select 1"]])
def test_non_string_input_is_rejected(bad):
    assert isinstance(
        check_query(bad, engine="postgresql", catalog=catalog("postgresql")), Rejected
    )  # type: ignore[arg-type]


def test_the_output_is_analysed_again_and_must_match():
    # every accepted query's SQL passes the whole analysis a second time, unchanged
    for engine in ENGINES:
        for sql, _ in ACCEPTED:
            if "*" in sql.replace("count(*)", ""):
                continue
            first = accepted(sql, engine)
            assert accepted(first.sql, engine).sql == first.sql
