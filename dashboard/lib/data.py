"""Helper data ter-cache di atas supabase_client. Semua fungsi cached menerima
argumen primitif (string ISO) yang dihitung oleh caller -- bukan datetime.now()
di dalam fungsi, supaya cache key stabil dan bisa diverifikasi."""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from lib import supabase_client
from lib.config import TIMEZONE

RANGE_PRESETS = {
    "1 Jam": timedelta(hours=1),
    "6 Jam": timedelta(hours=6),
    "24 Jam": timedelta(hours=24),
    "7 Hari": timedelta(days=7),
}

# Di atas ambang ini, resample ke rata-rata 5 menit di sisi klien supaya tidak
# menarik puluhan ribu baris mentah (data masuk tiap 20 detik) untuk sekadar
# digambar sebagai grafik garis.
RESAMPLE_THRESHOLD_HOURS = 24


def compute_range(range_key: str, custom_start: datetime | None = None, custom_end: datetime | None = None):
    now = datetime.now(TIMEZONE)
    if range_key == "Custom" and custom_start is not None and custom_end is not None:
        return custom_start, custom_end
    delta = RANGE_PRESETS.get(range_key, timedelta(hours=24))
    return now - delta, now


@st.cache_data(ttl=5, show_spinner=False)
def get_latest_reading() -> dict | None:
    df = supabase_client.fetch_latest(1)
    if df.empty:
        return None
    row = df.iloc[0]
    return {"timestamp": row["timestamp"], "suhu": row["suhu"], "kelembaban": row["kelembaban"]}


@st.cache_data(ttl=5, show_spinner=False)
def get_recent_window(start_iso: str, end_iso: str, span_hours: float) -> pd.DataFrame:
    df = supabase_client.fetch_range(start_iso, end_iso)
    if not df.empty and span_hours > RESAMPLE_THRESHOLD_HOURS:
        df = (
            df.set_index("timestamp")
            .resample("5min")
            .mean(numeric_only=True)
            .dropna(how="all")
            .reset_index()
        )
    return df


def summary_stats(df: pd.DataFrame) -> dict:
    stats = {}
    for col in ("suhu", "kelembaban"):
        series = df[col].dropna() if col in df.columns else pd.Series(dtype=float)
        if series.empty:
            stats[col] = {"min": None, "max": None, "avg": None, "median": None}
        else:
            stats[col] = {
                "min": float(series.min()),
                "max": float(series.max()),
                "avg": float(series.mean()),
                "median": float(series.median()),
            }
    return stats


@st.cache_data(ttl=5, show_spinner=False)
def get_hourly_resampled(start_iso: str, end_iso: str) -> pd.DataFrame:
    df = supabase_client.fetch_range(start_iso, end_iso)
    if df.empty:
        return df
    return (
        df.set_index("timestamp")
        .resample("1h")
        .mean(numeric_only=True)
        .reset_index()
    )
