# MQTT Recorder

Sistem recording (logging) data IoT suhu & kelembaban dari broker **HiveMQ Cloud**
(MQTT over TLS) ke **SQLite** dan **CSV**, berjalan nonstop sebagai systemd service.
Sistem ini **hanya membaca/mencatat data**, tidak melakukan publish/kontrol apapun.

## Struktur folder

```
~/mqtt-recorder/
  recorder.py                     # script utama
  backfill_supabase.py            # reconcile CSV -> Supabase (idempotent)
  requirements.txt
  .env                             # kredensial asli (JANGAN dibagikan)
  .env.example                     # template tanpa kredensial
  supabase_schema.sql              # SQL setup tabel Supabase
  mqtt-recorder.service            # unit file systemd (recorder utama)
  mqtt-recorder-reconcile.service  # unit file systemd (reconcile, oneshot)
  mqtt-recorder-reconcile.timer    # jadwal reconcile (tiap 6 jam)
  README.md
  venv/                            # virtualenv Python (dibuat saat instalasi)
  data/
    sensor_data.db
    sensor_data.csv
  logs/
    recorder.log            # log aplikasi (rotasi harian, 14 hari)
    reconcile.log           # log tiap kali reconcile jalan
    service.out.log         # stdout systemd
    service.err.log         # stderr systemd
  streamlit/                       # dashboard publik (deploy terpisah, lihat bagian akhir)
    streamlit_app.py                # halaman Monitoring (entry point)
    pages/
      1_Forecasting.py
      2_Data_Eksplorasi.py
    lib/
      config.py, supabase_client.py, data.py, mqtt_live.py, llm.py, forecasting.py
    requirements.txt
    .streamlit/secrets.toml.example
  .gitignore
```

## Cara kerja singkat

- Subscribe ke 2 topik: `tas_ai_surya_fsm_uksw/suhu` dan `tas_ai_surya_fsm_uksw/kelembaban`.
- Nilai terakhir tiap topik disimpan in-memory (thread-safe).
- Setiap 20 detik (thread terpisah dari `on_message`), satu baris data
  (`timestamp` ISO, zona waktu Asia/Jakarta, `suhu`, `kelembaban`) ditulis ke
  SQLite (`data/sensor_data.db`) dan di-append ke CSV (`data/sensor_data.csv`).
  Jika salah satu nilai belum pernah diterima, kolomnya disimpan NULL/kosong.
- Koneksi MQTT auto-reconnect (paho-mqtt `reconnect_delay_set` + `loop_start`);
  disconnect sementara hanya dicatat di log, proses tidak mati.
- Koneksi SQLite dibuka sekali di awal dan dipakai selama proses berjalan
  (bukan buka/tutup tiap tulis) — aman untuk proses yang jalan berhari-hari.
- Baris yang sama juga di-push ke **Supabase** (opsional, lihat bagian 2b) sebagai
  mirror tambahan. Kalau push gagal, di-retry singkat otomatis; kalau masih gagal,
  ditangkap oleh reconcile timer (lihat "Penanganan gagal push ke Supabase").

## 1. Instalasi dependency

