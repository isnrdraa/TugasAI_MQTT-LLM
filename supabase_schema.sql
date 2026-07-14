-- Jalankan di Supabase Dashboard > SQL Editor
create table if not exists public.sensor_data (
  id bigint generated always as identity primary key,
  timestamp timestamptz not null,
  suhu double precision,
  kelembaban double precision,
  inserted_at timestamptz not null default now()
);

create index if not exists sensor_data_timestamp_idx on public.sensor_data (timestamp);

-- Unique constraint: basis untuk upsert (on_conflict=timestamp) dari recorder.py
-- dan backfill_supabase.py, supaya retry/reconcile tidak pernah menduplikasi baris.
alter table public.sensor_data
  add constraint sensor_data_timestamp_key unique (timestamp);

-- RLS diaktifkan by default di project baru. Karena recorder pakai service_role key
-- (yang otomatis bypass RLS), tidak wajib bikin policy untuk recorder.py sendiri.
--
-- WAJIB dijalankan kalau memakai dashboard Streamlit (folder streamlit/): dashboard
-- itu deploy publik dan hanya boleh pakai anon key (bukan service_role), jadi perlu
-- policy read-only eksplisit ini supaya anon key bisa SELECT dari tabel:
alter table public.sensor_data enable row level security;

create policy "Allow anon read" on public.sensor_data
  for select
  to anon
  using (true);
