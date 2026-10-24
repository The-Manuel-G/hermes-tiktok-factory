import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
state_file = ROOT_DIR / "contexts" / "porkbun_clean_state.json"
shot_path = ROOT_DIR / "evidence" / "porkbun_login_check.png"

with sync_playwright() as p:
    browser = p.chromium.launch(
        channel="chrome",
        headless=False,
        ignore_default_args=["--enable-automation"],
        args=[
            "--mute-audio",
            "--disable-blink-features=AutomationControlled",
            "--window-position=-2400,-2400",
        ],
    )
    ctx = browser.new_context(viewport={"width": 1366, "height": 950})
    page = ctx.new_page()

    api_logs = []
    def on_resp(resp):
        if "porkbun.com" in resp.url and any(k in resp.url for k in ["/api/", "login", "emailhosting", "domains", "captcha", "pork"]):
            try:
                api_logs.append((resp.url, resp.status, resp.text()[:500]))
            except Exception:
                pass

    page.on("response", on_resp)
    page.goto("https://porkbun.com/account/login?redir=%2Faccount%2Femailhosting%2Fexample.com", timeout=35000)
    time.sleep(3.5)

    # Hide cookie banner via JS so it neither intercepts nor reloads
    page.evaluate("""() => {
        document.querySelectorAll('[id*="cookie"], [class*="cookie"], #pbUserPrefs').forEach(el => el.style.display = 'none');
    }""")

    captcha_html = page.evaluate("""() => {
        const el = Array.from(document.querySelectorAll('div')).find(d => (d.innerText || '').includes('Pork Captcha'));
        return el ? el.parentElement.outerHTML.slice(0, 1200) : 'NOT_FOUND';
    }""")
    print("CAPTCHA HTML:", captcha_html)

    page.locator("#loginUsername").fill("REPLACE_WITH_LOCAL_CREDENTIAL")
    time.sleep(0.3)
    page.locator("#loginPassword").fill("REPLACE_WITH_LOCAL_CREDENTIAL")
    time.sleep(0.5)

    # Click Pork Captcha checkbox with force=True and also via JS
    pork_cb = page.locator('text="I\'m not a robot"').first
    if pork_cb.count() > 0:
        pork_cb.click(force=True)
        print("Clicked I'm not a robot (force=True)!")

    time.sleep(2.0)
    page.screenshot(path=str(ROOT_DIR / "evidence" / "porkbun_captcha_clicked.png"))

    btn = page.locator("#accountLoginButton")
    for i in range(20):
        time.sleep(0.5)
        if btn.count() > 0 and not btn.is_disabled():
            print(f"Login button enabled after {(i+1)*0.5}s!")
            break

    print("Login button disabled now?", btn.is_disabled())
    if not btn.is_disabled():
        btn.click(force=True)
        time.sleep(6.0)

    print("Current URL:", page.url)
    body_txt = " | ".join(line.strip() for line in page.locator("body").inner_text().splitlines() if line.strip())
    print("Body preview:", body_txt[:1500])
    for u, s, b in api_logs:
        if "/api/" in u or "emailhosting" in u or "login" in u or "pork" in u:
            print("API:", u, s, b[:300])

    page.screenshot(path=str(shot_path))
    ctx.storage_state(path=str(state_file))
    ctx.close()
    browser.close()
