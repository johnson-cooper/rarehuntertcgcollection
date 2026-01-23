import os, json
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from .models import CollectionCard, Order
import stripe
import time
from django.db import transaction
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST

def get_sell_price(card: CollectionCard) -> float:
    return card.effective_mid or card.value_mid or 0

stripe.api_key = settings.STRIPE_SECRET_KEY

def get_cart(request):
    return request.session.setdefault("cart", {})

def save_cart(request):
    request.session.modified = True

def about(request):
    return render(request, "collection/about.html")

def terms(request):
    return render(request, "collection/terms.html")

def privacy(request):
    return render(request, "collection/privacy.html")

def success(request):
    return render(request, "collection/success.html")

def cancel(request):
    return render(request, "collection/cancel.html")

def index(request):
    return render(request, 'collection/index.html', {
        'stripe_publishable_key': os.getenv('STRIPE_PUBLISHABLE_KEY', 'pk_test_...')
    })

def cart_status(request):
    cart = request.session.get('cart', {})
    cart_count = sum(cart.values())
    total = 0
    updated_cart = cart.copy()

    for card_id_str, qty in cart.items():
        try:
            card_id = int(card_id_str)
            card = CollectionCard.objects.get(id=card_id)

            available = card.quantity - card.reserved

            if available <= 0:
                # remove from cart if sold/reserved
                updated_cart.pop(card_id_str, None)
            else:
                # cap quantity to available stock
                if qty > available:
                    updated_cart[card_id_str] = available
                    qty = available
                total += get_sell_price(card) * qty
        except CollectionCard.DoesNotExist:
            updated_cart.pop(card_id_str, None)

    request.session['cart'] = updated_cart
    request.session.modified = True
    cart_count = sum(updated_cart.values())

    return JsonResponse({
        'cart': updated_cart,
        'cart_count': cart_count,
        'total': total
    })

@require_POST
def add_to_cart(request):
    data = json.loads(request.body)
    card_id = str(data.get("collection_card_id"))
    qty = int(data.get("quantity", 1))

    cart = get_cart(request)

    try:
        card = CollectionCard.objects.get(id=int(card_id))
        available = card.quantity - card.reserved
        if available <= 0:
            cart.pop(card_id, None)
            save_cart(request)
            return JsonResponse({"error": "Item sold out", "cart": cart, "cart_count": sum(cart.values())}, status=400)

        # cap to available stock
        new_qty = min(qty + cart.get(card_id, 0), available)
        cart[card_id] = new_qty
        save_cart(request)

        return JsonResponse({"cart": cart, "cart_count": sum(cart.values())})
    except CollectionCard.DoesNotExist:
        return JsonResponse({"error": "Card not found"}, status=404)

@require_POST
def remove_from_cart(request):
    data = json.loads(request.body)
    card_id = str(data["collection_card_id"])

    cart = get_cart(request)
    if card_id in cart:
        cart.pop(card_id)
        save_cart(request)

    return JsonResponse({"cart": cart, "cart_count": sum(cart.values())})




def cart_view(request):
    cart = request.session.get("cart", {})

    items = []
    total = 0

    collection_cards = (
        CollectionCard.objects
        .select_related("card")  # avoid extra queries
        .filter(id__in=cart.keys())
    )

    for cc in collection_cards:
        qty = cart[str(cc.id)]
        subtotal = get_sell_price(cc) * qty  # safer
        total += subtotal

        items.append({
            "collection_card": cc,
            "card": cc.card,          # convenience for template
            "quantity": qty,
            "subtotal": subtotal,
        })

    return render(request, "collection/cart.html", {
        "items": items,
        "total": total
    })

