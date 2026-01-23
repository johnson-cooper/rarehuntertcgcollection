from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from collection import views as coll_views
from collection import api_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', coll_views.index, name='home'),
    path('api/', include('collection.urls')),
    path('webhook/', coll_views.stripe_webhook, name='stripe-webhook'),
    path('api/card-status/<int:card_id>/', api_views.card_status),
    path("cart/add/", coll_views.add_to_cart),
    path("cart/remove/", coll_views.remove_from_cart),
    path("checkout/cart/", coll_views.create_cart_checkout_session),
     # Static pages
    path('about/', coll_views.about, name='about'),
    path('terms/', coll_views.terms, name='terms'),
    path('privacy/', coll_views.privacy, name='privacy'),
    path('success/', coll_views.success, name='success'),
    path('cancel/', coll_views.cancel, name='cancel'),
    path('view-cart/', coll_views.cart_view, name='view-cart'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
