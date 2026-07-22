"""Logika inti forecasting (bebas Streamlit) -- dipakai bersama oleh:

  - forecasting/model_training.py  (CLI: training + evaluasi backtest)
  - forecasting/predict.py         (CLI: prediksi 6 jam ke depan)
  - dashboard/lib/forecasting.py   (halaman Forecasting di Streamlit)

Ada DUA mode jendela waktu, dua-duanya bersumber HANYA dari data Supabase:

1. TETAP (default, sesuai spesifikasi tugas): training 13-19 Juli,
   testing 20 Juli (RMSE/MAE/MAPE), forecast 21 Juli 00:00-06:00 WIB --
   yaitu 6 jam setelah akhir periode recording 13-20 Juli.
2. DINAMIS: forecast 6 jam setelah timestamp data TERAKHIR yang terekam;
   evaluasi backtest dengan data testing = 24 jam terakhir (analog
   "testing 1 hari").
"""

import logging
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)

try:
    from prophet import Prophet

    PROPHET_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - tergantung lingkungan deploy
    Prophet = None
    PROPHET_IMPORT_ERROR = exc

TARGETS = ("suhu", "kelembaban")
FORECAST_HORIZON_HOURS = 6
BACKTEST_TEST_HOURS = 24  # data testing = 24 jam terakhir (analog "testing 1 hari" di spesifikasi)
MIN_TRAINING_DAYS = 7  # "Minimal ada 7 hari data" (spesifikasi tugas)
DEFAULT_TZ = ZoneInfo("Asia/Jakarta")

# --- Jendela kalender TETAP sesuai spesifikasi tugas ---
#   Training : 13 - 19 Juli 2026 (6 hari)
#   Testing  : 20 Juli 2026 (1 hari, untuk RMSE/MAE/MAPE)
#   Forecast : 21 Juli 2026, 00:00 - 06:00 WIB (per jam, 6 titik)
TRAIN_START = "2026-07-13"
TRAIN_END = "2026-07-19"
TEST_DATE = "2026-07-20"
FORECAST_DATE = "2026-07-21"


