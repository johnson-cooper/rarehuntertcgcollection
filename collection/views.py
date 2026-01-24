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
    updated_cart = {}

    items = []
    total = 0

    collection_cards = (
        CollectionCard.objects
        .select_related("card")
        .filter(id__in=cart.keys())
    )

    for cc in collection_cards:
        cart_qty = int(cart.get(str(cc.id), 0))
        available = max(cc.quantity - cc.reserved, 0)

        # ❌ Item no longer available → drop it
        if available <= 0 or cart_qty <= 0:
            continue

        # ✅ Clamp quantity to availability
        qty = min(cart_qty, available)

        price = get_sell_price(cc)
        subtotal = price * qty

        total += subtotal

        items.append({
            "collection_card": cc,
            "card": cc.card,
            "quantity": qty,
            "subtotal": subtotal,
        })

        updated_cart[str(cc.id)] = qty

    # 🔒 Never allow negative totals
    total = max(total, 0)

    # 🧹 Persist cleaned cart
    request.session["cart"] = updated_cart
    request.session.modified = True

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

    try:
        with transaction.atomic():
            for card_id_str, qty in cart.items():
                card_id = int(card_id_str)

                c = (
                    CollectionCard.objects
                    .select_for_update()
                    .get(id=card_id)
                )

                available = c.quantity - c.reserved
                if available < qty:
                    raise ValueError(
                        f"Not enough stock for {c.card.name}"
                    )

                c.reserved += qty
                c.save()

                price = get_sell_price(c)
                images = (
                    [request.build_absolute_uri(c.images.first().img.url)]
                    if c.images.exists() else []
                )

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

            expires_at = int(time.time()) + (30 * 60)

            session = stripe.checkout.Session.create(
                mode="payment",
                payment_method_types=["card"],
                line_items=line_items,
                shipping_address_collection={"allowed_countries": ["US"]},
                success_url=f"{settings.BASE_URL}/success/",
                cancel_url=f"{settings.BASE_URL}/cancel/",
                metadata={
                    "source": "rarehunter_cart",
                    "items": json.dumps(reserved_items)
                },
                expires_at=expires_at
            )

    except ValueError as e:
        # Inventory conflict
        return JsonResponse(
            {"error": str(e)},
            status=409
        )

    except Exception:
        # Unexpected failure
        return JsonResponse(
            {"error": "Checkout failed"},
            status=500
        )

    # Clear cart only AFTER session succeeds
    request.session["cart"] = {}
    request.session.modified = True

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
                    "source": "rarehunter_cart",
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

    # --- Verify webhook ---
    try:
        if webhook_secret:
            event = stripe.Webhook.construct_event(
                payload, sig_header, webhook_secret
            )
        else:
            event = json.loads(payload)
    except Exception:
        return HttpResponse(status=400)

    event_type = event.get("type")
    sess = event["data"]["object"]
    metadata = sess.get("metadata", {})

    # --- helper to parse reserved items ---
    def get_reserved_items():
        """
        Returns a list of dicts:
        [{ "id": int, "qty": int }, ...]
        """
        # Cart checkout
        if "items" in metadata:
            return json.loads(metadata["items"])

        # Single-item checkout (legacy)
        if "collection_card_id" in metadata and "reserved_qty" in metadata:
            return [{
                "id": int(metadata["collection_card_id"]),
                "qty": int(metadata["reserved_qty"])
            }]

        return []

    reserved_items = get_reserved_items()

    # --- PAYMENT COMPLETED ---
    if event_type == "checkout.session.completed":
        with transaction.atomic():
            for item in reserved_items:
                c = CollectionCard.objects.select_for_update().get(id=item["id"])
                qty = item["qty"]

                c.quantity -= qty
                c.reserved -= qty
                c.save()

                print(f"Sold {qty} of {c.card.name}")

            # Create order (once per session)
            items = stripe.checkout.Session.list_line_items(sess["id"], limit=100)
            shipping = sess.get("shipping") or {}
            customer_email = sess.get("customer_details", {}).get("email", "")

            Order.objects.create(
                stripe_order_id=sess["id"],
                email=customer_email,
                shipping_name=shipping.get("name", ""),
                shipping_address=shipping.get("address", {}),
                status="paid",
                items=[
                    {"description": li.description, "quantity": li.quantity}
                    for li in items.data
                ]
            )

    # --- PAYMENT FAILED OR SESSION EXPIRED ---
    elif event_type in (
        "checkout.session.expired",
        "payment_intent.payment_failed",
    ):
        with transaction.atomic():
            for item in reserved_items:
                c = CollectionCard.objects.select_for_update().get(id=item["id"])
                c.reserved -= item["qty"]
                c.save()

                print(f"Released {item['qty']} of {c.card.name}")

    return HttpResponse(status=200)
