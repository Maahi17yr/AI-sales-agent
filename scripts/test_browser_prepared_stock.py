"""Isolated Chromium validation of prepared stock from owner to customer."""
import json
import os
from uuid import uuid4
from playwright.sync_api import sync_playwright
from core.services import auth_service
from core.services.owner_access_service import promote_registered_customer
from data.database import models
from data.database.customization_schema import create_customization_tables

BASE = "http://127.0.0.1:3000"

def main():
    assert os.getenv("NAIJA_BITES_TEST_MOCK_PAYMENTS") == "1"
    assert os.getenv("NAIJA_BITES_ENV") == "development"
    assert os.getenv("NAIJA_BITES_DATA_DIR", "").startswith("/tmp/naija-bites-chromium-isolated")
    create_customization_tables()  # Optional tables are not seeded by the mock server.
    suffix=uuid4().hex[:8]
    pwd="Fictional-Browser-Password-2026"
    owner=f"prepared.owner.{suffix}@example.test"
    email=f"prepared.customer.{suffix}@example.test"
    auth_service.register_customer("Fictional Owner",owner,pwd)
    promote_registered_customer(owner)
    customer=auth_service.register_customer("Fictional Customer",email,pwd)
    product=f"Fictional Jollof {suffix}"
    with models.get_connection() as c:
        pid=c.execute("INSERT INTO products(name,category,price) VALUES (?,'Rice',1500)",
                      (product,)).lastrowid
        c.execute("INSERT INTO inventory(product_id,variant,quantity) VALUES (?,'standard',20)",(pid,))
        zone="Fictional Zone "+suffix
        c.execute("""INSERT INTO delivery_zones(area,fee,estimated_min_hours,estimated_max_hours)
                  VALUES (?,1000,1,2)""",(zone,))
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        def login(mail,width):
            ctx=browser.new_context(viewport={"width":width,"height":840})
            page=ctx.new_page()
            page.goto(BASE,wait_until="domcontentloaded")
            page.locator('input[name="email"]').fill(mail)
            page.locator('input[name="password"]').fill(pwd)
            page.get_by_role("button",name="Sign in",exact=True).click()
            return ctx,page
        owner_ctx,owner_page=login(owner,1366)
        owner_page.wait_for_url("**/owner",timeout=30000)
        owner_page.get_by_role("button",name="Prepared stock").click()
        owner_page.get_by_label("Menu item").select_option(label=product)
        owner_page.get_by_role("button",name="Save serving configuration").click()
        owner_page.get_by_text("Serving estimates saved.",exact=False).wait_for(timeout=30000)
        owner_page.get_by_label("Quantity (kg)").fill("5")
        owner_page.get_by_label("Reason for adjustment").fill("Fictional cooked batch")
        owner_page.get_by_role("button",name="Record adjustment").click()
        owner_page.get_by_text("Stock adjustment saved",exact=False).wait_for(timeout=30000)
        owner_page.get_by_role("button",name="Activate prepared stock").click()
        owner_page.get_by_text("Stock source updated.",exact=False).wait_for(timeout=30000)
        label=f"Extra Rice {suffix}"
        owner_page.get_by_label("Create an extra serving choice").fill(label)
        owner_page.get_by_role("button",name="Create extra").click()
        owner_page.get_by_text("Extra serving option created",exact=False).wait_for(timeout=30000)
        with models.get_connection() as c:
            extra=c.execute("""SELECT i.id FROM ingredients i
                JOIN meal_addons m ON m.ingredient_id=i.id
                WHERE m.product_id=? AND i.name=?""",(pid,label)).fetchone()["id"]
        buyer_ctx,buyer_page=login(email,390)
        buyer_page.locator("#message-input").wait_for(timeout=30000)
        menu=buyer_ctx.request.get(BASE+"/api/backend/menu")
        assert menu.status==200
        assert any(p["name"]==product for p in menu.json()["products"])
        session=f"customer-{customer['id']}"
        with models.get_connection() as c:
            items=[{"product_id":pid,"variant":"standard","quantity":1,"modifications":[]},
                   {"product_id":pid,"variant":"standard","quantity":1,
                    "modifications":[{"action":"add","ingredient_id":extra,
                                      "name":label,"quantity":1,"unit_price_delta":700}]}]
            c.execute("UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                      (json.dumps({"items":items}),session))
        prep=buyer_ctx.request.post(BASE+"/api/backend/checkout/prepare",data={
            "customer_name":"Fictional Customer","phone":"08012345678",
            "delivery_address":"Fictional Test Street","delivery_area":zone})
        assert prep.status==200,prep.text()
        state=prep.json()
        confirm_data={"customer_approved":True,"reviewed_fingerprint":state["fingerprint"],
                      "checkout_id":state["checkout_id"]}
        confirmed=buyer_ctx.request.post(BASE+"/api/backend/checkout/confirm",data=confirm_data)
        assert confirmed.status==200,confirmed.text()
        order_id=confirmed.json()["order_id"]
        replay=buyer_ctx.request.post(BASE+"/api/backend/checkout/confirm",data=confirm_data)
        assert replay.status==200 and replay.json()["order_id"]==order_id
        with models.get_connection() as c:
            remaining=c.execute("SELECT quantity_units FROM prepared_stock WHERE product_id=?",
                                (pid,)).fetchone()["quantity_units"]
            deduction=c.execute("""SELECT quantity_units FROM prepared_order_deductions
                                   WHERE order_id=? AND product_id=?""",
                                (order_id,pid)).fetchone()["quantity_units"]
            assert (remaining,deduction)==(4345,655),(remaining,deduction)
            assert c.execute("SELECT quantity FROM inventory WHERE product_id=?",
                             (pid,)).fetchone()["quantity"]==20
        exposed=json.dumps([menu.json(),state,confirmed.json()])
        for marker in ("quantity_units","serving_units","tolerance_units","5000g","250g"):
            assert marker not in exposed,marker
        print("Owner Chromium + customer checkout PASS: 655g once, 4345g remains")
        browser.close()

if __name__ == "__main__":
    main()
