#!/usr/bin/env python3
"""Training model Prophet dari data historis + evaluasi backtest.

Alur:
  1. Baca seluruh data hasil recording dari data/sensor_data.db (fallback .csv).
  2. Resample ke rata-rata per jam, tampilkan rentang data (syarat: minimal 7 hari).
  3. Evaluasi backtest: latih TANPA 24 jam terakhir (data testing, analog
     "testing 1 hari" di spesifikasi), prediksi jam-jam itu, hitung
     RMSE/MAE/MAPE terhadap data aktual -> simpan forecasting/metrics.json.
  4. Latih model final pada SELURUH data -> simpan forecasting/models/*.json
     (dipakai oleh predict.py).

Jalankan dari root project:
    python forecasting/model_training.py
"""

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from forecasting import core

MODELS_DIR = Path(__file__).resolve().parent / "models"
METRICS_PATH = Path(__file__).resolve().parent / "metrics.json"


def main():
    parser = argparse.ArgumentParser(description="Training Prophet + evaluasi backtest 6 jam terakhir")
    parser.add_argument("--data-dir", default=str(ROOT_DIR / "data"),
                        help="Folder berisi sensor_data.db/sensor_data.csv (default: data/)")
    args = parser.parse_args()

    core.require_prophet()
    from prophet.serialize import model_to_json

    print(f"Membaca data dari {args.data_dir} ...")
    raw = core.load_local_data(args.data_dir)
    hourly = core.hourly_resample(raw)
    span = core.data_span_info(hourly)

    print(f"  Baris mentah      : {len(raw)}")
    print(f"  Titik per jam     : {span['points']}")
    print(f"  Rentang data      : {span['first']}  s/d  {span['last']}")
    print(f"  Durasi            : {span['days']:.2f} hari")
    if not span["meets_min_days"]:
        print(f"  PERINGATAN: durasi data < {core.MIN_TRAINING_DAYS} hari (syarat minimal tugas).")

    print(f"\nEvaluasi backtest ({core.BACKTEST_TEST_HOURS} jam terakhir sebagai data testing, "
          "analog 'testing 1 hari' di spesifikasi) ...")
    backtest = core.backtest_last_hours(hourly)
    metrics_out = {
        "trained_at": str(span["last"]),
        "data_first": str(span["first"]),
        "data_last": str(span["last"]),
        "data_days": round(span["days"], 3),
        "hourly_points": span["points"],
        "backtest_test_hours": core.BACKTEST_TEST_HOURS,
    }
    if backtest is None:
        print("  Data belum cukup untuk backtest.")
    else:
        for target in core.TARGETS:
            m = backtest[target]["metrics"]
            if m is None:
                print(f"  {target}: tidak ada titik yang bisa dibandingkan.")
                continue
            mape_str = f"{m['mape']:.2f}%" if m["mape"] is not None else "-"
            print(f"  {target:11s}: RMSE={m['rmse']:.3f}  MAE={m['mae']:.3f}  MAPE={mape_str}")
            metrics_out[target] = m

    print("\nMelatih model final pada seluruh data ...")
    MODELS_DIR.mkdir(exist_ok=True)
    for target in core.TARGETS:
        model = core.fit_model(hourly, target)
        out_path = MODELS_DIR / f"prophet_{target}.json"
        out_path.write_text(model_to_json(model), encoding="utf-8")
        print(f"  Model {target} disimpan -> {out_path}")

    METRICS_PATH.write_text(json.dumps(metrics_out, indent=2), encoding="utf-8")
    print(f"  Metrik evaluasi disimpan -> {METRICS_PATH}")
    print("\nSelesai. Jalankan 'python forecasting/predict.py' untuk prediksi 6 jam ke depan.")


if __name__ == "__main__":
    main()
