"""Bounded live Bedrock turns through Chromium, production Next and the real agent."""
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright
from scripts.stage1_agent_journey import register
from scripts.test_browser_customer_journey import require_isolated

LOG = Path("/tmp/stage1-live-provider.log")
RESULTS = Path("/tmp/stage1-live-results.json")


@contextmanager
def provider(mode):
    with LOG.open("a") as output:
        process = subprocess.Popen([sys.executable, "-m", "scripts.stage1_live_server"],
            stdout=output, stderr=subprocess.STDOUT,
            env={**os.environ, "STAGE1_SDK_BASELINE": "1" if mode == "baseline" else "0"})
        try:
            with httpx.Client(verify="/tmp/naija-proxy-test.crt", timeout=2) as client:
                for _ in range(60):
                    if process.poll() is not None:
                        raise RuntimeError("Isolated live provider exited before readiness")
                    try:
                        if client.get("https://localhost:8444/ping").is_success:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.5)
                else:
                    raise RuntimeError("Isolated live provider readiness timed out")
            yield
        finally:
            process.terminate()
            process.wait(timeout=20)


def seed_long_chat(page):
    chat_id = str(uuid4())
    messages = []
    for index in range(12):
        messages.extend([
            {"id": f"seed-user-{index}", "role": "user",
             "content": f"Meal discussion {index}: I enjoy mildly spiced jollof rice and grilled chicken. "
                        "This is a fictional planning conversation. I am not placing an order."},
            {"id": f"seed-assistant-{index}", "role": "assistant",
             "content": "We discussed a family meal with mildly spiced rice, grilled chicken and vegetables. "
                        "No order has been placed and no preference has been saved."},
        ])
    response = page.request.put("http://127.0.0.1:3000/api/backend/chats/" + chat_id,
        data={"title": "Fictional long Stage 1 conversation", "messages": messages})
    assert response.ok, "Could not seed isolated long chat"
    page.reload()
    page.locator("#message-input").wait_for()


def run_turn(page, prompt, scenario):
    page.locator("#message-input").fill(prompt)
    page.evaluate("""() => {
        const count = document.querySelectorAll('.message-row.assistant .bubble').length;
        const t = window.liveTrace = {first: null, firstPaint: null, last: null,
            maxGap: 0, changes: 0, gaps: [], longTasks: []};
        let started = 0;
        let length = 0;
        window.liveObserver?.disconnect();
        window.livePerformanceObserver?.disconnect();
        document.addEventListener('click', () => { started = performance.now(); },
            {once: true, capture: true});
        window.liveObserver = new MutationObserver(() => {
            const rows = document.querySelectorAll('.message-row.assistant .bubble');
            if (rows.length <= count) return;
            const current = rows[rows.length-1].textContent.length;
            if (current <= length || !started) return;
            const elapsed = performance.now() - started;
            if (t.first === null) {
                t.first = elapsed;
                requestAnimationFrame(() => requestAnimationFrame(() => {
                    t.firstPaint = performance.now() - started;
                }));
            }
            if (t.last !== null) {
                t.gaps.push(elapsed - t.last);
                t.maxGap = Math.max(t.maxGap, elapsed - t.last);
            }
            t.last = elapsed;
            t.changes++;
            length = current;
        });
        window.liveObserver.observe(document.querySelector('.conversation'),
            {subtree: true, childList: true, characterData: true});
        if (PerformanceObserver.supportedEntryTypes.includes('longtask')) {
            window.livePerformanceObserver = new PerformanceObserver(entries => {
                for (const entry of entries.getEntries()) {
                    if (started && entry.startTime >= started) t.longTasks.push(entry.duration);
                }
            });
            window.livePerformanceObserver.observe({entryTypes: ['longtask']});
        }
    }""")
    with page.expect_console_message(
        predicate=lambda msg: msg.text.startswith("[Naija Bites latency]"), timeout=120000
    ) as message:
        page.get_by_role("button", name="Send message").click()
    arrived = message.value.args[-1].json_value()
    page.wait_for_function("window.liveTrace.first !== null && window.liveTrace.firstPaint !== null")
    visible = page.locator(".message-row.assistant .bubble").last.inner_text()
    assert visible.strip(), "Live reply was empty"
    render = page.evaluate("window.liveTrace")
    assert arrived["text_events"] > 1 and render["changes"] > 1, "Live response was not progressive"
    records = [json.loads(line.split("STAGE1_SERVER ", 1)[1]) for line in LOG.read_text().splitlines()
               if "STAGE1_SERVER " in line]
    server = next(row for row in records if row["request_id"] == arrived["request_id"])
    assert server.get("provider_first_text_ms"), "Missing Bedrock first-text measurement"
    assert bool(server["menu_tool_ms"]) == (scenario == "menu"), "Unexpected menu tool execution"
    if scenario == "long":
        assert len(visible.split()) >= 120, "Long scenario did not produce enough text"
    if scenario == "menu":
        assert "rice" in visible.lower(), "Menu result missing rice"
    return {**arrived, **server,
        "first_dom_ms": render["first"], "first_paint_estimate_ms": render["firstPaint"],
        "max_dom_gap_ms": render["maxGap"], "dom_updates": render["changes"],
        "long_task_count": len(render["longTasks"]),
        "max_long_task_ms": max(render["longTasks"], default=0),
        "first_text_delivery_ms": arrived["first_text_epoch_ms"] - server["server_first_text_epoch_ms"],
        "word_count": len(visible.split())}


