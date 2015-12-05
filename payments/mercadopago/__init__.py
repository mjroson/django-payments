from __future__ import unicode_literals

from urllib.error import URLError
from urllib.parse import urlencode

from django.http import HttpResponseRedirect

from .forms import MercadoPagoForm
from payments import BasicProvider, RedirectNeeded, PaymentError, get_base_url

import mercadopago
from owncommerce.settings.base import DEFAULT_CURRENCY
import json
from django.core.urlresolvers import reverse

from decimal import Decimal, ROUND_HALF_UP
CENTS = Decimal('0.01')
from django.shortcuts import redirect
from django.contrib import messages
from django.http import HttpResponseForbidden


class MercadoPagoProvider(BasicProvider):
    '''
    Mercado pago payment provider

    Glosary payment status
        - "pending" The user has not yet completed the payment process
        - "approved" The payment has been approved and accredited
        - "in_process" Payment is being reviewed
        - "in_mediation" Users have initiated a dispute
        - "rejected" Payment was rejected. The user may retry payment.
        - "cancelled" Payment was cancelled by one of the parties or because time for payment has expired
        - "refunded" Payment was refunded to the user
        - "charged_back" Was made a chargeback in the buyer’s credit card
    '''

    def __init__(self, client_id, client_secret, sandbox=True, **kwargs):
        self.secret = client_secret
        self.client_id = client_id
        self.mp = mercadopago.MP(client_id, client_secret)
        self.mp.sandbox_mode(sandbox)
        self.sandbox = sandbox
        self.endpoint = (
                'https://api.mercadopago.com/')
        super(MercadoPagoProvider, self).__init__(**kwargs)

    def get_access_token(self, payment):
        return self.mp.get_access_token()

    def get_form(self, payment, data=None):
        result = self.create_ceckout(payment)
        print(30*"====")
        print(result)
        if self.sandbox:
            url = result['response']['sandbox_init_point']
        else:
            url = result["response"]["init_point"]

        payment.change_status('input') # TODO: Warging this status is ¿¿¿input or waiting??
        payment.extra_data = str(result["response"])
        payment.transaction_id = result['response']['id']
        payment.save()
        raise RedirectNeeded(url)
        return MercadoPagoForm()

    def create_ceckout(self, payment, **kwargs):
        # Create a Checkout preference
        # sub_total = (
        #     payment.total - payment.delivery - payment.tax)
        # sub_total = sub_total.quantize(CENTS, rounding=ROUND_HALF_UP)
        # total = payment.total.quantize(CENTS, rounding=ROUND_HALF_UP)
        # tax = payment.tax.quantize(CENTS, rounding=ROUND_HALF_UP)
        # delivery = payment.delivery.quantize(
        #     CENTS, rounding=ROUND_HALF_UP)
        return_url = self.get_return_url(payment)
        preference = {
            "items": list(self.get_transactions_items(payment)),
             "back_urls": {
                "success": return_url ,
                "pending": return_url,
                "failure": return_url,
            }

        }
        preferenceResult = self.mp.create_preference(preference)
        return preferenceResult


    def get_transactions_items(self, payment):
        for purchased_item in payment.get_purchased_items():
            price = purchased_item.price.quantize(
                CENTS, rounding=ROUND_HALF_UP)
            item = {'title': purchased_item.name[:255],
                    'quantity': int(purchased_item.quantity),
                    'unit_price': float(price),
                    'currency_id': payment.currency,
                    'picture_url': get_base_url() + purchased_item.image,
                    'id': purchased_item.sku
                    }
            yield item

    def process_data(self, payment, request):
        success_url = payment.get_success_url()
        collection_status = request.GET.get('collection_status')

        if 'approved' in collection_status:
            payment.change_status('confirmed')
            messages.success(request,  "Se ha recibido el pago con exito")
        elif 'pending' in collection_status or 'in_process' in collection_status or 'in_mediation' in collection_status:
            messages.warning(request,  "Se ha recibido el pago con exito, su pago esta a la espera de confirmacion, le avisaremos a la brevedad")
        elif 'rejected' in collection_status:
            payment.change_status('rejected')
            messages.error(request,  "Su pago ha sido rechazado")
        else:
            messages.error(request, "Error al recibir el pago")

        return redirect(success_url)


    def update_checkout(self, payment, **kwargs):
        preference = {
                "items": [
                    {
                        "title": payment.description,
                        "quantity": 1,
                        "currency_id": payment.currency,
                        "unit_price": payment.total
                    }
                ]
            }

        preferenceResult = self.mp.update_preference(payment.transaction_id, preference)

        return preferenceResult


    def search_payment(self, **kwargs):
        filters = {
            "id": kwargs.get('id'),
            "site_id": kwargs.get('site_id'),
            "external_reference": kwargs.get('external_reference')
        }

        searchResult = self.mp.search_payment(filters)

        return searchResult


    def get_payment_data(self, payment, **kwargs):
        paymentInfo = self.mp.get_payment(payment.transaction_id)

        if paymentInfo["status"] == 200:
            return paymentInfo
        else:
            return None


    def cancel(self, payment, **kwargs):
        # Only for pending payment
        result = self.mp.cancel_payment(payment.transaction_id)
        return result


    def refund(self, payment, **kwargs):
        # Only for accredited payments
        result = self.mp.refund_payment(payment.transaction_id)
        return result