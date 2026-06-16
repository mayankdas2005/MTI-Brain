import os
import re
from datetime import date
from pathlib import Path

import psycopg2
from psycopg2 import ProgrammingError
from dotenv import load_dotenv

load_dotenv()

VALIDATE_SQL = Path(__file__).parent / "99_validate.sql"
OLD_END      = "2026-05-04"
WINDOW_START = date(2023, 5, 1)

CONN = {
    "host":     os.environ["REDSHIFT_HOST"],
    "port":     int(os.environ.get("REDSHIFT_PORT", "5439")),
    "dbname":   os.environ["REDSHIFT_DBNAME"],
    "user":     os.environ["REDSHIFT_USER"],
    "password": os.environ["REDSHIFT_PASSWORD"],
}


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


def symbol(check_name: str, value) -> str:
    name = str(check_name).upper()
    if value is None:
        return "⚠️ "
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "⚠️ "
    if "_FUTURE_" in name:
        return "✅" if v == 0 else "❌"
    if "_COVERAGE" in name:
        return "✅" if v >= 90 else "❌"
    return "✅" if v == 0 else "⚠️ "


def main() -> None:
    conn = psycopg2.connect(**CONN)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("SET search_path TO lpp")

    cur.execute("SELECT value FROM lpp.gen_control WHERE key='WINDOW_END'")
    window_end = cur.fetchone()[0]
    new_days   = (date.fromisoformat(str(window_end)) - WINDOW_START).days + 1

    sql = VALIDATE_SQL.read_text(encoding="utf-8")
    sql = sql.replace(OLD_END, str(window_end))
    sql = re.sub(r'\bn < 1100\b', f'n < {new_days}', sql)

    print(f"Running 99_validate.sql  (WINDOW_END={window_end})\n")
    print(f"  {'CHECK':<50} {'VALUE':>12}  STATUS")
    print("  " + "-" * 68)

    for stmt in split_statements(sql):
        cur.execute(stmt)
        try:
            rows = cur.fetchall()
        except ProgrammingError:
            continue
        for row in rows:
            if len(row) >= 2:
                check, value = row[0], row[1]
                sym = symbol(str(check), value)
                print(f"  {str(check):<50} {str(value):>12}  {sym}")

    print()
    conn.close()


if __name__ == "__main__":
    main()
