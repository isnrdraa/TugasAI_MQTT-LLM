#!/usr/bin/env python3
"""Training model Prophet + evaluasi, sesuai jendela spesifikasi PDF penugasan.

  Data     : diambil dari SUPABASE (REST API), dibatasi periode recording
             13 - 20 Juli 2026 saja.
  Training : 13 - 19 Juli 2026 (untuk model evaluasi)
  Testing  : 20 Juli 2026 -> RMSE/MAE/MAPE -> forecasting/metrics.json
  Model final : dilatih pada seluruh periode 13 - 20 Juli
                -> forecasting/models/*.json (dipakai predict.py)

Kredensial dibaca dari .env di root project (SUPABASE_URL + SUPABASE_ANON_KEY
atau SUPABASE_KEY). Fallback: --source local untuk membaca data/sensor_data.db.

Jalankan dari root project:
    python forecasting/model_training.py
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from forecasting import core

MODELS_DIR = Path(__file__).resolve().parent / "models"
METRICS_PATH = Path(__file__).resolve().parent / "metrics.json"


def load_data(args):
    start, end = core.fixed_window_bounds()

    if args.source == "local":
        print(f"Membaca data lokal dari {args.data_dir} ...")
        raw = core.load_local_data(args.data_dir)
    else:
        from dotenv import load_dotenv

        load_dotenv(ROOT_DIR / ".env")
        url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY", "")
        table = os.environ.get("SUPABASE_TABLE", "sensor_data")
        if not url or not key:
            sys.exit(
                "SUPABASE_URL / SUPABASE_ANON_KEY (atau SUPABASE_KEY) belum diset di .env. "
                "Alternatif: jalankan dengan --source local jika punya data/sensor_data.db."
            )
        print(f"Mengambil data dari Supabase ({table}) ...")
        raw = core.fetch_supabase_range(url, key, table, start.isoformat(), end.isoformat())

    # Batasi ke periode recording sesuai PDF, apapun sumbernya
    return raw[(raw["timestamp"] >= start) & (raw["timestamp"] < end)].reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(
        description="Training Prophet + evaluasi (training 13-19 Juli, testing 20 Juli)"
    )
    parser.add_argument("--source", choices=["supabase", "local"], default="supabase",
                        help="Sumber data (default: supabase)")
    parser.add_argument("--data-dir", default=str(ROOT_DIR / "data"),
                        help="Folder sensor_data.db/.csv untuk --source local")
    args = parser.parse_args()

    core.require_prophet()
    from prophet.serialize import model_to_json

    raw = load_data(args)
    if raw.empty:
        sys.exit("Tidak ada data pada periode 13-20 Juli 2026 di sumber yang dipilih.")

    hourly = core.hourly_resample(raw)
    span = core.data_span_info(hourly)

    print(f"  Baris mentah      : {len(raw)}")
    print(f"  Titik per jam     : {span['points']}")
    print(f"  Rentang data      : {span['first']}  s/d  {span['last']}")
    print(f"  Durasi            : {span['days']:.2f} hari (periode PDF: {core.TRAIN_START} s/d {core.TEST_DATE})")

    print(f"\nEvaluasi sesuai PDF: training {core.TRAIN_START} s/d {core.TRAIN_END}, "
          f"testing {core.TEST_DATE} ...")
    evaluation = core.evaluate_fixed(hourly)
    metrics_out = {
        "train_window": f"{core.TRAIN_START} s/d {core.TRAIN_END}",
        "test_date": core.TEST_DATE,
        "forecast_window": f"{core.FORECAST_DATE} 00:00-06:00 WIB",
        "data_first": str(span["first"]),
        "data_last": str(span["last"]),
        "data_days": round(span["days"], 3),
        "hourly_points": span["points"],
    }
    if evaluation is None:
        print("  Data training/testing belum lengkap untuk evaluasi.")
    else:
        for target in core.TARGETS:
            m = evaluation[target]["metrics"]
            if m is None:
                print(f"  {target}: tidak ada titik yang bisa dibandingkan.")
                continue
            mape_str = f"{m['mape']:.2f}%" if m["mape"] is not None else "-"
            print(f"  {target:11s}: RMSE={m['rmse']:.3f}  MAE={m['mae']:.3f}  MAPE={mape_str}")
            metrics_out[target] = m

    print(f"\nMelatih model final pada seluruh periode {core.TRAIN_START} s/d {core.TEST_DATE} ...")
    MODELS_DIR.mkdir(exist_ok=True)
    for target in core.TARGETS:
        model = core.fit_model(hourly, target)
        out_path = MODELS_DIR / f"prophet_{target}.json"
        out_path.write_text(model_to_json(model), encoding="utf-8")
        print(f"  Model {target} disimpan -> {out_path}")

    METRICS_PATH.write_text(json.dumps(metrics_out, indent=2), encoding="utf-8")
    print(f"  Metrik evaluasi disimpan -> {METRICS_PATH}")
    print("\nSelesai. Jalankan 'python forecasting/predict.py' untuk prediksi "
          f"{core.FORECAST_DATE} 00:00-06:00 WIB.")


if __name__ == "__main__":
    main()
