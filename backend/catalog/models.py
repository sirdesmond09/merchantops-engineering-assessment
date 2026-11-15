from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Merchant(models.Model):
    name = models.CharField(max_length=150)
    slug = models.SlugField(unique=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="merchant_accounts",
        blank=True,
    )

    def __str__(self):
        return self.name


class Category(models.Model):
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="categories"
    )
    name = models.CharField(max_length=120)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["merchant", "name"],
                name="unique_merchant_category",
            )
        ]

    def __str__(self):
        return self.name


class Product(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="products"
    )
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products"
    )
    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=64)
    image_url = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["merchant", "sku"],
                name="unique_merchant_product_sku",
            )
        ]

    def __str__(self):
        return self.name


class ProductVariant(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants"
    )
    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=120)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "sku"],
                name="unique_product_variant_sku",
            )
        ]

    def __str__(self):
        return self.sku


class PriceRule(models.Model):
    variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="price_rules"
    )
    tier = models.CharField(max_length=50)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["variant", "tier"],
                name="unique_variant_price_tier",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="price_rule_nonnegative",
            ),
        ]


class Inventory(models.Model):
    variant = models.OneToOneField(
        ProductVariant, on_delete=models.CASCADE, related_name="inventory"
    )
    quantity = models.PositiveIntegerField(default=0)
    reserved = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=models.F("reserved")),
                name="inventory_reservation_within_stock",
            ),
        ]


class Availability(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="availability"
    )
    channel = models.CharField(max_length=50)
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "channel"],
                name="unique_product_channel",
            ),
        ]


class AuditLog(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="activity"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=80)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class AdjustmentType(models.TextChoices):
    INCREASE = "increase", "Increase"
    DECREASE = "decrease", "Decrease"


class AdjustmentReason(models.TextChoices):
    PHYSICAL_COUNT = "physical_count", "Physical count"
    RETURNED_TO_STOCK = "returned_to_stock", "Returned to stock"
    FOUND_STOCK = "found_stock", "Found stock"
    DAMAGED = "damaged", "Damaged"
    LOST_OR_STOLEN = "lost_or_stolen", "Lost or stolen"
    EXPIRED = "expired", "Expired"
    OTHER = "other", "Other"


# The reasons an operator may give for each adjustment type.
ADJUSTMENT_REASONS = {
    AdjustmentType.INCREASE: (
        AdjustmentReason.PHYSICAL_COUNT,
        AdjustmentReason.RETURNED_TO_STOCK,
        AdjustmentReason.FOUND_STOCK,
        AdjustmentReason.OTHER,
    ),
    AdjustmentType.DECREASE: (
        AdjustmentReason.PHYSICAL_COUNT,
        AdjustmentReason.DAMAGED,
        AdjustmentReason.LOST_OR_STOLEN,
        AdjustmentReason.EXPIRED,
        AdjustmentReason.OTHER,
    ),
}


class InventoryAdjustment(models.Model):
    """A manual stock correction. Rows are written once and never changed."""

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="inventory_adjustments",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_adjustments",
    )
    # Kept so the record still names the operator if the account is deleted.
    actor_username = models.CharField(max_length=150)
    type = models.CharField(max_length=16, choices=AdjustmentType.choices)
    quantity = models.PositiveIntegerField()
    reason = models.CharField(max_length=32, choices=AdjustmentReason.choices)
    note = models.CharField(max_length=255, blank=True)
    quantity_before = models.PositiveIntegerField()
    quantity_after = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="inventory_adjustment_quantity_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        type=AdjustmentType.INCREASE,
                        quantity_after=models.F("quantity_before")
                        + models.F("quantity"),
                    )
                    | models.Q(
                        type=AdjustmentType.DECREASE,
                        quantity_before=models.F("quantity_after")
                        + models.F("quantity"),
                    )
                ),
                name="inventory_adjustment_quantities_match_type",
            ),
        ]
