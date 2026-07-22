"""Komentator AI via Groq API (LLaMA3, gratis) -- modul inti bebas Streamlit.

Dipakai oleh dashboard (lewat wrapper dashboard/lib/llm.py yang menambahkan
cache Streamlit), dan bisa juga dipanggil langsung dari terminal:

    python llm_integration/groq_commentator.py   # butuh GROQ_API_KEY di .env
"""

import os
import sys
from pathlib import Path

DEFAULT_MODEL = "llama-3.1-8b-instant"


def _fmt(value, unit=""):
    return f"{value:.1f}{unit}" if value is not None else "tidak ada data"


def build_prompt(latest: dict, stats: dict, forecast_summary: dict | None) -> str:
    """Format prompt mengikuti contoh di penugasan: persona analis lingkungan,
    blok DATA TERKINI / DATA HISTORIS / PREDIKSI, output 4 bagian bernomor."""
    suhu_stats = stats.get("suhu", {})
    kelembaban_stats = stats.get("kelembaban", {})

    lines = [
        "Anda adalah analis lingkungan berpengalaman. Analisis data sensor IoT",
        "(suhu & kelembaban ruangan) berikut.",
        "",
        "DATA TERKINI",
        f"- Suhu: {_fmt(latest.get('suhu'), ' C')}",
        f"- Kelembaban: {_fmt(latest.get('kelembaban'), ' %')}",
        f"- Waktu: {latest.get('timestamp')}",
        "",
        "DATA HISTORIS",
        f"- Suhu: min {_fmt(suhu_stats.get('min'), ' C')}, max {_fmt(suhu_stats.get('max'), ' C')}, "
        f"rata-rata {_fmt(suhu_stats.get('avg'), ' C')}, median {_fmt(suhu_stats.get('median'), ' C')}",
        f"- Kelembaban: min {_fmt(kelembaban_stats.get('min'), ' %')}, max {_fmt(kelembaban_stats.get('max'), ' %')}, "
        f"rata-rata {_fmt(kelembaban_stats.get('avg'), ' %')}, median {_fmt(kelembaban_stats.get('median'), ' %')}",
    ]

    if forecast_summary:
        lines += ["", "PREDIKSI 6 JAM SETELAH DATA TERAKHIR"]
        if forecast_summary.get("suhu_range"):
            lines.append(f"- Suhu diprediksi berkisar {forecast_summary['suhu_range']}")
        if forecast_summary.get("kelembaban_range"):
            lines.append(f"- Kelembaban diprediksi berkisar {forecast_summary['kelembaban_range']}")
        if forecast_summary.get("suhu_tren"):
            lines.append(f"- Tren suhu: {forecast_summary['suhu_tren']}")
        if forecast_summary.get("suhu_puncak"):
            lines.append(f"- Puncak suhu: {forecast_summary['suhu_puncak']}")

    lines += [
        "",
        "Berikan dalam Bahasa Indonesia, ringkas (maksimal 180 kata), tanpa emoji",
        "atau simbol ikon, tanpa klaim medis/pertanian berlebihan:",
        "1. Analisis kondisi terkini (kenyamanan ruangan)",
        "2. Prediksi singkat untuk 6 jam ke depan",
        "3. Rekomendasi tindakan (jika diperlukan)",
        "4. Insight menarik dari tren data historis",
    ]

    return "\n".join(lines)


def call_groq(prompt: str, api_key: str, model: str = DEFAULT_MODEL) -> str:
    try:
        from groq import Groq
    except ImportError:
        return "Package 'groq' belum terinstal di lingkungan ini."

    try:
        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Anda adalah analis lingkungan berpengalaman."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.4,
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:
        return f"Analisis AI sedang tidak tersedia (error: {exc})."


def get_commentary(latest: dict, stats: dict, forecast_summary: dict | None,
                   api_key: str, model: str = DEFAULT_MODEL) -> str:
    if not api_key:
        return "Komentator AI belum dikonfigurasi (GROQ_API_KEY belum diisi)."
    return call_groq(build_prompt(latest, stats, forecast_summary), api_key, model)


def _demo():
    """Uji cepat dari terminal memakai statistik data lokal (data/sensor_data.db)."""
    from dotenv import load_dotenv

    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")
    sys.path.insert(0, str(root))

    from forecasting import core

    raw = core.load_local_data(root / "data")
    latest_row = raw.iloc[-1]
    latest = {
        "timestamp": latest_row["timestamp"],
        "suhu": latest_row["suhu"],
        "kelembaban": latest_row["kelembaban"],
    }
    stats = {
        col: {
            "min": float(raw[col].min()),
            "max": float(raw[col].max()),
            "avg": float(raw[col].mean()),
            "median": float(raw[col].median()),
        }
        for col in ("suhu", "kelembaban")
    }
    api_key = os.environ.get("GROQ_API_KEY", "")
    model = os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
    print(get_commentary(latest, stats, None, api_key, model))


if __name__ == "__main__":
    _demo()
