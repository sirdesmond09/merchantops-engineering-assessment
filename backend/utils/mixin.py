from django.shortcuts import get_object_or_404
from rest_framework import permissions
from rest_framework.exceptions import ValidationError

from catalog.models import Merchant


class MerchantScopedMixin:
    """Base for views that work inside one merchant the user belongs to."""

    permission_classes = [permissions.IsAuthenticated]

    def get_merchant_id(self):
        if hasattr(self, "_merchant_id"):
            return self._merchant_id

        merchant_id = self.request.query_params.get("merchant_id")

        if not merchant_id:
            raise ValidationError({"merchant_id": "This parameter is required."})

        try:
            merchant_id = int(merchant_id)
        except (TypeError, ValueError):
            raise ValidationError({"merchant_id": "Must be a valid integer."})

        if merchant_id <= 0:
            raise ValidationError({"merchant_id": "Must be a positive integer."})

        # A merchant the user is not a member of is reported as not found.
        get_object_or_404(
            Merchant.objects.filter(members=self.request.user),
            pk=merchant_id,
        )

        self._merchant_id = merchant_id
        return merchant_id