def fixed_window_bounds(tz=DEFAULT_TZ) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(start, end-eksklusif) rentang data jendela tetap: 13 Juli 00:00 s/d
    21 Juli 00:00 = periode recording 13-20 Juli."""
    start = pd.Timestamp(TRAIN_START, tz=tz)
    end = pd.Timestamp(TEST_DATE, tz=tz) + pd.Timedelta(days=1)
    return start, end


def fixed_forecast_periods(tz=DEFAULT_TZ) -> pd.DatetimeIndex:
    """6 titik per jam untuk 21 Juli 00:00-06:00 WIB. Label bin 00:00 s/d
    05:00, konsisten dengan label kiri hasil resample per jam data historis."""
    return pd.date_range(f"{FORECAST_DATE} 00:00", periods=FORECAST_HORIZON_HOURS, freq="1h", tz=tz)


def split_fixed_train_test(hourly_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    tz = hourly_df["timestamp"].dt.tz
    test_start = pd.Timestamp(TEST_DATE, tz=tz)
    test_end = test_start + pd.Timedelta(days=1)
    train = hourly_df[hourly_df["timestamp"] < test_start]
    test = hourly_df[(hourly_df["timestamp"] >= test_start) & (hourly_df["timestamp"] < test_end)]
    return train, test


def evaluate_fixed(hourly_df: pd.DataFrame) -> dict | None:
    """Evaluasi sesuai spesifikasi: model dilatih pada 13-19 Juli, diminta memprediksi
    per jam tanggal 20 Juli, dibandingkan dengan aktualnya (RMSE/MAE/MAPE)."""
    train_df, test_df = split_fixed_train_test(hourly_df)
    if train_df.empty or test_df.empty:
        return None

    periods = pd.DatetimeIndex(test_df["timestamp"])
    results = {"train_points": len(train_df), "test_points": len(test_df)}
    for target in TARGETS:
        forecast = fit_and_forecast(train_df, target, periods)
        actual = test_df[["timestamp", target]].rename(columns={"timestamp": "ds"})
        merged = actual.merge(forecast, on="ds", how="inner").dropna(subset=[target, "yhat"])
        results[target] = {
            "forecast": forecast,
            "merged": merged,
            "metrics": metrics(merged[target], merged["yhat"]) if not merged.empty else None,
        }
    return results


def forecast_fixed(hourly_df: pd.DataFrame) -> dict:
    """Forecast 21 Juli 00:00-06:00 WIB. Model final dilatih pada SELURUH
    periode recording (13-20 Juli), sesuai kalimat spesifikasi 'menggunakan
    data historis yang telah dikumpulkan' (evaluasi tetap memakai model yang
    dilatih tanpa hari testing, lihat evaluate_fixed)."""
    tz = hourly_df["timestamp"].dt.tz
    periods = fixed_forecast_periods(tz)
    result = {"periods": periods, "last_ts": hourly_df["timestamp"].iloc[-1]}
    for target in TARGETS:
        result[target] = fit_and_forecast(hourly_df, target, periods)
    return result


def fetch_supabase_range(url: str, key: str, table: str, start_iso: str, end_iso: str,
                         page_size: int = 10000, max_pages: int = 200, tz=DEFAULT_TZ) -> pd.DataFrame:
    """Ambil data dari Supabase REST API (paginated, bebas Streamlit) -- dipakai
    script CLI supaya sumber data seragam: semuanya dari Supabase."""
    import requests

    endpoint = url.rstrip("/") + f"/rest/v1/{table}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    frames = []
    cursor = None
    for _ in range(max_pages):
        params = {
            "select": "timestamp,suhu,kelembaban",
            "timestamp": [f"gt.{cursor}" if cursor else f"gte.{start_iso}", f"lte.{end_iso}"],
            "order": "timestamp.asc",
            "limit": page_size,
        }
        resp = requests.get(endpoint, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        frames.append(pd.DataFrame(rows))
        cursor = rows[-1]["timestamp"]  # strictly increasing (unique constraint)

    if not frames:
        return pd.DataFrame(columns=["timestamp", "suhu", "kelembaban"])

    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(tz)
    df["suhu"] = pd.to_numeric(df["suhu"], errors="coerce")
    df["kelembaban"] = pd.to_numeric(df["kelembaban"], errors="coerce")
    return df.sort_values("timestamp").reset_index(drop=True)


def load_local_data(data_dir) -> pd.DataFrame:
    """Baca data hasil recording dari data/sensor_data.db (fallback: .csv).

    Dipakai oleh model_training.py & predict.py yang jalan lokal di mesin
    yang sama dengan recorder (dashboard membaca lewat Supabase, bukan ini).
    """
    from pathlib import Path

    data_dir = Path(data_dir)
    db_path = data_dir / "sensor_data.db"
    csv_path = data_dir / "sensor_data.csv"

    if db_path.exists():
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            df = pd.read_sql_query(
                "SELECT timestamp, suhu, kelembaban FROM sensor_data ORDER BY timestamp", conn
            )
    elif csv_path.exists():
        df = pd.read_csv(csv_path)
    else:
        raise FileNotFoundError(
            f"Tidak menemukan {db_path} maupun {csv_path}. "
            "Jalankan recorder dulu, atau salin data hasil recording ke folder data/."
        )

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601").dt.tz_convert(DEFAULT_TZ)
    df["suhu"] = pd.to_numeric(df["suhu"], errors="coerce")
    df["kelembaban"] = pd.to_numeric(df["kelembaban"], errors="coerce")
    return df.sort_values("timestamp").reset_index(drop=True)


def require_prophet():
    if Prophet is None:
        raise RuntimeError(
            f"Package 'prophet' gagal di-import ({PROPHET_IMPORT_ERROR}). "
            "Lihat README bagian 'Risiko deployment Prophet' untuk fallback."
        )


def hourly_resample(df: pd.DataFrame) -> pd.DataFrame:
    """Rata-rata per jam dari data mentah (interval 20 detik).

    Prophet dilatih pada resolusi per jam: jauh lebih cepat dari 20 detik,
    dan sesuai granularitas penilaian ("data 6 jam setelah email").
    """
    if df.empty:
        return df
    return (
        df.set_index("timestamp")
        .resample("1h")
        .mean(numeric_only=True)
        .dropna(how="all")
        .reset_index()
    )


def data_span_info(hourly_df: pd.DataFrame) -> dict:
    """Ringkasan rentang data untuk ditampilkan/divalidasi terhadap syarat 7 hari."""
    if hourly_df.empty:
        return {"first": None, "last": None, "days": 0.0, "points": 0, "meets_min_days": False}
    first = hourly_df["timestamp"].iloc[0]
    last = hourly_df["timestamp"].iloc[-1]
    days = (last - first).total_seconds() / 86400
    return {
        "first": first,
        "last": last,
        "days": days,
        "points": len(hourly_df),
        "meets_min_days": days >= MIN_TRAINING_DAYS,
    }


def forecast_periods(last_ts: pd.Timestamp, hours: int = FORECAST_HORIZON_HOURS) -> pd.DatetimeIndex:
    """6 titik per jam setelah data terakhir. Contoh: data terakhir 14:37
    -> prediksi jam 15:00, 16:00, ..., 20:00."""
    start = last_ts.ceil("1h")
    if start == last_ts:  # data terakhir pas di batas jam
        start = last_ts + pd.Timedelta(hours=1)
    return pd.date_range(start, periods=hours, freq="1h")


def fit_model(hourly_df: pd.DataFrame, target: str):
    """Latih satu model Prophet untuk kolom target ('suhu'/'kelembaban')."""
    require_prophet()
    train = (
        hourly_df[["timestamp", target]]
        .dropna()
        .rename(columns={"timestamp": "ds", target: "y"})
    )
    if train["ds"].dt.tz is not None:
        train["ds"] = train["ds"].dt.tz_localize(None)
    model = Prophet()
    model.fit(train)
    return model


def predict_at(model, periods: pd.DatetimeIndex, tz=None) -> pd.DataFrame:
    """Prediksi pada titik waktu tertentu; kembalikan ds/yhat/yhat_lower/yhat_upper."""
    naive = periods.tz_localize(None) if periods.tz is not None else periods
    forecast = model.predict(pd.DataFrame({"ds": naive}))
    out = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    if tz is not None:
        out["ds"] = out["ds"].dt.tz_localize(tz)
    return out


def fit_and_forecast(hourly_df: pd.DataFrame, target: str, periods: pd.DatetimeIndex) -> pd.DataFrame:
    model = fit_model(hourly_df, target)
    return predict_at(model, periods, tz=periods.tz)


def metrics(actual: pd.Series, predicted: pd.Series) -> dict:
    actual_arr = actual.to_numpy(dtype=float)
    predicted_arr = predicted.to_numpy(dtype=float)
    diff = actual_arr - predicted_arr

    rmse = float(np.sqrt(np.mean(diff**2)))
    mae = float(np.mean(np.abs(diff)))

    nonzero = actual_arr != 0
    mape = float(np.mean(np.abs(diff[nonzero] / actual_arr[nonzero])) * 100) if nonzero.any() else None

    return {"rmse": rmse, "mae": mae, "mape": mape}


def forecast_next_hours(hourly_df: pd.DataFrame, hours: int = FORECAST_HORIZON_HOURS) -> dict:
    """Forecast 6 jam setelah data terakhir, untuk kedua target.

    Return: {"periods": DatetimeIndex, "suhu": df, "kelembaban": df}
    """
    last_ts = hourly_df["timestamp"].iloc[-1]
    periods = forecast_periods(last_ts, hours)
    result = {"periods": periods, "last_ts": last_ts}
    for target in TARGETS:
        result[target] = fit_and_forecast(hourly_df, target, periods)
    return result


def backtest_last_hours(hourly_df: pd.DataFrame, hours: int = BACKTEST_TEST_HOURS) -> dict | None:
    """Evaluasi RMSE/MAE/MAPE: sisihkan `hours` jam terakhir sebagai data testing,
    latih pada sisanya, prediksi jam-jam tersebut, bandingkan dengan aktual."""
    if hourly_df.empty:
        return None
    cutoff = hourly_df["timestamp"].iloc[-1] - pd.Timedelta(hours=hours)
    train_df = hourly_df[hourly_df["timestamp"] <= cutoff]
    test_df = hourly_df[hourly_df["timestamp"] > cutoff]
    if train_df.empty or test_df.empty:
        return None

    periods = pd.DatetimeIndex(test_df["timestamp"])
    results = {"cutoff": cutoff, "test_points": len(test_df)}
    for target in TARGETS:
        forecast = fit_and_forecast(train_df, target, periods)
        actual = test_df[["timestamp", target]].rename(columns={"timestamp": "ds"})
        merged = actual.merge(forecast, on="ds", how="inner").dropna(subset=[target, "yhat"])
        results[target] = {
            "forecast": forecast,
            "merged": merged,
            "metrics": metrics(merged[target], merged["yhat"]) if not merged.empty else None,
        }
    return results


def forecast_table(result: dict) -> pd.DataFrame:
    """Tabel gabungan hasil forecast: Jam | Suhu (°C) | Kelembaban (%)."""
    table = result["suhu"][["ds", "yhat"]].rename(columns={"ds": "Jam", "yhat": "Suhu (°C)"})
    table["Kelembaban (%)"] = result["kelembaban"]["yhat"].values
    return table
