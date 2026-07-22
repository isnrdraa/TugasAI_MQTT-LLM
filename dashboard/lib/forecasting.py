"""Lapisan data Streamlit untuk halaman Forecasting. Semua data HANYA dari
Supabase (MQTT tidak dipakai di sini). Dua mode jendela waktu:

1. TETAP (default, sesuai tabel PDF): data dibatasi periode recording
   13-20 Juli 2026; training 13-19, testing 20, forecast 21 Juli 00:00-06:00.
2. DINAMIS (klarifikasi WhatsApp): seluruh data historis, forecast 6 jam
   setelah data terakhir, backtest 24 jam terakhir.

Logika model (Prophet, split, metrik) ada di forecasting/core.py di root
project -- dipakai bersama dengan script CLI model_training.py & predict.py.
Modul ini hanya menambahkan: pengambilan data dari Supabase + cache Streamlit.
"""

import importlib

import pandas as pd
import streamlit as st

from lib import supabase_client
from lib.config import TIMEZONE

# Root project sudah ada di sys.path (di-insert oleh tiap halaman dashboard).
# reload() wajib: file watcher Streamlit Cloud hanya me-reload module di dalam
# folder dashboard/, jadi tanpa ini core.py versi lama bisa tertinggal di
# sys.modules setelah git push (hot-reload parsial).
from forecasting import core

core = importlib.reload(core)

# Re-export supaya halaman cukup import satu modul ini
Prophet = core.Prophet
PROPHET_IMPORT_ERROR = core.PROPHET_IMPORT_ERROR
FORECAST_HORIZON_HOURS = core.FORECAST_HORIZON_HOURS
MIN_TRAINING_DAYS = core.MIN_TRAINING_DAYS
TRAIN_START = core.TRAIN_START
TRAIN_END = core.TRAIN_END
TEST_DATE = core.TEST_DATE
FORECAST_DATE = core.FORECAST_DATE
forecast_table = core.forecast_table


# ---------------------------------------------------------------------------
# Mode TETAP (jendela kalender PDF) -- datanya statis, cache boleh lama
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_hourly_fixed() -> pd.DataFrame:
    """Data per jam periode recording PDF saja: 13 Juli 00:00 s/d 21 Juli 00:00."""
    start, end = core.fixed_window_bounds(TIMEZONE)
    raw = supabase_client.fetch_all_range(
        start.isoformat(), (end - pd.Timedelta(seconds=1)).isoformat()
    )
    return core.hourly_resample(raw)


@st.cache_data(ttl=3600, show_spinner=False)
def evaluate_fixed_cached() -> dict | None:
    return core.evaluate_fixed(load_hourly_fixed())


@st.cache_data(ttl=3600, show_spinner=False)
def forecast_fixed_cached() -> dict:
    return core.forecast_fixed(load_hourly_fixed())


@st.cache_data(ttl=3600, show_spinner=False)
def load_forecast_window_actual() -> pd.DataFrame:
    """Data aktual per jam pada jendela forecast (21 Juli 00:00-06:00), untuk
    verifikasi prediksi vs kenyataan -- tersedia karena recorder tetap jalan
    setelah periode PDF berakhir."""
    periods = core.fixed_forecast_periods(TIMEZONE)
    end = periods[-1] + pd.Timedelta(hours=1)
    raw = supabase_client.fetch_all_range(
        periods[0].isoformat(), (end - pd.Timedelta(seconds=1)).isoformat()
    )
    return core.hourly_resample(raw)


# ---------------------------------------------------------------------------
# Mode DINAMIS (6 jam setelah data terakhir)
# ---------------------------------------------------------------------------


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
