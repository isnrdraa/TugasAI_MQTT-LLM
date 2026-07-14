"""Forecasting suhu & kelembaban dengan Prophet, memakai jendela tanggal
KALENDER TETAP sesuai spek tugas -- bukan 'sekarang + 6 jam' yang bergeser.

  Training : 2026-07-13 s/d 2026-07-19 (6 hari efektif, karena recorder baru
             mulai jalan 2026-07-14)
  Testing  : 2026-07-20 (1 hari, untuk hitung RMSE/MAE/MAPE)
  Forecast : 2026-07-21 01:00-06:00 WIB, per jam (6 titik)

Semua pengecekan ketersediaan data berbasis data aktual di Supabase (MAX(timestamp)
lewat jumlah baris hasil query), BUKAN datetime.now() -- supaya halaman otomatis
menampilkan hasil lengkap begitu tanggal 20/21 Juli benar-benar terekam, tanpa
perlu ubah kode atau redeploy.
"""

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st

from lib import data
from lib.config import TIMEZONE

logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)

try:
    from prophet import Prophet

    PROPHET_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - tergantung lingkungan deploy
    Prophet = None
    PROPHET_IMPORT_ERROR = exc

TRAIN_START = "2026-07-13"
TRAIN_END = "2026-07-19"
TEST_DATE = "2026-07-20"
FORECAST_DATE = "2026-07-21"
MIN_TRAINING_HOURLY_POINTS = 24  # ambang longgar: minimal ~1 hari data per-jam


def _day_range_iso(date_str: str) -> tuple[str, str]:
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TIMEZONE)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return start.isoformat(), end.isoformat()


def forecast_target_timestamps() -> pd.DatetimeIndex:
    return pd.date_range(f"{FORECAST_DATE} 01:00", periods=6, freq="1h", tz=TIMEZONE)


def load_training_hourly() -> pd.DataFrame:
    start_iso, _ = _day_range_iso(TRAIN_START)
    _, end_iso = _day_range_iso(TRAIN_END)
    return data.get_hourly_resampled(start_iso, end_iso)


def load_test_hourly() -> pd.DataFrame | None:
    start_iso, end_iso = _day_range_iso(TEST_DATE)
    df = data.get_hourly_resampled(start_iso, end_iso)
    if df.empty:
        return None
    return df


def get_data_availability() -> dict:
    train_df = load_training_hourly()
    test_df = load_test_hourly()
    return {
        "has_enough_training": len(train_df) >= MIN_TRAINING_HOURLY_POINTS,
        "training_points": len(train_df),
        "has_test_day": test_df is not None,
        "test_points": 0 if test_df is None else len(test_df),
    }


def _fit_and_forecast(train_df: pd.DataFrame, target: str, periods: pd.DatetimeIndex) -> pd.DataFrame:
    if Prophet is None:
        raise RuntimeError(
            f"Package 'prophet' gagal di-import ({PROPHET_IMPORT_ERROR}). "
            "Lihat README bagian 'Risiko deployment Prophet' untuk fallback."
        )

    train = train_df[["timestamp", target]].dropna().rename(columns={"timestamp": "ds", target: "y"})
    train["ds"] = train["ds"].dt.tz_localize(None)

    model = Prophet()
    model.fit(train)

    future = pd.DataFrame({"ds": periods.tz_localize(None)})
    forecast = model.predict(future)
    forecast["ds"] = forecast["ds"].dt.tz_localize(TIMEZONE)
    return forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]]


def forecast_next_period() -> dict:
    train_df = load_training_hourly()
    periods = forecast_target_timestamps()
    return {target: _fit_and_forecast(train_df, target, periods) for target in ("suhu", "kelembaban")}


def metrics(actual: pd.Series, predicted: pd.Series) -> dict:
    actual_arr = actual.to_numpy(dtype=float)
    predicted_arr = predicted.to_numpy(dtype=float)
    diff = actual_arr - predicted_arr

    rmse = float(np.sqrt(np.mean(diff**2)))
    mae = float(np.mean(np.abs(diff)))

    nonzero = actual_arr != 0
    mape = float(np.mean(np.abs(diff[nonzero] / actual_arr[nonzero])) * 100) if nonzero.any() else None

    return {"rmse": rmse, "mae": mae, "mape": mape}


def evaluate_on_test_day() -> dict | None:
    test_df = load_test_hourly()
    if test_df is None:
        return None

    train_df = load_training_hourly()
    periods = pd.DatetimeIndex(test_df["timestamp"])

    results = {}
    for target in ("suhu", "kelembaban"):
        forecast = _fit_and_forecast(train_df, target, periods)
        actual = test_df[["timestamp", target]].rename(columns={"timestamp": "ds"})
        merged = actual.merge(forecast, on="ds", how="inner").dropna(subset=[target, "yhat"])
        results[target] = {
            "forecast": forecast,
            "actual": actual,
            "merged": merged,
            "metrics": metrics(merged[target], merged["yhat"]) if not merged.empty else None,
        }
    return results
