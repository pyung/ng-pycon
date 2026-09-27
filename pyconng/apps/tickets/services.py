import hashlib
import hmac
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class PaystackService:
    """Wrapper around the Paystack API."""

    def __init__(self):
        self.base_url = getattr(settings, "PAYSTACK_BASE_URL", "https://api.paystack.co")
        self.secret_key = settings.PAYSTACK_SECRET_KEY
        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    def verify_transaction(self, reference):
        """
        Verify a transaction with Paystack.

        Args:
            reference: The Paystack transaction reference to verify.

        Returns:
            dict with 'amount_paid' (in naira) if successful, None otherwise.
        """
        url = f"{self.base_url}/transaction/verify/{reference}"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            data = response.json()

            if data.get("status") and data.get("data", {}).get("status") == "success":
                amount_paid = data["data"]["amount"] / 100  # Paystack returns amount in kobo
                return {
                    "amount_paid": amount_paid,
                    "reference": data["data"]["reference"],
                    "currency": data["data"].get("currency", "NGN"),
                    "channel": data["data"].get("channel", ""),
                    "paid_at": data["data"].get("paid_at", ""),
                }

            logger.warning("Paystack verification failed for ref %s: %s", reference, data)
            return None

        except requests.RequestException as e:
            logger.error("Paystack API error verifying ref %s: %s", reference, e)
            return None

    def initialize_transaction(self, email, amount, reference, callback_url=None):
        """
        Initialize a transaction on Paystack (server-side).

        Args:
            email: Customer email
            amount: Amount in naira (will be converted to kobo)
            reference: Unique transaction reference
            callback_url: URL to redirect to after payment

        Returns:
            dict with 'authorization_url' and 'access_code' if successful, None otherwise.
        """
        url = f"{self.base_url}/transaction/initialize"
        payload = {
            "email": email,
            "amount": int(amount * 100),  # Convert naira to kobo
            "reference": reference,
        }
        if callback_url:
            payload["callback_url"] = callback_url

        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            data = response.json()

            if data.get("status"):
                return {
                    "authorization_url": data["data"]["authorization_url"],
                    "access_code": data["data"]["access_code"],
                    "reference": data["data"]["reference"],
                }

            logger.warning("Paystack initialization failed: %s", data)
            return None

        except requests.RequestException as e:
            logger.error("Paystack API error during initialization: %s", e)
            return None

    def refund_transaction(self, reference, amount=None):
        """
        Ask Paystack to reverse a charge.

        ``amount`` is in naira and may be less than the original, for a partial
        refund; omitting it refunds the whole charge. Returns a dict with
        ``reference`` on success and None otherwise, matching the other methods
        here -- the caller records nothing until it has a reference, because a
        record saying the money went back when it did not is worse than no record.
        """
        url = f"{self.base_url}/refund"
        payload = {"transaction": reference}
        if amount is not None:
            payload["amount"] = int(float(amount) * 100)  # naira to kobo

        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            data = response.json()

            if data.get("status"):
                refund = data.get("data", {})
                return {
                    "reference": str(
                        refund.get("id") or refund.get("transaction", {}).get("reference") or reference
                    ),
                    "status": refund.get("status", ""),
                    "amount": (refund.get("amount") or 0) / 100,
                }

            logger.warning("Paystack refund refused for %s: %s", reference, data)
            return None

        except requests.RequestException as exc:
            logger.error("Paystack API error refunding %s: %s", reference, exc)
            return None

    @staticmethod
    def verify_webhook_signature(request):
        """
        Verify the HMAC signature of a Paystack webhook request.

        Args:
            request: Django HttpRequest object.

        Returns:
            True if the signature is valid, False otherwise.
        """
        secret_key = settings.PAYSTACK_SECRET_KEY
        signature = request.headers.get("X-Paystack-Signature", "")

        if not signature:
            return False

        computed = hmac.new(
            secret_key.encode("utf-8"),
            request.body,
            hashlib.sha512,
        ).hexdigest()

        return hmac.compare_digest(computed, signature)
