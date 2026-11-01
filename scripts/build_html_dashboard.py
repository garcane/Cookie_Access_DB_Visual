# /// script
# requires-python = ">=3.11"
# dependencies = ["psycopg[binary]"]
# ///
"""Build the HTML dashboard from the cookie_creations Postgres database.

Usage (from the repo root):
    uv run --native-tls scripts/build_html_dashboard.py

Queries the analysis.* views (plus a few rollups), embeds the results as JSON in
dashboard/template.html and writes dashboard/index.html. The page is a snapshot:
re-run this script after the data changes.

Connection settings come from the standard libpq environment (PGHOST, PGPORT,
PGUSER, PGPASSWORD) and pgpass.conf; defaults are localhost / postgres.
"""

import datetime as dt
import json
import os
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "dashboard" / "template.html"
OUTPUT = ROOT / "dashboard" / "index.html"
TARGET_REVENUE = 70000  # same target as the Power BI gauge

os.environ.setdefault("PGHOST", "localhost")
os.environ.setdefault("PGUSER", "postgres")

QUERIES = {
    "kpis": """
        SELECT count(*)                                         AS orders,
               sum(revenue)                                     AS revenue,
               sum(cookies_ordered)                             AS cookies,
               avg(revenue)                                     AS avg_order_value,
               count(*) FILTER (WHERE NOT order_filled)         AS unfilled_orders,
               coalesce(sum(revenue) FILTER (WHERE NOT order_filled), 0) AS unfilled_revenue,
               count(DISTINCT customer_id)                      AS active_customers,
               (SELECT count(*) FROM customers)                 AS customers,
               min(order_date)                                  AS first_order,
               max(order_date)                                  AS last_order
        FROM orders""",
    "monthly": """
        SELECT date_trunc('month', order_date)::date            AS month,
               count(*)                                         AS orders,
               sum(revenue)                                     AS revenue,
               sum(cookies_ordered)                             AS cookies,
               count(*) FILTER (WHERE NOT order_filled)         AS unfilled_orders,
               max(order_date)                                  AS last_order
        FROM orders
        GROUP BY 1
        ORDER BY 1""",
    "products": """
        SELECT s.product_id, s.cookie_name, s.order_count, s.cookies_sold, s.total_revenue,
               p.price AS list_price,
               count(o.order_id) FILTER (WHERE NOT o.order_filled) AS unfilled_orders
        FROM analysis.total_sales_by_product s
        JOIN products p USING (product_id)
        LEFT JOIN orders o USING (product_id)
        GROUP BY s.product_id, s.cookie_name, s.order_count, s.cookies_sold, s.total_revenue, p.price
        ORDER BY s.total_revenue DESC""",
    "revenue_by_customer": """
        SELECT customer_id, first_name || ' ' || last_name AS customer, order_count, cookies_ordered, total_revenue
        FROM analysis.revenue_by_customer""",
    "customers_by_orders": """
        SELECT order_rank, customer_id, first_name || ' ' || last_name AS customer, order_count, cookies_ordered
        FROM analysis.customers_by_order_count""",
    "unfilled": """
        SELECT order_id, order_date, first_name || ' ' || last_name AS customer, cookie_name, cookies_ordered, revenue
        FROM analysis.unfilled_orders""",
    "latest_month": """
        SELECT order_id, order_date, first_name || ' ' || last_name AS customer, city, cookie_name,
               cookies_ordered, revenue, order_filled
        FROM analysis.orders_last_month""",
    # Same day-range in the previous month, so a partial latest month is compared like for like.
    "latest_vs_prior": """
        WITH bounds AS (
            SELECT date_trunc('month', max(order_date))::date AS start, max(order_date) AS last FROM orders
        )
        SELECT b.start, b.last,
               (b.start - interval '1 month')::date AS prior_start,
               (b.last  - interval '1 month')::date AS prior_last,
               count(*) FILTER (WHERE o.order_date BETWEEN b.start AND b.last)                 AS orders,
               coalesce(sum(o.revenue) FILTER (WHERE o.order_date BETWEEN b.start AND b.last), 0) AS revenue,
               coalesce(sum(o.cookies_ordered) FILTER (WHERE o.order_date BETWEEN b.start AND b.last), 0) AS cookies,
               count(*) FILTER (WHERE o.order_date BETWEEN b.start - interval '1 month' AND b.last - interval '1 month') AS prior_orders,
               coalesce(sum(o.revenue) FILTER (WHERE o.order_date BETWEEN b.start - interval '1 month' AND b.last - interval '1 month'), 0) AS prior_revenue,
               coalesce(sum(o.cookies_ordered) FILTER (WHERE o.order_date BETWEEN b.start - interval '1 month' AND b.last - interval '1 month'), 0) AS prior_cookies
        FROM orders o CROSS JOIN bounds b
        GROUP BY b.start, b.last""",
}


def to_json(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value)}")


def main():
    with psycopg.connect(dbname=os.environ.get("COOKIE_DB", "cookie_creations"), row_factory=dict_row) as conn:
        data = {name: conn.execute(query).fetchall() for name, query in QUERIES.items()}
    data["kpis"] = data["kpis"][0]
    data["latest_vs_prior"] = data["latest_vs_prior"][0]
    data["target_revenue"] = TARGET_REVENUE
    data["generated"] = dt.date.today().isoformat()

    payload = json.dumps(data, default=to_json).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
