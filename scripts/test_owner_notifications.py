"""Owner notification integration tests on disposable SQLite and authenticated API."""
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


class OwnerNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(models, "DB_PATH", Path(self.temp.name) / "owner-notifications.db")
        self.db_patch.start()
        models.create_tables()
        models.create_tables()  # additive schema stays idempotent
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 32132)),
            base_url="http://127.0.0.1:8000",
        )
        for role in ("owner", "customer"):
            response = await self.client.post("/auth/register", json={
                "name": "Fictional " + role,
                "email": f"notify.{role}@example.test",
                "password": "Fictional-test-password-2026",
            })
            self.assertEqual(response.status_code, 201, response.text)
        promote_registered_customer("notify.owner@example.test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.db_patch.stop()
        self.temp.cleanup()

    async def login(self, role):
        response = await self.client.post("/auth/login", json={
            "email": f"notify.{role}@example.test",
            "password": "Fictional-test-password-2026",
        })
        self.assertEqual(response.status_code, 200, response.text)

    def seed_events(self):
        with models.get_connection() as conn:
            owner = conn.execute("SELECT id FROM customers WHERE email = ?",
                ("notify.owner@example.test",)).fetchone()["id"]
            customer = conn.execute("SELECT id FROM customers WHERE email = ?",
                ("notify.customer@example.test",)).fetchone()["id"]
            zone = conn.execute("""INSERT INTO delivery_zones
                (area, fee, estimated_min_hours, estimated_max_hours)
                VALUES ('Fictional Zone', 500, 1, 2)""").lastrowid
            product = conn.execute("""INSERT INTO products(name,category,price)
                VALUES ('Fictional Rice','Rice',2000)""").lastrowid
            conn.execute("INSERT INTO inventory(product_id,variant,quantity) VALUES (?,'standard',20)", (product,))
            order = conn.execute("""INSERT INTO orders
                (customer_id,delivery_zone_id,delivery_address,subtotal,delivery_fee,discount,total,status)
                VALUES (?,?,'Fictional Road',2000,500,0,2500,'confirmed')""",
                (customer, zone)).lastrowid
            conn.execute("""INSERT INTO payments(order_id,amount,status,paid_at)
                VALUES (?,2500,'paid','2026-11-19 11:15:00')""", (order,))
            conn.execute("""INSERT INTO prepared_stock
                (product_id,variant,unit,quantity_units,serving_units,tolerance_units,
                 extra_units,extra_tolerance_units,extra_price,active)
                VALUES (?,'standard','g',5000,250,10,125,10,700,1)""", (product,))
            conn.execute("""INSERT INTO prepared_stock_movements
                (product_id,variant,actor_customer_id,action,change_units,before_units,after_units,reason)
                VALUES (?,'standard',?,'add',5000,0,5000,'Fictional stock')""", (product, owner))
            conn.commit()

    async def test_persistent_real_events_and_access_control(self):
        response = await self.client.get("/owner/notifications")
        self.assertEqual(response.status_code, 401)
        await self.login("customer")
        self.assertEqual((await self.client.get("/owner/notifications")).status_code, 403)
        self.assertEqual((await self.client.post("/owner/notifications/read-all")).status_code, 403)
        await self.login("owner")
        initial = await self.client.get("/owner/notifications")
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(initial.json()["items"], [], "No fake or seeded notifications")
        self.seed_events()
        response = await self.client.get("/owner/notifications")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual({event["category"] for event in data["items"]}, {"order", "payment", "stock"})
        self.assertEqual(len(data["items"]), 3)
        self.assertEqual(data["unread_count"], 3)
        self.assertTrue(all(event["created_at"] for event in data["items"]))
        key = data["items"][0]["key"]
        put = await self.client.put("/owner/notifications/" + key + "/read", json={"read": True})
        self.assertEqual(put.status_code, 200, put.text)
        self.assertEqual((await self.client.get("/owner/notifications")).json()["unread_count"], 2)
        # Re-fetch from a new DB connection, not local browser storage.
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM owner_notification_reads").fetchone()[0], 1)
        self.assertEqual((await self.client.put("/owner/notifications/" + key + "/read", json={"read": False})).status_code, 200)
        self.assertEqual((await self.client.get("/owner/notifications")).json()["unread_count"], 3)
        self.assertEqual((await self.client.post("/owner/notifications/read-all")).status_code, 200)
        self.assertEqual((await self.client.get("/owner/notifications")).json()["unread_count"], 0)
        forged = await self.client.put("/owner/notifications/stock:999999/read", json={"read": True})
        self.assertEqual(forged.status_code, 404)
        self.assertEqual((await self.client.put("/owner/notifications/" + key + "/read", json={"read": "yes"})).status_code, 422)


if __name__ == "__main__":
    unittest.main()
