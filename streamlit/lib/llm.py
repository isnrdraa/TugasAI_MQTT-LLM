"""Komentator AI via Groq API (LLaMA3, gratis). Dipanggil hanya saat halaman
Monitoring dimuat dan saat tombol 'Minta analisis ulang' ditekan -- TIDAK pada
tiap auto-refresh 5 detik (cache ttl=300 + key latest_ts menjaga ini)."""

import streamlit as st

from lib.config import GROQ_API_KEY, GROQ_MODEL, GROQ_CONFIGURED


def _fmt(value, unit=""):
    return f"{value:.1f}{unit}" if value is not None else "tidak ada data"


def _build_prompt(latest: dict, stats: dict, forecast_summary: dict | None) -> str:
    suhu_stats = stats.get("suhu", {})
    kelembaban_stats = stats.get("kelembaban", {})

    lines = [
        "Kamu adalah asisten analisis data sensor IoT (suhu & kelembaban).",
        "Berikan komentar singkat (maksimal 120 kata) dalam Bahasa Indonesia,",
        "berupa observasi dan rekomendasi praktis berdasarkan data berikut.",
        "Jangan membuat klaim medis atau pertanian yang berlebihan.",
        "",
        f"Data terbaru: suhu {_fmt(latest.get('suhu'), ' C')}, "
        f"kelembaban {_fmt(latest.get('kelembaban'), ' %')} pada {latest.get('timestamp')}.",
        "",
        "Statistik historis:",
        f"- Suhu: min {_fmt(suhu_stats.get('min'))}, max {_fmt(suhu_stats.get('max'))}, "
        f"rata-rata {_fmt(suhu_stats.get('avg'))}, median {_fmt(suhu_stats.get('median'))}",
        f"- Kelembaban: min {_fmt(kelembaban_stats.get('min'))}, max {_fmt(kelembaban_stats.get('max'))}, "
        f"rata-rata {_fmt(kelembaban_stats.get('avg'))}, median {_fmt(kelembaban_stats.get('median'))}",
    ]

    if forecast_summary:
        lines += [
            "",
            "Ringkasan hasil forecasting 6 jam ke depan:",
            f"- Suhu diprediksi berkisar {forecast_summary.get('suhu_range', 'tidak tersedia')}",
            f"- Kelembaban diprediksi berkisar {forecast_summary.get('kelembaban_range', 'tidak tersedia')}",
        ]

    return "\n".join(lines)


def call_groq(prompt: str) -> str:
    try:
        from groq import Groq
    except ImportError:
        return "Package 'groq' belum terinstal di lingkungan ini."

    try:
        client = Groq(api_key=GROQ_API_KEY)
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.4,
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:
        return f"Analisis AI sedang tidak tersedia (error: {exc})."


@st.cache_data(ttl=300, show_spinner="Meminta analisis AI...")
def get_commentary(latest_ts: str, latest: dict, stats: dict, forecast_summary: dict | None, _nonce: int = 0) -> str:
    if not GROQ_CONFIGURED:
        return "Komentator AI belum dikonfigurasi (GROQ_API_KEY belum diisi di secrets)."
    prompt = _build_prompt(latest, stats, forecast_summary)
    return call_groq(prompt)
