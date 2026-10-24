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
        locale="en-US"
    )
    page = ctx.new_page()
    page.goto("https://porkbun.com/account/login", timeout=30000)
    time.sleep(3)
    # Find all forms and inputs including inside iframes or shadow DOM
    html_info = page.evaluate("""() => {
        const inputs = Array.from(document.querySelectorAll('input, button, a')).map(el => ({
            tag: el.tagName,
            id: el.id,
            name: el.name,
            type: el.type,
            text: (el.innerText || el.value || '').trim().slice(0, 50)
        }));
        return {url: location.href, inputs};
    }""")
    print("Login DOM:", html_info)
    browser.close()
