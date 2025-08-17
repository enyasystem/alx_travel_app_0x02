import os
import json
import requests
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404
from django.conf import settings
from .models import Booking, Payment

CHAPA_INIT_URL = "https://api.chapa.co/v1/transaction/initialize"
CHAPA_VERIFY_URL = "https://api.chapa.co/v1/transaction/verify/"  # append transaction_id

@csrf_exempt
@require_POST
def initiate_payment(request):
    """
    Expected JSON body: {"booking_id": <int>, "amount": <decimal>, "first_name": "...", "last_name": "...", "email": "..."}
    Returns JSON with payment_url and transaction id.
    """
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    booking_id = payload.get('booking_id')
    amount = payload.get('amount')
    first_name = payload.get('first_name', '')
    last_name = payload.get('last_name', '')
    email = payload.get('email')

    if not booking_id or not amount or not email:
        return HttpResponseBadRequest("booking_id, amount and email are required")

    booking = get_object_or_404(Booking, pk=booking_id)

    # Create a Payment record with status pending
    payment = Payment.objects.create(booking=booking, amount=amount)

    secret = os.environ.get('CHAPA_SECRET_KEY') or getattr(settings, 'CHAPA_SECRET_KEY', None)
    if not secret:
        payment.chapa_response = 'Missing CHAPA_SECRET_KEY'
        payment.mark_failed()
        payment.save()
        return JsonResponse({'error': 'Chapa secret key not configured'}, status=500)

    headers = {
        'Authorization': f'Bearer {secret}',
        'Content-Type': 'application/json'
    }

    callback_url = payload.get('callback_url') or payload.get('redirect_url') or getattr(settings, 'CHAPA_CALLBACK_URL', 'http://localhost:8000/listings/payment/verify/')

    chapa_payload = {
        'amount': str(amount),
        'currency': payment.currency,
        'email': email,
        'first_name': first_name,
        'last_name': last_name,
        'callback_url': callback_url,
        'tx_ref': f"booking_{booking.pk}_pay_{payment.pk}",
    }

    try:
        resp = requests.post(CHAPA_INIT_URL, headers=headers, json=chapa_payload, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        payment.chapa_response = str(e)
        payment.mark_failed()
        payment.save()
        return JsonResponse({'error': 'Failed to initiate payment', 'details': str(e)}, status=502)

    data = resp.json()
    # Expected structure: {"status": "success", "data": {"payment_url": "...", "reference": "...", "tx_ref": "..."}}
    payment.chapa_response = json.dumps(data)
    if data.get('status') == 'success' and data.get('data'):
        chapa_data = data['data']
        payment.transaction_id = chapa_data.get('reference') or chapa_data.get('tx_ref')
        payment.status = Payment.STATUS_PENDING
        payment.save()
        return JsonResponse({'payment_url': chapa_data.get('payment_url'), 'transaction_id': payment.transaction_id})

    payment.mark_failed()
    payment.save()
    return JsonResponse({'error': 'Chapa initialization failed', 'details': data}, status=400)


@csrf_exempt
@require_GET
def verify_payment(request):
    """
    Verify payment status with Chapa.
    Query params: ?transaction_id=<id> or ?tx_ref=<tx_ref>
    """
    tx_id = request.GET.get('transaction_id') or request.GET.get('reference') or request.GET.get('tx_ref')
    if not tx_id:
        return HttpResponseBadRequest('transaction_id or tx_ref is required')

    secret = os.environ.get('CHAPA_SECRET_KEY') or getattr(settings, 'CHAPA_SECRET_KEY', None)
    if not secret:
        return JsonResponse({'error': 'Chapa secret not configured'}, status=500)

    headers = {
        'Authorization': f'Bearer {secret}'
    }

    try:
        # Try verify endpoint
        resp = requests.get(CHAPA_VERIFY_URL + tx_id, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        return JsonResponse({'error': 'Failed to verify payment', 'details': str(e)}, status=502)

    data = resp.json()

    # Find payment by transaction_id or tx_ref
    payment = Payment.objects.filter(transaction_id=tx_id).first()
    if not payment:
        payment = Payment.objects.filter(chapa_response__icontains=tx_id).first()

    # Interpret Chapa response (common fields)
    # Expected: data['status'] == 'success' and data['data']['status'] == 'success' or 'failed'
    if data.get('status') == 'success':
        chapa_status = data.get('data', {}).get('status')
        if chapa_status in ('success', 'completed'):
            if payment:
                payment.mark_completed()
                payment.chapa_response = json.dumps(data)
                payment.save()
                # Trigger confirmation email (background)
                try:
                    from .tasks import send_payment_confirmation
                    send_payment_confirmation.delay(payment.pk)
                except Exception:
                    # Celery not configured; ignore
                    pass
            return JsonResponse({'status': 'completed', 'data': data})
        else:
            if payment:
                payment.mark_failed()
                payment.chapa_response = json.dumps(data)
                payment.save()
            return JsonResponse({'status': 'failed', 'data': data})

    return JsonResponse({'error': 'Unexpected response from Chapa', 'data': data}, status=400)
