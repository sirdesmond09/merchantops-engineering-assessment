import re

from rest_framework import serializers

from catalog.models import (
    AuditLog,
    Availability,
    Category,
    Inventory,
    PriceRule,
    Product,
    ProductVariant,
)


class ProductListSerializer(serializers.ModelSerializer):
    category = serializers.CharField(
        source="category.name",
        read_only=True,
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "sku",
            "status",
            "category",
            "image_url",
        ]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name"]


class PriceRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceRule
        fields = ["id", "tier", "amount"]


class InventorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Inventory
        fields = ["id", "quantity", "reserved"]


class ProductVariantSerializer(serializers.ModelSerializer):
    prices = PriceRuleSerializer(source="price_rules", many=True)
    inventory = InventorySerializer(allow_null=True)

    class Meta:
        model = ProductVariant
        fields = ["id", "name", "sku", "prices", "inventory"]


class AvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Availability
        fields = ["id", "channel", "enabled"]


class ProductEditorSerializer(serializers.ModelSerializer):
    category = CategorySerializer()
    variants = ProductVariantSerializer(many=True)
    availability = AvailabilitySerializer(many=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "sku",
            "description",
            "status",
            "image_url",
            "category",
            "variants",
            "availability",
        ]


class ProductUpdateSerializer(serializers.ModelSerializer):
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "sku",
            "description",
            "status",
            "category",
            "image_url",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        allowed = set(self.fields)
        unexpected = set(self.initial_data) - allowed

        # Read-only fields must not be submitted either.
        unexpected |= set(self.initial_data) & {"id"}

        if unexpected:
            raise serializers.ValidationError(
                {
                    field: "This field cannot be modified."
                    for field in sorted(unexpected)
                }
            )

        return attrs

    def validate_sku(self, value):
        product = self.instance

        duplicate = Product.objects.filter(
            merchant_id=product.merchant_id,
            sku=value,
        ).exclude(pk=product.pk).exists()

        if duplicate:
            raise serializers.ValidationError(
                "A product with this SKU already exists " "for this merchant."
            )

        return value

    def validate_category(self, category):
        if category.merchant_id != self.instance.merchant_id:
            raise serializers.ValidationError(
                "Category does not belong to this merchant."
            )

        return category

    def validate_image_url(self, value):
        if value and not re.fullmatch(
            r"/product-images/[a-z0-9][a-z0-9_-]*\.webp",
            value,
        ):
            raise serializers.ValidationError(
                "Expected a local /product-images/*.webp path."
            )

        return value


class ProductActivitySerializer(serializers.ModelSerializer):
    actor = serializers.CharField(
        source="actor.username",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor",
            "action",
            "details",
            "created_at",
        ]
