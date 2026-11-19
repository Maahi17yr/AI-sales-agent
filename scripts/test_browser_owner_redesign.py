"""Isolated owner dark-theme/notification/modal/responsive Chromium regression.

Uses fictional records in the disposable browser CI database only.
"""
import os
from uuid import uuid4

from playwright.sync_api import sync_playwright
from core.services import auth_service
from core.services.owner_access_service import promote_registered_customer
from data.database import models

BASE = "http://127.0.0.1:3000"


def main():
    assert os.getenv("NAIJA_BITES_ENV") == "development"
    assert os.getenv("NAIJA_BITES_TEST_MOCK_PAYMENTS") == "1"
    assert os.getenv("NAIJA_BITES_DATA_DIR", "").startswith("/tmp/naija-bites-chromium-isolated")
    suffix = uuid4().hex[:8]
    password = "Fictional-Owner-Browser-2026"
    email = "redesign.owner." + suffix + "@example.test"
    buyer_email = "redesign.buyer." + suffix + "@example.test"
    auth_service.register_customer("Fictional Owner", email, password)
    auth_service.register_customer("Fictional Buyer", buyer_email, password)
    promote_registered_customer(email)
    with models.get_connection() as conn:
        buyer = conn.execute("SELECT id FROM customers WHERE email=?", (buyer_email,)).fetchone()["id"]
        zone = conn.execute("""INSERT INTO delivery_zones
            (area,fee,estimated_min_hours,estimated_max_hours)
            VALUES (?,500,1,2)""", ("Fictional Zone " + suffix,)).lastrowid
        product = conn.execute("INSERT INTO products(name,category,price,available) VALUES (?,'Rice',2000,1)",
                               ("Fictional Jollof " + suffix,)).lastrowid
        conn.execute("INSERT INTO inventory(product_id,variant,quantity) VALUES (?,'standard',20)", (product,))
        order = conn.execute("""INSERT INTO orders
          (customer_id,delivery_zone_id,delivery_address,subtotal,delivery_fee,discount,total,status)
          VALUES (?,?,'Fictional Street',2000,500,0,2500,'confirmed')""", (buyer, zone)).lastrowid
        conn.execute("INSERT INTO payments(order_id,amount,status,paid_at) VALUES (?,2500,'paid','2026-11-19 12:00:00')", (order,))
        conn.commit()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width in (390, 768, 1366):
            ctx = browser.new_context(viewport={"width": width, "height": 850})
            page = ctx.new_page()
            # Every confirmation must be a branded DOM modal, never a browser alert.
            dialogs = []
            page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
            page.goto(BASE, wait_until="domcontentloaded")
            page.locator('input[name="email"]').fill(email)
            page.locator('input[name="password"]').fill(password)
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url("**/owner", timeout=30000)
            page.get_by_text("Total verified revenue").wait_for(timeout=30000)
            assert page.locator(".owner-shell").evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(13, 20, 19)"
            assert page.get_by_text("Needs attention", exact=True).count() == 0
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 3"), width
            page.screenshot(path=f"/tmp/naija-ui-owner-dark-{width}.png", full_page=True)

            bell = page.get_by_role("button", name="Toggle notifications")
            bell.click()
            panel = page.get_by_role("region", name="In-app notifications")
            panel.get_by_text("Payment verified for order #" + str(order)).wait_for(timeout=15000)
            assert panel.get_by_text("New order #" + str(order)).count() == 1
            assert panel.locator("time").count() >= 2
            panel.get_by_role("button", name="Mark all read").click()
            page.wait_for_timeout(250)
            assert panel.get_by_text("0 unread", exact=False).count() >= 1
            panel.get_by_role("button", name="Mark unread").first.click()
            page.wait_for_timeout(250)
            assert panel.get_by_text("1 unread", exact=False).count() >= 1
            panel.get_by_role("button", name="Close notifications").click()

            page.get_by_role("button", name="Orders", exact=True).click()
            page.get_by_role("button", name="Preparing", exact=True).first.click()
            modal = page.get_by_role("alertdialog")
            modal.get_by_text("Update order status").wait_for(timeout=5000)
            modal.get_by_role("button", name="Cancel").click()
            assert page.get_by_role("alertdialog").count() == 0
            # Check no native prompts were shown.
            assert dialogs == [], dialogs
            if width == 390:
                page.get_by_role("button", name="Preparing", exact=True).first.click()
                page.get_by_role("alertdialog").screenshot(path="/tmp/naija-ui-owner-modal-390.png")
                page.get_by_role("alertdialog").get_by_role("button", name="Cancel").click()
            page.get_by_role("button", name="Prepared stock", exact=True).click()
            page.get_by_text("Food on hand").wait_for(timeout=15000)
            page.screenshot(path=f"/tmp/naija-ui-prepared-dark-{width}.png", full_page=True)
            ctx.close()
        browser.close()
    print("Owner mode dark UI, live notifications, persistence, modal, prepared stock and 3 viewports passed")


if __name__ == "__main__":
    main()
