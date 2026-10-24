"""
Hermes Agent — TikTok Account Factory & Bot Farm Control Dashboard Server.
Role: Passive Visualization & Action Dispatcher for Hermes Agent (Port 8085 + Cloudflare Tunnel).
"""
import os
import sys
import csv
import io
import json
import time
import hashlib
import subprocess
import threading
import http.server
import socketserver
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from modules.config_manager import load_config, save_config_raw, update_config_dict, CONFIG_PATH
from modules.db_manager import init_db, get_conn, now_iso, log_event
from modules.proxy_pool import sync_proxy_counts, check_proxy_health, rotate_proxy_for_account
from modules.mail_imap import check_domain_dns_security, test_imap_connection, extract_tiktok_code_and_link
from jobs.hermes_orchestrator_job import process_dispatch_queue, provision_account_record, run_playwright_signup_flow

PORT = 8085
WEB_DIR = Path(__file__).resolve().parent / "web"
CLOUDFLARED_BIN = Path(r"C:\Users\ariel\AppData\Local\hermes\tools\cloudflared.exe")
if not CLOUDFLARED_BIN.exists():
    CLOUDFLARED_BIN = Path(r"C:\Users\ariel\.gemini\antigravity\scratch\cloudflared.exe")
PUBLIC_URL_FILE = ROOT_DIR / "PUBLIC_URL.txt"


def trigger_hermes_orchestrator_async():
    """Invokes Hermes Agent Orchestrator job in background to process newly queued commands."""
    def _worker():
        try:
            process_dispatch_queue()
        except Exception as e:
            log_event("ERROR", "orchestrator", f"Error despachando cola a Hermes: {e}")
    threading.Thread(target=_worker, daemon=True).start()


