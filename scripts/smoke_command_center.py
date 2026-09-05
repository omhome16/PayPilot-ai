"""Browser smoke test for the PayPilot Command Center.

Requires a running server (e.g. `uv run uvicorn paypilot.api.app:create_app
--factory --port 8017`) and system Python with playwright installed:

    python scripts/smoke_command_center.py [base_url]

Walks every panel, asserts live content rendered, and prints any console errors.
"""
# ruff: noqa: S101, E501 -- a smoke test is a series of intentional asserts

import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8017"
errors: list[str] = []


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(f"console[{m.type}]: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        page.goto(BASE + "/command")
        page.wait_for_load_state("networkidle")
        assert "PAYPILOT" in page.title() or "PayPilot" in page.content()
        print("✓ command center loaded")

        # Overview: kpis should populate from live APIs
        page.wait_for_selector("#ov-kpis .kpi", timeout=8000)
        print("✓ overview KPIs rendered")

        # Live recovery: fire the cash-crunch scenario and expect an event card
        page.click("button.nav[data-panel=live]")
        page.wait_for_selector("#live-stream", timeout=5000)
        page.click("text=Cash-crunch (₹499)")
        page.wait_for_selector("#live-stream .event", timeout=8000)
        print("✓ live recovery fired & rendered an event")

        # Agent graph: run the trace, expect node steps to reveal
        page.click("button.nav[data-panel=graph]")
        page.click("#g-run")
        page.wait_for_selector("#g-steps .step-line.show", timeout=12000)
        page.wait_for_timeout(2200)  # let the reveal sequence finish
        shown = page.locator("#g-steps .step-line.show").count()
        assert shown >= 3, f"expected >=3 revealed steps, got {shown}"
        print(f"✓ agent graph trace revealed {shown} steps")

        # Outage trace: expect the escalate node
        page.check("#g-outage")
        page.click("#g-run")
        page.wait_for_timeout(3000)
        txt = page.locator("#g-steps").inner_text()
        assert "escalate" in txt.lower() or "human review" in txt.lower()
        print("✓ fail-loud outage trace shows escalation")

        # Voice studio: customer list, start a call (no key → loud escalation)
        page.click("button.nav[data-panel=voice]")
        page.wait_for_selector("#v-customer option", state="attached", timeout=8000)
        page.click("#v-start")
        page.wait_for_timeout(2500)
        calltxt = page.locator("#v-status").inner_text() + " " + page.locator("#v-call").inner_text()
        assert ("no LLM dialogue brain" in calltxt) or ("Listening" in calltxt)
        print("✓ voice studio call opened (fail-loud or live)")

        # Ledger: decisions should exist from fired webhooks
        page.click("button.nav[data-panel=ledger]")
        page.wait_for_selector("#ledger-table table tbody tr", timeout=8000)
        print("✓ ledger rendered decision rows")

        # Memory: table list + a browser open
        page.click("button.nav[data-panel=memory]")
        page.wait_for_selector(".tbl-pill", timeout=8000)
        page.click(".tbl-pill:has-text('customers')")
        page.wait_for_selector("#mem-browser table.data tbody tr", timeout=8000)
        print("✓ memory panel lists tables and browses rows")

        # Webhook lab: fire custom and expect a decision response
        page.click("button.nav[data-panel=lab]")
        page.fill("input[name=amount]", "99900")
        page.fill("input[name=attempt]", "1")  # revoked mandate: fresh episode → win-back link
        page.select_option("select[name=mode]", "mandate_revoked")
        page.click("button[type=submit]")
        page.wait_for_selector("#lab-response .event", timeout=10000)
        resp = page.locator("#lab-response").inner_text()
        print("  [lab response preview] " + resp[:220].replace("\n", " "))
        assert "payment_link" in resp
        print("✓ webhook lab fired and returned the agent decision")

        # Eval: run a tiny sweep
        page.click("button.nav[data-panel=eval]")
        page.click("#ev-run")
        page.wait_for_selector("#ev-results .kpi", timeout=60000)
        print("✓ quick eval returned numbers")

        page.screenshot(path="data/shots/command_center.png", full_page=True)
        print("✓ screenshot saved to data/shots/command_center.png")
        browser.close()

    if errors:
        print("\nCONSOLE ERRORS:")
        for e in errors:
            print("  " + e)
        return 1
    print("\nAll panels green, zero console errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
