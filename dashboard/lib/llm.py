"""Wrapper Streamlit di atas llm_integration/groq_commentator.py: menambahkan
cache (ttl=300) supaya komentar AI tidak dipanggil ulang tiap auto-refresh
5 detik -- hanya saat halaman dimuat, tombol ditekan, atau nonce berubah."""

import importlib

import streamlit as st

from lib.config import GROQ_API_KEY, GROQ_MODEL, GROQ_CONFIGURED

# reload() wajib: file watcher Streamlit Cloud hanya me-reload module di dalam
# folder dashboard/, jadi tanpa ini groq_commentator.py versi lama bisa
# tertinggal di sys.modules setelah git push (hot-reload parsial).
from llm_integration import groq_commentator

groq_commentator = importlib.reload(groq_commentator)


@st.cache_data(ttl=300, show_spinner="Meminta analisis AI...")
def get_commentary(latest_ts: str, latest: dict, stats: dict, forecast_summary: dict | None, _nonce: int = 0) -> str:
    if not GROQ_CONFIGURED:
        return "Komentator AI belum dikonfigurasi (GROQ_API_KEY belum diisi di secrets)."
    return groq_commentator.get_commentary(latest, stats, forecast_summary, GROQ_API_KEY, GROQ_MODEL)
