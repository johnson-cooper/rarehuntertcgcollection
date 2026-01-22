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
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
