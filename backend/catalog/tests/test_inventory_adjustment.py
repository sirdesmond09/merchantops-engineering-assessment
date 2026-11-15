import itertools
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
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
from catalog.services import adjust_inventory

key_numbers = itertools.count(1)


def new_key():
    """A fresh key in the format the frontend sends."""
    return f"1794700000000-{next(key_numbers):016x}"


class InventoryAdjustmentTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()

        cls.user = User.objects.create_user(
            username="stock-operator",
            password="test-password",
        )
        cls.outsider = User.objects.create_user(
            username="other-operator",
            password="test-password",
        )

        cls.merchant = Merchant.objects.create(
            name="Stock Merchant",
            slug="stock-merchant",
        )
        cls.other_merchant = Merchant.objects.create(
            name="Other Stock Merchant",
            slug="other-stock-merchant",
        )
        cls.merchant.members.add(cls.user)
        cls.other_merchant.members.add(cls.outsider)

        cls.product = cls.create_product(cls.merchant, "STOCK-001")
        cls.second_product = cls.create_product(cls.merchant, "STOCK-002")
        cls.other_product = cls.create_product(cls.other_merchant, "STOCK-001")

        # 20 in stock, 3 reserved, so 17 can be removed.
        cls.variant = cls.create_variant(cls.product, quantity=20, reserved=3)
        cls.other_variant = cls.create_variant(
            cls.other_product, quantity=20, reserved=3
        )
        cls.variant_without_inventory = ProductVariant.objects.create(
            product=cls.product,
            name="No stock record",
            sku="STOCK-001-NONE",
        )

    @staticmethod
    def create_product(merchant, sku):
        category, _ = Category.objects.get_or_create(
            merchant=merchant,
            name="Stock",
        )
        return Product.objects.create(
            merchant=merchant,
            category=category,
            name=f"Product {sku}",
            sku=sku,
        )

    @staticmethod
    def create_variant(product, quantity, reserved):
        variant = ProductVariant.objects.create(
            product=product,
            name="Variant 1",
            sku=f"{product.sku}-V1",
        )
        Inventory.objects.create(
            variant=variant,
            quantity=quantity,
            reserved=reserved,
        )
        return variant

    def setUp(self):
        self.client.force_login(self.user)

    def adjust(
        self,
        body,
        product=None,
        variant=None,
        merchant=None,
        client=None,
        key=None,
    ):
        product = product or self.product
        variant = variant or self.variant
        merchant = merchant or self.merchant
        url = reverse(
            "inventory-adjustment",
            kwargs={"pk": product.pk, "variant_pk": variant.pk},
        )

        return (client or self.client).post(
            f"{url}?merchant_id={merchant.pk}",
            data=json.dumps(body),
            content_type="application/json",
            headers={"Idempotency-Key": key or new_key()},
        )

    def stock(self, variant=None):
        return Inventory.objects.get(variant=variant or self.variant).quantity

    def assert_nothing_changed(self):
        self.assertEqual(self.stock(), 20)
        self.assertEqual(InventoryAdjustment.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)


