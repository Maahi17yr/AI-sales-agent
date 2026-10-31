"""Owner dashboard security, reporting, and mutation checks on disposable SQLite."""
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

PASSWORD = "Fictional-Password-1234"
OWNER_EMAIL = "owner@example.test"
CUSTOMER_EMAIL = "customer@example.test"


class OwnerDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(models, "DB_PATH", Path(self.temp.name) / "dashboard.db")
        self.db_patch.start()
        models.create_tables()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 54321)),
            base_url="http://127.0.0.1:8000",
        )
        for email in (OWNER_EMAIL, CUSTOMER_EMAIL):
            registered = await self.client.post("/auth/register", json={
                "name": email.split("@")[0], "email": email, "password": PASSWORD,
            })
            self.assertEqual(registered.status_code, 201, registered.text)
        promote_registered_customer(OWNER_EMAIL)
        with models.get_connection() as conn:
            conn.execute("""INSERT INTO products(name, category, price, available)
                            VALUES('Jollof', 'Rice', 3500, 1)""")
            conn.execute("INSERT INTO inventory(product_id, variant, quantity) VALUES(1, 'standard', 3)")
            conn.execute("""INSERT INTO delivery_zones(area, fee, estimated_min_hours, estimated_max_hours)
                            VALUES('Lekki', 1000, 1, 2)""")
            customer = conn.execute("SELECT id FROM customers WHERE email=?", (CUSTOMER_EMAIL,)).fetchone()
            conn.execute("""INSERT INTO orders(customer_id, delivery_zone_id, delivery_address,
                            subtotal, delivery_fee, discount, total, status)
                            VALUES(?, 1, 'Test address', 3500, 1000, 0, 4500, 'confirmed')""", (customer["id"],))
            conn.execute("INSERT INTO order_items(order_id, product_id, quantity, unit_price) VALUES(1,1,1,3500)")
            conn.execute("""INSERT INTO payments(order_id, amount, status, paid_at)
                            VALUES(1,4500,'paid', CURRENT_TIMESTAMP)""")
            conn.commit()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db_patch.stop()
        self.temp.cleanup()

    async def login(self, email):
        return await self.client.post("/auth/login", json={"email": email, "password": PASSWORD})

    async def test_owner_endpoints_denied_to_anonymous_and_customers(self):
        paths = ("/owner/overview", "/owner/orders", "/owner/menu", "/owner/customers")
        for path in paths:
            self.assertEqual((await self.client.get(path)).status_code, 401)
        await self.login(CUSTOMER_EMAIL)
        for path in paths:
            self.assertEqual((await self.client.get(path)).status_code, 403)
        self.assertEqual((await self.client.put("/owner/menu/1", json={"price": 1})).status_code, 403)
        self.assertEqual((await self.client.put("/owner/orders/1/status", json={"status": "delivered"})).status_code, 403)

    async def test_owner_reports_and_management(self):
        await self.login(OWNER_EMAIL)
        overview = (await self.client.get("/owner/overview")).json()
        self.assertEqual(overview["paid_revenue"], 4500)
        self.assertEqual(overview["popular_meals"][0]["name"], "Jollof")
        self.assertEqual(overview["low_stock"][0]["quantity"], 3)
        self.assertEqual(len((await self.client.get("/owner/orders")).json()["orders"]), 1)
        self.assertEqual(len((await self.client.get("/owner/customers")).json()["customers"]), 1)
        self.assertEqual((await self.client.get("/owner/menu")).json()["products"][0]["price"], 3500)

        illegal = await self.client.put("/owner/orders/1/status", json={"status": "delivered"})
        self.assertEqual(illegal.status_code, 409)
        advanced = await self.client.put("/owner/orders/1/status", json={"status": "preparing"})
        self.assertEqual(advanced.status_code, 200, advanced.text)
        changed = await self.client.put("/owner/menu/1", json={"price": 4200, "available": False})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual((await self.client.get("/owner/menu")).json()["products"][0]["price"], 4200)
        self.assertEqual((await self.client.get("/menu")).json()["products"], [])  # Owner changes reach the storefront.
        with models.get_connection() as conn:
            logs = conn.execute("SELECT action FROM owner_audit_log").fetchall()
            self.assertEqual(len(logs), 3)  # Promotion, order change, menu change.

    async def test_unpaid_order_cannot_advance(self):
        await self.login(OWNER_EMAIL)
        with models.get_connection() as conn:
            conn.execute("UPDATE payments SET status = 'unpaid' WHERE order_id = 1")
            conn.commit()
        response = await self.client.put("/owner/orders/1/status", json={"status": "preparing"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual((await self.client.get("/owner/overview")).json()["paid_revenue"], 0)


if __name__ == "__main__":
    unittest.main()
