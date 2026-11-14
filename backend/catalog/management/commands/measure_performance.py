import math
import statistics
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, reset_queries
from django.test import Client
from django.test.utils import CaptureQueriesContext

from catalog.models import Merchant, Product

MERCHANT_SLUG = "assessment-merchant-01"
USERNAME = "demo_operator"
STANDARD_SKU = "ASSESS-000005"
COMPLEX_SKU = "ASSESS-000001"
BROAD_SEARCH = "assessment"
NARROW_SEARCH = "000123"
PAGE_SIZE = 25

# Endpoints and tables whose queries are worth a query plan.
EXPLAIN_TABLES = ("catalog_product", "catalog_auditlog")
EXPLAIN_ENDPOINTS = (
    "Product list, first page",
    "Product list, last page",
    "Product search, many matches",
    "Product search, one match",
    "Product activity history",
)


class Command(BaseCommand):
    help = (
        "Report how much work the server does for each product screen. "
        "Read-only: it only issues GET requests."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--runs",
            type=int,
            default=30,
            help="Measured runs per scenario, after one warm-up run. Times "
            "are the median, so more runs give steadier figures.",
        )
        parser.add_argument(
            "--explain",
            action="store_true",
            help="Also print MySQL EXPLAIN ANALYZE for the list, search and "
            "activity queries.",
        )

    def handle(self, *args, **options):
        runs = options["runs"]

        if runs < 1:
            raise CommandError("--runs must be at least 1.")

        merchant = Merchant.objects.filter(slug=MERCHANT_SLUG).first()
        user = get_user_model().objects.filter(username=USERNAME).first()

        if merchant is None or user is None:
            raise CommandError(
                "Seeded merchant or demo operator not found. Run ./setup.sh first."
            )

        self.merchant_id = merchant.pk
        self.client = Client(SERVER_NAME="localhost")
        self.client.force_login(user)

        screens, endpoints = self.scenarios(merchant)
        screens = [self.measure(*scenario, runs) for scenario in screens]
        endpoints = [self.measure(*scenario, runs) for scenario in endpoints]

        self.print_screens(screens)
        self.print_endpoints(endpoints)

        if options["explain"]:
            self.print_plans(endpoints)

    def scenarios(self, merchant):
        products = Product.objects.filter(merchant=merchant)
        standard = products.get(sku=STANDARD_SKU).pk
        complex_ = products.get(sku=COMPLEX_SKU).pk
        last_page = math.ceil(products.count() / PAGE_SIZE)

        def single(path, **params):
            return lambda: [(path, params)]

        # Each screen is the list of API calls its Vue view makes.
        screens = [
            ("Product list page", single("")),
            ("Product list page, searching", single("", search=BROAD_SEARCH)),
            ("Product page, 2 variants", single(f"{standard}/editor/")),
            ("Product page, 50 variants", single(f"{complex_}/editor/")),
            (
                "Product activity page",
                lambda: [(f"{standard}/", {}), (f"{standard}/activity/", {})],
            ),
        ]
        endpoints = [
            ("Product list, first page", single("")),
            ("Product list, last page", single("", page=last_page)),
            ("Product search, many matches", single("", search=BROAD_SEARCH)),
            ("Product search, one match", single("", search=NARROW_SEARCH)),
            ("Product basic fields", single(f"{standard}/")),
            ("Full product, 2 variants", single(f"{standard}/editor/")),
            ("Full product, 50 variants", single(f"{complex_}/editor/")),
            ("Product activity history", single(f"{standard}/activity/")),
        ]

        return screens, endpoints

    def get(self, path, params):
        response = self.client.get(
            "/api/products/" + path,
            {"merchant_id": self.merchant_id, **params},
        )

        if response.status_code != 200:
            raise CommandError(
                f"GET /api/products/{path} returned {response.status_code}."
            )

        return response

    def measure(self, name, build_requests, runs):
        requests = build_requests()

        def run_once():
            # Django keeps only the last 9,000 logged queries, so start clean.
            reset_queries()
            sql_seconds = []

            def timed(execute, sql, params, many, context):
                # connection.queries rounds to the millisecond, which is too
                # coarse for these queries, so time each execution directly.
                started = time.perf_counter()
                try:
                    return execute(sql, params, many, context)
                finally:
                    sql_seconds.append(time.perf_counter() - started)

            started = time.perf_counter()
            with connection.execute_wrapper(timed):
                with CaptureQueriesContext(connection) as context:
                    size = sum(len(self.get(*request).content) for request in requests)
            server_ms = (time.perf_counter() - started) * 1000
            return context.captured_queries, server_ms, size, sum(sql_seconds) * 1000

        run_once()

        samples = [run_once() for _ in range(runs)]
        counts = {len(sample[0]) for sample in samples}

        if len(counts) != 1:
            raise CommandError(f"{name}: query count varied between runs: {counts}")

        queries, _, size, _ = samples[0]

        return {
            "scenario": name,
            "url": self.url(*requests[0]),
            "http_requests": len(requests),
            "db_queries": len(queries),
            "db_time_ms": round(statistics.median(s[3] for s in samples), 1),
            "server_time_ms": round(statistics.median(s[1] for s in samples), 1),
            "response_bytes": size,
            "sql": [query["sql"] for query in queries],
        }

    def print_screens(self, screens):
        headers = ["Screen", "HTTP requests", "DB queries", "Server time ms"]
        rows = [
            [
                result["scenario"],
                result["http_requests"],
                result["db_queries"],
                result["server_time_ms"],
            ]
            for result in screens
        ]

        self.stdout.write("\n1. Work the server does to show each screen")
        self.print_table(headers, rows)

    def print_endpoints(self, endpoints):
        headers = [
            "What the call returns",
            "API URL",
            "DB queries",
            "DB time ms",
            "Server time ms",
            "Response bytes",
        ]
        rows = [
            [
                result["scenario"],
                result["url"],
                result["db_queries"],
                result["db_time_ms"],
                result["server_time_ms"],
                result["response_bytes"],
            ]
            for result in endpoints
        ]

        self.stdout.write("\n2. One call to each API endpoint")
        self.print_table(headers, rows)

    def print_plans(self, endpoints):
        by_name = {result["scenario"]: result for result in endpoints}

        self.stdout.write("\n3. MySQL query plans")

        for name in EXPLAIN_ENDPOINTS:
            self.stdout.write(f"\n== {name} ==")

            for sql in dict.fromkeys(by_name[name]["sql"]):
                if not sql.startswith("SELECT") or not any(
                    f"FROM `{table}`" in sql for table in EXPLAIN_TABLES
                ):
                    continue

                with connection.cursor() as cursor:
                    cursor.execute("EXPLAIN ANALYZE " + sql)
                    plan = "\n".join(row[0] for row in cursor.fetchall())

                self.stdout.write(f"\n{sql}\n{plan}")

    def print_table(self, headers, rows):
        widths = [
            max(len(str(cell)) for cell in column) for column in zip(headers, *rows)
        ]
        border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"

        def line(cells):
            # Text is left-aligned, numbers are right-aligned.
            padded = [
                (
                    str(cell).rjust(width)
                    if isinstance(cell, (int, float))
                    else str(cell).ljust(width)
                )
                for cell, width in zip(cells, widths)
            ]
            return "| " + " | ".join(padded) + " |"

        self.stdout.write(border)
        self.stdout.write(line(headers))
        self.stdout.write(border)

        for row in rows:
            self.stdout.write(line(row))

        self.stdout.write(border)

    @staticmethod
    def url(path, params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return "/api/products/" + path + ("?" + query if query else "")
