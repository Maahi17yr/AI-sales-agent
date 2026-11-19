"""Prepared stock integration tests. Disposable SQLite, no payment/provider calls."""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

os.environ["NAIJA_BITES_ENV"] = "development"
os.environ["DATABASE_URL"] = ""

import httpx
from core.api.server import app
from core.services import (auth_service, prepared_stock_service as stock,
                           stock_policy, paystack_service)
from core.services.checkout import CheckoutService
from core.services.order_service import fingerprint
from core.services.customization_service import CustomizationService
from core.services.cart import CartService
from core.services.checkout_state import cancel_draft_order
from data.database import models, repository
from data.database.customization_schema import create_customization_tables


class PreparedCheckoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(models, "DB_PATH", Path(self.tmp.name) / "prepared-checkout.db")
        self.patch.start()
        models.create_tables()
        create_customization_tables()
        self.owner = auth_service.register_customer(
            "Fictional Owner", "phase2.owner@example.test", "Fictional-Owner-Password-2026")
        from core.services.owner_access_service import promote_registered_customer
        promote_registered_customer("phase2.owner@example.test")
        self.owner["role"] = "owner"
        self.a = auth_service.register_customer(
            "Fictional A", "phase2.a@example.test", "Fictional-Password-A-2026")["id"]
        self.b = auth_service.register_customer(
            "Fictional B", "phase2.b@example.test", "Fictional-Password-B-2026")["id"]
        with models.get_connection() as conn:
            conn.execute("""INSERT INTO delivery_zones
                (area,fee,estimated_min_hours,estimated_max_hours)
                VALUES('Lekki',1000,1,2)""")
            self.jollof = conn.execute("""INSERT INTO products(name,category,price)
                VALUES('Party Jollof Rice','Rice',1500)""").lastrowid
            self.chicken = conn.execute("""INSERT INTO products(name,category,price)
                VALUES('Fried Chicken','Sides',1000)""").lastrowid
            self.legacy = conn.execute("""INSERT INTO products(name,category,price)
                VALUES('Beans','Meals',900)""").lastrowid
            for pid,count in ((self.jollof,20),(self.chicken,12),(self.legacy,8)):
                conn.execute("""INSERT INTO inventory(product_id,variant,quantity)
                    VALUES (?,'standard',?)""",(pid,count))
            self.extras = {}
            for name,count,price in (("Extra Rice",0,999),
                                     ("Extra Chicken",0,1000),
                                     ("Extra Egg",5,250)):
                conn.execute("""INSERT INTO ingredients(name,stock_quantity,available)
                    VALUES (?,?,1)""",(name,count))
                iid = conn.execute(
                    "SELECT id FROM ingredients WHERE name=?",(name,)).fetchone()["id"]
                self.extras[name] = iid
                conn.execute("""INSERT INTO meal_addons
                    (product_id,ingredient_id,extra_price,max_quantity)
                    VALUES (?,?,?,2)""",(self.jollof,iid,price))
            conn.commit()
        stock.configure(self.owner,dict(product_id=self.jollof,variant="standard",
            unit="g",serving_units=250,tolerance_units=10,extra_units=125,
            extra_tolerance_units=10,extra_price=700))
        stock.adjust(self.owner,dict(product_id=self.jollof,variant="standard",
            action="add",quantity_units=5000,reason="Prepared test rice batch"))
        stock.configure(self.owner,dict(product_id=self.chicken,variant="standard",
            unit="piece",serving_units=1,tolerance_units=0,extra_units=1,
            extra_tolerance_units=0,extra_price=1000))
        stock.adjust(self.owner,dict(product_id=self.chicken,variant="standard",
            action="add",quantity_units=3,reason="Prepared chicken test batch"))
        stock.set_active(self.owner,self.jollof,"standard",True)
        stock.set_active(self.owner,self.chicken,"standard",True)
        stock.map_addon(self.owner,self.jollof,self.extras["Extra Rice"],"self_extra")
        stock.map_addon(self.owner,self.jollof,self.extras["Extra Chicken"],
                        "prepared_other",self.chicken,"standard")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(
            app=app,client=("127.0.0.1",54321)),base_url="http://127.0.0.1:8000")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.patch.stop()
        self.tmp.cleanup()

    def item(self,pid=None,quantity=1,*extras):
        return dict(product_id=pid or self.jollof,variant="standard",
                    quantity=quantity,modifications=[
                        dict(action="add",ingredient_id=self.extras[name],
                             quantity=count,unit_price_delta=999,name=name)
                        for name,count in extras])

    def cart(self, cid, lines):
        sid=f"customer-{cid}"
        with models.get_connection() as conn:
            conn.execute("""INSERT INTO conversation_sessions
                (session_id,customer_id,cart_data) VALUES (?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET cart_data=excluded.cart_data""",
                (sid,cid,json.dumps({"items":lines})))
            conn.commit()
        return sid

    def review(self, cid, lines):
        sid=self.cart(cid,lines)
        result=CheckoutService(sid).prepare_checkout(
            "Fictional Customer","08012345678","123 Example Street","Lekki")
        self.assertTrue(result["success"],result)
        return sid,result

    def confirm(self,sid,review):
        return CheckoutService(sid).confirm_checkout(
            customer_approved=True,reviewed_fingerprint=fingerprint(review["summary"]),
            checkout_id=review["checkout_id"])

    def stock_left(self,pid):
        with models.get_connection() as conn:
            return conn.execute("""SELECT quantity_units FROM prepared_stock
                WHERE product_id=? AND variant='standard'""",(pid,)).fetchone()["quantity_units"]

    async def test_standard_and_single_extra_use_655g_once(self):
        # Customizations are PER LINE. One of two standard meals gets 1 extra.
        lines=[self.item(quantity=1),self.item(None,1,("Extra Rice",1))]
        sid,review=self.review(self.a,lines)
        self.assertEqual(review["summary"]["items"][1]["unit_price"],2200)
        placed=self.confirm(sid,review)
        self.assertTrue(placed["success"],placed)
        self.assertEqual(self.stock_left(self.jollof),4345)
        self.assertEqual(repository.get_stock(self.jollof),16)  # 4345//260
        self.assertEqual(CartService(sid).get_cart(),[])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0],2)
            deduct=conn.execute("""SELECT quantity_units,before_units,after_units FROM
                prepared_order_deductions WHERE product_id=?""",(self.jollof,)).fetchone()
            self.assertEqual(tuple(deduct), (655,5000,4345))
            self.assertEqual(conn.execute("""SELECT quantity FROM inventory WHERE product_id=?""",
                         (self.jollof,)).fetchone()["quantity"],20)
            self.assertEqual(conn.execute("""SELECT stock_quantity FROM ingredients WHERE id=?""",
                         (self.extras["Extra Rice"],)).fetchone()["stock_quantity"],0)
        events=stock.recent_movements()
        self.assertTrue(any(e["action"]=="order" and e["change_units"]==-655 for e in events))

    async def test_mixed_addons_and_legacy_stock_exactly_once(self):
        sid,review=self.review(self.a,[self.item(None,1,("Extra Rice",1),
                            ("Extra Chicken",1),("Extra Egg",1)),self.item(self.legacy)])
        self.assertEqual(review["summary"]["items"][0]["unit_price"],3450)
        result=self.confirm(sid,review)
        self.assertTrue(result["success"],result)
        self.assertEqual(self.stock_left(self.jollof),4605)  # 260 + 135
        self.assertEqual(self.stock_left(self.chicken),2)
        with models.get_connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id=?",(self.legacy,)
            ).fetchone()["quantity"],7)
            self.assertEqual(conn.execute(
                "SELECT quantity FROM inventory WHERE product_id=?",(self.chicken,)
            ).fetchone()["quantity"],12)
            self.assertEqual(conn.execute(
                "SELECT stock_quantity FROM ingredients WHERE id=?",
                (self.extras["Extra Egg"],)
            ).fetchone()["stock_quantity"],4)
            self.assertEqual(conn.execute(
                "SELECT stock_quantity FROM ingredients WHERE id=?",
                (self.extras["Extra Chicken"],)
            ).fetchone()["stock_quantity"],0)
            rows=conn.execute("""SELECT product_id,quantity_units
                FROM prepared_order_deductions ORDER BY product_id""").fetchall()
            self.assertEqual({x["product_id"]:x["quantity_units"] for x in rows},
                             {self.jollof:395,self.chicken:1})
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM order_item_customizations"
            ).fetchone()[0],3)

    async def test_duplicate_confirm_and_verified_payment_do_not_restock(self):
        sid,review=self.review(self.a,[self.item(quantity=2)])
        result=self.confirm(sid,review)
        replay=self.confirm(sid,review)
        self.assertEqual(result["order_id"],replay["order_id"])
        self.assertEqual(self.stock_left(self.jollof),4480)
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM prepared_order_deductions").fetchone()[0],1)
            conn.execute("""INSERT INTO payments(order_id,amount,payment_reference,status)
                VALUES (?,?,?,'unpaid')""",
                (result["order_id"],result["total"],"nb_test_prepared_check_1234"))
            conn.commit()
        data=dict(reference="nb_test_prepared_check_1234",currency="NGN",
                  amount=result["total"]*100,status="success")
        with patch("core.services.paystack_service._request",return_value=data):
            first=paystack_service.verify(data["reference"],sid)
            second=paystack_service.verify(data["reference"],sid)
        self.assertEqual(first["payment_status"],"paid")
        self.assertEqual(second["order_status"],"confirmed")
        self.assertEqual(self.stock_left(self.jollof),4480)
        self.assertEqual(self.confirm(sid,review)["order_id"],result["order_id"])

    async def test_cancel_draft_and_insufficient_stock_never_deduct(self):
        sid,review=self.review(self.a,[self.item(quantity=2)])
        cancel=cancel_draft_order(sid)
        self.assertTrue(cancel["success"])
        failed=self.confirm(sid,review)
        self.assertFalse(failed["success"])
        self.assertEqual(self.stock_left(self.jollof),5000)
        stock.adjust(self.owner,dict(product_id=self.jollof,variant="standard",
            action="set",quantity_units=259,reason="Corrected physical balance"))
        self.assertEqual(repository.get_stock(self.jollof),0)
        error=CartService(sid).add_item(self.jollof,1)
        self.assertFalse(error["success"])
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],0)

    async def test_owner_correction_between_review_and_confirmation(self):
        sid,review=self.review(self.a,[self.item()])
        stock.adjust(self.owner,dict(product_id=self.jollof,variant="standard",
            action="set",quantity_units=250,reason="Found less rice in pot"))
        failed=self.confirm(sid,review)
        self.assertFalse(failed["success"])
        self.assertEqual(self.stock_left(self.jollof),250)
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],0)
        stock.adjust(self.owner,dict(product_id=self.jollof,variant="standard",
            action="add",quantity_units=10,reason="Additional ten grams measured"))
        result=self.confirm(sid,review)
        self.assertTrue(result["success"],result)
        self.assertEqual(self.stock_left(self.jollof),0)
        self.assertEqual(repository.get_stock(self.jollof),0)

    async def test_rollback_after_partial_deductions(self):
        sid,review=self.review(self.a,[self.item(None,1,("Extra Chicken",1))])
        actual=stock_policy.deduct
        def after_writes(*args,**kwargs):
            actual(*args,**kwargs)
            raise ValueError("Simulated failed commit after stock changes")
        with patch("core.services.stock_policy.deduct",side_effect=after_writes):
            failed=self.confirm(sid,review)
        self.assertFalse(failed["success"])
        self.assertEqual(self.stock_left(self.jollof),5000)
        self.assertEqual(self.stock_left(self.chicken),3)
        with models.get_connection() as conn:
            for table in ("orders","order_items","prepared_order_deductions"):
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM "+table).fetchone()[0],0)
        retry=self.confirm(sid,review)
        self.assertTrue(retry["success"],retry)
        self.assertEqual(self.stock_left(self.chicken),2)

    async def test_two_customers_compete_for_last_prepared_serving(self):
        stock.adjust(self.owner,dict(product_id=self.jollof,variant="standard",
            action="set",quantity_units=260,reason="Only one serving remains"))
        a,ra=self.review(self.a,[self.item()])
        b,rb=self.review(self.b,[self.item()])
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1=pool.submit(self.confirm,a,ra)
            f2=pool.submit(self.confirm,b,rb)
            results=[f1.result(),f2.result()]
        self.assertEqual(sum(r["success"] for r in results),1,results)
        self.assertEqual(self.stock_left(self.jollof),0)
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM prepared_order_deductions").fetchone()[0],1)

    async def test_owner_switch_blocked_for_pending_orders_and_mapped_extras(self):
        sid,review=self.review(self.a,[self.item()])
        placed=self.confirm(sid,review)
        self.assertTrue(placed["success"])
        with self.assertRaisesRegex(ValueError,"Resolve existing"):
            stock.set_active(self.owner,self.jollof,"standard",False)
        with self.assertRaisesRegex(ValueError,"Resolve existing"):
            stock.map_addon(self.owner,self.jollof,self.extras["Extra Rice"],
                            "legacy_ingredient")
        with models.get_connection() as conn:
            conn.execute("UPDATE orders SET status='delivered' WHERE id=?",
                         (placed["order_id"],))
            conn.commit()
        # An active extra cannot be orphaned by source switching.
        with self.assertRaisesRegex(ValueError,"mapping"):
            stock.set_active(self.owner,self.jollof,"standard",False)
        stock.map_addon(self.owner,self.jollof,self.extras["Extra Rice"],
                        "legacy_ingredient")
        stock.map_addon(self.owner,self.jollof,self.extras["Extra Chicken"],
                        "legacy_ingredient")
        changed=stock.set_active(self.owner,self.jollof,"standard",False)
        self.assertFalse(changed["active"])
        self.assertEqual(repository.get_stock(self.jollof),20)
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],1)

    async def test_customer_menu_cart_and_ai_options_hide_measurements(self):
        login=await self.client.post("/auth/login",json={
            "email":"phase2.a@example.test","password":"Fictional-Password-A-2026"})
        self.assertEqual(login.status_code,200,login.text)
        menu=await self.client.get("/menu")
        self.assertEqual(menu.status_code,200,menu.text)
        options=CustomizationService.get_options(self.jollof)
        self.assertTrue(options["success"],options)
        rice=[x for x in options["extras"] if x["name"]=="Extra Rice"][0]
        self.assertEqual(rice["extra_price"],700)
        self.assertTrue(rice["available"])
        self.assertNotIn("stock_quantity",rice)
        cart=CartService(f"customer-{self.a}")
        result=cart.add_item(self.jollof,1,add={"Extra Rice":1})
        self.assertTrue(result["success"],result)
        review=CheckoutService(f"customer-{self.a}").prepare_checkout(
            "Fictional Customer","08012345678","Test Street","Lekki")
        self.assertTrue(review["success"],review)
        combined=json.dumps((menu.json(),options,result,review["summary"]))
        for forbidden in ("quantity_units","serving_units","tolerance_units",
                          "extra_tolerance_units","5000g","250g","125g","kilograms"):
            self.assertNotIn(forbidden,combined)
        self.assertIn("Extra Rice",combined)
        self.assertIn("Jollof",combined)

    async def test_same_pot_extra_option_can_be_created_without_ingredient_accounting(self):
        created=stock.create_self_extra(self.owner,self.jollof,"Extra Party Rice",3)
        self.assertEqual(created["source"],"self_extra")
        options=CustomizationService.get_options(self.jollof)
        self.assertTrue(any(x["name"]=="Extra Party Rice" for x in options["extras"]))
        self.assertTrue(CartService(self.cart(self.a,[])).add_item(
            self.jollof,1,add={"Extra Party Rice":1})["success"])
        # No raw ingredient purchasing or recipe data was created.
        with models.get_connection() as conn:
            row=conn.execute("SELECT stock_quantity FROM ingredients WHERE id=?",
                             (created["ingredient_id"],)).fetchone()
            self.assertEqual(row["stock_quantity"],0)


    async def test_same_chicken_stock_shared_by_side_and_addon(self):
        # Customer buys one chicken standalone plus one as a rice extra.
        # Both draw from the SAME prepared chicken record, one deduction row.
        sid,review=self.review(self.a,[
            self.item(None,1,("Extra Chicken",1)),
            self.item(self.chicken,1),
        ])
        placed=self.confirm(sid,review)
        self.assertTrue(placed["success"],placed)
        self.assertEqual(self.stock_left(self.chicken),1)
        with models.get_connection() as conn:
            rows=conn.execute("""SELECT quantity_units FROM prepared_order_deductions
                WHERE order_id=? AND product_id=?""",
                (placed["order_id"],self.chicken)).fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["quantity_units"],2)

    async def test_owner_adjustment_and_checkout_compete_atomically(self):
        stock.adjust(self.owner,dict(product_id=self.jollof,variant="standard",
            action="set",quantity_units=520,reason="Two standards on hand"))
        sid,review=self.review(self.a,[self.item(quantity=2)])
        def adjustment():
            try:
                stock.adjust(self.owner,dict(product_id=self.jollof,
                    variant="standard",action="remove",quantity_units=260,
                    reason="Cook used a standard portion"))
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            ordering=pool.submit(self.confirm,sid,review)
            correction=pool.submit(adjustment)
            order_result=ordering.result()
            corrected=correction.result()
        self.assertEqual(int(bool(order_result["success"]))+int(corrected),1)
        self.assertIn(self.stock_left(self.jollof),(0,260))
        with models.get_connection() as conn:
            order_count=conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            alloc_count=conn.execute("SELECT COUNT(*) FROM prepared_order_deductions").fetchone()[0]
            self.assertEqual(order_count,alloc_count)
            self.assertEqual(order_count,int(bool(order_result["success"])))

    async def test_open_order_prevents_serving_resize_but_allows_price_only_edit(self):
        sid,review=self.review(self.a,[self.item()])
        placed=self.confirm(sid,review)
        self.assertTrue(placed["success"],placed)
        old=self.stock_left(self.jollof)
        changed=dict(product_id=self.jollof,variant="standard",unit="g",
            serving_units=300,tolerance_units=10,extra_units=125,
            extra_tolerance_units=10,extra_price=700)
        with self.assertRaisesRegex(ValueError,"Resolve existing"):
            stock.configure(self.owner,changed)
        self.assertEqual(self.stock_left(self.jollof),old)
        with models.get_connection() as conn:
            row=conn.execute("""SELECT serving_units FROM prepared_stock
                WHERE product_id=?""",(self.jollof,)).fetchone()
            self.assertEqual(row["serving_units"],250)
        changed["serving_units"]=250
        changed["extra_price"]=800
        result=stock.configure(self.owner,changed)
        self.assertEqual(result["extra_price"],800)
        with models.get_connection() as conn:
            conn.execute("UPDATE orders SET status='delivered' WHERE id=?",
                         (placed["order_id"],))
            conn.commit()
        changed["serving_units"]=300
        result=stock.configure(self.owner,changed)
        self.assertEqual(result["serving_units"],300)

    async def test_extra_price_change_requires_new_customer_review(self):
        sid,review=self.review(self.a,[self.item(None,1,("Extra Rice",1))])
        stock.configure(self.owner,dict(product_id=self.jollof,variant="standard",
            unit="g",serving_units=250,tolerance_units=10,extra_units=125,
            extra_tolerance_units=10,extra_price=800))
        stale=self.confirm(sid,review)
        self.assertFalse(stale["success"])
        self.assertEqual(self.stock_left(self.jollof),5000)
        with models.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],0)
        fresh=CheckoutService(sid).prepare_checkout(
            "Fictional Customer","08012345678","123 Example Street","Lekki")
        self.assertTrue(fresh["success"],fresh)
        self.assertEqual(fresh["summary"]["items"][0]["unit_price"],2300)
        placed=self.confirm(sid,fresh)
        self.assertTrue(placed["success"],placed)
        self.assertEqual(self.stock_left(self.jollof),4605)


if __name__ == "__main__":
    unittest.main()