class InventoryAdjustmentSuccessTests(InventoryAdjustmentTestData):
    def test_increase_adds_stock_and_records_it(self):
        response = self.adjust(
            {"type": "increase", "quantity": 5, "reason": "found_stock"}
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.stock(), 25)

        adjustment = InventoryAdjustment.objects.get()

        self.assertEqual(adjustment.variant, self.variant)
        self.assertEqual(adjustment.actor, self.user)
        self.assertEqual(adjustment.actor_username, "stock-operator")
        self.assertEqual(adjustment.type, "increase")
        self.assertEqual(adjustment.quantity, 5)
        self.assertEqual(adjustment.reason, "found_stock")
        self.assertEqual(adjustment.quantity_before, 20)
        self.assertEqual(adjustment.quantity_after, 25)
        self.assertIsNotNone(adjustment.created_at)

    def test_decrease_removes_stock_and_records_it(self):
        response = self.adjust(
            {
                "type": "decrease",
                "quantity": 5,
                "reason": "damaged",
                "note": "Dropped in the warehouse",
            }
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.stock(), 15)

        adjustment = InventoryAdjustment.objects.get()

        self.assertEqual(adjustment.type, "decrease")
        self.assertEqual(adjustment.quantity, 5)
        self.assertEqual(adjustment.note, "Dropped in the warehouse")
        self.assertEqual(adjustment.quantity_before, 20)
        self.assertEqual(adjustment.quantity_after, 15)

    def test_response_describes_the_adjustment_and_new_stock(self):
        response = self.adjust({"type": "decrease", "quantity": 5, "reason": "expired"})
        adjustment = InventoryAdjustment.objects.get()

        self.assertEqual(response.data["id"], adjustment.pk)
        self.assertEqual(response.data["variant"], self.variant.pk)
        self.assertEqual(response.data["type"], "decrease")
        self.assertEqual(response.data["quantity"], 5)
        self.assertEqual(response.data["reason"], "expired")
        self.assertEqual(response.data["quantity_before"], 20)
        self.assertEqual(response.data["quantity_after"], 15)
        self.assertEqual(response.data["actor"], "stock-operator")
        self.assertEqual(
            response.data["inventory"],
            {
                "id": self.variant.inventory.pk,
                "quantity": 15,
                "reserved": 3,
            },
        )

    def test_adjustment_appears_in_product_activity(self):
        self.adjust({"type": "decrease", "quantity": 5, "reason": "damaged"})

        entry = AuditLog.objects.get()
        adjustment = InventoryAdjustment.objects.get()

        self.assertEqual(entry.product, self.product)
        self.assertEqual(entry.actor, self.user)
        self.assertEqual(entry.action, "inventory.adjusted")
        self.assertEqual(
            entry.details,
            {
                "adjustment_id": adjustment.pk,
                "variant_id": self.variant.pk,
                "variant_sku": "STOCK-001-V1",
                "type": "decrease",
                "quantity": 5,
                "reason": "damaged",
                "note": "",
                "changes": {"quantity": {"from": 20, "to": 15}},
            },
        )

    def test_stock_can_be_reduced_exactly_to_reserved(self):
        response = self.adjust(
            {"type": "decrease", "quantity": 17, "reason": "physical_count"}
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.stock(), 3)

    def test_adjustments_follow_one_another(self):
        self.adjust({"type": "increase", "quantity": 10, "reason": "found_stock"})
        self.adjust({"type": "decrease", "quantity": 4, "reason": "damaged"})

        first, second = InventoryAdjustment.objects.order_by("id")

        self.assertEqual((first.quantity_before, first.quantity_after), (20, 30))
        self.assertEqual((second.quantity_before, second.quantity_after), (30, 26))
        self.assertEqual(self.stock(), 26)

    def test_record_keeps_operator_name_after_account_is_deleted(self):
        self.adjust({"type": "increase", "quantity": 5, "reason": "found_stock"})

        self.user.delete()

        adjustment = InventoryAdjustment.objects.get()

        self.assertIsNone(adjustment.actor)
        self.assertEqual(adjustment.actor_username, "stock-operator")


