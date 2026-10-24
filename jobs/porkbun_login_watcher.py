import os
import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ctx_dir = r"C:\Users\ariel\AppData\Local\hermes\hermes-tiktok-factory\contexts\porkbun_session"
shot_dir = r"C:\Users\ariel\AppData\Local\hermes\hermes-tiktok-factory\evidence"
otp_file = r"C:\Users\ariel\AppData\Local\hermes\hermes-tiktok-factory\porkbun_otp.txt"
status_file = r"C:\Users\ariel\AppData\Local\hermes\hermes-tiktok-factory\porkbun_status.txt"

if os.path.exists(otp_file):
    os.remove(otp_file)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=ctx_dir,
        channel="chrome",
        headless=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/192.0.2.1 Safari/537.36",
        viewport={"width": 1280, "height": 1200},
        args=["--mute-audio", "--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    page.goto("https://porkbun.com/account/login?redir=%2Faccount%2Femailhosting%2Fexample.com", timeout=25000)
    page.wait_for_timeout(1500)

    page.evaluate("""() => {
        document.querySelectorAll('[id*="cookie"], [class*="cookie"]').forEach(el => el.style.display = 'none');
    }""")

    def handle_resp(r):
        if "login_2023" in r.url or "api/user" in r.url:
            try:
                print("PB_API_JSON:", r.url, r.status, r.text(), flush=True)
            except Exception as e:
                print("PB_API_ERR:", e, flush=True)

    page.on("response", handle_resp)

    if "login" in page.url:
        page.locator("#loginUsername").fill("REPLACE_WITH_LOCAL_CREDENTIAL")
        page.locator("#loginPassword").fill("REPLACE_WITH_LOCAL_CREDENTIAL")
        try:
            rm = page.locator("#rememberMe")
            if rm.count() > 0 and not rm.is_checked():
                rm.check(force=True)
        except Exception:
            pass

        cb = page.locator('[data-role="pork-checkbox"]')
        if cb.count() > 0 and cb.first.is_visible():
            cb.first.click(force=True)
            for _ in range(8):
                page.wait_for_timeout(800)
                if page.locator("#porkcaptcha-token_accountLogin").input_value():
                    break

        page.evaluate("logInExec();")
        page.wait_for_timeout(4500)
        page.screenshot(path=os.path.join(shot_dir, "porkbun_waiting_code.png"))
        print("Porkbun waiting for 2FA/device code on URL:", page.url, flush=True)
        with open(status_file, "w", encoding="utf-8") as f:
            f.write("WAITING_OTP")

        for _ in range(1200):
            if os.path.exists(otp_file):
                code = open(otp_file, "r", encoding="utf-8").read().strip()
                if code:
                    print(f"Entering OTP code: {code}", flush=True)
                    os.remove(otp_file)
                    page.evaluate(f"""(codeStr) => {{
                        const inp = document.getElementById('twoFactorLoginCodeEmailNoCookie') || document.getElementById('twoFactorLoginCodeEmail');
                        if (inp) {{
                            inp.value = codeStr;
                        }}
                        logInExec();
                    }}""", code)
                    page.wait_for_timeout(5000)
                    page.screenshot(path=os.path.join(shot_dir, "porkbun_logged_in.png"))
                    print("After OTP URL:", page.url, flush=True)
                    with open(status_file, "w", encoding="utf-8") as f:
                        f.write(f"LOGGED_IN:{page.url}\n" + page.locator("body").inner_text()[:3000])
                    break
            page.wait_for_timeout(1000)
    ctx.close()
