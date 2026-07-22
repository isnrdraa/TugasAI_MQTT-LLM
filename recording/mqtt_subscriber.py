#!/usr/bin/env python3
"""
MQTT Recorder - merekam data suhu & kelembaban dari HiveMQ Cloud (MQTT/TLS)
ke SQLite dan CSV setiap N detik. Hanya untuk logging, tidak melakukan kontrol apapun.
"""

import csv
import logging
import logging.handlers
import os
import signal
import sqlite3
import ssl
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

# File ini ada di recording/, sedangkan .env, data/, dan logs/ ada di root project.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USERNAME = os.environ["MQTT_USERNAME"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]
TOPIC_SUHU = os.environ.get("MQTT_TOPIC_SUHU", "tas_ai_surya_fsm_uksw/suhu")
TOPIC_KELEMBABAN = os.environ.get("MQTT_TOPIC_KELEMBABAN", "tas_ai_surya_fsm_uksw/kelembaban")
WRITE_INTERVAL_SECONDS = float(os.environ.get("WRITE_INTERVAL_SECONDS", "20"))
TIMEZONE = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Jakarta"))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_TABLE = os.environ.get("SUPABASE_TABLE", "sensor_data")
SUPABASE_TIMEOUT_SECONDS = float(os.environ.get("SUPABASE_TIMEOUT_SECONDS", "10"))
SUPABASE_MAX_ATTEMPTS = int(os.environ.get("SUPABASE_MAX_ATTEMPTS", "3"))
SUPABASE_RETRY_BACKOFF_SECONDS = float(os.environ.get("SUPABASE_RETRY_BACKOFF_SECONDS", "2"))
SUPABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_KEY)

DATA_DIR = BASE_DIR / os.environ.get("DATA_DIR", "data")
LOG_DIR = BASE_DIR / os.environ.get("LOG_DIR", "logs")
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "sensor_data.db"
CSV_PATH = DATA_DIR / "sensor_data.csv"
LOG_PATH = LOG_DIR / "recorder.log"

# --------------------------------------------------------------------------
# Logging: rotasi harian, simpan maks 14 hari
# --------------------------------------------------------------------------
logger = logging.getLogger("mqtt_recorder")
logger.setLevel(logging.INFO)

