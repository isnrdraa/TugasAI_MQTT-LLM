#!/usr/bin/env python3
"""Prediksi suhu & kelembaban 6 JAM SETELAH WAKTU RECORDING TERAKHIR.

Memakai model hasil forecasting/model_training.py (jalankan itu dulu).
Timestamp target dihitung dari data terakhir di data/sensor_data.db/.csv,
BUKAN dari jam sekarang -- sesuai spesifikasi tugas.

Jalankan dari root project:
    python forecasting/predict.py

Output: tabel prediksi di terminal + data/forecast_6h.csv.
"""

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from forecasting import core

MODELS_DIR = Path(__file__).resolve().parent / "models"


def load_models():
    core.require_prophet()
    from prophet.serialize import model_from_json

    models = {}
    for target in core.TARGETS:
        path = MODELS_DIR / f"prophet_{target}.json"
        if not path.exists():
            sys.exit(
                f"Model {path} belum ada. Jalankan dulu: python forecasting/model_training.py"
            )
        models[target] = model_from_json(path.read_text(encoding="utf-8"))
    return models


def main():
    parser = argparse.ArgumentParser(description="Prediksi 6 jam setelah data terakhir")
    parser.add_argument("--data-dir", default=str(ROOT_DIR / "data"),
                        help="Folder berisi sensor_data.db/sensor_data.csv (default: data/)")
    parser.add_argument("--hours", type=int, default=core.FORECAST_HORIZON_HOURS,
                        help="Horizon prediksi dalam jam (default: 6)")
    args = parser.parse_args()

    raw = core.load_local_data(args.data_dir)
    last_ts = raw["timestamp"].iloc[-1]
    periods = core.forecast_periods(last_ts, args.hours)

    print(f"Data terakhir terekam : {last_ts}")
    print(f"Jendela prediksi      : {periods[0]}  s/d  {periods[-1]}  ({args.hours} jam)\n")

    models = load_models()
    result = {"periods": periods, "last_ts": last_ts}
    for target in core.TARGETS:
        result[target] = core.predict_at(models[target], periods, tz=periods.tz)

    table = core.forecast_table(result)
    table_print = table.copy()
    table_print["Jam"] = table_print["Jam"].dt.strftime("%Y-%m-%d %H:%M")
    print(table_print.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    out_path = Path(args.data_dir) / "forecast_6h.csv"
    table.to_csv(out_path, index=False)
    print(f"\nHasil prediksi disimpan -> {out_path}")
    print("Catatan: kirim email/laporan SEBELUM jendela 6 jam ini lewat, "
          "karena dosen membandingkan prediksi dengan data aktual setelah jam email.")


if __name__ == "__main__":
    main()
