"""Halaman Forecasting. Semua data dari Supabase. Dua mode jendela waktu:
tetap sesuai spesifikasi (default), atau dinamis 6 jam setelah data terakhir."""

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
st.caption("Sumber data: Supabase (hasil recording MQTT). Model: Prophet, resolusi per jam.")

MODE_FIXED = "Sesuai spesifikasi tugas (jendela tetap)"
MODE_DYNAMIC = "Dinamis (6 jam setelah data terakhir)"
mode = st.radio(
    "Jendela waktu forecasting",
    [MODE_FIXED, MODE_DYNAMIC],
    horizontal=True,
    help=(
        "Jendela tetap: training 13-19 Juli, testing 20 Juli, forecast 21 Juli "
        "00:00-06:00 WIB (6 jam setelah akhir periode recording). Mode dinamis: "
        "forecast 6 jam setelah timestamp data terakhir yang terekam."
    ),
)

if forecasting.Prophet is None:
    st.error(
        f"Package 'prophet' gagal di-import di lingkungan ini ({forecasting.PROPHET_IMPORT_ERROR}). "
        "Lihat README bagian 'Risiko deployment Prophet' -- sebagai fallback, jalankan "
        "forecasting/model_training.py dan forecasting/predict.py secara lokal."
    )
    st.stop()


# ---------------------------------------------------------------------------
# Helper render (dipakai kedua mode)
# ---------------------------------------------------------------------------
def render_span(span: dict):
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Data pertama", span["first"].strftime("%d %b %Y %H:%M"))
    with col2:
        st.metric("Data terakhir", span["last"].strftime("%d %b %Y %H:%M"))
    with col3:
        st.metric("Durasi", f"{span['days']:.1f} hari")
    with col4:
        st.metric("Titik per jam", f"{span['points']}")


def render_forecast_charts(history_df: pd.DataFrame, result: dict, actual_overlay: pd.DataFrame | None = None):
    for target, label, unit in (("suhu", "Suhu", "°C"), ("kelembaban", "Kelembaban", "%")):
        fdf = result[target]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=history_df["timestamp"], y=history_df[target],
                                 name="Aktual (historis)", mode="lines",
                                 line=dict(color="#636efa")))
        fig.add_trace(go.Scatter(x=fdf["ds"], y=fdf["yhat_upper"], line=dict(width=0), showlegend=False))
        fig.add_trace(go.Scatter(x=fdf["ds"], y=fdf["yhat_lower"], fill="tonexty", line=dict(width=0),
                                 name="Rentang prediksi", fillcolor="rgba(239,85,59,0.2)"))
        fig.add_trace(go.Scatter(x=fdf["ds"], y=fdf["yhat"], name=f"Prediksi {label}",
                                 mode="lines+markers", line=dict(color="#ef553b")))
        if actual_overlay is not None and not actual_overlay.empty:
            fig.add_trace(go.Scatter(x=actual_overlay["timestamp"], y=actual_overlay[target],
                                     name="Aktual (jendela forecast)", mode="lines+markers",
                                     line=dict(color="#00cc96", dash="dot")))
        fig.update_layout(title=f"{label} ({unit}): historis + prediksi 6 jam",
                          margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width="stretch")


def render_forecast_table(result: dict):
    table = forecasting.forecast_table(result)
    table["Jam"] = table["Jam"].dt.strftime("%Y-%m-%d %H:%M")
    st.subheader("Tabel hasil prediksi")
    st.dataframe(table.round(2), width="stretch", hide_index=True)


def render_evaluation(evaluation: dict):
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
        fig.update_layout(title=f"Prediksi vs Aktual (data testing): {label} ({unit})",
                          margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width="stretch")


def render_ai_commentary(span: dict, hourly: pd.DataFrame, result: dict):
    st.divider()
    st.subheader("Analisis AI atas hasil forecast (Groq / LLaMA3)")
    if not GROQ_CONFIGURED:
        st.info("Komentator AI belum dikonfigurasi. Isi GROQ_API_KEY di secrets untuk mengaktifkan.")
        return
    latest = {"timestamp": span["last"], "suhu": hourly["suhu"].iloc[-1],
              "kelembaban": hourly["kelembaban"].iloc[-1]}
    stats = data.summary_stats(hourly)
    summary = forecasting.forecast_summary_for_llm(result)
    commentary = llm.get_commentary(span["last"].isoformat(), latest, stats, summary)
    st.info(commentary)