```bash
cd ~/mqtt-recorder
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

## 2. Setup `.env`

File `.env` di folder ini **sudah diisi** dengan kredensial HiveMQ Cloud yang diberikan.
Jika perlu membuat ulang di server lain, salin dari template:

```bash
cp .env.example .env
nano .env   # isi MQTT_USERNAME dan MQTT_PASSWORD
chmod 600 .env
```

Isi `.env`:

```
MQTT_HOST=aifsmukswsurya-397a2de2.a03.euc1.aws.hivemq.cloud
MQTT_PORT=8883
MQTT_USERNAME=mhsw
MQTT_PASSWORD=********
MQTT_TOPIC_SUHU=tas_ai_surya_fsm_uksw/suhu
MQTT_TOPIC_KELEMBABAN=tas_ai_surya_fsm_uksw/kelembaban
WRITE_INTERVAL_SECONDS=20
TIMEZONE=Asia/Jakarta
DATA_DIR=data
LOG_DIR=logs
```

## 2b. Setup Supabase (opsional)

Kalau ingin data juga ter-mirror ke Supabase (selain SQLite & CSV):

1. Buat tabel di **Supabase Dashboard > SQL Editor**, jalankan isi `supabase_schema.sql`:

   ```sql
   create table if not exists public.sensor_data (
     id bigint generated always as identity primary key,
     timestamp timestamptz not null,
     suhu double precision,
     kelembaban double precision,
     inserted_at timestamptz not null default now()
   );

   create index if not exists sensor_data_timestamp_idx on public.sensor_data (timestamp);

   -- wajib: basis upsert supaya retry/reconcile tidak pernah menduplikasi baris
   alter table public.sensor_data
     add constraint sensor_data_timestamp_key unique (timestamp);
   ```

2. Isi di `.env` (Project Settings > API di dashboard Supabase):

   ```
   SUPABASE_URL=https://xxxxx.supabase.co
   SUPABASE_KEY=<< service_role key, BUKAN anon key >>
   SUPABASE_TABLE=sensor_data
   SUPABASE_TIMEOUT_SECONDS=10
   SUPABASE_MAX_ATTEMPTS=3
   SUPABASE_RETRY_BACKOFF_SECONDS=2
   ```

   Kosongkan `SUPABASE_URL`/`SUPABASE_KEY` untuk menonaktifkan push ke Supabase
   sepenuhnya (recorder akan tetap jalan normal, hanya menulis ke SQLite & CSV).

`service_role` key dipakai (bukan `anon`) karena recorder jalan di backend/VPS dan
perlu insert tanpa terblokir Row Level Security.

## 3. Test manual (opsional, sebelum jadi service)

```bash
cd ~/mqtt-recorder
source venv/bin/activate
python recorder.py
# tekan Ctrl+C untuk berhenti setelah yakin data masuk
```

## 4. Install & jalankan sebagai systemd service

Service dikonfigurasi jalan sebagai user **non-root** yang sudah ada (`isnrdra`),
sehingga tidak perlu membuat user baru. Jika Anda ingin memakai user khusus (opsional,
lebih terisolasi), lihat bagian "User khusus (opsional)" di bawah.

```bash
sudo cp ~/mqtt-recorder/mqtt-recorder.service /etc/systemd/system/mqtt-recorder.service
sudo systemctl daemon-reload
sudo systemctl enable mqtt-recorder.service   # auto-start saat boot
sudo systemctl start mqtt-recorder.service
```

### Cek status service

```bash
systemctl status mqtt-recorder.service
```

### Lihat log real-time (systemd journal)

```bash
journalctl -u mqtt-recorder -f
```

### Lihat log aplikasi (rotasi harian, 14 hari)

```bash
tail -f ~/mqtt-recorder/logs/recorder.log
```

### Install reconcile timer (hanya jika Supabase dipakai)

Timer ini menjalankan `backfill_supabase.py` tiap 6 jam untuk menutup baris yang
gagal push ke Supabase (mis. Supabase sempat down lebih lama dari retry bawaan
`recorder.py`). Aman dijalankan berkali-kali (idempotent, pakai upsert).

```bash
sudo cp ~/mqtt-recorder/mqtt-recorder-reconcile.service /etc/systemd/system/
sudo cp ~/mqtt-recorder/mqtt-recorder-reconcile.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mqtt-recorder-reconcile.timer
```

## 5. Verifikasi data masuk

### Cek isi CSV terbaru

```bash
tail -n 20 ~/mqtt-recorder/data/sensor_data.csv
```

### Query SQLite

```bash
sqlite3 ~/mqtt-recorder/data/sensor_data.db \
  "SELECT * FROM sensor_data ORDER BY id DESC LIMIT 10;"
```

Hitung total baris (berguna untuk bukti "recording jalan 7 hari penuh" di laporan —
idealnya sekitar `7 hari * 24 jam * 3600 detik / 20 detik ≈ 30240` baris):

```bash
sqlite3 ~/mqtt-recorder/data/sensor_data.db "SELECT COUNT(*) FROM sensor_data;"
```

Cek ringkasan jumlah baris per jam dari log aplikasi (termasuk hitung sukses/gagal
ke Supabase):

```bash
grep "Ringkasan jam" ~/mqtt-recorder/logs/recorder.log
```

### Cek data di Supabase (jika dipakai)

Lewat Table Editor di dashboard, atau via REST API:

```bash
curl -s "$SUPABASE_URL/rest/v1/sensor_data?select=*&order=id.desc&limit=10" \
  -H "apikey: $SUPABASE_KEY" -H "Authorization: Bearer $SUPABASE_KEY"
