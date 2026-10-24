import sys
import time
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

with sync_playwright() as p:
    browser = p.chromium.launch(
        channel="chrome",
        headless=True,
        args=["--mute-audio", "--disable-blink-features=AutomationControlled"]
    )
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/192.0.2.1 Safari/537.36",
        viewport={"width": 1440, "height": 900},
        locale="en-US",
        timezone_id="America/Los_Angeles"
    )
    page = ctx.new_page()
    page.goto("https://www.tiktok.com/signup/phone-or-email/email", timeout=30000)
    time.sleep(3)
    dom = page.evaluate("""() => {
        const els = Array.from(document.querySelectorAll('div[role="combobox"], div[aria-haspopup="listbox"], input, button'));
        return els.map(e => ({
            tag: e.tagName,
            role: e.getAttribute('role'),
            e2e: e.getAttribute('data-e2e'),
            name: e.getAttribute('name'),
            placeholder: e.getAttribute('placeholder'),
            text: (e.innerText || '').trim().slice(0, 40),
            disabled: e.disabled
        }));
    }""")
    for item in dom:
        print(item)
    browser.close()
