"""Coherent Anti-Detection Fingerprint & Identity Generator for Hermes Agent."""
import random
import secrets
import string
import hashlib
import json
from .config_manager import load_config
from .db_manager import get_conn, now_iso

GEO_PROFILES = {
    "US": {
        "timezones": ["America/Los_Angeles", "America/New_York", "America/Chicago", "America/Denver"],
        "locale": "en-US",
        "accept_language": "en-US,en;q=0.9,es;q=0.8",
        "ciudades": ["Los Angeles", "Miami", "New York", "Austin", "Santa Clara", "Orem"],
    },
    "GB": {
        "timezones": ["Europe/London"],
        "locale": "en-GB",
        "accept_language": "en-GB,en;q=0.9",
        "ciudades": ["London", "Manchester", "Bristol"],
    },
    "ES": {
        "timezones": ["Europe/Madrid"],
        "locale": "es-ES",
        "accept_language": "es-ES,es;q=0.9,en;q=0.8",
        "ciudades": ["Madrid", "Barcelona", "Valencia"],
    },
    "DE": {
        "timezones": ["Europe/Berlin"],
        "locale": "de-DE",
        "accept_language": "de-DE,de;q=0.9,en;q=0.8",
        "ciudades": ["Frankfurt", "Berlin", "Munich"],
    },
    "PL": {
        "timezones": ["Europe/Warsaw"],
        "locale": "pl-PL",
        "accept_language": "pl-PL,pl;q=0.9,en;q=0.8",
        "ciudades": ["Warsaw", "Krakow", "Gdansk"],
    },
}

USER_AGENTS_POOL = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/192.0.2.1 Safari/537.36", "Windows 11 Desktop Chrome 131", "Win32"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/192.0.2.1 Safari/537.36", "Windows 11 Workstation Chrome 130", "Win32"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/192.0.2.1 Safari/537.36", "MacBook Pro 16 M3 Chrome 131", "MacIntel"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/192.0.2.1 Safari/537.36", "MacBook Air M2 Chrome 130", "MacIntel"),
]

FONTS_POOL = [
    ["Segoe UI", "Calibri", "Arial", "Consolas", "Verdana", "Tahoma"],
    ["Helvetica Neue", "San Francisco", "Arial", "Menlo", "Georgia"],
    ["Arial", "Trebuchet MS", "Segoe UI", "Courier New", "Impact"],
    ["Calibri", "Cambria", "Segoe UI", "Lucida Console", "Franklin Gothic"],
]

FIRST_NAMES = ["sofia", "mateo", "valentina", "lucas", "camila", "daniel", "ELENA".lower(), "marco", "lucia", "alejandro", "paula", "diego", "clara", "adrian", "natalia"]
LAST_NAMES = ["mendez", "castro", "herrera", "vargas", "morales", "ortiz", "silva", "rojas", "navarro", "delgado", "molina", "vega", "campos", "fuentes"]


