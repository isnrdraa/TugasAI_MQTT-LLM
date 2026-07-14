"""Halaman Monitoring -- entry point dashboard MQTT Recorder."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from lib import data, llm, mqtt_live
from lib.config import GROQ_CONFIGURED, TIMEZONE
from lib.supabase_client import SupabaseQueryError

st.set_page_config(page_title="Monitoring Suhu & Kelembaban", page_icon="🌡️", layout="wide")

st.title("🌡️ Monitoring Suhu & Kelembaban")
st.caption("Data IoT dari HiveMQ Cloud, direkam via recorder.py, dimirror ke Supabase.")

with st.sidebar:
    st.header("Pengaturan")
    range_key = st.radio("Rentang waktu", list(data.RANGE_PRESETS.keys()) + ["Custom"], index=2)
    custom_start = custom_end = None
    if range_key == "Custom":
        col1, col2 = st.columns(2)
        with col1:
            custom_start_date = st.date_input("Mulai", value=datetime.now(TIMEZONE).date())
            custom_start_time = st.time_input("Jam mulai", value=datetime.min.time())
        with col2:
            custom_end_date = st.date_input("Sampai", value=datetime.now(TIMEZONE).date())
            custom_end_time = st.time_input("Jam sampai", value=datetime.now(TIMEZONE).time())
        custom_start = datetime.combine(custom_start_date, custom_start_time, tzinfo=TIMEZONE)
        custom_end = datetime.combine(custom_end_date, custom_end_time, tzinfo=TIMEZONE)

    st.divider()
    live_mode = st.toggle("Mode real-time langsung (MQTT)", value=False,
                           help="Default: polling Supabase tiap 5 detik. Kalau aktif, "
                                "dashboard subscribe langsung ke broker HiveMQ Cloud "
                                "(satu koneksi dipakai bersama semua pengunjung).")

st_autorefresh(interval=5000, key="monitoring_autorefresh")

start_dt, end_dt = data.compute_range(range_key, custom_start, custom_end)
span_hours = (end_dt - start_dt).total_seconds() / 3600

try:
    if live_mode:
        live = mqtt_live.get_live_values()
        latest = {"timestamp": live["timestamp"], "suhu": live["suhu"], "kelembaban": live["kelembaban"]}
        status = "🟢 Terhubung" if live["connected"] else "🟠 Menghubungkan..."
        st.caption(f"Mode MQTT langsung -- {status}")
    else:
        latest = data.get_latest_reading()

    df = data.get_recent_window(start_dt.isoformat(), end_dt.isoformat(), span_hours)
except SupabaseQueryError as exc:
    st.error(f"Gagal mengambil data dari Supabase: {exc}")
    st.stop()

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Suhu terbaru", f"{latest['suhu']:.1f} °C" if latest and latest.get("suhu") is not None else "-")
with col2:
    st.metric("Kelembaban terbaru", f"{latest['kelembaban']:.1f} %" if latest and latest.get("kelembaban") is not None else "-")
with col3:
    ts = latest.get("timestamp") if latest else None
    st.metric("Waktu data terakhir", ts.strftime("%Y-%m-%d %H:%M:%S") if ts is not None else "-")

st.subheader("Statistik ringkasan")
stats = data.summary_stats(df)
stat_cols = st.columns(4)
for i, label in enumerate(("min", "max", "avg", "median")):
    with stat_cols[i]:
        suhu_v = stats["suhu"][label]
        kelembaban_v = stats["kelembaban"][label]
        st.metric(f"{label.capitalize()} Suhu", f"{suhu_v:.1f} °C" if suhu_v is not None else "-")
        st.metric(f"{label.capitalize()} Kelembaban", f"{kelembaban_v:.1f} %" if kelembaban_v is not None else "-")

st.subheader("Grafik historis")
if df.empty:
    st.info("Belum ada data pada rentang waktu ini.")
else:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["timestamp"], y=df["suhu"], name="Suhu (°C)", mode="lines"))
    fig.add_trace(go.Scatter(x=df["timestamp"], y=df["kelembaban"], name="Kelembaban (%)", mode="lines", yaxis="y2"))
    fig.update_layout(
        xaxis_title="Waktu",
        yaxis=dict(title="Suhu (°C)"),
        yaxis2=dict(title="Kelembaban (%)", overlaying="y", side="right"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig, width="stretch")

st.subheader("🤖 Analisis AI (Groq / LLaMA3)")
if not GROQ_CONFIGURED:
    st.info("Komentator AI belum dikonfigurasi. Isi GROQ_API_KEY di secrets untuk mengaktifkan.")
else:
    if "llm_nonce" not in st.session_state:
        st.session_state.llm_nonce = 0
    if st.button("Minta analisis ulang"):
        st.session_state.llm_nonce += 1

    latest_ts_str = latest["timestamp"].isoformat() if latest and latest.get("timestamp") is not None else "none"
    commentary = llm.get_commentary(latest_ts_str, latest or {}, stats, None, st.session_state.llm_nonce)
    st.info(commentary)
