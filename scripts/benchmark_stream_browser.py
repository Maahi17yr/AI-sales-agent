"""Deterministic browser A/B for SSE burst handling, never live Bedrock or payments.

Measures the real Next.js chat UI using equal simulated agent streams.
The dev-only baseline flag exercises the prior per-delta React update path.
"""
import json
import statistics
import math
from uuid import uuid4

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3000"
DELTAS = 700
TEXT = "bite " * DELTAS



def run_one(page, mode: str, run: int) -> dict:
    path = "/?stream_render_baseline=1" if mode == "baseline" else "/"
    page.goto(BASE + path, wait_until="domcontentloaded")
    page.locator("#message-input").wait_for(timeout=30000)
    page.get_by_role("button", name="New chat").click()
    page.wait_for_function("document.querySelectorAll('.message-row.assistant').length === 0")
    page.evaluate("""() => {
      window.__streamMeasure = {start: 0, first: null, last: null, maxGap: 0, size: 0, mutationCount: 0};
      if (window.__streamObserver) window.__streamObserver.disconnect();
      window.__streamObserver = new MutationObserver(() => {
        const m = window.__streamMeasure;
        const all = document.querySelectorAll('.message-row.assistant .bubble');
        if (!all.length || !m.start) return;
        const text = all[all.length - 1].textContent || '';
        if (!text.includes('bite') || text.length <= m.size) return;
        const elapsed = performance.now() - m.start;
        if (m.first === null) m.first = elapsed;
        if (m.last !== null) m.maxGap = Math.max(m.maxGap, elapsed - m.last);
        m.last = elapsed;
        m.size = text.length;
        m.mutationCount++;
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
      return {first_visible_ms:m.first, total_visible_ms:m.last, max_gap_ms:m.maxGap,
              mutations:m.mutationCount, text};
    }""")
    assert result["text"] == TEXT, "SSE text was lost or duplicated"
    assert result["first_visible_ms"] is not None, "First render was not observed"
    return {key: result[key] for key in ("first_visible_ms", "total_visible_ms", "max_gap_ms", "mutations")}


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()
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
            for index in range(20):
                order = ("baseline", "batched") if index % 2 == 0 else ("batched", "baseline")
                for mode in order:
                    measurements[mode].append(run_one(page, mode, index))
            summary = {}
            for mode, runs in measurements.items():
                summary[mode] = {
                    name + "_p50": round(statistics.median(r[name] for r in runs), 2)
                    for name in ("first_visible_ms", "total_visible_ms", "max_gap_ms", "mutations")
                }
                for name in ("first_visible_ms", "total_visible_ms", "max_gap_ms", "mutations"):
                    ordered = sorted(run[name] for run in runs)
                    summary[mode][name + "_p95"] = round(ordered[math.ceil(len(ordered) * .95) - 1], 2)
                assert min(run["mutations"] for run in runs) > 1, "Response did not grow progressively"
                summary[mode]["total_visible_ms_max"] = round(
                    max(r["total_visible_ms"] for r in runs), 2)
            print("STREAM_RENDER_AB_RESULT=" + json.dumps(summary, sort_keys=True), flush=True)
            old = summary["baseline"]
            new = summary["batched"]
            reduction = 100 * (old["total_visible_ms_p50"] -
                               new["total_visible_ms_p50"]) / old["total_visible_ms_p50"]
            print(f"STREAM_RENDER_MEDIAN_TOTAL_REDUCTION_PERCENT={reduction:.2f}", flush=True)
            assert new["mutations_p50"] < old["mutations_p50"], (
                "No measured reduction in DOM updates: release blocked")
            assert new["total_visible_ms_p50"] <= old["total_visible_ms_p50"] * 1.05, (
                "Visible completion regressed more than 5%: release blocked")
            assert new["first_visible_ms_p50"] - old["first_visible_ms_p50"] <= 25, (
                "First visible character delayed by over 25 ms")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
