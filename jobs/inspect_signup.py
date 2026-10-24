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
    # 1. Inspect TikTok email signup page
    page.goto("https://www.tiktok.com/signup/phone-or-email/email", timeout=30000)
    time.sleep(3)
    print("=== TIKTOK SIGNUP EMAIL PAGE ===")
    print("URL:", page.url)
    inputs = page.locator("input").evaluate_all("els => els.map(e => ({name: e.name, placeholder: e.placeholder, type: e.type}))")
    print("Inputs:", inputs)
    text_snippet = page.locator("body").inner_text()
    print("Visible text:", " | ".join([l.strip() for l in text_snippet.splitlines() if l.strip()][:20]))

    # 2. Inspect Porkbun login
    page2 = ctx.new_page()
    page2.goto("https://porkbun.com/account/login", timeout=30000)
    time.sleep(3)
    print("\n=== PORKBUN LOGIN PAGE ===")
    print("URL:", page2.url, "Title:", page2.title())
    pb_text = page2.locator("body").inner_text()
    print("Porkbun text:", " | ".join([l.strip() for l in pb_text.splitlines() if l.strip()][:15]))
    browser.close()
