from django.urls import path
from . import views

urlpatterns = [
    path('products/', views.api_products, name='api-products'),
    path('create-checkout-session/', views.create_checkout_session, name='create-checkout-session'),
    path('card/<int:card_id>/', views.card_detail, name='card-detail'),
     # Static / info pages
    path('about/', views.about, name='about'),
    path('terms/', views.terms, name='terms'),
    path('privacy/', views.privacy, name='privacy'),
    path('success/', views.success, name='success'),
    path('cancel/', views.cancel, name='cancel'),
    
]