def api_products(request):
    qs = (
        CollectionCard.objects
        .select_related('card', 'card_set')
        .prefetch_related('images')
    )

    out = []

    for c in qs:
        # image
        img_url = ''
        img = c.images.first()
        if img and img.img:
            img_url = request.build_absolute_uri(img.img.url)

        price = get_sell_price(c)
        available = c.quantity - c.reserved

        out.append({
            'id': c.id,
            'name': c.card.name,
            'konami_id': getattr(c.card, 'konami_id', None),

            'set': {
                'name': c.card_set.name if c.card_set else None,
                'code': getattr(c.card_set, 'code', None),
            },

            # ⚠️ SAFE field access
            'edition': getattr(c, 'edition', None),
            'condition': getattr(c, 'condition', None),
            'misprint': getattr(c, 'misprint', None),
            'graded': bool(getattr(c, 'psa', None)),
            'psa_grade': getattr(c, 'psa', None),

            'price_cents': int(price * 100),
            'currency': 'USD',

            'quantity': c.quantity,
            'reserved': c.reserved,
            'available': available,

            'is_sold_out': available <= 0,
            'is_reserved': c.reserved > 0 and available > 0,

            'image': img_url,
        })

    return JsonResponse(out, safe=False)

def card_detail(request, card_id):
    c = CollectionCard.objects \
        .select_related('card', 'card_set') \
        .prefetch_related('images') \
        .get(id=card_id)

    img_url = ''
    img = c.images.first()
    if img and img.img:
        img_url = request.build_absolute_uri(img.img.url)

    price = get_sell_price(c)
    available = c.quantity - c.reserved

    product = {
        'id': c.id,
        'name': c.card.name,
        'konami_id': c.card.konami_id,
        'set': c.card_set,
        'edition': c.edition,
        'condition': c.condition,
        'misprint': c.misprint,
        'psa': c.psa,
        'price': price,
        'available': available,
        'is_sold_out': available <= 0,
        'image': img_url,
        'notes': c.notes,
    }

    return render(request, 'collection/card_detail.html', {
        'product': product
    })

@csrf_exempt
def create_cart_checkout_session(request):
    cart = get_cart(request)
    if not cart:
        return JsonResponse({"error": "Cart empty"}, status=400)

    line_items = []
    reserved_items = []

    with transaction.atomic():
        for card_id_str, qty in cart.items():
            card_id = int(card_id_str)
            c = CollectionCard.objects.select_for_update().get(id=card_id)

            available = c.quantity - c.reserved
            if available < qty:
                raise Exception(f"Not enough stock for {c.card.name}")

            c.reserved += qty
            c.save()

            price = get_sell_price(c)
            images = [request.build_absolute_uri(c.images.first().img.url)] if c.images.exists() else []

            line_items.append({
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": c.card.name,
                        "description": f"{c.edition} • {c.condition}",
                        "images": images
                    },
                    "unit_amount": int(price * 100)
                },
                "quantity": qty
            })

            reserved_items.append({
                "id": c.id,
                "qty": qty
            })

        # Set session to expire in 30 minutes
        expires_in_seconds = 30 * 60
        expires_at = int(time.time()) + expires_in_seconds

        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=line_items,
            shipping_address_collection={"allowed_countries": ["US"]},
            success_url=f"{settings.BASE_URL}/success/",
            cancel_url=f"{settings.BASE_URL}/cancel/",
            metadata={
                "items": json.dumps(reserved_items)
            },
            expires_at=expires_at  # <-- this makes the session auto-expire
        )

    return JsonResponse({"url": session.url})

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
            expires_in_seconds = 30 * 60
            expires_at = int(time.time()) + expires_in_seconds

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
                shipping_address_collection={"allowed_countries": ["US"],},
                success_url=f"{settings.BASE_URL}/success/",
                cancel_url=f"{settings.BASE_URL}/cancel/",
                expires_at=expires_at,  # <-- set expiration
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
            # --- finalize stock ---
            c = CollectionCard.objects.select_for_update().get(id=card_id)
            c.quantity -= reserved_qty
            c.reserved -= reserved_qty
            c.save()
            print(f"Payment completed: {reserved_qty} of {c.card.name} sold.")

            # --- create order ---
            items = stripe.checkout.Session.list_line_items(sess['id'], limit=100)
            shipping = sess.get('shipping') or {}
            customer_email = sess.get('customer_details', {}).get('email', '')

            Order.objects.create(
                stripe_order_id=sess['id'],
                email=customer_email,
                shipping_name=shipping.get('name', ''),
                shipping_address=shipping.get('address', {}),
                status='paid',
                items=[{'description': li.description, 'quantity': li.quantity} for li in items.data]
            )

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

