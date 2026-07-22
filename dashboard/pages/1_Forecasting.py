"""Halaman Forecasting -- prediksi 6 jam SETELAH waktu recording terakhir
(dinamis, sesuai spesifikasi tugas), plus evaluasi backtest RMSE/MAE/MAPE."""

import sys
from pathlib import Path

_DASHBOARD_DIR = Path(__file__).resolve().parent.parent
_ROOT_DIR = _DASHBOARD_DIR.parent
for _p in (str(_ROOT_DIR), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lib import data, forecasting, llm
from lib.config import GROQ_CONFIGURED
from lib.supabase_client import SupabaseQueryError

st.set_page_config(page_title="Forecasting", layout="wide")
st.title("Forecasting Suhu & Kelembaban")

st.markdown(
    f"""
Sesuai spesifikasi tugas, forecasting dilakukan untuk **{forecasting.FORECAST_HORIZON_HOURS} jam
setelah waktu recording terakhir** (dinamis mengikuti data, bukan tanggal tetap),
menggunakan **seluruh data historis** yang telah dikumpulkan. Model: **Prophet**,
resolusi per jam.
"""
)

if forecasting.Prophet is None:
    st.error(
        f"Package 'prophet' gagal di-import di lingkungan ini ({forecasting.PROPHET_IMPORT_ERROR}). "
        "Lihat README bagian 'Risiko deployment Prophet' -- sebagai fallback, jalankan "
        "forecasting/model_training.py dan forecasting/predict.py secara lokal."
    )
    st.stop()

try:
    with st.spinner("Mengambil seluruh data historis dari Supabase..."):
        hourly = forecasting.load_hourly_history()
except SupabaseQueryError as exc:
    st.error(f"Gagal mengambil data dari Supabase: {exc}")
    st.stop()

if hourly.empty:
    st.warning("Belum ada data terekam sama sekali.")
    st.stop()

span = forecasting.get_span_info(hourly)

st.subheader("Data historis yang dipakai")
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Data pertama", span["first"].strftime("%d %b %Y %H:%M"))
with col2:
    st.metric("Data terakhir", span["last"].strftime("%d %b %Y %H:%M"))
with col3:
    st.metric("Durasi", f"{span['days']:.1f} hari")
with col4:
    st.metric("Titik per jam", f"{span['points']}")

if not span["meets_min_days"]:
    st.warning(
        f"Durasi data baru {span['days']:.1f} hari -- spesifikasi tugas meminta minimal "
        f"{forecasting.MIN_TRAINING_DAYS} hari data historis. Forecast tetap dihitung, "
        "tapi biarkan recorder berjalan sampai syarat terpenuhi."
    )
else:
    st.success(
        f"Syarat minimal {forecasting.MIN_TRAINING_DAYS} hari data historis terpenuhi "
        f"({span['days']:.1f} hari)."
    )

# ---------------------------------------------------------------------------
# Bagian 1: Forecast 6 jam setelah data terakhir
# ---------------------------------------------------------------------------
st.divider()
st.subheader(f"Prediksi {forecasting.FORECAST_HORIZON_HOURS} jam setelah data terakhir")

with st.spinner("Melatih model Prophet pada seluruh data historis..."):
    result = forecasting.forecast_next_6h(hourly)

periods = result["periods"]
st.caption(
    f"Data terakhir terekam **{result['last_ts'].strftime('%d %b %Y %H:%M %Z')}** -> "
    f"jendela prediksi **{periods[0].strftime('%d %b %Y %H:%M')}** s/d "
    f"**{periods[-1].strftime('%H:%M %Z')}** (per jam, {len(periods)} titik)."
)

history_tail = hourly[hourly["timestamp"] >= span["last"] - pd.Timedelta(hours=48)]
for target, label, unit in (("suhu", "Suhu", "°C"), ("kelembaban", "Kelembaban", "%")):
    fdf = result[target]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=history_tail["timestamp"], y=history_tail[target],
                             name="Aktual (48 jam terakhir)", mode="lines",
                             line=dict(color="#636efa")))
    fig.add_trace(go.Scatter(x=fdf["ds"], y=fdf["yhat_upper"], line=dict(width=0), showlegend=False))
    fig.add_trace(go.Scatter(x=fdf["ds"], y=fdf["yhat_lower"], fill="tonexty", line=dict(width=0),
                             name="Rentang prediksi", fillcolor="rgba(239,85,59,0.2)"))
    fig.add_trace(go.Scatter(x=fdf["ds"], y=fdf["yhat"], name=f"Prediksi {label}",
                             mode="lines+markers", line=dict(color="#ef553b")))
    fig.update_layout(title=f"{label} ({unit}): 48 jam terakhir + prediksi 6 jam ke depan",
                      margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, width="stretch")

