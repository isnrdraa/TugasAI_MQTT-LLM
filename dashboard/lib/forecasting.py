"""Lapisan data Streamlit untuk forecasting DINAMIS: 6 jam setelah timestamp
data TERAKHIR yang terekam (sesuai spesifikasi tugas + klarifikasi dosen),
bukan jendela tanggal kalender tetap.

Logika model (Prophet, backtest, metrik) ada di forecasting/core.py di root
project -- dipakai bersama dengan script CLI model_training.py & predict.py.
Modul ini hanya menambahkan: pengambilan data dari Supabase + cache Streamlit.
"""

import pandas as pd
import streamlit as st

from lib import supabase_client

# Root project sudah ada di sys.path (di-insert oleh tiap halaman dashboard)
from forecasting import core

# Re-export supaya halaman cukup import satu modul ini
Prophet = core.Prophet
PROPHET_IMPORT_ERROR = core.PROPHET_IMPORT_ERROR
FORECAST_HORIZON_HOURS = core.FORECAST_HORIZON_HOURS
MIN_TRAINING_DAYS = core.MIN_TRAINING_DAYS
forecast_table = core.forecast_table


@st.cache_data(ttl=300, show_spinner=False)
def _load_hourly_history(first_iso: str, last_iso: str) -> pd.DataFrame:
    """Seluruh data historis (paginated) di-resample per jam. Cache key memakai
    timestamp pertama & terakhir aktual, jadi otomatis segar saat ada data baru."""
    raw = supabase_client.fetch_all_range(first_iso, last_iso)
    return core.hourly_resample(raw)


def load_hourly_history() -> pd.DataFrame:
    first_ts = supabase_client.fetch_first_timestamp()
    last_ts = supabase_client.fetch_latest_timestamp()
    if first_ts is None or last_ts is None:
        return pd.DataFrame(columns=["timestamp", "suhu", "kelembaban"])
    return _load_hourly_history(first_ts.isoformat(), last_ts.isoformat())


def get_span_info(hourly_df: pd.DataFrame) -> dict:
    return core.data_span_info(hourly_df)


@st.cache_data(ttl=300, show_spinner=False)
def _forecast_cached(last_iso: str, _hourly_df: pd.DataFrame) -> dict:
    return core.forecast_next_hours(_hourly_df)


def forecast_next_6h(hourly_df: pd.DataFrame) -> dict:
    """Forecast 6 jam setelah data terakhir. Cache key = timestamp terakhir,
    jadi hasil stabil selama belum ada jam data baru."""
    last_iso = hourly_df["timestamp"].iloc[-1].isoformat()
    return _forecast_cached(last_iso, hourly_df)


@st.cache_data(ttl=300, show_spinner=False)
def _backtest_cached(last_iso: str, _hourly_df: pd.DataFrame) -> dict | None:
    return core.backtest_last_hours(_hourly_df)


def backtest(hourly_df: pd.DataFrame) -> dict | None:
    last_iso = hourly_df["timestamp"].iloc[-1].isoformat()
    return _backtest_cached(last_iso, hourly_df)


def forecast_summary_for_llm(result: dict) -> dict:
    """Ringkasan prediksi untuk komentator AI (rentang, tren, puncak --
    mengikuti format contoh prompt di penugasan)."""
    def _range(df):
        return f"{df['yhat'].min():.1f} - {df['yhat'].max():.1f}"

    suhu_df = result["suhu"]
    delta = suhu_df["yhat"].iloc[-1] - suhu_df["yhat"].iloc[0]
    arah = "meningkat" if delta > 0.2 else ("menurun" if delta < -0.2 else "relatif stabil")
    peak = suhu_df.loc[suhu_df["yhat"].idxmax()]

    return {
        "suhu_range": _range(suhu_df) + " C",
        "kelembaban_range": _range(result["kelembaban"]) + " %",
        "suhu_tren": f"{arah} ({delta:+.1f} C dalam 6 jam)",
        "suhu_puncak": f"{peak['yhat']:.1f} C (pukul {peak['ds'].strftime('%H:%M')})",
    }