def main():
    require_isolated()
    previous = Path("/tmp/stage1-prior/stage1-live-results.json")
    results = json.loads(previous.read_text())["runs"] if previous.exists() else []
    previous_budget = Path("/tmp/stage1-prior/stage1-live-budget.json")
    if previous_budget.exists():
        Path("/tmp/stage1-live-budget.json").write_text(previous_budget.read_text())
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        pages = {mode: register(browser) for mode in ("baseline", "optimized")}
        scenarios = [
            ("simple", "Hello! Please greet me in one short sentence without looking anything up."),
            ("menu", "Show the current available rice dishes and prices. Please check the actual menu. Do not change my cart."),
            ("long", "Describe a fictional Nigerian family meal in about 220 words, focusing on flavours and presentation. "
                     "This is creative writing, so no menu lookup or other tools are needed. I am not placing an order."),
            ("followup", "Based on our earlier discussion, remind me which rice and chicken I enjoy in two sentences. "
                         "Use the conversation only, without tools or saving preferences."),
        ]
        try:
            for index, (scenario, prompt) in enumerate(scenarios):
                order = ("baseline", "optimized") if index % 2 == 0 else ("optimized", "baseline")
                for mode in order:
                    existing = [row for row in results if row["mode"] == mode and row["scenario"] == scenario]
                    if {row["temperature"] for row in existing} == {"process_cold", "warm"}:
                        continue
                    results = [row for row in results if row not in existing]
                    page = pages[mode]
                    if scenario == "followup":
                        seed_long_chat(page)
                    with provider(mode):
                        for temperature in ("process_cold", "warm"):
                            row = run_turn(page, prompt, scenario)
                            row.update(mode=mode, scenario=scenario, temperature=temperature)
                            results.append(row)
                            RESULTS.write_text(json.dumps({"runs": results}, indent=2))
                            print("STAGE1_LIVE_RUN", json.dumps(row), flush=True)
            for mode in ("baseline", "optimized"):
                for scenario, _ in scenarios:
                    rows = [row for row in results if row["mode"] == mode and row["scenario"] == scenario]
                    summary = {}
                    for key in ("agent_init_ms", "first_token_ms", "first_dom_ms",
                                "first_text_delivery_ms", "max_dom_gap_ms", "total_ms"):
                        values = sorted(row[key] for row in rows)
                        summary[key] = {"p50": statistics.median(values),
                                        "p95": values[math.ceil(len(values) * .95) - 1]}
                    print("STAGE1_LIVE_SUMMARY", mode, scenario, json.dumps(summary), flush=True)
        finally:
            RESULTS.write_text(json.dumps({"runs": results,
                "budget": json.loads(Path("/tmp/stage1-live-budget.json").read_text())
                    if Path("/tmp/stage1-live-budget.json").exists() else None}, indent=2))
            browser.close()


if __name__ == "__main__":
    main()
