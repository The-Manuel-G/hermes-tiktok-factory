"""IMAP Catch-All Mail Worker & DNS Security Auditor for example.com."""
import re
import time
import imaplib
import email
from email.header import decode_header
import urllib.request
import json
from .config_manager import load_config
from .db_manager import get_conn, now_iso, log_event

CODE_6_REGEX = re.compile(r"(?:code|c[oó]digo|verification|verificaci[oó]n|is|es|\b)[:\s]*([0-9]{6})\b", re.IGNORECASE)
FALLBACK_6_REGEX = re.compile(r"\b([0-9]{6})\b")
VERIFY_LINK_REGEX = re.compile(r"https?://[^\s\"'<>]+tiktok\.com[^\s\"'<>]+", re.IGNORECASE)


def extract_tiktok_code_and_link(raw_text: str) -> dict:
    """Extracts 6-digit verification code and any verification link from email body/subject."""
    if not raw_text:
        return {"code": None, "link": None}

    # Strip HTML tags for cleaner matching
    clean_text = re.sub(r"<[^>]+>", " ", raw_text)
    code = None
    m = CODE_6_REGEX.search(clean_text)
    if m:
        code = m.group(1)
    else:
        # Exclude common years or hex colors like 202600 or 000000
        for candidate in FALLBACK_6_REGEX.findall(clean_text):
            if candidate not in ("000000", "111111", "123456") and not candidate.startswith("202"):
                code = candidate
                break

    link_m = VERIFY_LINK_REGEX.search(raw_text)
    link = link_m.group(0) if link_m else None
    return {"code": code, "link": link}


def record_alias_failure(alias_email: str) -> bool:
    """Increments failure count for an alias; marks it as quemado if >= max_fallos_por_alias."""
    cfg = load_config()
    max_fallos = int(cfg.get("correos", {}).get("max_fallos_por_alias", 3))
    conn = get_conn()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO alias_correos (alias_email, usado, fallos, quemado, creado_en) VALUES (?, 1, 0, 0, ?)",
            (alias_email, now_iso())
        )
        conn.execute("UPDATE alias_correos SET fallos = fallos + 1 WHERE alias_email = ?", (alias_email,))
        row = conn.execute("SELECT fallos FROM alias_correos WHERE alias_email = ?", (alias_email,)).fetchone()
        fallos = row["fallos"] if row else 1
        quemado = 1 if fallos >= max_fallos else 0
        if quemado:
            conn.execute("UPDATE alias_correos SET quemado = 1 WHERE alias_email = ?", (alias_email,))
            log_event("WARN", "mail_worker", f"Alias {alias_email} marcado como QUEMADO tras {fallos} fallos.")
    conn.close()
    return bool(quemado)


def check_domain_dns_security(domain: str = None) -> dict:
    """Checks SPF, DMARC, and MX records for the configured domain via DNS-over-HTTPS (Cloudflare/Google)."""
    cfg = load_config()
    dom = domain or cfg.get("correos", {}).get("dominio", "example.com")
    result = {"domain": dom, "mx": False, "spf": False, "dmarc": False, "dkim_hint": "Configurado en Porkbun", "details": []}

    def query_txt(name: str, qtype: str = "TXT") -> list[str]:
        try:
            url = f"https://dns.google/resolve?name={name}&type={qtype}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [ans.get("data", "").strip('"') for ans in data.get("Answer", [])]
        except Exception:
            return []

    mx_records = query_txt(dom, "MX")
    if mx_records:
        result["mx"] = True
        result["details"].append(f"MX activos: {', '.join(mx_records[:2])}")

    txt_records = query_txt(dom, "TXT")
    for t in txt_records:
        if "v=spf1" in t.lower():
            result["spf"] = True
            result["details"].append(f"SPF: {t}")

    dmarc_records = query_txt(f"_dmarc.{dom}", "TXT")
    for d in dmarc_records:
        if "v=dmarc1" in d.lower():
            result["dmarc"] = True
            result["details"].append(f"DMARC: {d}")

    return result


