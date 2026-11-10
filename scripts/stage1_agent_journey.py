"""Chromium -> production Next -> FastAPI -> Strands -> mocked Bedrock -> React."""
import json
import math
import subprocess
import sys
import time
from contextlib import contextmanager

import httpx
import statistics
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright
from scripts.test_browser_customer_journey import require_isolated


@contextmanager
def fresh_provider():
    with open("/tmp/stage1-provider.log", "a") as log:
        proc = subprocess.Popen([sys.executable, "-m", "scripts.stage1_provider_server"],
                                stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(verify="/tmp/naija-proxy-test.crt") as client:
                for _ in range(60):
                    if proc.poll() is not None:
                        raise RuntimeError("Isolated provider exited during startup")
                    try:
                        if client.get("https://localhost:8444/ping").is_success:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.5)
                else:
                    raise RuntimeError("Isolated provider did not become ready")
            yield
        finally:
            proc.terminate()
            proc.wait(timeout=20)


def run_turn(page, text, expected_words):
    page.locator("#message-input").fill(text)
    page.evaluate("""() => {
        const rows = () => document.querySelectorAll('.message-row.assistant .bubble');
        const count = rows().length;
        let start = 0;
        document.addEventListener('click', () => { start = performance.now(); },
            {once: true, capture: true});
        const trace = window.stage1 = {first: null, last: null, gap: 0, changes: 0};
        let size = 0;
        window.stageObserver?.disconnect();
        window.stageObserver = new MutationObserver(() => {
            const bubbles = rows();
            if (bubbles.length <= count) return;
            const current = bubbles[bubbles.length-1].textContent.length;
            if (current <= size) return;
            const now = performance.now() - start;
            trace.first ??= now;
            if (trace.last !== null) trace.gap = Math.max(trace.gap, now - trace.last);
            trace.last = now;
            trace.changes++;
            size = current;
        });
        window.stageObserver.observe(document.querySelector('.conversation'),
            {subtree: true, childList: true, characterData: true});
    }""")
    with page.expect_console_message(predicate=lambda msg: msg.text.startswith("[Naija Bites latency]"), timeout=30000) as log:
        page.get_by_role("button", name="Send message").click()
    event = log.value.args[-1].json_value()
    bubble = page.locator(".message-row.assistant .bubble").last
    page.wait_for_function("""n => {
        const rows = document.querySelectorAll('.message-row.assistant .bubble');
        return rows.length && rows[rows.length-1].textContent.trim().split(/\\s+/).length === n
            && window.stage1.first !== null;
    }""", arg=expected_words)
    assert bubble.inner_text().split() == ["bite"] * expected_words
    trace = page.evaluate("window.stage1")
    assert event["request_id"] and event["text_events"] > 0
    records = [json.loads(line.split("STAGE1_SERVER ", 1)[1]) for line in
               Path("/tmp/stage1-provider.log").read_text().splitlines() if "STAGE1_SERVER " in line]
    server = next(record for record in records if record["request_id"] == event["request_id"])
    assert bool(server["menu_tool_ms"]) == ("browse_menu" in text), server
    delivery = event["first_text_epoch_ms"] - server["server_first_text_epoch_ms"]
    return {**event, **server, "first_text_delivery_ms": delivery, "first_dom_ms": trace["first"], "last_dom_ms": trace["last"],
            "max_dom_gap_ms": trace["gap"], "dom_updates": trace["changes"]}


def main():
    require_isolated()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:3000/")
        page.get_by_role("button", name="Create an account").click()
        page.locator('input[name="name"]').fill("Stage One")
        page.locator('input[name="email"]').fill(f"stage1-{uuid4().hex}@example.test")
        page.locator('input[name="password"]').fill("Stage1-Fictional-Password-25!")
        page.get_by_role("button", name="Create account").click()
        page.locator("#message-input").wait_for()
        for scenario, prompt, count in [
            ("simple", "Stage one simple question.", 10),
            ("menu", "Use browse_menu to show the menu.", 10),
            ("long", "Give me a long response.", 100),
            ("followup", "Following up on our earlier conversation.", 10),
        ]:
            with fresh_provider():
                first = run_turn(page, prompt, count)
                print('STAGE1_PROCESS_COLD', scenario, json.dumps(first), flush=True)
                runs = [run_turn(page, prompt, count) for _ in range(5)]
            print("STAGE1_MOCK_RUNS", scenario, json.dumps(runs), flush=True)
            for key in ("first_token_ms", "first_text_delivery_ms", "first_dom_ms", "max_text_gap_ms", "max_dom_gap_ms", "total_ms"):
                values = sorted(row[key] for row in runs)
                print(scenario, key, "p50", round(statistics.median(values), 1),
                      "p95", round(values[math.ceil(len(values) * .95) - 1], 1))
        browser.close()


if __name__ == "__main__":
    main()
