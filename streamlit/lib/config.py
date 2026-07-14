"""Loader konfigurasi terpusat: coba st.secrets dulu, fallback ke .env/os.environ
untuk pengembangan lokal (Streamlit Community Cloud hanya punya st.secrets)."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def get_secret(key: str, default: str | None = None) -> str | None:
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


SUPABASE_URL = (get_secret("SUPABASE_URL") or "").rstrip("/")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY") or ""
SUPABASE_TABLE = get_secret("SUPABASE_TABLE", "sensor_data")

GROQ_API_KEY = get_secret("GROQ_API_KEY") or ""
GROQ_MODEL = get_secret("GROQ_MODEL", "llama-3.1-8b-instant")

TIMEZONE = ZoneInfo(get_secret("TIMEZONE", "Asia/Jakarta"))

SUPABASE_CONFIGURED = bool(SUPABASE_URL and SUPABASE_ANON_KEY)
GROQ_CONFIGURED = bool(GROQ_API_KEY)


def mqtt_config() -> dict:
    """Dipanggil lazy hanya kalau toggle MQTT langsung diaktifkan, supaya
    secrets MQTT yang belum diisi tidak bikin app gagal load sama sekali."""
    return {
        "host": get_secret("MQTT_HOST", ""),
        "port": int(get_secret("MQTT_PORT", "8883")),
        "username": get_secret("MQTT_USERNAME", ""),
        "password": get_secret("MQTT_PASSWORD", ""),
        "topic_suhu": get_secret("MQTT_TOPIC_SUHU", "tas_ai_surya_fsm_uksw/suhu"),
        "topic_kelembaban": get_secret("MQTT_TOPIC_KELEMBABAN", "tas_ai_surya_fsm_uksw/kelembaban"),
    }