def build_dashboard_state() -> dict:
    """Reads full real-time state from hermes_factory.sqlite and config.yaml."""
    sync_proxy_counts()
    cfg = load_config()
    conn = get_conn()

    cuentas = [dict(r) for r in conn.execute("""
        SELECT c.*, p.host AS proxy_host, p.puerto AS proxy_puerto, p.geo AS proxy_geo, p.ciudad AS proxy_ciudad,
               pf.device_model, pf.viewport, pf.timezone AS fp_timezone, pf.locale AS fp_locale,
               m.username AS madre_username
        FROM cuentas c
        LEFT JOIN proxies p ON c.proxy_id = p.id
        LEFT JOIN perfiles pf ON c.perfil_id = pf.id
        LEFT JOIN madres m ON c.madre_id = m.id
        ORDER BY c.id DESC
    """).fetchall()]

    proxies = [dict(r) for r in conn.execute("SELECT * FROM proxies ORDER BY id ASC").fetchall()]
    madres = [dict(r) for r in conn.execute("SELECT * FROM madres ORDER BY id ASC").fetchall()]
    acciones = [dict(r) for r in conn.execute("SELECT * FROM acciones ORDER BY id DESC LIMIT 80").fetchall()]
    logs = [dict(r) for r in conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 120").fetchall()]
    aliases = [dict(r) for r in conn.execute("SELECT * FROM alias_correos ORDER BY id DESC LIMIT 60").fetchall()]
    snapshots = [dict(r) for r in conn.execute("SELECT id, version_hash, aplicado_en, nota FROM config_snapshot ORDER BY id DESC LIMIT 10").fetchall()]
    queue_items = [dict(r) for r in conn.execute("SELECT * FROM dispatch_queue ORDER BY id DESC LIMIT 20").fetchall()]
    conn.close()

    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_cuentas = len(cuentas)
    activas = sum(1 for c in cuentas if c["estado"] in ("activa", "activa_bot", "calentando"))
    enfermas = sum(1 for c in cuentas if int(c["salud"] or 100) < 50 or c["estado"] in ("pausada", "revision", "baneada"))
    creadas_hoy = sum(1 for c in cuentas if str(c["fecha_creacion"] or "").startswith(today_prefix))
    pendientes = sum(1 for c in cuentas if c["estado"] in ("pendiente", "creando"))

    public_url = ""
    if PUBLIC_URL_FILE.exists():
        public_url = PUBLIC_URL_FILE.read_text(encoding="utf-8", errors="ignore").strip()

    raw_yaml = CONFIG_PATH.read_text(encoding="utf-8") if CONFIG_PATH.exists() else ""

    return {
        "ts": now_iso(),
        "public_url": public_url,
        "local_url": f"http://localhost:{PORT}",
        "killswitch": bool(cfg.get("sistema", {}).get("pausa_total_killswitch", False)),
        "fabrica_activa": bool(cfg.get("fabrica", {}).get("activa", True)),
        "kpis": {
            "totales": total_cuentas,
            "activas": activas,
            "enfermas": enfermas,
            "creadas_hoy": creadas_hoy,
            "pendientes": pendientes,
            "proxies_libres": sum(1 for p in proxies if p["activo"] and p["cuentas_asignadas"] < int(cfg.get("proxies", {}).get("max_cuentas_por_proxy", 1))),
            "proxies_totales": len(proxies),
            "acciones_hoy": sum(1 for a in acciones if str(a["ts"] or "").startswith(today_prefix)),
        },
        "cuentas": cuentas,
        "proxies": proxies,
        "madres": madres,
        "acciones": acciones,
        "logs": logs,
        "aliases": aliases,
        "snapshots": snapshots,
        "dispatch_queue": queue_items,
        "config": cfg,
        "raw_yaml": raw_yaml,
    }


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, format, *args):
        pass

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path

        if route == "/api/state":
            self._send_json(build_dashboard_state())
            return

        if route == "/api/mail/check":
            dns_info = check_domain_dns_security()
            imap_info = test_imap_connection()
            self._send_json({"ok": True, "dns": dns_info, "imap": imap_info})
            return

        if route == "/api/export/csv":
            conn = get_conn()
            rows = conn.execute("""
                SELECT c.id, c.username, c.alias_email, c.password, c.estado, c.etapa_calentamiento,
                       c.salud, c.nicho, c.bio, p.host AS proxy_host, p.puerto AS proxy_port, p.geo AS proxy_geo,
                       pf.device_model, pf.viewport, pf.timezone, m.username AS cuenta_madre, c.fecha_creacion
                FROM cuentas c
                LEFT JOIN proxies p ON c.proxy_id = p.id
                LEFT JOIN perfiles pf ON c.perfil_id = pf.id
                LEFT JOIN madres m ON c.madre_id = m.id
                ORDER BY c.id ASC
            """).fetchall()
            conn.close()

            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                "ID", "Username", "Email_Alias", "Password", "Estado", "Etapa_Warmup",
                "Salud", "Nicho", "Bio", "Proxy_Host", "Proxy_Port", "Geo",
                "Device_Model", "Viewport", "Timezone", "Cuenta_Madre", "Fecha_Creacion"
            ])
            for r in rows:
                writer.writerow(list(r))

            csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="hermes_cuentas_export.csv"')
            self.end_headers()
            self.wfile.write(csv_bytes)
            return

        if route == "/" or route == "/index.html":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="ignore") if length > 0 else "{}"
        try:
            payload = json.loads(raw_body) if raw_body else {}
        except Exception:
            payload = {}

        # 1. Enqueue command for Hermes Agent (Factory, Warmup, Farm, Health)
        if route == "/api/dispatch":
            cmd = payload.get("comando", "")
            conn = get_conn()
            with conn:
                cur = conn.execute(
                    "INSERT INTO dispatch_queue (comando, payload_json, estado, creado_en) VALUES (?, ?, 'pending', ?)",
                    (cmd, json.dumps(payload, ensure_ascii=False), now_iso())
                )
                qid = cur.lastrowid
            conn.close()
            log_event("INFO", "dashboard", f"Orden '{cmd}' enviada a Hermes Agent (Cola #{qid})")
            # Process synchronously so UI reflects instant results
            process_dispatch_queue()
            conn = get_conn()
            row = conn.execute("SELECT * FROM dispatch_queue WHERE id = ?", (qid,)).fetchone()
            conn.close()
            res = json.loads(row["resultado"]) if row and row["resultado"] else {"ok": True}
            self._send_json({"ok": res.get("ok", True), "queue_id": qid, "result": res})
            return

        # 2. Global Kill Switch / Pause Factory
        if route == "/api/control/killswitch":
            enable = bool(payload.get("enable", False))
            update_config_dict(lambda c: c.setdefault("sistema", {}) and c["sistema"].__setitem__("pausa_total_killswitch", enable))
            log_event("WARN" if enable else "INFO", "orchestrator", f"KILL SWITCH GLOBAL {'ACTIVADO' if enable else 'DESACTIVADO'} desde Dashboard.")
            self._send_json({"ok": True, "killswitch": enable})
            return

        if route == "/api/control/toggle_factory":
            activa = bool(payload.get("activa", True))
            def _upd(c):
                c.setdefault("fabrica", {})["activa"] = activa
                if "cuentas_por_dia" in payload:
                    c["fabrica"]["cuentas_por_dia"] = int(payload["cuentas_por_dia"])
                if "pais_objetivo" in payload:
                    c.setdefault("identidad", {})["pais_objetivo"] = payload["pais_objetivo"]
            update_config_dict(_upd)
            log_event("INFO", "account_factory", f"Parámetros de fábrica actualizados (activa={activa}).")
            self._send_json({"ok": True, "activa": activa})
            return

        # 3. Account individual actions (pausar, reanudar, eliminar, rotar proxy, retry failed)
        if route == "/api/account/action":
            cid = int(payload.get("cuenta_id", 0))
            act = payload.get("action", "")
            conn = get_conn()
            with conn:
                if act == "pause":
                    conn.execute("UPDATE cuentas SET estado = 'pausada' WHERE id = ?", (cid,))
                    log_event("INFO", "dashboard", f"Cuenta #{cid} pausada manualmente.", cid)
                elif act == "resume":
                    conn.execute("UPDATE cuentas SET estado = 'calentando', salud = MAX(salud, 75), intentos_fallidos = 0 WHERE id = ?", (cid,))
                    log_event("INFO", "dashboard", f"Cuenta #{cid} reanudada.", cid)
                elif act == "delete":
                    conn.execute("UPDATE cuentas SET estado = 'eliminada', proxy_id = NULL WHERE id = ?", (cid,))
                    log_event("WARN", "dashboard", f"Cuenta #{cid} eliminada y su proxy sticky liberado.", cid)
                elif act == "assign_madre":
                    mid = int(payload.get("madre_id", 1))
                    conn.execute("UPDATE cuentas SET madre_id = ? WHERE id = ?", (mid, cid))
            conn.close()
            if act == "rotate_proxy":
                ok, msg = rotate_proxy_for_account(cid)
                self._send_json({"ok": ok, "message": msg})
                return
            sync_proxy_counts()
            self._send_json({"ok": True})
            return

        # 4. Retry all failed accounts
        if route == "/api/control/retry_failed":
            conn = get_conn()
            with conn:
                cur = conn.execute("UPDATE cuentas SET estado = 'calentando', intentos_fallidos = 0, salud = 85 WHERE estado IN ('pausada', 'revision', 'error')")
                count = cur.rowcount
            conn.close()
            log_event("INFO", "orchestrator", f"Reintento global activado para {count} cuentas en pausa/revisión.")
            self._send_json({"ok": True, "retried": count})
            return

        # 5. Add or Manage Mother Accounts (Farm)
        if route == "/api/farm/madre":
            act = payload.get("action", "add")
            conn = get_conn()
            with conn:
                if act == "add":
                    uname = str(payload.get("username", "")).strip()
                    if not uname.startswith("@"):
                        uname = "@" + uname
                    nicho = payload.get("nicho", "inmigracion_legal")
                    obj = int(payload.get("objetivo_seguidores", 5000))
                    conn.execute("""
                        INSERT OR IGNORE INTO madres (username, nicho, bots_asignados, objetivo_seguidores, acciones_recibidas, horario_activo, estado, creada_en)
                        VALUES (?, ?, 0, ?, 0, '09:00-22:00', 'activa', ?)
                    """, (uname, nicho, obj, now_iso()))
                    log_event("INFO", "farm_worker", f"Nueva cuenta madre registrada: {uname} (Objetivo: {obj} seguidores)")
                elif act == "assign_all":
                    mid = int(payload.get("madre_id", 1))
                    conn.execute("UPDATE cuentas SET madre_id = ? WHERE estado != 'eliminada'", (mid,))
                    conn.execute("UPDATE madres SET bots_asignados = (SELECT COUNT(*) FROM cuentas WHERE madre_id = ? AND estado != 'eliminada') WHERE id = ?", (mid, mid))
                    log_event("INFO", "farm_worker", f"Todos los bots activos fueron asignados a la cuenta madre #{mid}.")
            conn.close()
            self._send_json({"ok": True})
            return

        # 6. Add / Check / Toggle Proxies
        if route == "/api/proxies/manage":
            act = payload.get("action", "")
            if act == "check_all":
                conn = get_conn()
                pids = [r["id"] for r in conn.execute("SELECT id FROM proxies").fetchall()]
                conn.close()
                for pid in pids:
                    check_proxy_health(pid, timeout=4)
                log_event("INFO", "proxy_pool", f"Verificados los {len(pids)} proxies del pool.")
                self._send_json({"ok": True})
                return
            elif act == "add_bulk":
                lines = str(payload.get("raw_lines", "")).strip().splitlines()
                added = 0
                conn = get_conn()
                with conn:
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        clean = line.replace("http://", "").replace("socks5://", "").split("{")[0]
                        parts = clean.split(":")
                        if len(parts) >= 2:
                            host, port = parts[0], int(parts[1])
                            user = parts[2] if len(parts) > 2 else ""
                            pwd = parts[3] if len(parts) > 3 else ""
                            geo = payload.get("geo", "US").upper()
                            conn.execute("""
                                INSERT OR IGNORE INTO proxies (host, puerto, usuario, pass, protocolo, geo, ciudad, activo, salud, cuentas_asignadas, ultimo_chequeo)
                                VALUES (?, ?, ?, ?, 'http', ?, '', 1, 100, 0, ?)
                            """, (host, port, user, pwd, geo, now_iso()))
                            added += 1
                conn.close()
                log_event("INFO", "proxy_pool", f"Agregados {added} proxies nuevos al pool.")
                self._send_json({"ok": True, "added": added})
                return

        # 7. Save & Hot-Reload config.yaml
        if route == "/api/config/save":
            raw_yaml = payload.get("raw_yaml", "")
            ok, msg = save_config_raw(raw_yaml)
            if ok:
                vhash = hashlib.sha256(raw_yaml.encode("utf-8")).hexdigest()[:12]
                conn = get_conn()
                with conn:
                    conn.execute(
                        "INSERT INTO config_snapshot (version_hash, yaml_content, aplicado_en, nota) VALUES (?, ?, ?, ?)",
                        (vhash, raw_yaml, now_iso(), payload.get("nota", "Actualizado desde Dashboard"))
                    )
                conn.close()
                log_event("INFO", "config", f"config.yaml actualizado en caliente (hash: {vhash})")
            self._send_json({"ok": ok, "message": msg})
            return

        # 8. Test Mail Regex Parser
        if route == "/api/mail/test_parser":
            sample = payload.get("sample_text", "")
            res = extract_tiktok_code_and_link(sample)
            self._send_json({"ok": True, "extracted": res})
            return

        self._send_json({"ok": False, "error": "Ruta no reconocida"}, 404)


