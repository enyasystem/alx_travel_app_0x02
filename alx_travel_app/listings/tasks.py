from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
from .models import Payment, Booking

@shared_task
def send_payment_confirmation(payment_id):
    try:
        payment = Payment.objects.get(pk=payment_id)
        booking = payment.booking
        recipient = booking.guest_email if booking and booking.guest_email else None
        if not recipient:
            return False
        subject = 'Payment Confirmation'
        message = f"Your payment for booking #{booking.pk} was successful. Amount: {payment.amount} {payment.currency}."
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [recipient])
        return True
    except Payment.DoesNotExist:
        return False