class InventoryAdjustmentInvalidRequestTests(InventoryAdjustmentTestData):
    def assert_rejected(self, body, field):
        response = self.adjust(body)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(field, response.data)
        self.assert_nothing_changed()

    def test_quantity_must_be_a_whole_number_of_one_or_more(self):
        for quantity in (0, -5, 1.5, "five", "", None, True):
            with self.subTest(quantity=quantity):
                self.assert_rejected(
                    {"type": "increase", "quantity": quantity, "reason": "found_stock"},
                    "quantity",
                )

    def test_quantity_is_required(self):
        self.assert_rejected({"type": "increase", "reason": "found_stock"}, "quantity")

    def test_type_must_be_increase_or_decrease(self):
        for body in (
            {"quantity": 5, "reason": "found_stock"},
            {"type": "set", "quantity": 5, "reason": "found_stock"},
            {"type": "", "quantity": 5, "reason": "found_stock"},
        ):
            with self.subTest(body=body):
                self.assert_rejected(body, "type")

    def test_reason_is_required_and_must_be_known(self):
        for body in (
            {"type": "increase", "quantity": 5},
            {"type": "increase", "quantity": 5, "reason": ""},
            {"type": "increase", "quantity": 5, "reason": "because"},
        ):
            with self.subTest(body=body):
                self.assert_rejected(body, "reason")

    def test_reason_must_belong_to_the_type(self):
        self.assert_rejected(
            {"type": "increase", "quantity": 5, "reason": "damaged"},
            "reason",
        )
        self.assert_rejected(
            {"type": "decrease", "quantity": 5, "reason": "found_stock"},
            "reason",
        )

    def test_other_reason_needs_a_note(self):
        self.assert_rejected(
            {"type": "increase", "quantity": 5, "reason": "other"},
            "note",
        )
        self.assert_rejected(
            {"type": "increase", "quantity": 5, "reason": "other", "note": "   "},
            "note",
        )

    def test_other_reason_with_a_note_is_accepted(self):
        response = self.adjust(
            {
                "type": "increase",
                "quantity": 5,
                "reason": "other",
                "note": "Supplier sent extra units",
            }
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.stock(), 25)

    def test_stock_cannot_go_below_reserved(self):
        response = self.adjust(
            {"type": "decrease", "quantity": 18, "reason": "damaged"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["quantity"],
            "Stock cannot go below the reserved quantity " "(reserved 3, in stock 20).",
        )
        self.assert_nothing_changed()

    def test_variant_without_inventory_record_is_rejected(self):
        response = self.adjust(
            {"type": "increase", "quantity": 5, "reason": "found_stock"},
            variant=self.variant_without_inventory,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("variant", response.data)
        self.assertEqual(InventoryAdjustment.objects.count(), 0)


class InventoryAdjustmentAuthorizationTests(InventoryAdjustmentTestData):
    body = {"type": "decrease", "quantity": 5, "reason": "damaged"}

    def assert_other_merchant_untouched(self):
        self.assertEqual(self.stock(self.other_variant), 20)
        self.assert_nothing_changed()

    def test_cannot_adjust_another_merchants_variant(self):
        response = self.adjust(
            self.body,
            product=self.other_product,
            variant=self.other_variant,
        )

        self.assertEqual(response.status_code, 404)
        self.assert_other_merchant_untouched()

    def test_cannot_reach_another_merchants_variant_through_own_product(self):
        response = self.adjust(self.body, variant=self.other_variant)

        self.assertEqual(response.status_code, 404)
        self.assert_other_merchant_untouched()

    def test_cannot_use_a_merchant_the_user_does_not_belong_to(self):
        response = self.adjust(
            self.body,
            product=self.other_product,
            variant=self.other_variant,
            merchant=self.other_merchant,
        )

        self.assertEqual(response.status_code, 404)
        self.assert_other_merchant_untouched()

    def test_variant_must_belong_to_the_product_in_the_url(self):
        response = self.adjust(self.body, product=self.second_product)

        self.assertEqual(response.status_code, 404)
        self.assert_nothing_changed()

    def test_merchant_id_is_required(self):
        url = reverse(
            "inventory-adjustment",
            kwargs={"pk": self.product.pk, "variant_pk": self.variant.pk},
        )

        response = self.client.post(
            url,
            data=json.dumps(self.body),
            content_type="application/json",
            headers={"Idempotency-Key": new_key()},
        )

        self.assertEqual(response.status_code, 400)
        self.assert_nothing_changed()

    def test_unauthenticated_user_is_rejected(self):
        self.client.logout()

        response = self.adjust(self.body)

        self.assertIn(response.status_code, (401, 403))
        self.assert_nothing_changed()

    def test_csrf_token_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = self.adjust(self.body, client=client)

        self.assertEqual(response.status_code, 403)
        self.assert_nothing_changed()

    def test_only_post_is_allowed(self):
        url = reverse(
            "inventory-adjustment",
            kwargs={"pk": self.product.pk, "variant_pk": self.variant.pk},
        )

        response = self.client.get(f"{url}?merchant_id={self.merchant.pk}")

        self.assertEqual(response.status_code, 405)


class InventoryAdjustmentRepeatedRequestTests(InventoryAdjustmentTestData):
    body = {"type": "decrease", "quantity": 5, "reason": "damaged"}

    def test_repeated_request_is_applied_once(self):
        key = new_key()

        first = self.adjust(self.body, key=key)
        second = self.adjust(self.body, key=key)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["id"], first.data["id"])
        self.assertEqual(second.data["quantity_before"], 20)
        self.assertEqual(second.data["quantity_after"], 15)
        self.assertEqual(second.data["inventory"]["quantity"], 15)

        self.assertEqual(self.stock(), 15)
        self.assertEqual(InventoryAdjustment.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_new_key_makes_a_new_adjustment(self):
        self.adjust(self.body)
        self.adjust(self.body)

        self.assertEqual(self.stock(), 10)
        self.assertEqual(InventoryAdjustment.objects.count(), 2)

    def assert_conflict(self, response):
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stock(), 15)
        self.assertEqual(InventoryAdjustment.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_key_cannot_be_reused_with_different_values(self):
        key = new_key()
        self.adjust(self.body, key=key)

        for body in (
            {"type": "decrease", "quantity": 6, "reason": "damaged"},
            {"type": "decrease", "quantity": 5, "reason": "expired"},
            {"type": "increase", "quantity": 5, "reason": "found_stock"},
            {"type": "decrease", "quantity": 5, "reason": "damaged", "note": "x"},
        ):
            with self.subTest(body=body):
                self.assert_conflict(self.adjust(body, key=key))

    def test_key_cannot_be_reused_for_another_variant(self):
        second_variant = self.create_variant(
            self.second_product, quantity=20, reserved=3
        )
        key = new_key()
        self.adjust(self.body, key=key)

        response = self.adjust(
            self.body,
            product=self.second_product,
            variant=second_variant,
            key=key,
        )

        self.assert_conflict(response)
        self.assertEqual(self.stock(second_variant), 20)

    def test_key_cannot_be_reused_by_another_operator(self):
        colleague = get_user_model().objects.create_user(
            username="second-operator",
            password="test-password",
        )
        self.merchant.members.add(colleague)
        key = new_key()
        self.adjust(self.body, key=key)

        client = Client()
        client.force_login(colleague)
        response = self.adjust(self.body, client=client, key=key)

        self.assert_conflict(response)
        self.assertNotIn("id", response.data)

    def test_key_is_required_and_must_be_well_formed(self):
        url = reverse(
            "inventory-adjustment",
            kwargs={"pk": self.product.pk, "variant_pk": self.variant.pk},
        )

        for headers in (
            {},
            {"Idempotency-Key": ""},
            {"Idempotency-Key": "not-a-key"},
            {"Idempotency-Key": "1794700000000"},
            {"Idempotency-Key": "1794700000000-XYZ"},
        ):
            with self.subTest(headers=headers):
                response = self.client.post(
                    f"{url}?merchant_id={self.merchant.pk}",
                    data=json.dumps(self.body),
                    content_type="application/json",
                    headers=headers,
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn("idempotency_key", response.data)
                self.assert_nothing_changed()


class InventoryAdjustmentAtomicityTests(InventoryAdjustmentTestData):
    def adjust_with_failure(self, target):
        with mock.patch(target, side_effect=RuntimeError("Simulated failure")):
            with self.assertRaises(RuntimeError):
                adjust_inventory(
                    variant=self.variant,
                    actor=self.user,
                    adjustment_type="decrease",
                    quantity=5,
                    reason="damaged",
                    idempotency_key=new_key(),
                )

    def test_stock_is_unchanged_when_the_adjustment_record_fails(self):
        self.adjust_with_failure("catalog.services.InventoryAdjustment.objects.create")

        self.assert_nothing_changed()

    def test_stock_and_record_are_undone_when_the_activity_entry_fails(self):
        self.adjust_with_failure("catalog.services.AuditLog.objects.create")

        self.assert_nothing_changed()


class InventoryAdjustmentConstraintTests(InventoryAdjustmentTestData):
    def create(self, **overrides):
        values = {
            "variant": self.variant,
            "actor": self.user,
            "actor_username": "stock-operator",
            "type": "decrease",
            "quantity": 5,
            "reason": "damaged",
            "quantity_before": 20,
            "quantity_after": 15,
            **overrides,
        }

        with transaction.atomic():
            return InventoryAdjustment.objects.create(**values)

    def test_valid_increase_and_decrease_are_accepted(self):
        self.create()
        self.create(type="increase", quantity_before=15, quantity_after=20)

        self.assertEqual(InventoryAdjustment.objects.count(), 2)

    def test_zero_quantity_is_rejected(self):
        with self.assertRaises(IntegrityError):
            self.create(quantity=0, quantity_after=20)

    def test_after_must_match_before_and_quantity(self):
        with self.assertRaises(IntegrityError):
            self.create(quantity_after=16)

    def test_direction_must_match_type(self):
        with self.assertRaises(IntegrityError):
            self.create(type="increase")
