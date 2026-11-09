"""Deterministic browser A/B for SSE burst handling, never live Bedrock or payments.

Measures the real Next.js chat UI using equal simulated agent streams.
The dev-only baseline flag exercises the prior per-delta React update path.
"""
import json
import statistics
from uuid import uuid4

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3000"
DELTAS = 700
TEXT = "bite " * DELTAS
EVENTS = (
    "\n\n".join(
        ['data: ' + json.dumps({"type": "TEXT_MESSAGE_CONTENT", "delta": "bite "})
         for _ in range(DELTAS)] +
        ['data: {"type":"RUN_FINISHED"}']
    ) + "\n\n"
)


def run_one(page, mode: str, run: int) -> dict:
    path = "/?stream_render_baseline=1" if mode == "baseline" else "/"
    page.goto(BASE + path, wait_until="domcontentloaded")
    page.locator("#message-input").wait_for(timeout=30000)
    page.evaluate("""() => {
      window.__streamMeasure = {start: 0, first: null, mutationCount: 0};
      if (window.__streamObserver) window.__streamObserver.disconnect();
      window.__streamObserver = new MutationObserver(() => {
        const m = window.__streamMeasure;
        const all = document.querySelectorAll('.message-row.assistant .bubble');
        if (!all.length || !m.start) return;
        m.mutationCount++;
        if (m.first === null && all[all.length - 1].textContent?.includes('bite')) {
          m.first = performance.now() - m.start;
        }
      });
      window.__streamObserver.observe(document.querySelector('.conversation'),
        {subtree:true, childList:true, characterData:true});
    }""")
    page.locator("#message-input").fill(f"benchmark {mode} {run}")
    page.evaluate("window.__streamMeasure.start = performance.now()")
    page.get_by_role("button", name="Send message").click()
    page.wait_for_function("""size => {
      const all = document.querySelectorAll('.message-row.assistant .bubble');
      return all.length > 0 && all[all.length - 1].textContent?.length === size;
    }""", arg=len(TEXT), timeout=30000)
    result = page.evaluate("""() => {
      const m=window.__streamMeasure;
      const all=document.querySelectorAll('.message-row.assistant .bubble');
      const text=all[all.length-1].textContent;
      return {first_visible_ms:m.first, total_visible_ms:performance.now()-m.start,
              mutations:m.mutationCount, text};
    }""")
    assert result["text"] == TEXT, "SSE text was lost or duplicated"
    assert result["first_visible_ms"] is not None, "First render was not observed"
    return {key: result[key] for key in ("first_visible_ms", "total_visible_ms", "mutations")}


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()
        page.route("**/api/backend/agent",
                   lambda route: route.fulfill(status=200, body=EVENTS,
                       content_type="text/event-stream",
                       headers={"Cache-Control": "no-cache"}))
        page.route("https://commons.wikimedia.org/**", lambda route: route.abort())
        try:
            email = f"stream-benchmark-{uuid4().hex[:12]}@example.test"
            page.goto(BASE + "/", wait_until="domcontentloaded")
            page.get_by_role("button", name="Create an account").click()
            page.locator('input[name="name"]').fill("Fictional Stream Benchmark")
            page.locator('input[name="email"]').fill(email)
            page.locator('input[name="password"]').fill("Fictional-Benchmark-Password-12345")
            page.get_by_role("button", name="Create account").click()
            page.locator("#message-input").wait_for(timeout=30000)

            measurements = {"baseline": [], "batched": []}
            for index in range(8):
                order = ("baseline", "batched") if index % 2 == 0 else ("batched", "baseline")
                for mode in order:
                    measurements[mode].append(run_one(page, mode, index))
            summary = {}
            for mode, runs in measurements.items():
                summary[mode] = {
                    name + "_p50": round(statistics.median(r[name] for r in runs), 2)
                    for name in ("first_visible_ms", "total_visible_ms", "mutations")
                }
                summary[mode]["total_visible_ms_max"] = round(
                    max(r["total_visible_ms"] for r in runs), 2)
            print("STREAM_RENDER_AB_RESULT=" + json.dumps(summary, sort_keys=True), flush=True)
            old = summary["baseline"]
            new = summary["batched"]
            reduction = 100 * (old["total_visible_ms_p50"] -
                               new["total_visible_ms_p50"]) / old["total_visible_ms_p50"]
            print(f"STREAM_RENDER_MEDIAN_TOTAL_REDUCTION_PERCENT={reduction:.2f}", flush=True)
            assert new["mutations_p50"] <= old["mutations_p50"], (
                "Frame-coalesced React rendering produced more DOM mutations")
            assert new["total_visible_ms_p50"] < old["total_visible_ms_p50"], (
                "No measured median visual completion improvement: release gate blocked")
            assert new["first_visible_ms_p50"] - old["first_visible_ms_p50"] <= 25, (
                "First visible character degraded by more than one to two frames")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
