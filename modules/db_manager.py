"""SQLite Database Schema, Idempotent Migrations & State Manager for Hermes Agent."""
import os
import json
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from .config_manager import BASE_DIR, load_config, CONFIG_PATH

DB_DIR = BASE_DIR / "database"
DB_PATH = DB_DIR / "hermes_factory.sqlite"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn(readonly: bool = False) -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    if readonly and DB_PATH.exists():
        conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=15)
    else:
        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Creates all required tables idempotently and seeds initial proxies and mother accounts."""
    for folder in ["database", "contexts", "avatars", "evidence", "logs"]:
        (BASE_DIR / folder).mkdir(parents=True, exist_ok=True)

    conn = get_conn()
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS cuentas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            username TEXT UNIQUE,
            estado TEXT DEFAULT 'pendiente',
            fecha_creacion TEXT,
            etapa_calentamiento INTEGER DEFAULT 0,
            salud INTEGER DEFAULT 100,
            proxy_id INTEGER,
            perfil_id INTEGER,
            madre_id INTEGER,
            nicho TEXT DEFAULT 'inmigracion_legal',
            bio TEXT DEFAULT '',
            avatar_path TEXT DEFAULT '',
            notas TEXT DEFAULT '',
            ultima_actividad TEXT,
            intentos_fallidos INTEGER DEFAULT 0,
            pausada_hasta TEXT,
            context_dir TEXT
        );

        CREATE TABLE IF NOT EXISTS perfiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_agent TEXT NOT NULL,
            viewport TEXT NOT NULL,
            timezone TEXT NOT NULL,
            locale TEXT NOT NULL,
            canvas_seed TEXT NOT NULL,
            webgl_seed TEXT NOT NULL,
            fuentes TEXT NOT NULL,
            device_model TEXT NOT NULL,
            android_id TEXT NOT NULL,
            geo TEXT DEFAULT 'US',
            creado_en TEXT
        );

        CREATE TABLE IF NOT EXISTS proxies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host TEXT NOT NULL,
            puerto INTEGER NOT NULL,
            usuario TEXT DEFAULT '',
            pass TEXT DEFAULT '',
            protocolo TEXT DEFAULT 'http',
            geo TEXT DEFAULT 'US',
            ciudad TEXT DEFAULT '',
            timezone TEXT DEFAULT 'America/Los_Angeles',
            locale TEXT DEFAULT 'en-US',
            activo INTEGER DEFAULT 1,
            salud INTEGER DEFAULT 100,
            cuentas_asignadas INTEGER DEFAULT 0,
            ultimo_chequeo TEXT,
            UNIQUE(host, puerto)
        );

        CREATE TABLE IF NOT EXISTS madres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            nicho TEXT DEFAULT 'inmigracion_legal',
            bots_asignados INTEGER DEFAULT 0,
            objetivo_seguidores INTEGER DEFAULT 5000,
            acciones_recibidas INTEGER DEFAULT 0,
            horario_activo TEXT DEFAULT '09:00-22:00',
            ratios_json TEXT DEFAULT '{"view":1.0,"like":0.65,"follow":0.30,"comment":0.15}',
            estado TEXT DEFAULT 'activa',
            creada_en TEXT
        );

        CREATE TABLE IF NOT EXISTS acciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cuenta_id INTEGER,
            username_bot TEXT,
            madre_username TEXT,
            tipo TEXT NOT NULL,
            ts TEXT NOT NULL,
            duracion REAL DEFAULT 0.0,
            resultado TEXT NOT NULL,
            error TEXT DEFAULT '',
            evidencia_path TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nivel TEXT NOT NULL,
            componente TEXT NOT NULL,
            cuenta_id INTEGER,
            mensaje TEXT NOT NULL,
            screenshot_path TEXT DEFAULT '',
            ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS config_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_hash TEXT NOT NULL,
            yaml_content TEXT NOT NULL,
            aplicado_en TEXT NOT NULL,
            nota TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS alias_correos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_email TEXT UNIQUE NOT NULL,
            usado INTEGER DEFAULT 0,
            fallos INTEGER DEFAULT 0,
            quemado INTEGER DEFAULT 0,
            codigo_ultimo TEXT DEFAULT '',
            creado_en TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dispatch_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comando TEXT NOT NULL,
            payload_json TEXT DEFAULT '{}',
            estado TEXT DEFAULT 'pending',
            creado_en TEXT NOT NULL,
            ejecutado_en TEXT,
            resultado TEXT DEFAULT ''
        );
        """)

        cfg = load_config()
        # Seed proxies from config.yaml
        proxies_cfg = cfg.get("proxies", {}).get("lista", [])
        proto = cfg.get("proxies", {}).get("protocolo_preferido", "http")
        for p in proxies_cfg:
            conn.execute("""
                INSERT OR IGNORE INTO proxies (host, puerto, usuario, pass, protocolo, geo, ciudad, timezone, locale, activo, salud, cuentas_asignadas, ultimo_chequeo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 100, 0, ?)
            """, (
                p["host"], int(p["puerto"]), p.get("usuario", ""), p.get("pass", ""),
                proto, p.get("geo", "US"), p.get("ciudad", ""),
                p.get("timezone", "America/Los_Angeles"), p.get("locale", "en-US"),
                now_iso()
            ))

        # Seed primary mother account (@colombohurdimmigrationla)
        conn.execute("""
            INSERT OR IGNORE INTO madres (username, nicho, bots_asignados, objetivo_seguidores, acciones_recibidas, horario_activo, estado, creada_en)
            VALUES ('@colombohurdimmigrationla', 'inmigracion_legal', 0, 10000, 0, '09:00-22:00', 'activa', ?)
        """, (now_iso(),))

        # Sync proxy assignment counters
        conn.execute("""
            UPDATE proxies SET cuentas_asignadas = (
                SELECT COUNT(*) FROM cuentas WHERE cuentas.proxy_id = proxies.id AND cuentas.estado != 'eliminada'
            )
        """)

        # Save initial config snapshot if empty
        snap_count = conn.execute("SELECT COUNT(*) FROM config_snapshot").fetchone()[0]
        if snap_count == 0 and CONFIG_PATH.exists():
            raw = CONFIG_PATH.read_text(encoding="utf-8")
            vhash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
            conn.execute(
                "INSERT INTO config_snapshot (version_hash, yaml_content, aplicado_en, nota) VALUES (?, ?, ?, ?)",
                (vhash, raw, now_iso(), "Snapshot inicial de Hermes TikTok Factory")
            )

    conn.close()


def log_event(nivel: str, componente: str, mensaje: str, cuenta_id: int = None, screenshot_path: str = ""):
    """Writes structured log both to SQLite and JSONL file."""
    ts = now_iso()
    try:
        conn = get_conn()
        with conn:
            conn.execute(
                "INSERT INTO logs (nivel, componente, cuenta_id, mensaje, screenshot_path, ts) VALUES (?, ?, ?, ?, ?, ?)",
                (nivel.upper(), componente, cuenta_id, mensaje, screenshot_path, ts)
            )
        conn.close()
    except Exception:
        pass

    try:
        log_file = BASE_DIR / "logs" / "hermes_events.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": ts,
            "nivel": nivel.upper(),
            "componente": componente,
            "cuenta_id": cuenta_id,
            "mensaje": mensaje,
            "screenshot_path": screenshot_path
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
