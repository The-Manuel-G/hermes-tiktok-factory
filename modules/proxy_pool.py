"""Sticky Proxy Pool Manager (1 Proxy = 1 Active Account) for Hermes Agent."""
import urllib.request
import socket
from .db_manager import get_conn, now_iso, log_event
from .config_manager import load_config


def sync_proxy_counts():
    conn = get_conn()
    with conn:
        conn.execute("""
            UPDATE proxies SET cuentas_asignadas = (
                SELECT COUNT(*) FROM cuentas
                WHERE cuentas.proxy_id = proxies.id AND cuentas.estado NOT IN ('eliminada', 'baneada')
            )
        """)
    conn.close()


def acquire_free_proxy(preferred_geo: str = None) -> dict | None:
    """Acquires an active proxy respecting max_cuentas_por_proxy (default 1 sticky per account)."""
    sync_proxy_counts()
    cfg = load_config()
    max_per_proxy = int(cfg.get("proxies", {}).get("max_cuentas_por_proxy", 1))
    geo = (preferred_geo or cfg.get("identidad", {}).get("pais_objetivo", "US")).upper()

    conn = get_conn()
    # 1. Try matching preferred geo first
    row = conn.execute("""
        SELECT * FROM proxies
        WHERE activo = 1 AND salud >= 40 AND cuentas_asignadas < ? AND UPPER(geo) = ?
        ORDER BY cuentas_asignadas ASC, salud DESC, id ASC LIMIT 1
    """, (max_per_proxy, geo)).fetchone()

    # 2. Fallback to any available sticky proxy if preferred geo is full
    if not row:
        row = conn.execute("""
            SELECT * FROM proxies
            WHERE activo = 1 AND salud >= 40 AND cuentas_asignadas < ?
            ORDER BY cuentas_asignadas ASC, salud DESC, id ASC LIMIT 1
        """, (max_per_proxy,)).fetchone()

    conn.close()
    return dict(row) if row else None


def check_proxy_health(proxy_id: int, timeout: int = 7) -> tuple[bool, str]:
    """Tests TCP/HTTP reachability of a proxy and updates its health in SQLite."""
    conn = get_conn()
    p = conn.execute("SELECT * FROM proxies WHERE id = ?", (proxy_id,)).fetchone()
    if not p:
        conn.close()
        return False, "Proxy no encontrado"

    host, port, user, pwd = p["host"], int(p["puerto"]), p["usuario"], p["pass"]
    ok = False
    detail = ""
    try:
        proxy_url = f"http://{user}:{pwd}@{host}:{port}" if user else f"http://{host}:{port}"
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        opener = urllib.request.build_opener(handler)
        req = urllib.request.Request("http://ip-api.com/json", headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            ok = resp.status == 200
            detail = f"Conectado OK ({body[:80]})"
    except Exception as e:
        # Fallback to raw TCP socket check
        try:
            with socket.create_connection((host, port), timeout=timeout):
                ok = True
                detail = f"Puerto TCP {host}:{port} abierto y respondiendo"
        except Exception as sock_err:
            ok = False
            detail = f"Fallo conexión: {sock_err}"

    new_health = 100 if ok else max(0, int(p["salud"]) - 35)
    with conn:
        conn.execute(
            "UPDATE proxies SET salud = ?, activo = ?, ultimo_chequeo = ? WHERE id = ?",
            (new_health, 1 if new_health >= 30 else 0, now_iso(), proxy_id)
        )
    conn.close()
    return ok, detail


def rotate_proxy_for_account(cuenta_id: int) -> tuple[bool, str]:
    """Rotates the sticky proxy assigned to an account to a different free proxy."""
    conn = get_conn()
    acc = conn.execute("SELECT * FROM cuentas WHERE id = ?", (cuenta_id,)).fetchone()
    if not acc:
        conn.close()
        return False, "Cuenta no encontrada"

    old_proxy_id = acc["proxy_id"]
    cfg = load_config()
    max_per_proxy = int(cfg.get("proxies", {}).get("max_cuentas_por_proxy", 1))

    new_proxy = conn.execute("""
        SELECT * FROM proxies
        WHERE activo = 1 AND id != ? AND cuentas_asignadas < ?
        ORDER BY cuentas_asignadas ASC, salud DESC LIMIT 1
    """, (old_proxy_id or -1, max_per_proxy)).fetchone()

    if not new_proxy:
        conn.close()
        return False, "No hay otro proxy libre disponible para rotar respetando 1:1 sticky."

    with conn:
        conn.execute("UPDATE cuentas SET proxy_id = ? WHERE id = ?", (new_proxy["id"], cuenta_id))
    conn.close()
    sync_proxy_counts()
    log_event("INFO", "proxy_pool", f"Proxy rotado de #{old_proxy_id} a #{new_proxy['id']} ({new_proxy['host']}:{new_proxy['puerto']})", cuenta_id)
    return True, f"Asignado nuevo proxy {new_proxy['host']}:{new_proxy['puerto']} ({new_proxy['geo']})"
