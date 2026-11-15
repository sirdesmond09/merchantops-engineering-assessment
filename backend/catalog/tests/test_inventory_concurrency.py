import json
import threading

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TransactionTestCase
from django.urls import reverse

from catalog.models import (
    AuditLog,
    Category,
    Inventory,
    InventoryAdjustment,
    Merchant,
    Product,
    ProductVariant,
)


class InventoryAdjustmentConcurrencyTests(TransactionTestCase):
    """
    Send adjustments for one variant at the same moment.

    Each request runs in its own thread with its own database connection, and
    TransactionTestCase lets every request commit for real, so the requests
    compete for the stock row as they would in production.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="concurrent-operator",
            password="test-password",
        )
        self.merchant = Merchant.objects.create(
            name="Concurrent Merchant",
            slug="concurrent-merchant",
        )
        self.merchant.members.add(self.user)

        category = Category.objects.create(merchant=self.merchant, name="Stock")
        product = Product.objects.create(
            merchant=self.merchant,
            category=category,
            name="Contended product",
            sku="CONC-001",
        )
        self.variant = ProductVariant.objects.create(
            product=product,
            name="Variant 1",
            sku="CONC-001-V1",
        )
        self.url = (
            reverse(
                "inventory-adjustment",
                kwargs={"pk": product.pk, "variant_pk": self.variant.pk},
            )
            + f"?merchant_id={self.merchant.pk}"
        )

    def set_stock(self, quantity, reserved):
        Inventory.objects.create(
            variant=self.variant,
            quantity=quantity,
            reserved=reserved,
        )

    def adjust_together(self, bodies, keys=None):
        """Post every body at the same time and return the status codes."""
        start = threading.Barrier(len(bodies))
        statuses = [None] * len(bodies)
        # Every request gets its own key unless the test supplies them.
        keys = keys or [f"1794700000000-{index:016x}" for index in range(len(bodies))]

        def worker(index, body):
            try:
                client = Client()
                client.force_login(self.user)
                # Wait until every thread is signed in, then go together.
                start.wait(timeout=30)
                response = client.post(
                    self.url,
                    data=json.dumps(body),
                    content_type="application/json",
                    headers={"Idempotency-Key": keys[index]},
                )
                statuses[index] = response.status_code
            finally:
                # Each thread has its own connection and must close it.
                connection.close()

        threads = [
            threading.Thread(target=worker, args=(index, body))
            for index, body in enumerate(bodies)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=60)

        return statuses

    def assert_history_is_unbroken(self, starting_quantity):
        """Each adjustment must start from where the previous one ended."""
        expected = starting_quantity

        for adjustment in InventoryAdjustment.objects.order_by("id"):
            self.assertEqual(adjustment.quantity_before, expected)
            expected = adjustment.quantity_after

        self.assertEqual(Inventory.objects.get(variant=self.variant).quantity, expected)

    def test_simultaneous_adjustments_are_all_applied(self):
        self.set_stock(quantity=100, reserved=0)

        increases = [{"type": "increase", "quantity": 3, "reason": "found_stock"}] * 8
        decreases = [{"type": "decrease", "quantity": 2, "reason": "damaged"}] * 4

        statuses = self.adjust_together(increases + decreases)

        self.assertEqual(statuses, [201] * 12)

        # 100 + (8 x 3) - (4 x 2) = 116. A lost update would give less or more.
        self.assertEqual(Inventory.objects.get(variant=self.variant).quantity, 116)
        self.assertEqual(InventoryAdjustment.objects.count(), 12)
        self.assertEqual(AuditLog.objects.count(), 12)
        self.assert_history_is_unbroken(starting_quantity=100)

    def test_competing_decreases_never_go_below_reserved(self):
        # 10 in stock and 4 reserved, so only 6 can be removed: three lots of 2.
        self.set_stock(quantity=10, reserved=4)

        statuses = self.adjust_together(
            [{"type": "decrease", "quantity": 2, "reason": "damaged"}] * 5
        )

        self.assertEqual(sorted(statuses), [201, 201, 201, 400, 400])
        self.assertEqual(Inventory.objects.get(variant=self.variant).quantity, 4)
        self.assertEqual(InventoryAdjustment.objects.count(), 3)
        self.assertEqual(AuditLog.objects.count(), 3)
        self.assert_history_is_unbroken(starting_quantity=10)

    def test_simultaneous_copies_of_one_request_are_applied_once(self):
        self.set_stock(quantity=100, reserved=0)

        statuses = self.adjust_together(
            [{"type": "decrease", "quantity": 2, "reason": "damaged"}] * 5,
            keys=["1794700000000-00000000000000aa"] * 5,
        )

        # One request makes the adjustment and the other four get it back.
        self.assertEqual(sorted(statuses), [200, 200, 200, 200, 201])
        self.assertEqual(Inventory.objects.get(variant=self.variant).quantity, 98)
        self.assertEqual(InventoryAdjustment.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)