def test_imap_connection() -> dict:
    """Tests live IMAP connection with current config.yaml credentials."""
    cfg = load_config().get("correos", {})
    host = cfg.get("imap_host", "imap.porkbun.com")
    port = int(cfg.get("imap_port", 993))
    user = cfg.get("imap_user", "")
    pwd = cfg.get("imap_pass", "")

    try:
        mail = imaplib.IMAP4_SSL(host, port, timeout=8)
        mail.login(user, pwd)
        status, messages = mail.select("INBOX")
        total = int(messages[0]) if messages and messages[0] else 0
        mail.logout()
        return {"ok": True, "host": host, "user": user, "inbox_messages": total, "message": f"Conectado OK a {host} ({total} correos en INBOX)"}
    except Exception as e:
        return {"ok": False, "host": host, "user": user, "inbox_messages": 0, "message": f"Aviso IMAP ({user} en {host}): {e}"}


def poll_verification_code(alias_email: str, timeout_sec: int = None) -> dict:
    """Polls the catch-all IMAP mailbox for a TikTok 6-digit code sent to alias_email."""
    cfg = load_config().get("correos", {})
    host = cfg.get("imap_host", "imap.porkbun.com")
    port = int(cfg.get("imap_port", 993))
    user = cfg.get("imap_user", "")
    pwd = cfg.get("imap_pass", "")
    wait_total = timeout_sec if timeout_sec is not None else int(cfg.get("timeout_codigo_seg", 180))

    deadline = time.monotonic() + wait_total
    alias_lower = alias_email.lower().strip()

    while time.monotonic() < deadline:
        try:
            mail = imaplib.IMAP4_SSL(host, port, timeout=10)
            mail.login(user, pwd)
            mail.select("INBOX")
            status, data = mail.search(None, "UNSEEN")
            if status == "OK" and data and data[0]:
                msg_ids = data[0].split()
                for mid in reversed(msg_ids[-25:]):
                    res, msg_data = mail.fetch(mid, "(RFC822)")
                    if res != "OK" or not msg_data:
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    to_hdr = str(msg.get("To", "") + " " + msg.get("Delivered-To", "") + " " + msg.get("X-Original-To", "")).lower()
                    from_hdr = str(msg.get("From", "")).lower()
                    subj_raw = msg.get("Subject", "")
                    decoded_parts = decode_header(subj_raw)
                    subj = "".join(
                        p.decode(enc or "utf-8", errors="ignore") if isinstance(p, bytes) else str(p)
                        for p, enc in decoded_parts
                    )

                    if alias_lower in to_hdr or "tiktok" in from_hdr or "tiktok" in subj.lower():
                        body = subj + "\n"
                        if msg.is_multipart():
                            for part in msg.walk():
                                ctype = part.get_content_type()
                                if ctype in ("text/plain", "text/html"):
                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        body += payload.decode(part.get_content_charset() or "utf-8", errors="ignore") + "\n"
                        else:
                            payload = msg.get_payload(decode=True)
                            if payload:
                                body += payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")

                        extracted = extract_tiktok_code_and_link(body)
                        if extracted["code"]:
                            # Mark as Seen so code is never reused
                            mail.store(mid, "+FLAGS", "\\Seen")
                            mail.logout()
                            conn = get_conn()
                            with conn:
                                conn.execute(
                                    "UPDATE alias_correos SET codigo_ultimo = ? WHERE alias_email = ?",
                                    (extracted["code"], alias_email)
                                )
                            conn.close()
                            log_event("INFO", "mail_worker", f"Código {extracted['code']} extraído para {alias_email}")
                            return {"ok": True, "code": extracted["code"], "link": extracted["link"]}
            mail.logout()
        except Exception as e:
            log_event("WARN", "mail_worker", f"Intento lectura IMAP para {alias_email}: {e}")
            if wait_total <= 10:
                break
        time.sleep(5)

    return {"ok": False, "code": None, "link": None, "error": "Timeout esperando código de 6 dígitos en buzón IMAP"}
