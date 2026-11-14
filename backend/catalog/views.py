from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import (
    AuditLog,
    Availability,
    PriceRule,
    Product,
    ProductVariant,
)
from catalog.serializers import (
    ProductActivitySerializer,
    ProductEditorSerializer,
    ProductListSerializer,
    ProductUpdateSerializer,
)
from utils.mixin import MerchantScopedMixin


class ProductPagination(PageNumberPagination):
    page_size = 25


class ProductListView(MerchantScopedMixin, generics.ListAPIView):
    serializer_class = ProductListSerializer
    pagination_class = ProductPagination

    def get_queryset(self):
        queryset = (
            Product.objects.filter(merchant_id=self.get_merchant_id())
            .select_related("category")
            .order_by("id")
        )

        search = self.request.query_params.get("search", "").strip()

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(sku__icontains=search)
            )

        return queryset


class ProductEditorView(MerchantScopedMixin, APIView):

    def get(self, request, pk):
        # Load everything the editor shows in a fixed number of queries,
        # however many variants the product has.
        variants = (
            ProductVariant.objects.select_related("inventory")
            .prefetch_related(
                Prefetch("price_rules", queryset=PriceRule.objects.order_by("id"))
            )
            .order_by("id")
        )

        product = get_object_or_404(
            Product.objects.select_related("category").prefetch_related(
                Prefetch("variants", queryset=variants),
                Prefetch("availability", queryset=Availability.objects.order_by("id")),
            ),
            pk=pk,
            merchant_id=self.get_merchant_id(),
        )

        return Response(ProductEditorSerializer(product).data)


class ProductDetailView(MerchantScopedMixin, generics.RetrieveUpdateAPIView):
    serializer_class = ProductUpdateSerializer
    http_method_names = [
        "get",
        "patch",
        "head",
        "options",
    ]

    def get_queryset(self):
        return Product.objects.filter(
            merchant_id=self.get_merchant_id()
        ).select_related("category")

    def perform_update(self, serializer):
        tracked_fields = (
            "name",
            "sku",
            "description",
            "status",
            "category_id",
            "image_url",
        )

        with transaction.atomic():
            product = serializer.instance

            # Lock the row before comparing old and new values.
            locked_product = Product.objects.select_for_update().get(pk=product.pk)

            before = {field: getattr(locked_product, field) for field in tracked_fields}

            # Use the locked instance for the actual update.
            serializer.instance = locked_product
            updated_product = serializer.save()

            changes = {}

            for field in tracked_fields:
                old_value = before[field]
                new_value = getattr(updated_product, field)

                if old_value != new_value:
                    changes[field] = {
                        "from": old_value,
                        "to": new_value,
                    }

            if changes:
                AuditLog.objects.create(
                    product=updated_product,
                    actor=self.request.user,
                    action="product.updated",
                    details={"changes": changes},
                )


class ProductActivityView(MerchantScopedMixin, generics.ListAPIView):
    serializer_class = ProductActivitySerializer
    pagination_class = ProductPagination

    def get_queryset(self):
        product = get_object_or_404(
            Product.objects.filter(merchant_id=self.get_merchant_id()),
            pk=self.kwargs["pk"],
        )

        return (
            AuditLog.objects.filter(product_id=product.pk)
            .select_related("actor")
            .order_by("-created_at", "-id")
        )
