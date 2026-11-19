"""One-shot authenticated Owner AI -> REAL Bedrock smoke with fictional SQLite data.

This is *not* a unit test and must never target Render or Neon databases.
Only execute on an ephemeral GitHub Actions runner with approved staging secrets.
"""
import asyncio
import os
import re

# Existing OwnerAITests creates and cleans its own disposable SQLite database.
os.environ["NAIJA_BITES_ENV"] = "development"
os.environ["DATABASE_URL"] = ""

from scripts.test_owner_assistant import (
    EMAIL_CUSTOMER, EMAIL_OWNER, OwnerAITests,
)
from core.services import owner_assistant_service as assistant
from data.database import models


async def run() -> None:
    if os.getenv("DATABASE_URL"):
        raise AssertionError("Refusing to run against an external database")
    if not os.getenv("BEDROCK_MODEL_ID") or not os.getenv("AWS_ACCESS_KEY_ID"):
        raise RuntimeError("Live Bedrock test secrets are unavailable")

    case = OwnerAITests("test_requires_verified_owner_and_never_sends_private_customer_data")
    await case.asyncSetUp()
    try:
        request = {"message": "From the verified data only, what is total paid revenue? State the amount in NGN.", "history": []}
        unauthenticated = await case.client.post("/owner/assistant", json=request)
        assert unauthenticated.status_code == 401, "Anonymous access should be denied"
        await case.login(EMAIL_CUSTOMER)
        customer = await case.client.post("/owner/assistant", json=request)
        assert customer.status_code == 403, "Non-owner access should be denied"
        await case.login(EMAIL_OWNER)

        snapshot = assistant.build_snapshot()
        assert snapshot["metrics"]["paid_revenue"] == 3500
        assert snapshot["menu_and_stock"][0]["variants"]
        snapshot_string = str(snapshot)
        assert EMAIL_CUSTOMER not in snapshot_string
        assert "Private Fictional Address" not in snapshot_string

        with models.get_connection() as conn:
            before = {
                "price": conn.execute("SELECT price FROM products WHERE id=1").fetchone()[0],
                "stock": conn.execute("SELECT quantity FROM inventory WHERE product_id=1").fetchone()[0],
                "orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                "payments": conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
            }

        # Do not patch the model: this invokes the actual app endpoint and Strands -> AWS Bedrock.
        response = await asyncio.wait_for(case.client.post("/owner/assistant", json=request), timeout=150)
        if response.status_code != 200:
            raise AssertionError(f"Owner AI Bedrock request did not succeed (HTTP {response.status_code})")
        payload = response.json()
        assert payload.get("read_only") is True
        reply = payload.get("reply", "")
        assert isinstance(reply, str) and reply.strip()
        assert "3500" in re.sub(r"[,\s]", "", reply), "Model response did not accurately report verified paid revenue"

        with models.get_connection() as conn:
            after = {
                "price": conn.execute("SELECT price FROM products WHERE id=1").fetchone()[0],
                "stock": conn.execute("SELECT quantity FROM inventory WHERE product_id=1").fetchone()[0],
                "orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                "payments": conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
            }
        assert after == before, "Business records changed during a read-only question"
        print("OWNER_AI_REAL_BEDROCK_PASS: authenticated owner, fictional DB, verified NGN 3500, no business mutations")
    finally:
        await case.asyncTearDown()


if __name__ == "__main__":
    asyncio.run(run())
