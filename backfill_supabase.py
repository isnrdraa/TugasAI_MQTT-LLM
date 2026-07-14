#!/usr/bin/env python3
"""
Reconcile data/sensor_data.csv -> Supabase. Idempotent: memakai upsert
(on_conflict=timestamp), jadi aman dipanggil berkali-kali atau dijadwalkan
(lihat mqtt-recorder-reconcile.timer) tanpa menduplikasi baris yang sudah
berhasil ter-push sebelumnya. Berguna untuk menutup "bolong" akibat kegagalan
push yang lebih lama dari retry bawaan recorder.py.
"""

import csv
import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_TABLE = os.environ.get("SUPABASE_TABLE", "sensor_data")
CSV_PATH = BASE_DIR / os.environ.get("DATA_DIR", "data") / "sensor_data.csv"
LOG_PATH = BASE_DIR / os.environ.get("LOG_DIR", "logs") / "reconcile.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
BATCH_SIZE = 500

logger = logging.getLogger("reconcile_supabase")
logger.setLevel(logging.INFO)
_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(_handler)
logger.addHandler(logging.StreamHandler(sys.stdout))

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("SUPABASE_URL/SUPABASE_KEY belum diset di .env")
    sys.exit(1)

session = requests.Session()
session.headers.update(
    {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
)


def read_rows():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {
                "timestamp": row["timestamp"],
                "suhu": float(row["suhu"]) if row["suhu"] not in ("", None) else None,
                "kelembaban": float(row["kelembaban"]) if row["kelembaban"] not in ("", None) else None,
            }


def main():
    rows = list(read_rows())
    logger.info("Reconcile dimulai, total baris di CSV: %d", len(rows))

    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?on_conflict=timestamp"
    sent = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        try:
            resp = session.post(url, json=batch, timeout=30)
        except requests.exceptions.RequestException as exc:
            logger.error("Batch %d GAGAL (network/timeout): %s", i // BATCH_SIZE + 1, exc)
            sys.exit(1)

        if resp.status_code in (200, 201):
            sent += len(batch)
            logger.info("Batch %d: %d baris ter-upsert (total %d)", i // BATCH_SIZE + 1, len(batch), sent)
        else:
            logger.error("Batch %d GAGAL (HTTP %s): %s", i // BATCH_SIZE + 1, resp.status_code, resp.text[:300])
            sys.exit(1)

    logger.info("Reconcile selesai. %d/%d baris ter-upsert ke Supabase.", sent, len(rows))


if __name__ == "__main__":
    main()