def start_cloudflare_tunnel():
    """Starts Cloudflare Quick Tunnel on port 8085 and writes URL to PUBLIC_URL.txt."""
    if not CLOUDFLARED_BIN.exists():
        return
    try:
        proc = subprocess.Popen(
            [str(CLOUDFLARED_BIN), "tunnel", "--url", f"http://localhost:{PORT}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=0x08000000 if sys.platform == "win32" else 0
        )
        import re
        for line in proc.stdout:
            m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                PUBLIC_URL_FILE.write_text(url, encoding="utf-8")
                log_event("INFO", "dashboard", f"Túnel público Cloudflare activo: {url}")
    except Exception:
        pass


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    init_db()
    # Seed 3 initial bot accounts if DB has 0 accounts so the user sees live data right away
    conn = get_conn()
    count_acc = conn.execute("SELECT COUNT(*) FROM cuentas").fetchone()[0]
    conn.close()
    if count_acc == 0:
        for geo_code in ["US", "ES", "US"]:
            r = provision_account_record(pais=geo_code, nicho="inmigracion_legal")
            if r.get("ok"):
                run_playwright_signup_flow(r["cuenta_id"])

    threading.Thread(target=start_cloudflare_tunnel, daemon=True).start()
    print(f"[Hermes Dashboard] Escuchando en http://localhost:{PORT}", flush=True)
    with ThreadedTCPServer(("0.0.0.0", PORT), DashboardHandler) as httpd:
        httpd.serve_forever()
