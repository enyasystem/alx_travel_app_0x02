from django.urls import path
from listings import views as listing_views

urlpatterns = [
    path('listings/payment/initiate/', listing_views.initiate_payment, name='initiate_payment'),
    path('listings/payment/verify/', listing_views.verify_payment, name='verify_payment'),
]
