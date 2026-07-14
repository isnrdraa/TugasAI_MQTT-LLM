"""Halaman Forecasting -- evaluasi Prophet dengan jendela tanggal kalender tetap."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plotly.graph_objects as go
import streamlit as st

from lib import forecasting
from lib.supabase_client import SupabaseQueryError

st.set_page_config(page_title="Forecasting", layout="wide")
st.title("Forecasting Suhu & Kelembaban")

st.markdown(
    f"""
Evaluasi ini memakai jendela tanggal kalender **tetap** sesuai spesifikasi tugas
(bukan "sekarang + 6 jam" yang bergeser tiap dibuka):

- **Data training**: {forecasting.TRAIN_START} s/d {forecasting.TRAIN_END}
- **Data testing**: {forecasting.TEST_DATE} (dipakai untuk hitung RMSE/MAE/MAPE)
- **Forecast**: {forecasting.FORECAST_DATE}, 01:00-06:00 WIB (per jam, 6 titik)

Model: **Prophet**. Halaman ini otomatis menampilkan hasil evaluasi penuh begitu
data tanggal {forecasting.TEST_DATE} & {forecasting.FORECAST_DATE} benar-benar
terekam -- tidak perlu redeploy.
"""
)

if forecasting.Prophet is None:
    st.error(
        f"Package 'prophet' gagal di-import di lingkungan ini ({forecasting.PROPHET_IMPORT_ERROR}). "
        "Lihat README bagian 'Risiko deployment Prophet' -- sebagai fallback, jalankan halaman "
        "ini secara lokal untuk keperluan laporan/demo."
    )
    st.stop()

try:
    availability = forecasting.get_data_availability()
except SupabaseQueryError as exc:
    st.error(f"Gagal mengambil data dari Supabase: {exc}")
    st.stop()

if not availability["has_enough_training"]:
    st.warning(
        f"Data training belum cukup (baru {availability['training_points']} titik per-jam, "
        f"minimal {forecasting.MIN_TRAINING_HOURLY_POINTS}). Forecasting akan otomatis muncul "
        "begitu recorder mengumpulkan lebih banyak data."
    )
    st.stop()

st.subheader(f"Forecast {forecasting.FORECAST_DATE}, 01:00-06:00 WIB")
with st.spinner("Melatih model Prophet..."):
    forecast_result = forecasting.forecast_next_period()

forecast_table = None
for target, label, unit in (("suhu", "Suhu", "°C"), ("kelembaban", "Kelembaban", "%")):
    fdf = forecast_result[target]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=fdf["ds"], y=fdf["yhat_upper"], line=dict(width=0), showlegend=False))
    fig.add_trace(go.Scatter(x=fdf["ds"], y=fdf["yhat_lower"], fill="tonexty", line=dict(width=0),
                              name="Rentang prediksi", fillcolor="rgba(99,110,250,0.2)"))
    fig.add_trace(go.Scatter(x=fdf["ds"], y=fdf["yhat"], name=f"Prediksi {label}", mode="lines+markers"))
    fig.update_layout(title=f"Prediksi {label} ({unit})", margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, width="stretch")

table = forecast_result["suhu"][["ds", "yhat"]].rename(columns={"ds": "Jam", "yhat": "Suhu (°C)"})
table["Kelembaban (%)"] = forecast_result["kelembaban"]["yhat"].values
table["Jam"] = table["Jam"].dt.strftime("%Y-%m-%d %H:%M")
st.subheader("Tabel hasil prediksi")
st.dataframe(table, width="stretch", hide_index=True)

st.divider()
st.subheader(f"Evaluasi pada data testing ({forecasting.TEST_DATE})")

if not availability["has_test_day"]:
    st.info(
        f"Data testing ({forecasting.TEST_DATE}) belum tersedia -- recorder masih berjalan dan "
        "belum mencapai tanggal tersebut. Bagian ini akan otomatis terisi begitu datanya masuk."
    )
else:
    with st.spinner("Mengevaluasi model pada data testing..."):
        evaluation = forecasting.evaluate_on_test_day()

    metric_cols = st.columns(6)
    labels = [("suhu", "Suhu"), ("kelembaban", "Kelembaban")]
    idx = 0
    for target, label in labels:
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
        fig.update_layout(title=f"Prediksi vs Aktual: {label} ({unit})", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width="stretch")
