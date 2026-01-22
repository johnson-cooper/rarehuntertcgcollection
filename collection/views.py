import os, json
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from .models import CollectionCard
import stripe
from django.db import transaction

def get_sell_price(card: CollectionCard) -> float:
    return card.effective_mid or card.value_mid or 0

stripe.api_key = settings.STRIPE_SECRET_KEY

def index(request):
    return render(request, 'collection/index.html', {
        'stripe_publishable_key': os.getenv('STRIPE_PUBLISHABLE_KEY', 'pk_test_...')
    })

def api_products(request):
    qs = CollectionCard.objects.select_related('card', 'card_set').prefetch_related('images')
    out = []

    for c in qs:
        # Get first image if exists
        img_url = ''
        if c.images.exists():
            img = c.images.first()
            if img.img:  # this is ImageFieldFile
                img_url = request.build_absolute_uri(img.img.url)  # convert to full URL

        
        price = get_sell_price(c)
        out.append({
            'id': c.id,
            'name': c.card.name,
            'price_cents': int(price * 100),
            'currency': 'USD',
            'available_qty': c.quantity,
            'image': img_url  # must be string, not ImageFieldFile
        })

    return JsonResponse(out, safe=False)

@csrf_exempt
def create_checkout_session(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=400)

    try:
        data = json.loads(request.body)
        collection_card_id = int(data.get('collection_card_id'))
        qty = int(data.get('quantity', 1))
    except Exception:
        return JsonResponse({'error': 'invalid payload'}, status=400)

    try:
        with transaction.atomic():
            # Lock the row
            c = CollectionCard.objects.select_for_update().get(id=collection_card_id)

            # Check available stock
            available = c.quantity - c.reserved
            if available < qty:
                return JsonResponse({'error': 'Not enough stock'}, status=400)

            # Reserve stock
            c.reserved += qty
            c.save()

            # Stripe session
            price = get_sell_price(c)
            images = [request.build_absolute_uri(c.images.first().img.url)] if c.images.exists() else []

            description = f"Set: {c.card_set}, Edition: {c.edition}, Condition: {c.condition}, "
            description += f"PSA: {c.psa or 'N/A'}, Notes: {c.notes or 'None'}, "
            if c.misprint:
                description += f"Misprint: {c.misprint}"

            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': c.card.name,
                            'description': description,
                            'images': images
                        },
                        'unit_amount': int(price * 100)
                    },
                    'quantity': qty
                }],
                mode='payment',
                success_url=f"{settings.BASE_URL}/success/",
                cancel_url=f"{settings.BASE_URL}/cancel/",
                metadata={
                    'collection_card_id': str(c.id),
                    'reserved_qty': str(qty),
                    'konami_id': str(c.card.konami_id),
                    'edition': c.edition,
                    'condition': c.condition,
                    'set_code': c.card_set.code if c.card_set else '',
                    'effective_mid': str(price),
                    'misprint': c.misprint or ''
                }
            )
    except CollectionCard.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'url': session.url})

@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET

    # Verify webhook
    if webhook_secret:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except (ValueError, stripe.error.SignatureVerificationError):
            return HttpResponse(status=400)
    else:
        try:
            event = json.loads(payload)
        except Exception:
            return HttpResponse(status=400)

    # Handle events
    if event.get('type') == 'checkout.session.completed':
        sess = event['data']['object']
        card_id = int(sess['metadata']['collection_card_id'])
        reserved_qty = int(sess['metadata']['reserved_qty'])

        with transaction.atomic():
            c = CollectionCard.objects.select_for_update().get(id=card_id)
            c.quantity -= reserved_qty  # finalize stock
            c.reserved -= reserved_qty
            c.save()
            print(f"Payment completed: {reserved_qty} of {c.card.name} sold.")

    elif event.get('type') in ['checkout.session.expired', 'payment_intent.payment_failed']:
        sess = event['data']['object']
        card_id = int(sess['metadata']['collection_card_id'])
        reserved_qty = int(sess['metadata']['reserved_qty'])

        with transaction.atomic():
            c = CollectionCard.objects.select_for_update().get(id=card_id)
            c.reserved -= reserved_qty  # release reserved stock
            c.save()
            print(f"Payment failed or expired: {reserved_qty} of {c.card.name} released.")

    return HttpResponse(status=200)