_file_handler = logging.handlers.TimedRotatingFileHandler(
    LOG_PATH, when="midnight", backupCount=14, encoding="utf-8", utc=False
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
logger.addHandler(_file_handler)

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
logger.addHandler(_stream_handler)

# --------------------------------------------------------------------------
# State bersama antara thread MQTT (on_message) dan thread writer
# --------------------------------------------------------------------------
_state_lock = threading.Lock()
_latest = {"suhu": None, "kelembaban": None}

_stats_lock = threading.Lock()
_rows_written_this_hour = 0
_supabase_ok_this_hour = 0
_supabase_fail_this_hour = 0
_current_hour_label = None

_shutdown_event = threading.Event()


def _parse_float(payload: bytes):
    try:
        return float(payload.decode("utf-8").strip())
    except (ValueError, UnicodeDecodeError):
        return None


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0 or str(reason_code) == "Success":
        logger.info("Terhubung ke broker MQTT %s:%s", MQTT_HOST, MQTT_PORT)
        client.subscribe([(TOPIC_SUHU, 0), (TOPIC_KELEMBABAN, 0)])
        logger.info("Subscribe ke topik: %s, %s", TOPIC_SUHU, TOPIC_KELEMBABAN)
    else:
        logger.error("Gagal connect ke broker MQTT, reason_code=%s", reason_code)


def on_disconnect(client, userdata, disconnect_flags, reason_code=None, properties=None):
    # Jangan biarkan exception di sini mematikan proses; paho akan auto-reconnect
    # selama loop (loop_start/loop_forever) tetap berjalan.
    logger.warning("Terputus dari broker MQTT (reason_code=%s). Akan mencoba reconnect otomatis.", reason_code)


def on_message(client, userdata, msg):
    value = _parse_float(msg.payload)
    if value is None:
        logger.warning("Payload tidak valid di topik %s: %r", msg.topic, msg.payload)
        return

    with _state_lock:
        if msg.topic == TOPIC_SUHU:
            _latest["suhu"] = value
        elif msg.topic == TOPIC_KELEMBABAN:
            _latest["kelembaban"] = value


def on_log(client, userdata, level, buf):
    logger.debug("paho-mqtt: %s", buf)


# --------------------------------------------------------------------------
# SQLite: satu koneksi persisten untuk seluruh lifetime proses (long-running safe)
# --------------------------------------------------------------------------
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            suhu REAL,
            kelembaban REAL
        );
        """
    )
    conn.commit()
    return conn


def init_csv():
    is_new = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
    if is_new:
        with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp", "suhu", "kelembaban"])


def write_row(conn: sqlite3.Connection, timestamp_iso: str, suhu, kelembaban):
    conn.execute(
        "INSERT INTO sensor_data (timestamp, suhu, kelembaban) VALUES (?, ?, ?)",
        (timestamp_iso, suhu, kelembaban),
    )
    conn.commit()

    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([timestamp_iso, suhu if suhu is not None else "", kelembaban if kelembaban is not None else ""])


def init_supabase_session():
    if not SUPABASE_ENABLED:
        logger.warning("Push ke Supabase DINONAKTIFKAN (SUPABASE_URL/SUPABASE_KEY tidak diset di .env)")
        return None

    session = requests.Session()
    session.headers.update(
        {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
    )
    logger.info("Push ke Supabase AKTIF -> %s/rest/v1/%s", SUPABASE_URL, SUPABASE_TABLE)
    return session


def push_to_supabase(session, timestamp_iso: str, suhu, kelembaban) -> bool:
    if session is None:
        return False

    # on_conflict=timestamp + merge-duplicates: upsert, supaya retry tidak pernah
    # menduplikasi baris (termasuk kasus attempt pertama sukses di server tapi
    # responsnya hilang karena timeout di sisi klien).
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?on_conflict=timestamp"
    payload = {"timestamp": timestamp_iso, "suhu": suhu, "kelembaban": kelembaban}
    headers = {"Prefer": "resolution=merge-duplicates,return=minimal"}

    for attempt in range(1, SUPABASE_MAX_ATTEMPTS + 1):
        try:
            resp = session.post(url, json=payload, headers=headers, timeout=SUPABASE_TIMEOUT_SECONDS)
            if resp.status_code in (200, 201):
                return True
            if 400 <= resp.status_code < 500:
                # Error klien (payload salah, auth, dll) - retry tidak akan membantu.
                logger.error(
                    "Gagal push ke Supabase (HTTP %s, tidak di-retry): %s", resp.status_code, resp.text[:300]
                )
                return False
            logger.warning(
                "Push ke Supabase gagal (HTTP %s), percobaan %d/%d",
                resp.status_code, attempt, SUPABASE_MAX_ATTEMPTS,
            )
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "Push ke Supabase gagal (network/timeout), percobaan %d/%d: %s",
                attempt, SUPABASE_MAX_ATTEMPTS, exc,
            )

        if attempt < SUPABASE_MAX_ATTEMPTS:
            _shutdown_event.wait(timeout=SUPABASE_RETRY_BACKOFF_SECONDS)

    logger.error(
        "Push ke Supabase GAGAL setelah %d percobaan untuk timestamp=%s (akan ditangkap oleh reconcile job)",
        SUPABASE_MAX_ATTEMPTS, timestamp_iso,
    )
    return False


def _bump_hourly_counter(supabase_ok: bool | None):
    global _rows_written_this_hour, _current_hour_label
    global _supabase_ok_this_hour, _supabase_fail_this_hour
    hour_label = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:00")
    with _stats_lock:
        if _current_hour_label is None:
            _current_hour_label = hour_label
        if hour_label != _current_hour_label:
            logger.info(
                "Ringkasan jam %s: %d baris tersimpan (lokal), %d berhasil ke Supabase, %d gagal ke Supabase",
                _current_hour_label, _rows_written_this_hour, _supabase_ok_this_hour, _supabase_fail_this_hour,
            )
            _current_hour_label = hour_label
            _rows_written_this_hour = 0
            _supabase_ok_this_hour = 0
            _supabase_fail_this_hour = 0
        _rows_written_this_hour += 1
        if supabase_ok is True:
            _supabase_ok_this_hour += 1
        elif supabase_ok is False:
            _supabase_fail_this_hour += 1


def writer_loop(conn: sqlite3.Connection, supabase_session):
    logger.info("Writer thread dimulai, interval=%ss", WRITE_INTERVAL_SECONDS)
    next_run = time.monotonic()
    while not _shutdown_event.is_set():
        next_run += WRITE_INTERVAL_SECONDS
        supabase_ok = None
        try:
            with _state_lock:
                suhu = _latest["suhu"]
                kelembaban = _latest["kelembaban"]

            timestamp_iso = datetime.now(TIMEZONE).isoformat()
            write_row(conn, timestamp_iso, suhu, kelembaban)

            if suhu is None or kelembaban is None:
                logger.warning(
                    "Baris tersimpan dengan nilai kosong (suhu=%s, kelembaban=%s) pada %s",
                    suhu, kelembaban, timestamp_iso,
                )
        except Exception:
            logger.exception("Error saat menulis data ke DB/CSV")
        else:
            if SUPABASE_ENABLED:
                supabase_ok = push_to_supabase(supabase_session, timestamp_iso, suhu, kelembaban)

        _bump_hourly_counter(supabase_ok)

        sleep_time = next_run - time.monotonic()
        if sleep_time > 0:
            _shutdown_event.wait(timeout=sleep_time)
        else:
            next_run = time.monotonic()

    # Log ringkasan jam terakhir sebelum keluar
    with _stats_lock:
        if _current_hour_label is not None:
            logger.info(
                "Ringkasan jam %s (final): %d baris tersimpan (lokal), %d berhasil ke Supabase, %d gagal ke Supabase",
                _current_hour_label, _rows_written_this_hour, _supabase_ok_this_hour, _supabase_fail_this_hour,
            )


def main():
    logger.info("=== MQTT Recorder starting ===")

    init_csv()
    conn = init_db()
    supabase_session = init_supabase_session()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"mqtt-recorder-{os.getpid()}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.on_log = on_log

    # Auto-reconnect bawaan paho-mqtt: backoff antara 1s dan 120s.
    client.reconnect_delay_set(min_delay=1, max_delay=120)

    def handle_signal(signum, _frame):
        logger.info("Menerima signal %s, shutting down...", signum)
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    except Exception:
        logger.exception("Gagal melakukan koneksi awal ke broker, akan tetap dicoba oleh loop")

    client.loop_start()  # network loop di thread terpisah, auto-reconnect otomatis

    writer_thread = threading.Thread(target=writer_loop, args=(conn, supabase_session), daemon=True)
    writer_thread.start()

    try:
        while not _shutdown_event.is_set():
            _shutdown_event.wait(timeout=1)
    finally:
        _shutdown_event.set()
        writer_thread.join(timeout=WRITE_INTERVAL_SECONDS + 5)
        client.loop_stop()
        client.disconnect()
        conn.close()
        if supabase_session is not None:
            supabase_session.close()
        logger.info("=== MQTT Recorder stopped ===")


if __name__ == "__main__":
    main()