```

## 6. Operasional lain

Restart service (mis. setelah update `.env` atau `recorder.py`):

```bash
sudo systemctl restart mqtt-recorder.service
```

Stop service:

```bash
sudo systemctl stop mqtt-recorder.service
```

Nonaktifkan auto-start saat boot:

```bash
sudo systemctl disable mqtt-recorder.service
```

## User khusus (opsional)

Jika ingin service berjalan dengan user terisolasi (bukan `isnrdra`):

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin mqttrecorder
sudo mkdir -p /home/mqttrecorder/mqtt-recorder
sudo cp -r ~/mqtt-recorder/* /home/mqttrecorder/mqtt-recorder/
sudo chown -R mqttrecorder:mqttrecorder /home/mqttrecorder/mqtt-recorder
sudo chmod 600 /home/mqttrecorder/mqtt-recorder/.env
```

Lalu edit `mqtt-recorder.service`: ganti `User=`, `Group=`, `WorkingDirectory=`,
dan path `ExecStart=` agar mengarah ke `/home/mqttrecorder/mqtt-recorder/...`,
lalu ulangi langkah instalasi service di atas.

## Penanganan gagal push ke Supabase

SQLite & CSV adalah sumber data utama dan **selalu** ditulis lengkap tiap siklus,
terlepas dari status Supabase. Push ke Supabase punya 2 lapis penanganan gagal:

1. **Retry otomatis di `recorder.py`** — tiap baris dicoba push sampai
   `SUPABASE_MAX_ATTEMPTS` kali (default 3) dengan jeda `SUPABASE_RETRY_BACKOFF_SECONDS`
   detik, untuk menutup gangguan jaringan sesaat. Error HTTP 4xx tidak di-retry
   (biasanya berarti config salah, mis. key/tabel tidak valid).
2. **Reconcile timer** (`mqtt-recorder-reconcile.timer`, tiap 6 jam) — membaca ulang
   seluruh `data/sensor_data.csv` dan meng-upsert ke Supabase. Menutup gangguan yang
   lebih lama dari retry di atas (mis. Supabase down berjam-jam), tanpa duplikat
   berkat `on_conflict=timestamp` + unique constraint di kolom `timestamp`.

Cek status & histori:

```bash
systemctl list-timers mqtt-recorder-reconcile.timer   # jadwal jalan berikutnya
journalctl -u mqtt-recorder-reconcile -f               # log tiap kali dijalankan systemd
tail -f ~/mqtt-recorder/logs/reconcile.log             # log detail dari script
```

Trigger reconcile manual kapan saja (mis. setelah tahu Supabase sempat down):

```bash
sudo systemctl start mqtt-recorder-reconcile.service
```

## Troubleshooting singkat

- **Service langsung `activating (auto-restart)` berulang**: cek
  `journalctl -u mqtt-recorder -n 50` — biasanya karena `.env` belum lengkap
  atau `venv/` belum dibuat.
- **Tidak ada data baru masuk tapi service `active (running)`**: cek
  `logs/recorder.log` untuk pesan "Terputus dari broker" — pastikan kredensial
  HiveMQ Cloud masih valid dan tidak ada pembatasan koneksi di dashboard HiveMQ.
- **Payload tidak valid**: cek log warning "Payload tidak valid di topik" —
  berarti sensor mengirim data bukan angka.

## Dashboard Streamlit (deploy terpisah)

Dashboard di folder `streamlit/` adalah aplikasi **terpisah** dari `recorder.py` —
tidak jalan di VPS, tapi di-deploy ke **Streamlit Community Cloud** (gratis, dapat
URL publik). Dashboard hanya **membaca** data lewat Supabase REST API, tidak pernah
menulis, dan memakai `anon` key (bukan `service_role` yang dipakai `recorder.py`).

Tiga halaman: **Monitoring** (grafik historis, data terbaru, statistik, filter
rentang waktu, mode real-time via polling Supabase 5 detik atau opsional MQTT
langsung), **Forecasting** (Prophet, jendela tanggal tetap 13-21 Juli 2026 sesuai
spek tugas, otomatis terisi begitu tanggalnya tercapai), **Data Eksplorasi**
(statistik deskriptif, histogram, korelasi, boxplot). Ada juga komentator AI
(Groq API, model LLaMA3 gratis).

### 1. Aktifkan akses baca (RLS) untuk dashboard

Dashboard publik tidak boleh pakai `service_role` key. Jalankan di **Supabase
Dashboard > SQL Editor** (bagian bawah `supabase_schema.sql`):

