from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from .models import CollectionCard
from django.views.decorators.http import require_GET

@require_GET
def card_status(request, card_id):
    card = get_object_or_404(CollectionCard, id=card_id)

    available = card.quantity - card.reserved

    return JsonResponse({
        "id": card.id,
        "is_sold_out": available <= 0,
        "is_reserved": card.reserved > 0 and available > 0,
        "quantity": card.quantity,
        "reserved": card.reserved,
        "available": available,
    })
