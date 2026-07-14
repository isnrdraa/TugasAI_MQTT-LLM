"""Toggle 'real-time langsung': subscribe MQTT langsung ke HiveMQ Cloud dari
proses Streamlit. st.cache_resource membuat ini SATU koneksi shared untuk
seluruh proses/semua pengunjung -- bukan satu koneksi per session. Ini reuse
pola connect yang sama dengan recorder.py di VPS."""

import ssl
import threading
from datetime import datetime

import paho.mqtt.client as mqtt
import streamlit as st

from lib.config import TIMEZONE, mqtt_config


class LiveState:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest = {"suhu": None, "kelembaban": None, "timestamp": None}
        self.connected = False


@st.cache_resource(show_spinner="Menghubungkan ke broker MQTT...")
def get_mqtt_client():
    cfg = mqtt_config()
    state = LiveState()

    def on_connect(client, userdata, flags, reason_code, properties=None):
        state.connected = reason_code == 0 or str(reason_code) == "Success"
        client.subscribe([(cfg["topic_suhu"], 0), (cfg["topic_kelembaban"], 0)])

    def on_disconnect(client, userdata, disconnect_flags, reason_code=None, properties=None):
        state.connected = False

    def on_message(client, userdata, msg):
        try:
            value = float(msg.payload.decode("utf-8").strip())
        except (ValueError, UnicodeDecodeError):
            return
        with state.lock:
            if msg.topic == cfg["topic_suhu"]:
                state.latest["suhu"] = value
            elif msg.topic == cfg["topic_kelembaban"]:
                state.latest["kelembaban"] = value
            state.latest["timestamp"] = datetime.now(TIMEZONE)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="streamlit-dashboard")
    client.username_pw_set(cfg["username"], cfg["password"])
    client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=120)
    client.connect(cfg["host"], cfg["port"], keepalive=60)
    client.loop_start()

    return client, state


def get_live_values() -> dict:
    _client, state = get_mqtt_client()
    with state.lock:
        values = dict(state.latest)
    values["connected"] = state.connected
    return values