```sql
alter table public.sensor_data enable row level security;

create policy "Allow anon read" on public.sensor_data
  for select
  to anon
  using (true);
```

Key `anon` sudah otomatis tersedia di **Project Settings > API** (tidak perlu
bikin key baru) — pakai nilai itu, bukan `service_role`.

### 2. Test lokal sebelum deploy

```bash
cd ~/mqtt-recorder/streamlit
python3 -m venv .venv-dash
source .venv-dash/bin/activate
pip install -r requirements.txt

cp .streamlit/secrets.toml.example .streamlit/secrets.toml
nano .streamlit/secrets.toml   # isi SUPABASE_URL, SUPABASE_ANON_KEY, GROQ_API_KEY, dll

streamlit run streamlit_app.py
```

Buka `http://localhost:8501` — cek halaman Monitoring update tiap ±20 detik
(sesuai interval tulis recorder), coba toggle "Mode real-time langsung (MQTT)",
ganti filter rentang waktu, dan klik "Minta analisis ulang" untuk uji komentator AI.

Halaman **Forecasting** sebelum tanggal 20/21 Juli 2026 tercapai akan tetap
menampilkan grafik forecast (dari data yang ada sejauh ini), tapi bagian evaluasi
(RMSE/MAE/MAPE) akan menampilkan pesan "data testing belum tersedia" — ini normal,
bukan bug, dan akan otomatis terisi begitu recorder merekam tanggal tersebut
(tanpa perlu ubah kode atau redeploy).

### 3. Push ke GitHub & deploy ke Streamlit Community Cloud

Folder ini belum jadi git repo. Di root `~/mqtt-recorder`:

```bash
git init
git add recorder.py requirements.txt .env.example supabase_schema.sql \
        backfill_supabase.py mqtt-recorder.service mqtt-recorder-reconcile.service \
        mqtt-recorder-reconcile.timer README.md .gitignore streamlit
git commit -m "Initial commit: mqtt recorder + streamlit dashboard"
git remote add origin <url-repo-github-anda>
git push -u origin main
```

**Jangan** `git add -A` atau `git add .` — pastikan `.env` (kredensial asli) tidak
pernah ke-commit (sudah di-`.gitignore`, tapi cek ulang dengan `git status`).

Lalu di [share.streamlit.io](https://share.streamlit.io):
1. "New app" → pilih repo & branch yang baru di-push.
2. **Main file path**: `streamlit/streamlit_app.py`.
3. Di **Advanced settings**, pastikan requirements terbaca dari
   `streamlit/requirements.txt` (Streamlit Cloud biasanya otomatis mendeteksi
   requirements.txt di folder yang sama dengan main file).
4. Deploy, lalu buka **Settings > Secrets** di app tersebut dan tempel isi
   `secrets.toml.example` yang sudah diisi nilai asli — **hanya `SUPABASE_ANON_KEY`,
   JANGAN PERNAH `service_role`**, karena secrets ini hidup di deployment publik.

### Risiko deployment Prophet

Package `prophet` butuh `cmdstanpy`/CmdStan yang kadang lambat atau gagal build di
Streamlit Community Cloud (bukan masalah lisensi/gratis, murni keterbatasan build
environment). `requirements.txt` sudah pin versi (`prophet>=1.1.5,<1.2`,
`cmdstanpy>=1.2,<1.3`) untuk memperbesar peluang dapat wheel siap pakai. Kalau
tetap gagal saat build di Cloud: halaman Monitoring & Data Eksplorasi tetap jalan
normal (tidak bergantung Prophet), hanya halaman Forecasting yang akan
menampilkan pesan error jelas — sebagai fallback, jalankan halaman Forecasting
secara lokal (langkah 2 di atas) untuk kebutuhan laporan/demo.

### Catatan toggle MQTT langsung

Kalau toggle "Mode real-time langsung (MQTT)" diaktifkan, dashboard memakai
kredensial MQTT yang sama dengan `recorder.py` (HiveMQ Cloud tidak menyediakan
ACL subscribe-only terpisah tanpa membuat credential baru manual di dashboard
HiveMQ). Ini simplifikasi yang disengaja untuk tugas ini — kredensial broker jadi
ikut ada di secrets deployment publik. Satu koneksi MQTT dipakai bersama untuk
seluruh pengunjung dashboard (bukan satu per pengunjung), jadi tidak membebani
broker meski banyak yang membuka dashboard bersamaan.
