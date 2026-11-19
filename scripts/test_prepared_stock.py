"""Isolated prepared-food inventory tests: owner-only, audit, safe capacity.

The new stock tables intentionally do not affect the legacy checkout path yet.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["NAIJA_BITES_ENV"] = "development"
os.environ["DATABASE_URL"] = ""

import httpx
from core.api.server import app
from core.services.owner_access_service import promote_registered_customer
from data.database import models


class PreparedStockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(models, "DB_PATH", Path(self.temp.name) / "prepared.db")
        self.patch.start()
        models.create_tables()
        models.create_tables()  # Idempotent additive migration.
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1:8000",
        )
        for email in ("stock.owner@example.test", "stock.customer@example.test"):
            response = await self.client.post("/auth/register", json={
                "name": "Fictional User", "email": email,
                "password": "Fictional-password-for-tests-2026",
            })
            self.assertEqual(response.status_code, 201, response.text)
        promote_registered_customer("stock.owner@example.test")
        with models.get_connection() as conn:
            conn.execute("""INSERT INTO products(name, category, price, available)
                            VALUES('Jollof Rice', 'Meals', 1500, 1)""")
            conn.execute("""INSERT INTO inventory(product_id, variant, quantity)
                            VALUES(1, 'standard', 20)""")
            conn.commit()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.patch.stop()
        self.temp.cleanup()

    async def login(self, role):
        email = f"stock.{role}@example.test"
        response = await self.client.post("/auth/login", json={
            "email": email, "password": "Fictional-password-for-tests-2026",
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def config(self, **overrides):
        payload = dict(product_id=1, variant="standard", unit="g",
                       serving_units=250, tolerance_units=10,
                       extra_units=125, extra_tolerance_units=10, extra_price=700)
        return {**payload, **overrides}

    async def configure(self, **overrides):
        return await self.client.post("/owner/prepared-stock/config",
                                      json=self.config(**overrides))

    async def adjust(self, action, quantity, reason="Physical kitchen stock check"):
        return await self.client.post("/owner/prepared-stock/adjust", json={
            "product_id": 1, "variant": "standard",
            "action": action, "quantity_units": quantity, "reason": reason,
        })

    async def test_owner_is_only_account_that_can_change_or_see_stock(self):
        self.assertEqual((await self.client.get("/owner/prepared-stock")).status_code, 401)
        self.assertEqual((await self.configure()).status_code, 401)
        await self.login("customer")
        self.assertEqual((await self.client.get("/owner/prepared-stock")).status_code, 403)
        self.assertEqual((await self.client.get("/owner/prepared-stock/history")).status_code, 403)
        self.assertEqual((await self.configure()).status_code, 403)
        self.assertEqual((await self.adjust("add", 5000)).status_code, 403)
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM prepared_stock").fetchone()[0], 0)
        await self.login("owner")
        self.assertEqual((await self.client.get("/owner/prepared-stock")).status_code, 200)

    async def test_configure_add_remove_reconcile_and_audit(self):
        await self.login("owner")
        response = await self.configure()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["checkout_enabled"])
        self.assertEqual(response.json()["safe_standard_servings"], 0)

        add = await self.adjust("add", 5000, "Cooked 5 kg of jollof")
        self.assertEqual(add.status_code, 200, add.text)
        self.assertEqual(add.json()["safe_standard_servings"], 19)
        self.assertEqual(add.json()["safe_extra_servings"], 37)
        self.assertEqual(add.json()["quantity_units"], 5000)

        removal = await self.adjust("remove", 300, "Spoiled or discarded rice")
        self.assertEqual(removal.status_code, 200, removal.text)
        self.assertEqual(removal.json()["quantity_units"], 4700)

        correction = await self.adjust("set", 4500, "Reconciled with physical stock")
        self.assertEqual(correction.status_code, 200, correction.text)
        self.assertEqual(correction.json()["safe_standard_servings"], 17)

        history = await self.client.get("/owner/prepared-stock/history")
        self.assertEqual(history.status_code, 200, history.text)
        moves = history.json()["movements"]
        self.assertEqual(len(moves), 3)
        self.assertEqual([m["action"] for m in moves], ["set", "remove", "add"])
        self.assertEqual([m["change_units"] for m in moves], [-200, -300, 5000])
        self.assertTrue(all(m["actor_name"] for m in moves))

        # Do NOT silently apply physical stock entries to the legacy checkout
        # stock. The cutover is a separate, end-to-end tested release gate.
        with models.get_connection() as conn:
            existing = conn.execute(
                "SELECT quantity FROM inventory WHERE product_id = 1 AND variant = 'standard'"
            ).fetchone()
            self.assertEqual(existing["quantity"], 20)
            logs = conn.execute(
                "SELECT COUNT(*) AS total FROM owner_audit_log"
            ).fetchone()["total"]
            self.assertEqual(logs, 5)  # One role promotion + config + three movements.

    async def test_invalid_movements_never_mutate_stock(self):
        await self.login("owner")
        self.assertEqual((await self.configure()).status_code, 200)
        self.assertEqual((await self.adjust("add", 1000)).status_code, 200)
        self.assertEqual((await self.adjust("remove", 1001)).status_code, 409)
        self.assertEqual((await self.adjust("add", 0)).status_code, 409)
        self.assertEqual((await self.adjust("set", -1)).status_code, 422)
        self.assertEqual((await self.adjust("set", 0, "x")).status_code, 422)
        self.assertEqual((await self.adjust("stolen", 50)).status_code, 409)
        items = (await self.client.get("/owner/prepared-stock")).json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["quantity_units"], 1000)
        moves = (await self.client.get("/owner/prepared-stock/history")).json()["movements"]
        self.assertEqual(len(moves), 1)

    async def test_configuration_rules_and_unit_change_guard(self):
        await self.login("owner")
        self.assertEqual((await self.configure(tolerance_units=250)).status_code, 400)
        self.assertEqual((await self.configure(product_id=999)).status_code, 404)
        self.assertEqual((await self.configure(unit="banana")).status_code, 400)
        self.assertEqual((await self.configure(unit="piece", serving_units=250)).status_code, 400)
        self.assertEqual((await self.configure()).status_code, 200)
        self.assertEqual((await self.adjust("add", 5000)).status_code, 200)
        cannot_change = await self.configure(unit="ml")
        self.assertEqual(cannot_change.status_code, 400)
        self.assertEqual((await self.adjust("set", 0)).status_code, 200)
        can_change = await self.configure(unit="ml")
        self.assertEqual(can_change.status_code, 200, can_change.text)
        self.assertEqual(can_change.json()["unit"], "ml")


if __name__ == "__main__":
    unittest.main()