def generate_coherent_fingerprint(proxy_row: dict = None) -> dict:
    """Generates a unique, geographically coherent browser fingerprint and saves it to DB."""
    cfg = load_config()
    fp_cfg = cfg.get("fingerprint", {})
    resolutions = fp_cfg.get("resoluciones_escritorio", [
        {"width": 1920, "height": 1080},
        {"width": 1440, "height": 900},
        {"width": 1536, "height": 864},
        {"width": 1366, "height": 768},
    ])

    geo = (proxy_row or {}).get("geo") or cfg.get("identidad", {}).get("pais_objetivo", "US")
    geo_meta = GEO_PROFILES.get(geo.upper(), GEO_PROFILES["US"])

    timezone_val = (proxy_row or {}).get("timezone") or random.choice(geo_meta["timezones"])
    locale_val = (proxy_row or {}).get("locale") or geo_meta["locale"]
    ua, device_model, platform = random.choice(USER_AGENTS_POOL)
    res = random.choice(resolutions)
    viewport_str = f"{res['width']}x{res['height']}"

    canvas_seed = secrets.token_hex(8)
    webgl_seed = secrets.token_hex(8)
    android_id = secrets.token_hex(8)
    fuentes = json.dumps(random.choice(FONTS_POOL))

    conn = get_conn()
    with conn:
        cur = conn.execute("""
            INSERT INTO perfiles (user_agent, viewport, timezone, locale, canvas_seed, webgl_seed, fuentes, device_model, android_id, geo, creado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ua, viewport_str, timezone_val, locale_val, canvas_seed, webgl_seed, fuentes, device_model, android_id, geo.upper(), now_iso()))
        perfil_id = cur.lastrowid
    conn.close()

    return {
        "id": perfil_id,
        "user_agent": ua,
        "viewport": viewport_str,
        "width": res["width"],
        "height": res["height"],
        "timezone": timezone_val,
        "locale": locale_val,
        "accept_language": geo_meta["accept_language"],
        "canvas_seed": canvas_seed,
        "webgl_seed": webgl_seed,
        "fuentes": json.loads(fuentes),
        "device_model": device_model,
        "platform": platform,
        "android_id": android_id,
        "geo": geo.upper(),
    }


def generate_unique_identity(nicho: str = "inmigracion_legal", geo: str = "US", ciudad: str = "Los Angeles") -> dict:
    """Generates unique catch-all alias email, strong password, natural username, and niche bio."""
    cfg = load_config()
    dominio = cfg.get("correos", {}).get("dominio", "example.com")
    patron = cfg.get("correos", {}).get("patron_alias", f"tk{{####}}@{dominio}")

    conn = get_conn()
    alias_email = None
    for _ in range(50):
        digits = "".join(random.choices(string.digits, k=4))
        candidate = patron.replace("{####}", digits).replace("{###}", digits[:3])
        if "@" not in candidate:
            candidate = f"{candidate}@{dominio}"
        exists = conn.execute(
            "SELECT 1 FROM alias_correos WHERE alias_email = ? UNION SELECT 1 FROM cuentas WHERE alias_email = ?",
            (candidate, candidate)
        ).fetchone()
        if not exists:
            alias_email = candidate
            break
    if not alias_email:
        alias_email = f"tk{secrets.token_hex(3)}@{dominio}"

    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO alias_correos (alias_email, usado, fallos, quemado, creado_en) VALUES (?, 1, 0, 0, ?)",
            (alias_email, now_iso())
        )

    # Generate human-looking username (avoiding long digit strings)
    fname = random.choice(FIRST_NAMES)
    lname = random.choice(LAST_NAMES)
    num2 = random.randint(11, 98)
    short_nicho = "legal" if "legal" in nicho else "vibes"
    patterns = [
        f"{fname}.{lname}{num2}",
        f"{fname}_{lname}{num2}",
        f"{fname}.{short_nicho}{num2}",
        f"real.{fname}{lname[:4]}{num2}",
    ]
    username = random.choice(patterns)[:18]

    # Ensure strong unique password for TikTok (8-20 chars, upper, lower, digit, special)
    pwd_core = secrets.token_urlsafe(7).replace("-", "x").replace("_", "z")
    password = f"Tk{pwd_core[:8]}!{random.randint(10, 99)}"

    # Bio from template
    bios_cfg = cfg.get("identidad", {}).get("bio_plantillas", {})
    templates = bios_cfg.get(nicho) or bios_cfg.get("lifestyle") or ["{ciudad} 📍 | {nicho}"]
    bio = random.choice(templates).replace("{ciudad}", ciudad or "Los Angeles").replace("{nicho}", nicho.replace("_", " "))

    conn.close()
    return {
        "alias_email": alias_email,
        "username": username,
        "password": password,
        "bio": bio,
        "nicho": nicho,
        "ciudad": ciudad,
    }
