"""Unified Configuration Loader & Hot-Reload Manager for Hermes Agent."""
import os
import json
from pathlib import Path
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    yaml = None

BASE_DIR = Path(r"C:\Users\ariel\AppData\Local\hermes\hermes-tiktok-factory")
if not BASE_DIR.exists():
    BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_PATH = BASE_DIR / "config.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Loads config.yaml dynamically on every call for hot-reload support."""
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(raw) or {}
    return {}


def save_config_raw(raw_yaml: str, path: Path = CONFIG_PATH) -> tuple[bool, str]:
    """Validates and writes updated YAML content to config.yaml."""
    if yaml is not None:
        try:
            parsed = yaml.safe_load(raw_yaml)
            if not isinstance(parsed, dict):
                return False, "El contenido YAML debe ser un diccionario raíz válido."
            required_keys = ["sistema", "correos", "fabrica", "identidad", "proxies", "curva_calentamiento", "farm"]
            missing = [k for k in required_keys if k not in parsed]
            if missing:
                return False, f"Faltan secciones requeridas en config.yaml: {', '.join(missing)}"
        except Exception as e:
            return False, f"Error de sintaxis YAML: {e}"
    tmp_path = path.with_suffix(".yaml.tmp")
    tmp_path.write_text(raw_yaml, encoding="utf-8")
    os.replace(tmp_path, path)
    return True, "Configuración validada y aplicada en caliente para Hermes Agent."


def update_config_dict(updater_fn, path: Path = CONFIG_PATH) -> dict:
    """Loads config.yaml, applies updater_fn(cfg), and writes back preserving structure."""
    cfg = load_config(path)
    updater_fn(cfg)
    if yaml is not None:
        raw = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False)
        path.write_text(raw, encoding="utf-8")
    return cfg
