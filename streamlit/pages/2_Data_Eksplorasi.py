"""Halaman Data Eksplorasi -- statistik deskriptif, distribusi, korelasi, boxplot."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plotly.express as px
import streamlit as st

from lib import data
from lib.config import TIMEZONE
from lib.supabase_client import SupabaseQueryError

st.set_page_config(page_title="Data Eksplorasi", layout="wide")
st.title("Data Eksplorasi")
st.caption("Statistik deskriptif dari seluruh data yang sudah terekam (maks. 7 hari terakhir).")

start_dt, end_dt = data.compute_range("7 Hari")

try:
    df = data.get_recent_window(start_dt.isoformat(), end_dt.isoformat(), 24)
except SupabaseQueryError as exc:
    st.error(f"Gagal mengambil data dari Supabase: {exc}")
    st.stop()

if df.empty:
    st.info("Belum ada data untuk dieksplorasi.")
    st.stop()

st.subheader("Statistik deskriptif")
st.dataframe(df[["suhu", "kelembaban"]].describe(), width="stretch")

st.subheader("Distribusi data")
col1, col2 = st.columns(2)
with col1:
    st.plotly_chart(px.histogram(df, x="suhu", nbins=30, title="Histogram Suhu (°C)"), width="stretch")
with col2:
    st.plotly_chart(px.histogram(df, x="kelembaban", nbins=30, title="Histogram Kelembaban (%)"), width="stretch")

st.subheader("Korelasi suhu vs kelembaban")
corr_df = df[["suhu", "kelembaban"]].dropna()
if len(corr_df) >= 2:
    corr = corr_df["suhu"].corr(corr_df["kelembaban"])
    st.metric("Koefisien korelasi (Pearson)", f"{corr:.3f}")
    st.plotly_chart(
        px.scatter(corr_df, x="suhu", y="kelembaban", trendline="ols",
                   title="Scatter Suhu vs Kelembaban"),
        width="stretch",
    )
else:
    st.info("Belum cukup data untuk menghitung korelasi.")

st.subheader("Boxplot per jam dalam hari")
df_box = df.copy()
df_box["jam"] = df_box["timestamp"].dt.hour
col3, col4 = st.columns(2)
with col3:
    st.plotly_chart(px.box(df_box, x="jam", y="suhu", title="Suhu per jam"), width="stretch")
with col4:
    st.plotly_chart(px.box(df_box, x="jam", y="kelembaban", title="Kelembaban per jam"), width="stretch")

st.subheader("Boxplot per tanggal")
df_box["tanggal"] = df_box["timestamp"].dt.strftime("%Y-%m-%d")
col5, col6 = st.columns(2)
with col5:
    st.plotly_chart(px.box(df_box, x="tanggal", y="suhu", title="Suhu per tanggal"), width="stretch")
with col6:
    st.plotly_chart(px.box(df_box, x="tanggal", y="kelembaban", title="Kelembaban per tanggal"), width="stretch")