# ---------------------------------------------------------------------------
# Mode TETAP: jendela kalender sesuai spesifikasi tugas
# ---------------------------------------------------------------------------
if mode == MODE_FIXED:
    st.markdown(
        f"""
Sesuai tabel spesifikasi penugasan:

- **Data training**: {forecasting.TRAIN_START} s/d {forecasting.TRAIN_END}
- **Data testing**: {forecasting.TEST_DATE} (untuk RMSE/MAE/MAPE)
- **Forecast**: {forecasting.FORECAST_DATE}, 00:00-06:00 WIB (per jam, 6 titik) --
  6 jam setelah akhir periode recording
"""
    )

    try:
        with st.spinner("Mengambil data periode 13-20 Juli dari Supabase..."):
            hourly = forecasting.load_hourly_fixed()
    except SupabaseQueryError as exc:
        st.error(f"Gagal mengambil data dari Supabase: {exc}")
        st.stop()

    if hourly.empty:
        st.warning("Tidak ada data pada periode 13-20 Juli 2026 di Supabase.")
        st.stop()

    span = forecasting.get_span_info(hourly)
    st.subheader("Data historis yang dipakai (periode recording 13-20 Juli)")
    render_span(span)

    st.divider()
    st.subheader(f"Prediksi {forecasting.FORECAST_DATE}, 00:00-06:00 WIB")
    with st.spinner("Melatih model Prophet pada periode 13-20 Juli..."):
        result = forecasting.forecast_fixed_cached()

    try:
        actual_fw = forecasting.load_forecast_window_actual()
    except SupabaseQueryError:
        actual_fw = pd.DataFrame()

    history_tail = hourly[hourly["timestamp"] >= span["last"] - pd.Timedelta(hours=48)]
    render_forecast_charts(history_tail, result, actual_overlay=actual_fw)
    render_forecast_table(result)

    if not actual_fw.empty:
        merged = actual_fw.merge(
            result["suhu"][["ds", "yhat"]].rename(columns={"ds": "timestamp", "yhat": "pred_suhu"}),
            on="timestamp", how="inner",
        ).merge(
            result["kelembaban"][["ds", "yhat"]].rename(columns={"ds": "timestamp", "yhat": "pred_kelembaban"}),
            on="timestamp", how="inner",
        ).dropna(subset=["suhu", "kelembaban"])
        if not merged.empty:
            st.subheader("Verifikasi: prediksi vs aktual pada jendela forecast")
            st.caption(
                "Karena recorder tetap berjalan setelah 20 Juli, data aktual "
                f"{forecasting.FORECAST_DATE} 00:00-06:00 sudah tersedia dan bisa "
                "dibandingkan langsung dengan prediksi di atas."
            )
            vcols = st.columns(2)
            m_suhu = forecasting.core.metrics(merged["suhu"], merged["pred_suhu"])
            m_kelembaban = forecasting.core.metrics(merged["kelembaban"], merged["pred_kelembaban"])
            with vcols[0]:
                st.metric("RMSE Suhu (jendela forecast)", f"{m_suhu['rmse']:.2f}")
            with vcols[1]:
                st.metric("RMSE Kelembaban (jendela forecast)", f"{m_kelembaban['rmse']:.2f}")

    st.divider()
    st.subheader(f"Evaluasi model pada data testing ({forecasting.TEST_DATE})")
    st.caption(
        f"Model dilatih HANYA pada data training ({forecasting.TRAIN_START} s/d "
        f"{forecasting.TRAIN_END}), lalu memprediksi per jam tanggal {forecasting.TEST_DATE} "
        "dan dibandingkan dengan aktualnya."
    )
    with st.spinner("Mengevaluasi model pada data testing..."):
        evaluation = forecasting.evaluate_fixed_cached()
    if evaluation is None:
        st.info("Data training/testing pada jendela tetap belum lengkap.")
    else:
        render_evaluation(evaluation)

    render_ai_commentary(span, hourly, result)

# ---------------------------------------------------------------------------
# Mode DINAMIS: 6 jam setelah data terakhir yang terekam
# ---------------------------------------------------------------------------
else:
    st.markdown(
        f"""
Mode ini melakukan forecast **{forecasting.FORECAST_HORIZON_HOURS} jam setelah timestamp
data terakhir yang terekam**, menggunakan seluruh data historis yang tersedia.
"""
    )

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
    st.subheader("Data historis yang dipakai (seluruh recording)")
    render_span(span)
    if not span["meets_min_days"]:
        st.warning(
            f"Durasi data baru {span['days']:.1f} hari -- syarat minimal "
            f"{forecasting.MIN_TRAINING_DAYS} hari data historis belum terpenuhi."
        )

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
    render_forecast_charts(history_tail, result)
    render_forecast_table(result)

    st.divider()
    st.subheader("Evaluasi model -- backtest data testing 24 jam terakhir")
    st.caption(
        "Analog dinamis dari pola spesifikasi (training 6 hari + testing 1 hari): model "
        "dilatih ulang TANPA 24 jam terakhir, lalu memprediksi jam-jam tersebut dan "
        "dibandingkan dengan aktualnya."
    )
    with st.spinner("Menjalankan backtest..."):
        evaluation = forecasting.backtest(hourly)
    if evaluation is None:
        st.info("Data belum cukup untuk backtest (butuh lebih dari 24 jam data).")
    else:
        render_evaluation(evaluation)

    render_ai_commentary(span, hourly, result)
