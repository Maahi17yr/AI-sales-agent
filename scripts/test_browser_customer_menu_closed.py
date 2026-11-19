"""Isolated seven-viewport regression: food menu starts closed for each customer session."""
import os
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from core.services import auth_service

BASE = "http://127.0.0.1:3000"


def main():
    if (os.getenv("NAIJA_BITES_ENV") != "development"
            or os.getenv("NAIJA_BITES_TEST_MOCK_PAYMENTS") != "1"
            or os.getenv("DATABASE_URL")
            or not os.getenv("NAIJA_BITES_DATA_DIR", "").startswith("/tmp/naija-bites-chromium-isolated")):
        raise RuntimeError("Customer UI regression must use isolated development database.")

    password = "Fictional-Menu-Default-Password-2026"
    email = "closed-menu-" + uuid4().hex[:12] + "@example.test"
    auth_service.register_customer("Fictional Menu Customer", email, password)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width, height in ((320, 700), (390, 850), (430, 932), (768, 1024),
                              (1024, 768), (1366, 850), (1920, 1080)):
            ctx = browser.new_context(viewport={"width": width, "height": height})
            page = ctx.new_page()
            page.goto(BASE, wait_until="domcontentloaded")
            page.locator('input[name="email"]').fill(email)
            page.locator('input[name="password"]').fill(password)
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.locator("#message-input").wait_for(timeout=30000)

            toggle = page.get_by_role("button", name="Show menu and order summary")
            expect(toggle).to_have_attribute("aria-expanded", "false")
            assert page.locator(".food-panel").count() == 1
            assert "food-panel-open" not in (page.locator(".food-panel").get_attribute("class") or "")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 3"), width

            toggle.click()
            expect(page.get_by_role("button", name="Hide menu and order summary")).to_have_attribute("aria-expanded", "true")
            expect(page.locator(".food-panel")).to_have_class("food-panel food-panel-open")
            page.get_by_role("button", name="Close menu", exact=True).click()
            expect(page.get_by_role("button", name="Show menu and order summary")).to_have_attribute("aria-expanded", "false")

            # Closing the modal/backdrop is required before accessing the chat header.
            page.get_by_role("button", name="Show menu and order summary").click()
            page.get_by_role("button", name="Close menu", exact=True).click()
            page.locator("button.new-chat").click()
            expect(page.get_by_role("button", name="Show menu and order summary")).to_have_attribute("aria-expanded", "false")
            page.reload(wait_until="domcontentloaded")
            page.locator("#message-input").wait_for(timeout=30000)
            expect(page.get_by_role("button", name="Show menu and order summary")).to_have_attribute("aria-expanded", "false")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 3"), width
            page.screenshot(path=f"/tmp/naija-ui-customer-full-chat-{width}.png", full_page=True)
            ctx.close()
        browser.close()
    print("PASS: food menu closed by default, manual toggle, new chat and refresh at seven viewports")


if __name__ == "__main__":
    main()
