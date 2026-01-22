from django.urls import path
from . import views

urlpatterns = [
    path('products/', views.api_products, name='api-products'),
    path('create-checkout-session/', views.create_checkout_session, name='create-checkout-session'),
    path('card/<int:card_id>/', views.card_detail, name='card-detail'),
    
]
