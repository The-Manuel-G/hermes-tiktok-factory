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
    page.fill("#loginUsername", "REPLACE_WITH_LOCAL_CREDENTIAL")
    page.fill("#loginPassword", "REPLACE_WITH_LOCAL_CREDENTIAL")
    time.sleep(1)
    page.click("#accountLoginButton")
    time.sleep(5)
    print("After Login URL:", page.url)
    print("Visible text:", " | ".join([l.strip() for l in page.locator("body").inner_text().splitlines() if l.strip()][:25]))
    browser.close()
