import os
import re
import sys
from datetime import date
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

WINDOW_START = date(2023, 5, 1)
OLD_END      = "2026-05-04"
OLD_DAYS     = 1100
SCRIPT_DIR   = Path(__file__).parent

CONN = {
    "host":     os.environ["REDSHIFT_HOST"],
    "port":     int(os.environ.get("REDSHIFT_PORT", "5439")),
    "dbname":   os.environ["REDSHIFT_DBNAME"],
    "user":     os.environ["REDSHIFT_USER"],
    "password": os.environ["REDSHIFT_PASSWORD"],
}


def patch(sql: str, new_end: str, new_days: int) -> str:
    sql = sql.replace(OLD_END, new_end)
    sql = re.sub(r'\bn < 1100\b', f'n < {new_days}', sql)
    return sql


def split_statements(sql: str) -> list:
    stmts    = []
    buf      = []
    in_sq    = False
    in_dq    = False
    in_lc    = False
    in_bc    = False
    in_dqtag = False
    dq_tag   = ""

    i = 0
    n = len(sql)

    while i < n:
        c = sql[i]

        if in_lc:
            buf.append(c)
            if c == '\n':
                in_lc = False
            i += 1
            continue

        if in_bc:
            buf.append(c)
            if c == '*' and i + 1 < n and sql[i + 1] == '/':
                buf.append('/')
                i += 2
                in_bc = False
            else:
                i += 1
            continue

        if in_sq:
            buf.append(c)
            if c == "'" and i + 1 < n and sql[i + 1] == "'":
                buf.append("'")
                i += 2
            elif c == "'":
                in_sq = False
                i += 1
            else:
                i += 1
            continue

        if in_dq:
            buf.append(c)
            if c == '"':
                in_dq = False
            i += 1
            continue

        if in_dqtag:
            rest      = sql[i:]
            close_pos = rest.find(dq_tag)
            if close_pos == -1:
                buf.append(c)
                i += 1
            else:
                chunk = rest[: close_pos + len(dq_tag)]
                buf.extend(chunk)
                i += close_pos + len(dq_tag)
                in_dqtag = False
                dq_tag   = ""
            continue

        if c == '$':
            rest = sql[i:]
            m = re.match(r'\$([A-Za-z_][A-Za-z_0-9]*)?\$', rest)
            if m:
                tag = m.group(0)
                buf.extend(tag)
                i += len(tag)
                in_dqtag = True
                dq_tag   = tag
                continue

        if c == '-' and i + 1 < n and sql[i + 1] == '-':
            in_lc = True
            buf.append(c)
            i += 1
            continue

        if c == '/' and i + 1 < n and sql[i + 1] == '*':
            in_bc = True
            buf.append(c)
            i += 1
            continue

        if c == "'":
            in_sq = True
            buf.append(c)
            i += 1
            continue

        if c == '"':
            in_dq = True
            buf.append(c)
            i += 1
            continue

        if c == ';':
            stmt = ''.join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(c)
        i += 1

    last = ''.join(buf).strip()
    if last:
        stmts.append(last)

    return stmts


def get_truncate_tables(sql: str) -> list:
    return re.findall(r'TRUNCATE\s+TABLE\s+(\w+)', sql, re.IGNORECASE)


def run_script_autocommit(conn, sql: str, name: str) -> None:
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET search_path TO lpp")
    for stmt in split_statements(sql):
        try:
            cur.execute(stmt)
        except Exception as e:
            print(f"    WARN ({name}): {e}")
    print(f"  ✓ {name}")


def run_script_transactional(conn, sql: str, name: str) -> bool:
    tables = get_truncate_tables(sql)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SET search_path TO lpp")
    try:
        for stmt in split_statements(sql):
            try:
                cur.execute(stmt)
            except psycopg2.ProgrammingError:
                pass
        empty = []
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            if cur.fetchone()[0] == 0:
                empty.append(t)
        if empty:
            conn.rollback()
            print(f"  ✗ {name}: empty tables {empty} — rolled back")
            return False
        conn.commit()
        print(f"  ✓ {name}")
        return True
    except Exception as e:
        conn.rollback()
        print(f"  ✗ {name}: {e} — rolled back")
        return False


def main() -> None:
    new_end  = date.today().isoformat()
    new_days = (date.today() - WINDOW_START).days + 1
    print(f"Updating window to {new_end} ({new_days} days from {WINDOW_START})\n")

    conn = psycopg2.connect(**CONN)

    setup = SCRIPT_DIR / "00_setup.sql"
    sql   = patch(setup.read_text(encoding="utf-8"), new_end, new_days)
    run_script_autocommit(conn, sql, setup.name)

    scripts = sorted(
        s for s in SCRIPT_DIR.glob("[0-1][0-9]_*.sql")
        if s.name != "00_setup.sql"
    )

    ok = failed = 0
    for script in scripts:
        sql = patch(script.read_text(encoding="utf-8"), new_end, new_days)
        if run_script_transactional(conn, sql, script.name):
            ok += 1
        else:
            failed += 1

    conn.close()
    print(f"\nDone: {ok} succeeded, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
