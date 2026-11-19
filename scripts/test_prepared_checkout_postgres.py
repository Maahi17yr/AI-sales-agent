"""Prepared-stock transaction tests on disposable CI PostgreSQL only.

The CI guard refuses real databases. Exercises row locking and rollback.
"""
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from core.services import auth_service, prepared_stock_service as stock, stock_policy
from core.services.checkout import CheckoutService
from core.services.order_service import fingerprint
from data.database import models
from data.database.customization_schema import create_customization_tables


class PreparedCheckoutPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url=os.getenv("DATABASE_URL","")
        if (os.getenv("NAIJA_BITES_POSTGRES_CI")!="1" or
            not url.startswith("postgresql://postgres:postgres@127.0.0.1:5432/")):
            raise unittest.SkipTest("Disposable CI PostgreSQL required.")
        models.create_tables()
        create_customization_tables()

    def setUp(self):
        self.suffix=uuid4().hex[:12]
        self.owner=auth_service.register_customer(
            "Fictional PG Owner",f"owner-{self.suffix}@example.test",
            "Fictional-Postgres-Owner-Password-2026")
        from core.services.owner_access_service import promote_registered_customer
        promote_registered_customer(self.owner["email"])
        self.customer_ids=[
            auth_service.register_customer(
                "Fictional PG Customer",f"customer-{i}-{self.suffix}@example.test",
                "Fictional-Postgres-Customer-Password-2026")["id"]
            for i in range(2)
        ]
        with models.get_connection() as conn:
            self.pid=conn.execute(
                "INSERT INTO products(name,category,price) VALUES (?,?,?)",
                (f"Fictional PG Jollof {self.suffix}","Rice",1500)
            ).lastrowid
            conn.execute("INSERT INTO inventory(product_id,variant,quantity) VALUES (?,'standard',10)",
                         (self.pid,))
            conn.execute("""INSERT INTO delivery_zones(area,fee,estimated_min_hours,estimated_max_hours)
                VALUES(?,1000,1,2)""",(f"PG Zone {self.suffix}",))
            conn.execute("INSERT INTO ingredients(name,stock_quantity,available) VALUES (?,0,1)",
                         (f"Extra PG Rice {self.suffix}",))
            self.extra=conn.execute(
                "SELECT id FROM ingredients WHERE name=?",
                (f"Extra PG Rice {self.suffix}",)).fetchone()["id"]
            conn.execute("INSERT INTO meal_addons(product_id,ingredient_id,extra_price,max_quantity)"
                         " VALUES (?,?,999,3)",(self.pid,self.extra))
        stock.configure(self.owner,dict(product_id=self.pid,variant="standard",
            unit="g",serving_units=250,tolerance_units=10,extra_units=125,
            extra_tolerance_units=10,extra_price=700))
        stock.adjust(self.owner,dict(product_id=self.pid,variant="standard",
            action="add",quantity_units=5000,reason="CI prepared batch"))
        stock.set_active(self.owner,self.pid,"standard",True)
        stock.map_addon(self.owner,self.pid,self.extra,"self_extra")

    def balance(self):
        with models.get_connection() as conn:
            return conn.execute("""SELECT quantity_units FROM prepared_stock
                WHERE product_id=? AND variant='standard'""",(self.pid,)
            ).fetchone()["quantity_units"]

    def prepare(self,cid,extra=False,qty=1):
        items=[dict(product_id=self.pid,variant="standard",quantity=qty,
            modifications=([dict(action="add",ingredient_id=self.extra,quantity=1,
                                 name="Extra Rice",unit_price_delta=999)] if extra else []))]
        sid=f"customer-{cid}"
        with models.get_connection() as conn:
            conn.execute("""INSERT INTO conversation_sessions(session_id,customer_id,cart_data)
                VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET
                cart_data=excluded.cart_data""",
                (sid,cid,json.dumps({"items":items})))
        review=CheckoutService(sid).prepare_checkout(
            "Fictional PG Customer","08012345678","PG Example Street",f"PG Zone {self.suffix}")
        self.assertTrue(review["success"],review)
        return sid,review

    @staticmethod
    def confirm(sid,review):
        return CheckoutService(sid).confirm_checkout(True,
            fingerprint(review["summary"]),review["checkout_id"])

    def test_postgres_combined_655g_and_last_serving_race(self):
        # Two standard meals, one with the extra, are separate cart lines.
        cid=self.customer_ids[0]
        sid=f"customer-{cid}"
        lines=[
            dict(product_id=self.pid,variant="standard",quantity=1,modifications=[]),
            dict(product_id=self.pid,variant="standard",quantity=1,
                 modifications=[dict(action="add",ingredient_id=self.extra,quantity=1,
                     name="Extra Rice",unit_price_delta=999)])]
        with models.get_connection() as conn:
            conn.execute("""INSERT INTO conversation_sessions(session_id,customer_id,cart_data)
                VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE
                SET cart_data=excluded.cart_data""",
                (sid,cid,json.dumps({"items":lines})))
        review=CheckoutService(sid).prepare_checkout(
            "Fictional PG Customer","08012345678","PG Example Street",f"PG Zone {self.suffix}")
        self.assertTrue(review["success"],review)
        a=self.confirm(sid,review)
        self.assertTrue(a["success"],a)
        self.assertEqual(self.balance(),4345)
        with models.get_connection() as conn:
            row=conn.execute("""SELECT quantity_units,before_units,after_units
                FROM prepared_order_deductions WHERE order_id=?""",
                (a["order_id"],)).fetchone()
            self.assertEqual((row["quantity_units"],row["before_units"],row["after_units"]),
                             (655,5000,4345))
            self.assertEqual(conn.execute("SELECT quantity FROM inventory WHERE product_id=?",
                (self.pid,)).fetchone()["quantity"],10)
        # Only one portion now available; two separate customers both review.
        stock.adjust(self.owner,dict(product_id=self.pid,variant="standard",
            action="set",quantity_units=260,reason="Only one standard remains"))
        first=self.prepare(self.customer_ids[0])
        second=self.prepare(self.customer_ids[1])
        with ThreadPoolExecutor(max_workers=2) as pool:
            x=pool.submit(self.confirm,*first)
            y=pool.submit(self.confirm,*second)
            results=[x.result(),y.result()]
        self.assertEqual(sum(bool(z["success"]) for z in results),1,results)
        self.assertEqual(self.balance(),0)
        with models.get_connection() as conn:
            rows=conn.execute("""SELECT quantity_units FROM prepared_order_deductions
                WHERE product_id=?""",(self.pid,)).fetchall()
            self.assertEqual(sorted([z["quantity_units"] for z in rows]),[260,655])

    def test_postgres_rollback_and_repeat_does_not_double_deduct(self):
        cid=self.customer_ids[0]
        sid,review=self.prepare(cid,extra=True)
        real=stock_policy.deduct
        def fail_after(*args,**kwargs):
            real(*args,**kwargs)
            raise ValueError("Simulated PostgreSQL rollback after deduction")
        with patch("core.services.stock_policy.deduct",side_effect=fail_after):
            failure=self.confirm(sid,review)
        self.assertFalse(failure["success"])
        self.assertEqual(self.balance(),5000)
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders o JOIN order_items oi"
               " ON oi.order_id=o.id WHERE oi.product_id=?",(self.pid,)).fetchone()[0],0)
            self.assertEqual(conn.execute("""SELECT COUNT(*) FROM prepared_order_deductions
                WHERE product_id=?""",(self.pid,)).fetchone()[0],0)
        good=self.confirm(sid,review)
        duplicate=self.confirm(sid,review)
        self.assertTrue(good["success"],good)
        self.assertEqual(good["order_id"],duplicate["order_id"])
        self.assertEqual(self.balance(),4605)
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("""SELECT COUNT(*) FROM prepared_order_deductions
                WHERE product_id=?""",(self.pid,)).fetchone()[0],1)


    def test_checkout_locks_prepared_other_before_owner_changes_serving_size(self):
        """A separate chicken/side source cannot be reconfigured mid-checkout."""
        with models.get_connection() as conn:
            target=conn.execute("INSERT INTO products(name,category,price) VALUES (?, 'Sides', 1000)",
                (f"Fictional Prepared Side {self.suffix}",)).lastrowid
            conn.execute("INSERT INTO inventory(product_id,variant,quantity) VALUES (?,'standard',10)",
                (target,))
        def payload(standard):
            return dict(product_id=target,variant="standard",unit="g",
                serving_units=standard,tolerance_units=10,extra_units=125,
                extra_tolerance_units=10,extra_price=500)
        stock.configure(self.owner,payload(250))
        stock.adjust(self.owner,dict(product_id=target,variant="standard",
            action="add",quantity_units=520,reason="Two side servings available"))
        stock.set_active(self.owner,target,"standard",True)
        stock.map_addon(self.owner,self.pid,self.extra,"prepared_other",target,"standard")
        sid,review=self.prepare(self.customer_ids[0],extra=True)

        locked=Event()
        release=Event()
        attempted=Event()
        real_lock=stock_policy.lock_prepared_sources
        real_owner_lock=stock._lock_product

        def pause_after_source_lock(conn, items):
            real_lock(conn,items)
            locked.set()
            if not release.wait(timeout=15):
                raise ValueError("Timed out waiting to release stock lock")

        def signal_owner_attempt(conn,product_id):
            if product_id == target:
                attempted.set()
            return real_owner_lock(conn,product_id)

        try:
            with patch("core.services.stock_policy.lock_prepared_sources",
                       side_effect=pause_after_source_lock), patch.object(
                           stock,"_lock_product",side_effect=signal_owner_attempt):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    confirmation=pool.submit(self.confirm,sid,review)
                    self.assertTrue(locked.wait(12),"Checkout never acquired target lock")
                    reconfigure=pool.submit(stock.configure,self.owner,payload(500))
                    self.assertTrue(attempted.wait(12),"Owner did not attempt reconfiguration")
                    # It must wait for checkout to commit, not alter the
                    # target's serving size between demand and allocation.
                    with self.assertRaises(FutureTimeout):
                        reconfigure.result(timeout=0.4)
                    release.set()
                    result=confirmation.result(timeout=20)
                    # A placed pending-payment order now protects the
                    # original serving rule; the owner change must fail after
                    # waiting for checkout's prepared row lock.
                    with self.assertRaisesRegex(ValueError,"Resolve existing"):
                        reconfigure.result(timeout=20)
        finally:
            release.set()
        self.assertTrue(result["success"],result)
        with models.get_connection() as conn:
            row=conn.execute("""SELECT quantity_units FROM prepared_order_deductions
                WHERE product_id=? AND order_id=?""",(target,result["order_id"])).fetchone()
            self.assertEqual(row["quantity_units"],260)
            stock_row=conn.execute("""SELECT quantity_units,serving_units
                FROM prepared_stock WHERE product_id=?""",(target,)).fetchone()
            self.assertEqual((stock_row["quantity_units"],stock_row["serving_units"]),
                             (260,250))
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id=?",(target,)
            ).fetchone()["quantity"],10)
            conn.execute("UPDATE orders SET status = 'delivered' WHERE id = ?",
                         (result["order_id"],))
        # Only after the committed order is resolved may an owner resize
        # portions; the remaining stock does not change during reconfiguration.
        changed=stock.configure(self.owner,payload(500))
        self.assertEqual(changed["serving_units"],500)
        self.assertEqual(changed["quantity_units"],260)


if __name__ == "__main__":
    unittest.main()
