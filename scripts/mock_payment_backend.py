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
    import asyncio
    import json
    import uvicorn
    from fastapi.responses import StreamingResponse
    from starlette.routing import Route
    from core.api.server import app

    async def benchmark_agent(request):
        # This route is available only in this strictly guarded, disposable
        # CI mock server. Pace small deltas to reproduce a real SSE stream
        # rather than delivering one giant browser-reassembled response.
        payload = await request.json()
        user_messages = [m.get("content", "") for m in payload.get("messages", [])
                         if m.get("role") == "user" and isinstance(m, dict)]
        if not user_messages or not str(user_messages[-1]).startswith("benchmark "):
            return StreamingResponse(iter([
                'data: {"type":"RUN_ERROR","message":"Mock agent only supports benchmarks."}\\n\\n'
            ]), media_type="text/event-stream")

        async def frames():
            for _ in range(700):
                yield 'data: ' + json.dumps({
                    "type": "TEXT_MESSAGE_CONTENT", "delta": "bite "
                }, separators=(",", ":")) + "\n\n"
                await asyncio.sleep(0.002)
            yield 'data: {"type":"RUN_FINISHED"}\n\n'
        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    app.router.routes.insert(0, Route("/agent", benchmark_agent, methods=["POST"]))
    if os.environ.get("NAIJA_BITES_TEST_HTTPS") == "1":
        # Strictly disposable, short-lived self-signed CA for the real Next
        # production build; never weaken HTTPS validation to run the test.
        uvicorn.run(app, host="127.0.0.1", port=8443, log_level="warning",
                    ssl_keyfile="/tmp/naija-proxy-test.key",
                    ssl_certfile="/tmp/naija-proxy-test.crt")
    else:
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
