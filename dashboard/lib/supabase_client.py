"""Wrapper REST read-only ke Supabase (PostgREST), pakai SUPABASE_ANON_KEY.
Dashboard ini tidak pernah menulis data -- hanya GET."""

from datetime import datetime

import pandas as pd
import requests

from lib.config import SUPABASE_ANON_KEY, SUPABASE_TABLE, SUPABASE_URL, TIMEZONE


class SupabaseQueryError(Exception):
    pass


def _headers() -> dict:
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    }


def _get(params: dict) -> list:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise SupabaseQueryError(
            "SUPABASE_URL/SUPABASE_ANON_KEY belum diset di secrets. "
            "Isi dashboard/.streamlit/secrets.toml (lihat secrets.toml.example)."
        )

    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    except requests.exceptions.RequestException as exc:
        raise SupabaseQueryError(f"Gagal menghubungi Supabase: {exc}") from exc

    if resp.status_code != 200:
        raise SupabaseQueryError(
            f"Supabase mengembalikan HTTP {resp.status_code}: {resp.text[:300]} "
            "-- cek RLS select policy untuk role anon sudah aktif (lihat supabase_schema.sql)."
        )
    return resp.json()


def _to_dataframe(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["timestamp", "suhu", "kelembaban"])
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(TIMEZONE)
        df["suhu"] = pd.to_numeric(df["suhu"], errors="coerce")
        df["kelembaban"] = pd.to_numeric(df["kelembaban"], errors="coerce")
    return df


def fetch_range(start_iso: str, end_iso: str, limit: int = 20000) -> pd.DataFrame:
    params = {
        "select": "timestamp,suhu,kelembaban",
        "timestamp": [f"gte.{start_iso}", f"lte.{end_iso}"],
        "order": "timestamp.asc",
        "limit": limit,
    }
    rows = _get(params)
    return _to_dataframe(rows)


def fetch_all_range(start_iso: str, end_iso: str, page_size: int = 10000, max_pages: int = 200) -> pd.DataFrame:
    """Ambil SEMUA baris pada rentang waktu, dengan pagination cursor di kolom
    timestamp (unique). Diperlukan untuk data training forecasting yang bisa
    jauh melebihi limit satu request (7+ hari x 4320 baris/hari), dan tetap
    benar meskipun server membatasi max-rows per response.
    """
    frames = []
    cursor = None
    for _ in range(max_pages):
        ts_filters = [f"gt.{cursor}" if cursor else f"gte.{start_iso}", f"lte.{end_iso}"]
        rows = _get({
            "select": "timestamp,suhu,kelembaban",
            "timestamp": ts_filters,
            "order": "timestamp.asc",
            "limit": page_size,
        })
        if not rows:
            break
        frames.append(pd.DataFrame(rows))
        cursor = rows[-1]["timestamp"]  # strictly increasing (unique constraint)
    if not frames:
        return _to_dataframe([])
    df = pd.concat(frames, ignore_index=True)
    return _to_dataframe(df.to_dict("records"))


def fetch_latest(n: int = 1) -> pd.DataFrame:
    params = {
        "select": "timestamp,suhu,kelembaban",
        "order": "timestamp.desc",
        "limit": n,
    }
    rows = _get(params)
    return _to_dataframe(rows)


def _fetch_single_timestamp(order: str) -> datetime | None:
    params = {"select": "timestamp", "order": order, "limit": 1}
    rows = _get(params)
    if not rows:
        return None
    return pd.to_datetime(rows[0]["timestamp"], utc=True).tz_convert(TIMEZONE)


def fetch_latest_timestamp() -> datetime | None:
    return _fetch_single_timestamp("timestamp.desc")


def fetch_first_timestamp() -> datetime | None:
    return _fetch_single_timestamp("timestamp.asc")
