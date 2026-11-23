"""MQTT-Anbindung mit automatischer Erkennung in Home Assistant."""
import json
import logging
import re

log = logging.getLogger("mqtt")


class Mqtt:
    def __init__(self, store, dispatch, get_state, version):
        self.store = store
        self.dispatch = dispatch  # dispatch(befehl, wert) – Aufruf aus MQTT-Thread
        self.get_state = get_state
        self.version = version
        self.client = None
        self.connected = False
        self.error = ""

    @property
    def cfg(self):
        return self.store.config["mqtt"]

    @property
    def base(self):
        return self.cfg["base_topic"].strip("/") or "klangkiste"

    def start(self):
        self.stop()
        cfg = self.cfg
        if not cfg["enabled"] or not cfg["host"]:
            return
        try:
            import paho.mqtt.client as paho
        except ImportError:
            self.error = "paho-mqtt ist nicht installiert"
            return
        cid = "klangkiste-" + re.sub(r"\W", "", self.base)
        try:
            c = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=cid)
        except AttributeError:  # paho-mqtt 1.x
            c = paho.Client(client_id=cid)
        if cfg["username"]:
            c.username_pw_set(cfg["username"], cfg["password"] or None)
        c.will_set(f"{self.base}/status", "offline", retain=True)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(2, 60)
        self.client = c
        try:
            c.connect_async(cfg["host"], int(cfg["port"]), keepalive=60)
            c.loop_start()
            self.error = ""
        except Exception as e:
            self.error = str(e)
            log.warning("MQTT: %s", e)

    def stop(self):
        if self.client:
            try:
                self.client.publish(f"{self.base}/status", "offline", retain=True)
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        self.client, self.connected = None, False

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        code = getattr(rc, "value", rc)
        if code != 0:
            self.error = f"Verbindung abgelehnt ({rc})"
            return
        self.connected, self.error = True, ""
        client.subscribe(f"{self.base}/cmd/#")
        client.publish(f"{self.base}/status", "online", retain=True)
        if self.cfg["discovery"]:
            self._discovery(client)
        self.publish_state(self.get_state())

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False

    def _on_message(self, client, userdata, msg):
        cmd = msg.topic.rsplit("/", 1)[-1]
        try:
            payload = msg.payload.decode("utf-8", "replace").strip()
            self.dispatch(cmd, payload)
        except Exception:
            log.exception("MQTT-Befehl %s", cmd)

    def publish_state(self, state):
        if self.client and self.connected:
            s = {k: state.get(k) for k in ("state", "volume", "title", "artist", "album", "folder",
                                           "track", "tracks", "duration", "stream", "card", "headphones",
                                           "elapsed", "episode", "episodes", "vmax", "spotify", "image_v", "idle")}
            s["series"] = (state.get("folder") or "").split("/")[1] if (state.get("folder") or "").count("/") >= 2 else ""
            s["stream"] = "ON" if s.get("stream") else "OFF"
            self.client.publish(f"{self.base}/state", json.dumps(s, ensure_ascii=False), retain=True)

    def publish_card(self, card_id, known):
        if self.client and self.connected:
            self.client.publish(f"{self.base}/card", json.dumps({"id": card_id, "known": known}))

    def _discovery(self, client):
        base, name = self.base, self.store.config["name"]
        uid = re.sub(r"\W", "_", base)
        device = {"identifiers": [f"klangkiste_{uid}"], "name": name, "manufacturer": "Eigenbau",
                  "model": "Klangkiste", "sw_version": self.version}
        avail = {"availability_topic": f"{base}/status"}

        def cfg(component, key, payload):
            payload.update(avail)
            payload.update({"unique_id": f"klangkiste_{uid}_{key}", "device": device})
            client.publish(f"homeassistant/{component}/klangkiste_{uid}/{key}/config",
                           json.dumps(payload, ensure_ascii=False), retain=True)

        st = f"{base}/state"
        cfg("sensor", "status", {"name": "Status", "state_topic": st, "icon": "mdi:play-circle",
                                 "value_template": "{{ value_json.state }}"})
        cfg("sensor", "titel", {"name": "Titel", "state_topic": st, "icon": "mdi:music-note",
                                "value_template": "{{ value_json.title }}"})
        cfg("sensor", "ordner", {"name": "Ordner", "state_topic": st, "icon": "mdi:folder-music",
                                 "value_template": "{{ value_json.folder }}"})
        cfg("number", "lautstaerke", {"name": "Lautstärke", "state_topic": st, "icon": "mdi:volume-high",
                                      "value_template": "{{ value_json.volume }}",
                                      "command_topic": f"{base}/cmd/volume", "min": 0,
                                      "max": int(self.store.config["audio"]["volume_max"]), "step": 1})
        cfg("switch", "hausstream", {"name": "Haus-Stream", "state_topic": st, "icon": "mdi:cast-audio",
                                     "value_template": "{{ value_json.stream }}",
                                     "command_topic": f"{base}/cmd/stream"})
        for key, label, icon in (("play_pause", "Play/Pause", "mdi:play-pause"), ("next", "Weiter", "mdi:skip-next"),
                                 ("prev", "Zurück", "mdi:skip-previous"), ("stop", "Stopp", "mdi:stop")):
            cfg("button", key, {"name": label, "icon": icon, "command_topic": f"{base}/cmd/{key}"})
