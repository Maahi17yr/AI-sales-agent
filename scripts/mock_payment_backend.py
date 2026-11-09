"""Mock Paystack only for disposable browser CI. Never run against production."""
import os
from urllib.parse import quote

if (os.environ.get("NAIJA_BITES_TEST_MOCK_PAYMENTS") != "1"
        or os.environ.get("NAIJA_BITES_ENV") != "development"
        or os.environ.get("DATABASE_URL")
        or not os.environ.get("NAIJA_BITES_DATA_DIR", "").startswith("/tmp/")):
    raise RuntimeError("Mock payment backend requires disposable local CI environment")

from core.services import paystack_service
from data.database.models import get_connection


def mocked_paystack_request(method, path, payload=None):
    if method == "POST" and path == "/transaction/initialize":
        reference = payload["reference"]
        return {
            "reference": reference,
            "authorization_url": "https://paystack-mock.test/return?reference=" + quote(reference),
        }
    if method == "GET" and path.startswith("/transaction/verify/"):
        reference = path.rsplit("/", 1)[-1]
        with get_connection() as conn:
            payment = conn.execute(
                "SELECT amount FROM payments WHERE payment_reference = ?", (reference,)
            ).fetchone()
        if payment is None:
            raise ValueError("Unknown fictional payment reference")
        return {"status": "success", "currency": "NGN",
                "reference": reference, "amount": payment["amount"] * 100}
    raise ValueError("Unsupported mocked Paystack request")


paystack_service._request = mocked_paystack_request

if __name__ == "__main__":
    import uvicorn
    from core.api.server import app
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
