"""Headless Chromium customer journey through Next.js -> FastAPI -> disposable SQLite.

Agent text and Paystack provider are mocked. Real customer auth, chat,
checkout, database writes and payment verification API paths are exercised.
"""
import json
import os
import re
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright
from data.database.models import get_connection

BASE = "http://127.0.0.1:3000"
API = BASE + "/api/backend"


def require_isolated():
    if (os.getenv("NAIJA_BITES_TEST_MOCK_PAYMENTS") != "1"
            or os.getenv("NAIJA_BITES_ENV") != "development"
            or os.getenv("DATABASE_URL")
            or not os.getenv("NAIJA_BITES_DATA_DIR", "").startswith("/tmp/")):
        raise RuntimeError("Refusing browser test outside isolated disposable CI")


def main():
    require_isolated()
    with httpx.Client(timeout=15) as client:
        assert client.get(API + "/ping").status_code == 200
    email = f"fictional-browser-{uuid4().hex[:12]}@example.test"
    password = "Fictional-Browser-Password-12345"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()
        page.add_init_script("""() => {
            window.successToastRecords = [];
            const active = new Map();
            new MutationObserver(() => {
                for (const element of document.querySelectorAll('.history-toast, .payment-toast--success')) {
                    if (active.has(element)) continue;
                    const rect = element.getBoundingClientRect();
                    const record = {
                        kind: element.classList.contains('history-toast') ? 'history' : 'payment',
                        start: performance.now(),
                        center: rect.left + rect.width / 2,
                        viewport: window.innerWidth
                    };
                    active.set(element, record);
                    window.successToastRecords.push(record);
                }
                for (const [element, record] of active) {
                    if (!element.isConnected) {
                        record.duration = performance.now() - record.start;
                        active.delete(element);
                    }
                }
            }).observe(document, {subtree:true, childList:true});
        }""")
        agent_calls = []
        page.on('console', lambda msg: print('BROWSER_CONSOLE:', msg.type, msg.text[:300]) if msg.type == 'error' else None)
        page.on('requestfailed', lambda req: print('BROWSER_REQUEST_FAILED:', req.url, req.failure))

        def serve_mock_checkout(route):
            query = parse_qs(urlsplit(route.request.url).query)
            reference = query.get("reference", [""])[0]
            assert reference.startswith("nb_test_"), reference
            target = BASE + "/?payment_return=1&reference=" + reference
            route.fulfill(status=200, content_type="text/html",
                          body="<html><script>location.replace(" + json.dumps(target) +
                               ")</script><body>Mock Paystack return</body></html>")

        def mock_agent(route):
            agent_calls.append(route.request.url)
            request_text = json.dumps(route.request.post_data_json or {})
            if "Simulate network interruption" in request_text:
                route.abort("failed")
                return
            newline = chr(10)
            if "Simulate provider timeout" in request_text:
                events = ['data: {"type":"RUN_ERROR","message":"The assistant was interrupted. Check your cart before retrying."}']
            else:
                events = [
                    'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"Fictional menu is ready."}',
                    'data: {"type":"RUN_FINISHED"}',
                ]
            body = (newline + newline).join(events) + newline + newline
            route.fulfill(status=200, content_type="text/event-stream",
                          headers={"Cache-Control": "no-cache"}, body=body)

        page.route("**/paystack-mock.test/**", serve_mock_checkout)
        page.route("**/api/backend/agent", mock_agent)
        try:
            page.goto(BASE + "/", wait_until="domcontentloaded")
            page.get_by_role("button", name="Create an account").click()
            page.locator('input[name="name"]').fill("Fictional Browser")
            page.locator('input[name="email"]').fill(email)
            page.locator('input[name="password"]').fill(password)
            page.get_by_role("button", name="Create account").click()
            page.locator("#message-input").wait_for(timeout=30000)
            page.get_by_role("button", name=re.compile("Browse the menu")).click()
            page.get_by_text("Fictional menu is ready.").wait_for(timeout=30000)
            page.reload(wait_until="domcontentloaded")
            page.locator("#message-input").wait_for(timeout=30000)
            page.get_by_text("Fictional menu is ready.").wait_for(timeout=30000)

            with get_connection() as conn:
                customer = conn.execute(
                    "SELECT id FROM customers WHERE email=?", (email,)
                ).fetchone()
                assert customer is not None
                customer_id = customer["id"]
                product_id = conn.execute(
                    "INSERT INTO products (name,category,price,available) VALUES (?,?,?,?)",
                    ("Fictional Browser Jollof", "rice", 4500, 1),
                ).lastrowid
                conn.execute(
                    "INSERT INTO inventory (product_id,variant,quantity) VALUES (?,?,?)",
                    (product_id, "standard", 20),
                )
                conn.execute(
                    "INSERT INTO delivery_zones (area,fee,estimated_min_hours,estimated_max_hours,available) VALUES (?,?,?,?,?)",
                    ("Browser Test Zone", 1000, 1, 2, 1),
                )
                conn.execute(
                    "UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                    (json.dumps({"items": [{"product_id": product_id,
                                            "quantity": 2, "variant": "standard",
                                            "modifications": []}]}),
                     f"customer-{customer_id}"),
                )

            page.reload(wait_until="domcontentloaded")
            page.get_by_role("button", name="Prepare order review").wait_for(timeout=30000)
            page.get_by_role("button", name="Prepare order review").click()
            page.get_by_label("Delivery name").fill("Fictional Browser")
            page.get_by_label("Delivery phone").fill("08012345678")
            page.get_by_label("Full delivery address").fill("Fictional Street 17")
            page.get_by_label(re.compile("Delivery area")).fill("Browser Test Zone")
            page.get_by_role("button", name="Review order", exact=True).click()
            page.get_by_role("region", name="Review your order").wait_for(timeout=30000)
            page.reload(wait_until="domcontentloaded")
            page.get_by_role("region", name="Review your order").wait_for(timeout=30000)
            page.get_by_role("button", name="Confirm order").click()
            payment_region = page.get_by_role("region", name="Paystack test payment")
            payment_region.wait_for(timeout=30000)
            page.get_by_label("Email for payment receipt").fill(email)
            page.get_by_role("button", name="Pay Now (Test Mode)").click()

            page.get_by_text(re.compile("Payment verified!")).wait_for(timeout=30000)
            page.wait_for_function("""() => window.successToastRecords.some(
                record => record.kind === 'payment' && record.duration !== undefined)""")
            payment_toast = page.evaluate("window.successToastRecords.find(record => record.kind === 'payment')")
            assert 950 <= payment_toast["duration"] <= 2000, payment_toast
            assert abs(payment_toast["center"] - payment_toast["viewport"] / 2) <= 1, payment_toast
            assert page.get_by_role("region", name="Paystack test payment").count() == 0
            page.reload(wait_until="domcontentloaded")
            page.locator("#message-input").wait_for(timeout=30000)
            assert page.get_by_role("region", name="Paystack test payment").count() == 0
            with get_connection() as conn:
                orders = conn.execute(
                    "SELECT id,status FROM orders WHERE customer_id=?", (customer_id,)
                ).fetchall()
                assert len(orders) == 1, orders
                assert orders[0]["status"] == "confirmed", orders
                assert conn.execute(
                    "SELECT quantity FROM inventory WHERE product_id=?", (product_id,)
                ).fetchone()["quantity"] == 18
                conn.execute(
                    "UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                    (json.dumps({"items": [{"product_id": product_id,
                                            "quantity": 1, "variant": "standard",
                                            "modifications": []}]}),
                     f"customer-{customer_id}"),
                )
            page.reload(wait_until="domcontentloaded")
            page.get_by_role("button", name="Prepare order review").wait_for(timeout=30000)
            assert page.get_by_role("region", name="Paystack test payment").count() == 0

            # Regression for the stuck ghost Paystack card: linked old orders
            # must expire, and a customer can dismiss a recent payment request
            # without cancelling or deleting the underlying fictional order.
            with get_connection() as conn:
                zone_id = conn.execute(
                    "SELECT id FROM delivery_zones WHERE area=?",
                    ("Browser Test Zone",),
                ).fetchone()["id"]
                old_id = conn.execute(
                    "INSERT INTO orders (customer_id, delivery_zone_id, "
                    "delivery_address, subtotal, delivery_fee, discount, total, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_payment')",
                    (customer_id, zone_id, "Old browser test", 4500, 1000, 0, 5500),
                ).lastrowid
                conn.execute(
                    "UPDATE orders SET created_at=? WHERE id=?",
                    ("2020-01-01 00:00:00", old_id),
                )
                conn.execute(
                    "UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                    (json.dumps({"items": [], "last_checkout": {"order_id": old_id}}),
                     f"customer-{customer_id}"),
                )
            page.reload(wait_until="domcontentloaded")
            page.locator("#message-input").wait_for(timeout=30000)
            assert page.get_by_role("region", name="Paystack test payment").count() == 0

            with get_connection() as conn:
                fresh_id = conn.execute(
                    "INSERT INTO orders (customer_id, delivery_zone_id, "
                    "delivery_address, subtotal, delivery_fee, discount, total, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_payment')",
                    (customer_id, zone_id, "Recent browser test", 4500, 1000, 0, 5500),
                ).lastrowid
                conn.execute(
                    "UPDATE conversation_sessions SET cart_data=? WHERE session_id=?",
                    (json.dumps({"items": [], "last_checkout": {"order_id": fresh_id}}),
                     f"customer-{customer_id}"),
                )
            page.reload(wait_until="domcontentloaded")
            payment_card = page.get_by_role("region", name="Paystack test payment")
            payment_card.wait_for(timeout=30000)
            assert f"Pay for order #{fresh_id}" in payment_card.inner_text()
            page.get_by_role("button", name="Not my current order").click()
            payment_card.wait_for(state="detached", timeout=30000)
            page.reload(wait_until="domcontentloaded")
            page.locator("#message-input").wait_for(timeout=30000)
            assert page.get_by_role("region", name="Paystack test payment").count() == 0
            with get_connection() as conn:
                assert conn.execute("SELECT status FROM orders WHERE id=?", (fresh_id,)
                    ).fetchone()["status"] == "pending_payment"

            other = browser.new_context()
            other_page = other.new_page()
            try:
                other_page.goto(BASE + "/", wait_until="domcontentloaded")
                other_page.get_by_role("heading", name="Welcome back").wait_for(timeout=30000)
                assert other_page.get_by_role("region", name="Paystack test payment").count() == 0
            finally:
                other.close()
            # Model failure and network disconnect must show an error, preserve
            # the user message, and not revive an old paid order.
            page.locator("#message-input").fill("Simulate provider timeout")
            page.get_by_role("button", name="Send message").click()
            page.locator(".inline-alert").filter(
                has_text="The assistant was interrupted"
            ).wait_for(timeout=30000)
            assert page.get_by_role("region", name="Paystack test payment").count() == 0
            page.locator("#message-input").fill("Simulate network interruption")
            page.get_by_role("button", name="Send message").click()
            page.locator(".inline-alert").wait_for(timeout=30000)
            assert page.get_by_text("Simulate network interruption").count() > 0
            page.locator("#message-input").fill("Recover after interruption")
            page.get_by_role("button", name="Send message").click()
            page.get_by_text("Fictional menu is ready.").last.wait_for(timeout=30000)

            # Shared-cookie tabs and account switching must not cross-contaminate
            # saved chats, checkout state or signed-in customer identity.
            second_tab = context.new_page()
            try:
                second_tab.goto(BASE + "/", wait_until="domcontentloaded")
                second_tab.locator("#message-input").wait_for(timeout=30000)
                # Logout is inside collapsible navigation; exercise its actual
                # React action without depending on viewport/hover animations.
                second_tab.locator(".account-logout").evaluate("(element) => element.click()")
                second_tab.get_by_role("heading", name="Welcome back").wait_for(timeout=30000)
                page.reload(wait_until="domcontentloaded")
                page.get_by_role("heading", name="Welcome back").wait_for(timeout=30000)

                second_tab.get_by_role("button", name="Create an account").click()
                second_tab.locator('input[name="name"]').fill("Second Browser Customer")
                second_tab.locator('input[name="email"]').fill(
                    f"other-browser-{uuid4().hex[:12]}@example.test"
                )
                second_tab.locator('input[name="password"]').fill(password)
                second_tab.get_by_role("button", name="Create account").click()
                second_tab.locator("#message-input").wait_for(timeout=30000)
                page.reload(wait_until="domcontentloaded")
                page.locator("#message-input").wait_for(timeout=30000)
                assert page.get_by_text("Fictional menu is ready.").count() == 0
                assert page.get_by_role("region", name="Paystack test payment").count() == 0

                # Logout is inside collapsible navigation; exercise its actual
                # React action without depending on viewport/hover animations.
                print("SECOND_TAB_BEFORE_LOGOUT:", second_tab.locator("body").inner_text()[-450:])
                print("SECOND_TAB_AUTH_BEFORE:", second_tab.evaluate("fetch('/api/backend/auth/me',{credentials:'include'}).then(r=>r.status)"))
                second_tab.locator(".account-logout").evaluate("(element) => element.click()")
                try:
                    second_tab.locator(".auth-card").wait_for(timeout=8000)
                except Exception:
                    print("SECOND_TAB_AFTER_LOGOUT_AUTH:", second_tab.evaluate("fetch('/api/backend/auth/me',{credentials:'include'}).then(r=>r.status)"))
                    print("SECOND_TAB_AFTER_LOGOUT_BODY:", second_tab.locator("body").inner_text()[-2000:])
                    print("SECOND_TAB_AFTER_LOGOUT_NOTICE:", second_tab.locator(".auth-notice").all_inner_texts())
                    raise
                print("SECOND_TAB_AUTH_AFTER:", second_tab.evaluate("fetch('/api/backend/auth/me',{credentials:'include'}).then(r=>r.status)"))
                page.reload(wait_until="domcontentloaded")
                page.get_by_role("heading", name="Welcome back").wait_for(timeout=30000)
                page.locator('input[name="email"]').fill(email)
                page.locator('input[name="password"]').fill(password)
                page.get_by_role("button", name="Sign in", exact=True).click()
                page.locator("#message-input").wait_for(timeout=30000)
                page.get_by_text("Fictional menu is ready.").last.wait_for(timeout=30000)
            finally:
                second_tab.close()
            # Clearing history acts immediately, reports failures persistently,
            # and shows success only as a one-second floating toast.
            dialogs = []
            def unexpected_dialog(dialog):
                dialogs.append(dialog.type)
                dialog.dismiss()
            page.on("dialog", unexpected_dialog)
            clear_button = page.get_by_role("button", name="Clear all saved conversations")
            page.wait_for_function("""() => {
                const button = document.querySelector('.chat-history-clear');
                return button && !button.disabled;
            }""")
            def fail_clear(route):
                if route.request.method == "DELETE":
                    route.fulfill(status=503, content_type="application/json", body='{"detail":"Unavailable"}')
                else:
                    route.continue_()
            page.route("**/api/backend/chats", fail_clear)
            clear_button.evaluate("(button) => button.click()")
            page.locator(".history-notice").filter(has_text="Could not clear history").wait_for()
            page.wait_for_timeout(1200)
            assert page.locator(".history-notice").is_visible()
            assert page.locator(".history-toast").count() == 0
            assert clear_button.count() == 1
            page.unroute("**/api/backend/chats", fail_clear)
            page.evaluate("""() => {
                window.historyToastTiming = {};
                const observer = new MutationObserver(() => {
                    const toast = document.querySelector('.history-toast');
                    if (toast && window.historyToastTiming.start === undefined) {
                        window.historyToastTiming.start = performance.now();
                    } else if (!toast && window.historyToastTiming.start !== undefined) {
                        window.historyToastTiming.duration = performance.now() - window.historyToastTiming.start;
                        observer.disconnect();
                    }
                });
                observer.observe(document.body, {subtree:true, childList:true});
            }""")
            clear_button.evaluate("(button) => button.click()")
            toast = page.locator(".history-toast")
            toast.wait_for()
            assert toast.inner_text() == "Chat history cleared successfully."
            assert page.locator(".history-notice").count() == 0
            assert toast.evaluate("(element) => getComputedStyle(element).position") == "fixed"
            toast.wait_for(state="detached", timeout=5000)
            duration = page.evaluate("window.historyToastTiming.duration")
            assert 950 <= duration <= 2000, duration
            history_toast = page.evaluate("window.successToastRecords.find(record => record.kind === 'history')")
            assert abs(history_toast["center"] - history_toast["viewport"] / 2) <= 1, history_toast
            assert dialogs == [], dialogs
            page.reload(wait_until="domcontentloaded")
            page.locator("#message-input").wait_for(timeout=30000)
            assert page.locator(".chat-history-item").count() == 0
            assert page.locator(".history-toast").count() == 0
            assert page.get_by_text("Fictional menu is ready.").count() == 0
            print("PASS: Chromium journey, chat isolation, immediate history clearing and one-second success toast")
        except Exception:
            if "second_tab" in locals() and not second_tab.is_closed():
                print("SECOND_TAB_DIAGNOSTIC_BODY:", second_tab.locator("body").inner_text()[-1800:])
            print("BROWSER_DIAGNOSTIC_AGENT_CALLS:", agent_calls)
            print("BROWSER_DIAGNOSTIC_URL:", page.url)
            print("BROWSER_DIAGNOSTIC_BODY:", page.locator("body").inner_text()[-3000:])
            page.screenshot(path="/tmp/naija-browser-failure.png", full_page=True)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
