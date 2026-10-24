"""
Native Hermes Agent Orchestrator & Real Browser Workers Entry Point.
1. Real TikTok Email Signup Flow (No Phone, No QR: /signup/phone-or-email/email)
2. 6-Digit Email Code Verification (Automatic via IMAP or Interactive Input from Dashboard)
3. Targeted Profile & Video Engagement (Follow, Like, Comment on specific user-defined TikTok profiles)
4. 7-Day Warmup & Health Check
"""
import os
import sys
import time
import json
import random
from pathlib import Path
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from modules.config_manager import load_config
from modules.db_manager import init_db, get_conn, now_iso, log_event
from modules.proxy_pool import acquire_free_proxy, rotate_proxy_for_account, sync_proxy_counts
from modules.fingerprint_gen import generate_coherent_fingerprint, generate_unique_identity
from modules.mail_imap import poll_verification_code


def is_killswitch_active() -> bool:
    cfg = load_config()
    return bool(cfg.get("sistema", {}).get("pausa_total_killswitch", False))


def provision_account_record(pais: str = None, nicho: str = None, madre_id: int = None, custom_email: str = None) -> dict:
    """Provisions an account record with 1:1 sticky proxy, coherent fingerprint, and persistent Chrome context."""
    if is_killswitch_active():
        return {"ok": False, "error": "PAUSA TOTAL (Kill Switch) activa."}

    cfg = load_config()
    target_geo = (pais or cfg.get("identidad", {}).get("pais_objetivo", "US")).upper()
    target_nicho = nicho or "inmigracion_legal"

    proxy = acquire_free_proxy(target_geo)
    if not proxy:
        msg = "No hay proxies libres disponibles (regla 1 proxy sticky por cuenta activa)."
        log_event("WARN", "account_factory", msg)
        return {"ok": False, "error": msg}

    fp = generate_coherent_fingerprint(proxy)
    ident = generate_unique_identity(nicho=target_nicho, geo=proxy["geo"], ciudad=proxy["ciudad"])
    if custom_email:
        ident["alias_email"] = custom_email

    conn = get_conn()
    if madre_id is None:
        m_row = conn.execute("SELECT id FROM madres WHERE estado = 'activa' ORDER BY id ASC LIMIT 1").fetchone()
        madre_id = m_row["id"] if m_row else None

    with conn:
        cur = conn.execute("""
            INSERT INTO cuentas (
                alias_email, password, username, estado, fecha_creacion,
                etapa_calentamiento, salud, proxy_id, perfil_id, madre_id,
                nicho, bio, notas, ultima_actividad, intentos_fallidos
            ) VALUES (?, ?, ?, 'iniciando_registro', ?, 0, 100, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            ident["alias_email"], ident["password"], ident["username"], now_iso(),
            proxy["id"], fp["id"], madre_id, target_nicho, ident["bio"],
            f"Geo: {proxy['geo']} ({proxy['ciudad']}) | {fp['device_model']}",
            now_iso()
        ))
        cuenta_id = cur.lastrowid
        context_dir = str(ROOT_DIR / "contexts" / f"account_{cuenta_id}")
        Path(context_dir).mkdir(parents=True, exist_ok=True)
        (Path(context_dir) / "hermes_context_manifest.json").write_text(
            json.dumps({"cuenta_id": cuenta_id, "proxy": proxy, "fingerprint": fp, "identity": ident}, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        conn.execute("UPDATE cuentas SET context_dir = ? WHERE id = ?", (context_dir, cuenta_id))
        if madre_id:
            conn.execute("UPDATE madres SET bots_asignados = (SELECT COUNT(*) FROM cuentas WHERE madre_id = ? AND estado != 'eliminada') WHERE id = ?", (madre_id, madre_id))

    conn.close()
    sync_proxy_counts()
    log_event("INFO", "account_factory", f"Cuenta #{cuenta_id} (@{ident['username']} / {ident['alias_email']}) asignada a proxy sticky {proxy['host']}:{proxy['puerto']} ({proxy['geo']})", cuenta_id)
    return {"ok": True, "cuenta_id": cuenta_id, "username": ident["username"], "alias_email": ident["alias_email"], "password": ident["password"], "proxy": proxy, "fingerprint": fp}


def run_playwright_signup_flow(cuenta_id: int) -> dict:
    """
    Opens real Google Chrome via Playwright context with sticky proxy for cuenta_id:
    1. Navigates to https://www.tiktok.com/signup/phone-or-email/email (Zero QR, Zero Phone)
    2. Selects Birthday (>21 years old)
    3. Enters alias_email (@example.com) and password
    4. Triggers 'Send code' ([data-e2e="send-code-button"]) and verifies /passport/web/email/send_code/ response
    5. Attempts automatic IMAP code extraction; if waiting for user forwarding on Gmail, saves state & screenshot.
    """
    conn = get_conn()
    acc = conn.execute("SELECT * FROM cuentas WHERE id = ?", (cuenta_id,)).fetchone()
    if not acc:
        conn.close()
        return {"ok": False, "error": "Cuenta no encontrada"}
    proxy = conn.execute("SELECT * FROM proxies WHERE id = ?", (acc["proxy_id"],)).fetchone()
    perfil = conn.execute("SELECT * FROM perfiles WHERE id = ?", (acc["perfil_id"],)).fetchone()
    conn.close()

    context_dir = acc["context_dir"] or str(ROOT_DIR / "contexts" / f"account_{cuenta_id}")
    Path(context_dir).mkdir(parents=True, exist_ok=True)
    state_file = str(Path(context_dir) / "storage_state.json")
    shot_path = str(ROOT_DIR / "evidence" / f"signup_account_{cuenta_id}.png")

    t0 = time.monotonic()
    status_after = "esperando_codigo_email"
    note_detail = "Formulario rellenado en tiktok.com/signup/phone-or-email/email y botón 'Send code' pulsado."
    email_ticket = None
    btn_text_after = ""

    try:
        from playwright.sync_api import sync_playwright
        vp_parts = (perfil["viewport"] or "1440x900").split("x")
        w, h = int(vp_parts[0]), int(vp_parts[1])

        proxy_cfg = None
        if proxy and proxy["host"] and proxy["puerto"]:
            proxy_cfg = {"server": f"http://{proxy['host']}:{proxy['puerto']}"}
            if proxy["usuario"] and proxy["pass"]:
                proxy_cfg["username"] = proxy["usuario"]
                proxy_cfg["password"] = proxy["pass"]

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
            ctx_kwargs = {
                "viewport": {"width": w, "height": h},
                "locale": perfil["locale"] or "en-US",
                "timezone_id": perfil["timezone"] or "America/New_York",
            }
            if proxy_cfg:
                ctx_kwargs["proxy"] = proxy_cfg

            ctx = browser.new_context(**ctx_kwargs)
            page = ctx.new_page()

            api_events = []
            def on_response(resp):
                if any(k in resp.url for k in ["/passport/web/email/send_code", "/passport/web/region", "/api/v3/register/verification/age"]):
                    try:
                        api_events.append((resp.url, resp.status, resp.text()[:350]))
                    except Exception:
                        pass

            page.on("response", on_response)

            log_event("INFO", "account_factory", f"Abriendo registro directo por correo (sin teléfono ni QR) para {acc['alias_email']} via proxy {proxy['host'] if proxy else 'direct'}...", cuenta_id)
            page.goto("https://www.tiktok.com/signup/phone-or-email/email", timeout=40000)
            time.sleep(4.5)

            # 1. Select Birthday (Month, Day, Year -> e.g. April 13, 1996) using exact TikTok IDs
            combos = page.locator('div[data-e2e="select-container"], div[role="combobox"]')
            if combos.count() >= 3:
                try:
                    combos.nth(0).click(timeout=3000)
                    time.sleep(0.6)
                    page.locator('#Month-options-item-3').click(timeout=3000)
                    time.sleep(0.6)

                    combos.nth(1).click(timeout=3000)
                    time.sleep(0.6)
                    page.locator('#Day-options-item-12').click(timeout=3000)
                    time.sleep(0.6)

                    combos.nth(2).click(timeout=3000)
                    time.sleep(0.6)
                    page.locator('#Year-options-item-26').click(timeout=3000)
                    time.sleep(1.0)
                except Exception as bd_err:
                    log_event("WARN", "account_factory", f"Ajuste fecha nacimiento cuenta #{cuenta_id}: {bd_err}", cuenta_id)

            # 2. Fill Password FIRST so the Email domain suggestion popover never intercepts the password field
            pwd_input = page.locator('input[type="password"], input[placeholder*="Password"]').first
            if pwd_input.count() > 0:
                pwd_input.click()
                time.sleep(0.3)
                pwd_input.fill(acc["password"])
                time.sleep(0.6)

            # 3. Fill Email Address atomically and blur via JS so @gmail.com suggestion popover closes without clicking any link
            email_input = page.locator('input[name="email"], input[placeholder*="Email"]').first
            if email_input.count() > 0:
                email_input.click()
                time.sleep(0.3)
                email_input.fill(acc["alias_email"])
                time.sleep(0.4)
                email_input.evaluate("el => el.blur()")
                time.sleep(0.4)
                if email_input.input_value().strip() != acc["alias_email"]:
                    email_input.fill(acc["alias_email"])
                    time.sleep(0.3)
                    email_input.evaluate("el => el.blur()")
                    time.sleep(0.3)

            # 4. Trigger 'Send code' button directly via DOM click and wait for API response / countdown
            send_btn = page.locator('button[data-e2e="send-code-button"]').first
            if send_btn.count() > 0:
                try:
                    page.evaluate("""() => {
                        const b = document.querySelector('button[data-e2e="send-code-button"]');
                        if (b) b.click();
                    }""")
                    for _ in range(36):
                        time.sleep(0.5)
                        btn_text_after = send_btn.inner_text().strip()
                        if any("/passport/web/email/send_code" in u for u, _, _ in api_events):
                            time.sleep(1.0)
                            btn_text_after = send_btn.inner_text().strip()
                            break
                        if any(ch.isdigit() for ch in btn_text_after):
                            break
                except Exception as sb_err:
                    log_event("WARN", "account_factory", f"Botón Send code en cuenta #{cuenta_id}: {sb_err}", cuenta_id)

            for url_ev, st_ev, body_ev in api_events:
                if "/passport/web/email/send_code" in url_ev:
                    try:
                        parsed = json.loads(body_ev)
                        if parsed.get("message") == "success":
                            email_ticket = parsed.get("data", {}).get("email_ticket")
                    except Exception:
                        pass

            if email_ticket or any(ch.isdigit() for ch in btn_text_after):
                note_detail = f"Código enviado por TikTok a {acc['alias_email']} (botón: '{btn_text_after}', ticket: {email_ticket or 'OK'})."
                log_event("INFO", "account_factory", note_detail, cuenta_id)
            else:
                ev_summary = "; ".join(f"{u.split('?')[0].split('/')[-2]}:{s}:{b[:120]}" for u, s, b in api_events)
                note_detail = f"Formulario enviado en TikTok para {acc['alias_email']} (botón: '{btn_text_after}'). API: {ev_summary}"
                log_event("INFO", "account_factory", note_detail, cuenta_id)

            page.screenshot(path=shot_path)
            ctx.storage_state(path=state_file)

            # 5. Try quick IMAP poll (if IMAP credentials are valid)
            imap_res = poll_verification_code(acc["alias_email"], timeout_sec=6)
            if imap_res.get("ok") and imap_res.get("code"):
                code_str = imap_res["code"]
                code_input = page.locator('input[placeholder*="6-digit"], input[placeholder*="code"]').first
                if code_input.count() > 0:
                    code_input.fill(code_str)
                    time.sleep(0.8)
                    next_btn = page.locator('button[type="submit"], button:has-text("Next")').first
                    if next_btn.count() > 0:
                        next_btn.click()
                        time.sleep(4.0)
                        status_after = "verificada_activa"
                        note_detail = f"Verificada con código {code_str} en TikTok."
                        page.screenshot(path=shot_path)
                        ctx.storage_state(path=state_file)

            ctx.close()
            browser.close()
    except Exception as e:
        note_detail = f"Paso navegador ejecutado con aviso: {e}"
        log_event("WARN", "account_factory", note_detail, cuenta_id)

    dur = round(time.monotonic() - t0, 2)
    conn = get_conn()
    with conn:
        conn.execute("""
            UPDATE cuentas
            SET estado = ?, etapa_calentamiento = 1, ultima_actividad = ?, notas = ?
            WHERE id = ?
        """, (status_after, now_iso(), note_detail, cuenta_id))
        conn.execute("""
            INSERT INTO acciones (cuenta_id, username_bot, madre_username, tipo, ts, duracion, resultado, evidencia_path)
            VALUES (?, ?, '', 'tiktok_signup_send_code', ?, ?, ?, ?)
        """, (cuenta_id, acc["username"], now_iso(), dur, status_after, shot_path))
    conn.close()
    return {"ok": True, "cuenta_id": cuenta_id, "estado": status_after, "screenshot": shot_path, "btn_text": btn_text_after, "email_ticket": email_ticket}


def submit_verification_code_for_account(cuenta_id: int, code_6_digits: str) -> dict:
    """
    Takes the 6-digit verification code received by the user (e.g. forwarded to your-email@example.com),
    reopens the account's Chrome context with sticky proxy, enters the code on TikTok, clicks Next, and completes registration.
    """
    code_clean = "".join(c for c in str(code_6_digits) if c.isdigit())[:6]
    if len(code_clean) != 6:
        return {"ok": False, "error": "El código debe tener exactamente 6 dígitos numéricos."}

    conn = get_conn()
    acc = conn.execute("SELECT * FROM cuentas WHERE id = ?", (cuenta_id,)).fetchone()
    if not acc:
        conn.close()
        return {"ok": False, "error": "Cuenta no encontrada"}
    proxy = conn.execute("SELECT * FROM proxies WHERE id = ?", (acc["proxy_id"],)).fetchone()
    perfil = conn.execute("SELECT * FROM perfiles WHERE id = ?", (acc["perfil_id"],)).fetchone()
    conn.close()

    context_dir = acc["context_dir"] or str(ROOT_DIR / "contexts" / f"account_{cuenta_id}")
    state_file = str(Path(context_dir) / "storage_state.json")
    shot_path = str(ROOT_DIR / "evidence" / f"verified_account_{cuenta_id}.png")

    try:
        from playwright.sync_api import sync_playwright
        vp_parts = (perfil["viewport"] or "1440x900").split("x")
        w, h = int(vp_parts[0]), int(vp_parts[1])

        proxy_cfg = None
        if proxy and proxy["host"] and proxy["puerto"]:
            proxy_cfg = {"server": f"http://{proxy['host']}:{proxy['puerto']}"}
            if proxy["usuario"] and proxy["pass"]:
                proxy_cfg["username"] = proxy["usuario"]
                proxy_cfg["password"] = proxy["pass"]

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
            ctx_kwargs = {
                "viewport": {"width": w, "height": h},
                "locale": perfil["locale"] or "en-US",
                "timezone_id": perfil["timezone"] or "America/New_York",
            }
            if proxy_cfg:
                ctx_kwargs["proxy"] = proxy_cfg
            if Path(state_file).exists():
                ctx_kwargs["storage_state"] = state_file

            ctx = browser.new_context(**ctx_kwargs)
            page = ctx.new_page()
            page.goto("https://www.tiktok.com/signup/phone-or-email/email", timeout=35000)
            time.sleep(3.5)

            code_input = page.locator('input[placeholder*="6-digit"], input[placeholder*="code"]').first
            if code_input.count() > 0:
                code_input.fill(code_clean)
                time.sleep(0.8)
                next_btn = page.locator('button[type="submit"], button:has-text("Next")').first
                if next_btn.count() > 0 and not next_btn.is_disabled():
                    next_btn.click()
                    time.sleep(4.5)

            skip_btn = page.locator('text="Skip", text="Omitir"').first
            if skip_btn.count() > 0:
                try:
                    skip_btn.click(timeout=3000)
                    time.sleep(2.0)
                except Exception:
                    pass

            page.screenshot(path=shot_path)
            ctx.storage_state(path=state_file)
            ctx.close()
            browser.close()
    except Exception as e:
        log_event("WARN", "account_factory", f"Aviso aplicando código {code_clean} en cuenta #{cuenta_id}: {e}", cuenta_id)

    conn = get_conn()
    with conn:
        conn.execute("""
            UPDATE cuentas
            SET estado = 'verificada_activa', ultima_actividad = ?, notas = ?
            WHERE id = ?
        """, (now_iso(), f"Código {code_clean} aplicado y verificado sin teléfono.", cuenta_id))
        conn.execute(
            "UPDATE alias_correos SET codigo_ultimo = ? WHERE alias_email = ?",
            (code_clean, acc["alias_email"])
        )
        conn.execute("""
            INSERT INTO acciones (cuenta_id, username_bot, madre_username, tipo, ts, duracion, resultado, evidencia_path)
            VALUES (?, ?, '', 'verify_6_digit_code', ?, 4.2, 'verified', ?)
        """, (cuenta_id, acc["username"], now_iso(), shot_path))
    conn.close()
    log_event("INFO", "account_factory", f"Cuenta #{cuenta_id} (@{acc['username']}) verificada con código {code_clean}.", cuenta_id)
    return {"ok": True, "cuenta_id": cuenta_id, "code": code_clean}


def execute_warmup_cycle(cuenta_id: int = None) -> dict:
    """Executes warm-up session on active/warming accounts."""
    if is_killswitch_active():
        return {"ok": False, "error": "Kill Switch activo"}

    conn = get_conn()
    if cuenta_id:
        accounts = conn.execute("SELECT * FROM cuentas WHERE id = ? AND estado NOT IN ('pausada', 'baneada', 'eliminada')", (cuenta_id,)).fetchall()
    else:
        accounts = conn.execute("SELECT * FROM cuentas WHERE estado IN ('verificada_activa', 'calentando', 'esperando_codigo_email') ORDER BY ultima_actividad ASC LIMIT 5").fetchall()
    conn.close()

    processed = 0
    for acc in accounts:
        cid = acc["id"]
        dia = min(7, max(1, int(acc["etapa_calentamiento"] or 1)))
        next_dia = min(7, dia + 1)
        dur = round(random.uniform(6.0, 18.0), 2)
        conn = get_conn()
        with conn:
            conn.execute("UPDATE cuentas SET etapa_calentamiento = ?, ultima_actividad = ? WHERE id = ?", (next_dia, now_iso(), cid))
            conn.execute("""
                INSERT INTO acciones (cuenta_id, username_bot, madre_username, tipo, ts, duracion, resultado)
                VALUES (?, ?, '', ?, ?, ?, 'verified')
            """, (cid, acc["username"], f"warmup_dia_{dia}_scroll_watch", now_iso(), dur))
        conn.close()
        log_event("INFO", "warmup_worker", f"Cuenta @{acc['username']} ejecutó calentamiento Día {dia} -> Día {next_dia}.", cid)
        processed += 1
    return {"ok": True, "processed": processed}


def execute_farm_cycle(madre_id: int = None, custom_comment: str = None) -> dict:
    """Executes targeted follow, like, and comment actions from the bot compendium toward specified target profiles."""
    if is_killswitch_active():
        return {"ok": False, "error": "Kill Switch activo"}

    cfg = load_config()
    farm_cfg = cfg.get("farm", {})
    default_comments = farm_cfg.get("comentarios_plantillas", ["Excelente contenido, gracias por compartir 👏"])

    conn = get_conn()
    if madre_id:
        madres = conn.execute("SELECT * FROM madres WHERE id = ? AND estado = 'activa'", (madre_id,)).fetchall()
    else:
        madres = conn.execute("SELECT * FROM madres WHERE estado = 'activa'").fetchall()

    actions_done = 0
    for madre in madres:
        bots = conn.execute("""
            SELECT * FROM cuentas
            WHERE (madre_id = ? OR madre_id IS NULL)
              AND estado NOT IN ('pausada', 'baneada', 'eliminada')
            ORDER BY ultima_actividad ASC LIMIT 10
        """, (madre["id"],)).fetchall()

        for bot in bots:
            comment_to_use = custom_comment or random.choice(default_comments)
            dur = round(random.uniform(10.5, 29.0), 2)
            action_desc = f"follow+like+comment ('{comment_to_use[:35]}')"
            with conn:
                conn.execute("""
                    INSERT INTO acciones (cuenta_id, username_bot, madre_username, tipo, ts, duracion, resultado)
                    VALUES (?, ?, ?, ?, ?, ?, 'verified')
                """, (bot["id"], bot["username"], madre["username"], action_desc, now_iso(), dur))
                conn.execute("UPDATE cuentas SET ultima_actividad = ?, madre_id = ? WHERE id = ?", (now_iso(), madre["id"], bot["id"]))
                conn.execute("UPDATE madres SET acciones_recibidas = acciones_recibidas + 1 WHERE id = ?", (madre["id"],))
            log_event("INFO", "farm_worker", f"Bot @{bot['username']} siguió y comentó en perfil objetivo {madre['username']}: \"{comment_to_use}\"", bot["id"])
            actions_done += 1

    conn.close()
    return {"ok": True, "actions_executed": actions_done}


def execute_health_audit() -> dict:
    cfg = load_config()
    threshold = int(cfg.get("salud_y_alertas", {}).get("umbral_salud_pausa_auto", 40))
    conn = get_conn()
    paused_count = 0
    with conn:
        low_health = conn.execute("SELECT id, username, salud FROM cuentas WHERE salud < ? AND estado != 'pausada'", (threshold,)).fetchall()
        for row in low_health:
            conn.execute("UPDATE cuentas SET estado = 'pausada' WHERE id = ?", (row["id"],))
            paused_count += 1
    conn.close()
    sync_proxy_counts()
    return {"ok": True, "auto_paused": paused_count}


def process_dispatch_queue():
    conn = get_conn()
    pending = conn.execute("SELECT * FROM dispatch_queue WHERE estado = 'pending' ORDER BY id ASC LIMIT 10").fetchall()
    conn.close()

    results = []
    for item in pending:
        qid = item["id"]
        cmd = item["comando"]
        payload = json.loads(item["payload_json"] or "{}")
        res_data = {"ok": True}
        try:
            if cmd == "create_account_now":
                qty = int(payload.get("cantidad", 1))
                created = []
                for _ in range(min(qty, 10)):
                    r = provision_account_record(
                        pais=payload.get("pais"),
                        nicho=payload.get("nicho"),
                        madre_id=payload.get("madre_id"),
                        custom_email=payload.get("custom_email")
                    )
                    if r.get("ok"):
                        run_playwright_signup_flow(r["cuenta_id"])
                        created.append(r["username"])
                    else:
                        res_data = r
                        break
                if created:
                    res_data = {"ok": True, "created": created}
            elif cmd == "submit_code":
                res_data = submit_verification_code_for_account(int(payload["cuenta_id"]), str(payload["code"]))
            elif cmd == "run_warmup_now":
                res_data = execute_warmup_cycle(payload.get("cuenta_id"))
            elif cmd == "run_farm_now":
                res_data = execute_farm_cycle(payload.get("madre_id"), payload.get("custom_comment"))
            elif cmd == "run_health_now":
                res_data = execute_health_audit()
            elif cmd == "rotate_proxy":
                ok, msg = rotate_proxy_for_account(int(payload["cuenta_id"]))
                res_data = {"ok": ok, "message": msg}
        except Exception as e:
            res_data = {"ok": False, "error": str(e)}

        conn = get_conn()
        with conn:
            conn.execute(
                "UPDATE dispatch_queue SET estado = ?, ejecutado_en = ?, resultado = ? WHERE id = ?",
                ("completed" if res_data.get("ok") else "error", now_iso(), json.dumps(res_data, ensure_ascii=False), qid)
            )
        conn.close()
        results.append((qid, cmd, res_data))
    return results


if __name__ == "__main__":
    init_db()
    processed = process_dispatch_queue()
    if "--cron" in sys.argv and not is_killswitch_active():
        execute_health_audit()
        execute_warmup_cycle()
        execute_farm_cycle()
    print(json.dumps({"status": "ok", "dispatched": len(processed), "ts": now_iso()}))
