from django.db import transaction
from rest_framework.exceptions import ValidationError

from catalog.models import AdjustmentType, AuditLog, Inventory, InventoryAdjustment

# Largest value Django accepts for a PositiveIntegerField.
MAX_QUANTITY = 2147483647


def adjust_inventory(*, variant, actor, adjustment_type, quantity, reason, note=""):
    """
    Increase or decrease one variant's stock and record who did it and why.

    The stock change and both audit records are written in one transaction,
    so they are all saved or none are.
    """
    with transaction.atomic():
        # Lock the stock row until the transaction ends. A second adjustment
        # to the same variant waits here, then reads the committed quantity.
        inventory = (
            Inventory.objects.select_for_update().filter(variant=variant).first()
        )

        if inventory is None:
            raise ValidationError(
                {"variant": "This variant has no inventory record to adjust."}
            )

        before = inventory.quantity

        if adjustment_type == AdjustmentType.INCREASE:
            after = before + quantity
        else:
            after = before - quantity

        if after < inventory.reserved:
            raise ValidationError(
                {
                    "quantity": "Stock cannot go below the reserved quantity "
                    f"(reserved {inventory.reserved}, in stock {before})."
                }
            )

        if after > MAX_QUANTITY:
            raise ValidationError(
                {"quantity": f"Stock cannot be more than {MAX_QUANTITY}."}
            )

        inventory.quantity = after
        inventory.save(update_fields=["quantity"])

        adjustment = InventoryAdjustment.objects.create(
            variant=variant,
            actor=actor,
            actor_username=actor.get_username(),
            type=adjustment_type,
            quantity=quantity,
            reason=reason,
            note=note,
            quantity_before=before,
            quantity_after=after,
        )

        # Also shown on the product's activity page.
        AuditLog.objects.create(
            product_id=variant.product_id,
            actor=actor,
            action="inventory.adjusted",
            details={
                "adjustment_id": adjustment.pk,
                "variant_id": variant.pk,
                "variant_sku": variant.sku,
                "type": adjustment_type,
                "quantity": quantity,
                "reason": reason,
                "note": note,
                "changes": {"quantity": {"from": before, "to": after}},
            },
        )

    return adjustment, inventory