table = forecasting.forecast_table(result)
table["Jam"] = table["Jam"].dt.strftime("%Y-%m-%d %H:%M")
st.subheader("Tabel hasil prediksi")
st.dataframe(table.round(2), width="stretch", hide_index=True)

st.info(
    "Untuk pengumpulan: kirim email/laporan **sebelum** jendela 6 jam di atas lewat. "
    "Dosen akan membandingkan prediksi ini dengan data aktual yang terekam 6 jam "
    "setelah jam email -- pastikan recorder tetap berjalan setelah submit."
)

# ---------------------------------------------------------------------------
# Bagian 2: Evaluasi backtest (RMSE/MAE/MAPE)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Evaluasi model -- prediksi vs aktual pada data testing (24 jam terakhir)")
st.caption(
    "Mengikuti pola spesifikasi tugas (training 6 hari + testing 1 hari), digeser dinamis: "
    "model dilatih ulang TANPA 24 jam terakhir (data testing), lalu diminta memprediksi "
    "jam-jam tersebut. Prediksi dibandingkan dengan data aktual untuk menghitung RMSE/MAE/MAPE."
)

with st.spinner("Menjalankan backtest..."):
    evaluation = forecasting.backtest(hourly)

if evaluation is None:
    st.info("Data belum cukup untuk backtest (butuh lebih dari 24 jam data).")
else:
    metric_cols = st.columns(6)
    idx = 0
    for target, label in (("suhu", "Suhu"), ("kelembaban", "Kelembaban")):
        m = evaluation[target]["metrics"]
        if m is None:
            st.warning(f"Tidak ada titik yang bisa dibandingkan untuk {label}.")
            continue
        with metric_cols[idx]:
            st.metric(f"RMSE {label}", f"{m['rmse']:.2f}")
        with metric_cols[idx + 1]:
            st.metric(f"MAE {label}", f"{m['mae']:.2f}")
        with metric_cols[idx + 2]:
            st.metric(f"MAPE {label}", f"{m['mape']:.1f}%" if m["mape"] is not None else "-")
        idx += 3

    for target, label, unit in (("suhu", "Suhu", "°C"), ("kelembaban", "Kelembaban", "%")):
        merged = evaluation[target]["merged"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=merged["ds"], y=merged[target], name="Aktual", mode="lines+markers"))
        fig.add_trace(go.Scatter(x=merged["ds"], y=merged["yhat"], name="Prediksi", mode="lines+markers"))
        fig.update_layout(title=f"Backtest -- Prediksi vs Aktual: {label} ({unit})",
                          margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------------------
# Bagian 3: Komentar AI atas hasil forecast
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Analisis AI atas hasil forecast (Groq / LLaMA3)")
if not GROQ_CONFIGURED:
    st.info("Komentator AI belum dikonfigurasi. Isi GROQ_API_KEY di secrets untuk mengaktifkan.")
else:
    latest = {"timestamp": span["last"], "suhu": hourly["suhu"].iloc[-1],
              "kelembaban": hourly["kelembaban"].iloc[-1]}
    stats = data.summary_stats(hourly)
    summary = forecasting.forecast_summary_for_llm(result)
    commentary = llm.get_commentary(span["last"].isoformat(), latest, stats, summary)
    st.info(commentary)
