#!/usr/bin/env python3
"""Prediksi suhu & kelembaban untuk jendela forecasting spesifikasi tugas:
21 Juli 2026, 00:00 - 06:00 WIB (per jam, 6 titik) -- yaitu 6 jam setelah
akhir periode recording 13-20 Juli.

Memakai model hasil forecasting/model_training.py (jalankan itu dulu).

Jalankan dari root project:
    python forecasting/predict.py

Output: tabel prediksi di terminal + data/forecast_6h.csv.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

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
    parser = argparse.ArgumentParser(
        description=f"Prediksi {core.FORECAST_DATE} 00:00-06:00 WIB (jendela spesifikasi tugas)"
    )
    parser.add_argument("--out-dir", default=str(ROOT_DIR / "data"),
                        help="Folder output forecast_6h.csv (default: data/)")
    args = parser.parse_args()

    periods = core.fixed_forecast_periods()
    print(f"Periode recording : {core.TRAIN_START} s/d {core.TEST_DATE} (sumber: Supabase, via model tersimpan)")
    print(f"Jendela prediksi  : {periods[0]}  s/d  {periods[-1] + pd.Timedelta(hours=1)}  (6 jam)\n")

    models = load_models()
    result = {"periods": periods}
    for target in core.TARGETS:
        result[target] = core.predict_at(models[target], periods, tz=periods.tz)

    table = core.forecast_table(result)
    table_print = table.copy()
    table_print["Jam"] = table_print["Jam"].dt.strftime("%Y-%m-%d %H:%M")
    print(table_print.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "forecast_6h.csv"
    table.to_csv(out_path, index=False)
    print(f"\nHasil prediksi disimpan -> {out_path}")


if __name__ == "__main__":
    main()
