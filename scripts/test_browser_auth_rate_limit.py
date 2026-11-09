"""Chromium regression for auth/me rate limiting and redundant preflight.

Runs only against disposable local backend and fictional customers.
"""
import os
import re
from uuid import uuid4

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3000"


def main():
    if (os.getenv("NAIJA_BITES_ENV") != "development"
            or os.getenv("NAIJA_BITES_TEST_MOCK_PAYMENTS") != "1"
            or os.getenv("DATABASE_URL")
            or not os.getenv("NAIJA_BITES_DATA_DIR", "").startswith("/tmp/")):
        raise RuntimeError("Refusing auth regression outside isolated development CI")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()
        checks = []
        agent_headers = []

        def rate_limited_auth(route):
            checks.append(route.request.url)
            route.fulfill(status=429, content_type="text/plain",
                          headers={"Retry-After": "60"}, body="Too Many Requests")

        def mock_agent(route):
            agent_headers.append(route.request.headers)
            newline = chr(10)
            events = [
                'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"Fictional response"}',
                'data: {"type":"RUN_FINISHED"}',
            ]
            route.fulfill(status=200, content_type="text/event-stream",
                          body=(newline + newline).join(events) + newline + newline)

        page.route("**/api/backend/auth/me", rate_limited_auth)
        page.route("**/api/backend/agent", mock_agent)
        try:
            page.goto(BASE + "/", wait_until="domcontentloaded")
            page.get_by_role("heading", name="Welcome back").wait_for(timeout=30000)
            page.get_by_text("Too many requests. Please try again in about 60 seconds.").wait_for()
            assert len(checks) == 1, checks
            for _ in range(12):
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_timeout(350)
            assert len(checks) == 1, "429 on auth/me triggered repeated checks"

            # Rate-limited session probing must not prevent a real login.
            email = f"fictional-rate-limit-{uuid4().hex[:12]}@example.test"
            page.get_by_role("button", name="Create an account").click()
            page.locator('input[name="name"]').fill("Fictional Rate Limit")
            page.locator('input[name="email"]').fill(email)
            page.locator('input[name="password"]').fill("Unique-Fictional-Password-123456")
            page.get_by_role("button", name="Create account").click()
            page.locator("#message-input").wait_for(timeout=30000)
            page.get_by_role("button", name=re.compile("Browse the menu")).click()
            page.get_by_text("Fictional response").wait_for(timeout=30000)
            assert agent_headers, "Chat agent request not issued"
            assert agent_headers[-1].get("x-naija-bites-expected-customer"), (
                "Mutation lost atomic customer identity safeguard")
            assert len(checks) == 1, (
                "The application still preflights each mutation using auth/me")
            # A stale or switched-tab account must be refused atomically
            # by FastAPI, without relying on any preliminary /auth/me call.
            stale_write = page.evaluate("""async () => {
              const response = await fetch('/api/backend/checkout/dismiss-payment', {
                method: 'POST', credentials: 'include',
                headers: {
                  'Content-Type': 'application/json',
                  'X-Naija-Bites-Expected-Customer': '999999999',
                },
                body: JSON.stringify({order_id: 1}),
              });
              return {status: response.status, body: await response.json()};
            }""")
            assert stale_write["status"] == 409, stale_write
            assert stale_write["body"]["detail"] == (
                "Account changed. Refresh before continuing."), stale_write

            print("PASS: auth/me 429 cooldown, no focus storm, login works, "
                  "mutations preserve expected-customer server guard")
        except Exception:
            page.screenshot(path="/tmp/naija-auth-rate-limit-failure.png", full_page=True)
            print("AUTH_ME_COUNT=", len(checks))
            print("BROWSER_BODY=", page.locator("body").inner_text()[-1000:])
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
