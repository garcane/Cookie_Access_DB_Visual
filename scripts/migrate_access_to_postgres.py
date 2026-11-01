# /// script
# requires-python = ">=3.11"
# dependencies = ["pyodbc", "psycopg[binary]"]
# ///
"""Migrate the Cookie Creations Access database into PostgreSQL.

Usage (from the repo root):
    uv run --native-tls scripts/migrate_access_to_postgres.py

Reads archive/Database_for_Cookie_Creations_Database_Project.accdb through the 64-bit
Microsoft Access ODBC driver, (re)creates the cookie_creations database schema from
sql/01_schema.sql, loads the data, then creates the analysis views from
sql/02_analysis_views.sql.

Connection settings come from the standard libpq environment (PGHOST, PGPORT,
PGUSER, PGPASSWORD) and pgpass.conf; defaults are localhost / postgres.
"""

import os
import sys
from pathlib import Path

import psycopg
import pyodbc
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
ACCDB = ROOT / "archive" / "Database_for_Cookie_Creations_Database_Project.accdb"
TARGET_DB = os.environ.get("COOKIE_DB", "cookie_creations")

os.environ.setdefault("PGHOST", "localhost")
os.environ.setdefault("PGUSER", "postgres")

# (target table, Access SELECT, target columns, identity column)
TABLES = [
    (
        "customers",
        "SELECT ID, [First Name], [Last Name], [Email Address], Phone, Address,"
        " City, State, Zip, Country, Notes FROM Customers ORDER BY ID",
        ["customer_id", "first_name", "last_name", "email_address", "phone", "address",
         "city", "state", "zip", "country", "notes"],
        "customer_id",
    ),
    (
        "products",
        "SELECT CookiesID, CookiesName, Price FROM Products ORDER BY CookiesID",
        ["product_id", "cookie_name", "price"],
        "product_id",
    ),
    (
        "orders",
        "SELECT ID, [Customer ID], [Product ID], [Order Date], [Cookies Ordered],"
        " [Price Per Cookie], Revenue, [Order Filled] FROM [Order] ORDER BY ID",
        ["order_id", "customer_id", "product_id", "order_date", "cookies_ordered",
         "price_per_cookie", "revenue", "order_filled"],
        "order_id",
    ),
]


def convert(table, row):
    """Map Access types onto the Postgres schema (DOUBLE ids -> int, DATETIME -> date)."""
    row = list(row)
    if table == "products":
        row[0] = int(row[0])
    elif table == "orders":
        row[1], row[2], row[4] = int(row[1]), int(row[2]), int(row[4])
        row[3] = row[3].date()
        row[7] = bool(row[7])
    return row


def ensure_database():
    with psycopg.connect(dbname="postgres", autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", [TARGET_DB]).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TARGET_DB)))
            print(f"created database {TARGET_DB}")


def main():
    if not ACCDB.exists():
        sys.exit(f"Access database not found: {ACCDB}")

    access = pyodbc.connect(
        f"Driver={{Microsoft Access Driver (*.mdb, *.accdb)}};Dbq={ACCDB};ReadOnly=1;"
    )
    data = {}
    for table, query, _, _ in TABLES:
        data[table] = [convert(table, r) for r in access.cursor().execute(query).fetchall()]
    access_revenue = access.cursor().execute("SELECT SUM(Revenue) FROM [Order]").fetchval()
    access.close()

    ensure_database()
    with psycopg.connect(dbname=TARGET_DB) as conn:
        conn.execute((ROOT / "sql" / "01_schema.sql").read_text())
        for table, _, columns, identity in TABLES:
            stmt = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.SQL(", ").join(sql.Placeholder() * len(columns)),
            )
            with conn.cursor() as cur:
                cur.executemany(stmt, data[table])
            # Access IDs were inserted explicitly, so move each identity past them.
            conn.execute(
                sql.SQL("SELECT setval(pg_get_serial_sequence({}, {}), max({})) FROM {}").format(
                    sql.Literal(table), sql.Literal(identity), sql.Identifier(identity), sql.Identifier(table)
                )
            )
            print(f"{table}: {len(data[table])} rows")
        conn.execute((ROOT / "sql" / "02_analysis_views.sql").read_text())

        pg_revenue = conn.execute("SELECT sum(revenue) FROM orders").fetchone()[0]
        if pg_revenue != access_revenue:
            sys.exit(f"revenue mismatch: Access {access_revenue} vs Postgres {pg_revenue}")
        print(f"total revenue matches Access: {pg_revenue}")


if __name__ == "__main__":
    main()
