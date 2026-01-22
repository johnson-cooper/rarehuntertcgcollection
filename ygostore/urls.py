from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from collection import views as coll_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', coll_views.index, name='home'),
    path('api/', include('collection.urls')),
    path('webhook/', coll_views.stripe_webhook, name='stripe-webhook'),
     # Static pages
    path('about/', coll_views.about, name='about'),
    path('terms/', coll_views.terms, name='terms'),
    path('privacy/', coll_views.privacy, name='privacy'),
    path('success/', coll_views.success, name='success'),
    path('cancel/', coll_views.cancel, name='cancel'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
