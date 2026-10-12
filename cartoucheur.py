#!/usr/bin/env python3
"""
Cartoucheur 4x4 — lecteur de jingles pour la radio.

- 16 pads, glisser-déposer des sons (wav, mp3, flac, ogg, aiff)
- choix de la sortie carte son
- mapping contrôleur MIDI (MIDI Learn), retour LED optionnel
- raccourcis clavier : 1 2 3 4 / A Z E R / Q S D F / W X C V, Échap = tout arrêter
- une configuration par .exe : "La Matinale.exe" lit/écrit "La Matinale.json" à côté de lui
"""
import json
import os
import sys
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
import sounddevice as sd
import mido
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

try:
    import soxr  # rééchantillonnage de qualité
except ImportError:
    soxr = None

# ----------------------------------------------------------------- constantes
FROZEN = getattr(sys, "frozen", False)
EXE_PATH = Path(sys.executable if FROZEN else __file__).resolve()
APP_DIR = EXE_PATH.parent
SHOW_NAME = EXE_PATH.stem
CONFIG_PATH = APP_DIR / f"{SHOW_NAME}.json"

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff"}
ROWS = COLS = 4
NPADS = ROWS * COLS
# Touches physiques (scancodes Windows) -> étiquettes AZERTY
SCANCODES = [2, 3, 4, 5, 16, 17, 18, 19, 30, 31, 32, 33, 44, 45, 46, 47]
KEY_LABELS = ["1", "2", "3", "4", "A", "Z", "E", "R", "Q", "S", "D", "F", "W", "X", "C", "V"]
MODES = {
    "toggle": "Stop au 2e appui",
    "restart": "Relancer du début",
    "loop": "Boucle (stop au 2e appui)",
}
DEFAULT_COLOR = "#3d4a5c"
FADE_SEC = 0.025  # micro-fondu pour éviter les clics à l'arrêt


def default_pad():
    return {"file": None, "label": "", "color": DEFAULT_COLOR,
            "mode": "toggle", "gain_db": 0.0, "midi": None}


def load_config():
    cfg = {"title": SHOW_NAME, "audio_device": None, "midi_in": None,
           "midi_out": None, "master_db": 0.0, "stop_all_midi": None, "pads": []}
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text("utf-8")))
        except Exception as e:
            print("Config illisible :", e)
    pads = cfg.get("pads") or []
    cfg["pads"] = [{**default_pad(), **(pads[i] if i < len(pads) else {})}
                   for i in range(NPADS)]
    return cfg


def save_config(cfg):
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, CONFIG_PATH)


def store_path(p):
    """Chemin relatif si le son est dans le dossier de l'exe (portable sur clé USB)."""
    p = Path(p).resolve()
    try:
        return str(p.relative_to(APP_DIR))
    except ValueError:
        return str(p)


def resolve_path(s):
    p = Path(s)
    return p if p.is_absolute() else APP_DIR / p


def fmt_time(sec):
    sec = max(0.0, sec)
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def load_audio(path, sr):
    data, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = data[:, :2]
    if file_sr != sr and len(data):
        if soxr is not None:
            data = soxr.resample(data, file_sr, sr)
        else:
            n = int(round(len(data) * sr / file_sr))
            x_old = np.arange(len(data))
            x_new = np.linspace(0, len(data) - 1, n)
            data = np.stack([np.interp(x_new, x_old, data[:, c]) for c in range(2)], axis=1)
    return np.ascontiguousarray(data, dtype=np.float32)


# ----------------------------------------------------------------- audio
class Voice:
    def __init__(self, pad, data, gain, loop):
        self.pad, self.data, self.gain, self.loop = pad, data, gain, loop
        self.pos = 0
        self.fade_len = 0
        self.fade_left = None
        self.done = len(data) == 0

    def fade_out(self, n):
        if self.fade_left is None:
            self.fade_len = self.fade_left = max(1, n)

    def render(self, mix, frames):
        written = 0
        while written < frames and not self.done:
            remain = len(self.data) - self.pos
            if remain <= 0:
                if self.loop:
                    self.pos = 0
                    continue
                self.done = True
                break
            n = min(frames - written, remain)
            chunk = self.data[self.pos:self.pos + n] * self.gain
            if self.fade_left is not None:
                n = min(n, self.fade_left)
                ramp = (self.fade_left - np.arange(1, n + 1, dtype=np.float32)) / self.fade_len
                chunk = chunk[:n] * ramp[:, None]
                self.fade_left -= n
                if self.fade_left <= 0:
                    self.done = True
            mix[written:written + n] += chunk
            written += n
            self.pos += n


class AudioEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.voices = []
        self.stream = None
        self.samplerate = 48000
        self.channels = 2
        self.master = 1.0

    def open(self, device_index):
        self.close()
        info = sd.query_devices(device_index)
        sr = int(info["default_samplerate"])
        ch = 2 if info["max_output_channels"] >= 2 else 1
        stream = sd.OutputStream(device=device_index, samplerate=sr, channels=ch,
                                 dtype="float32", latency="low", callback=self._callback)
        self.samplerate, self.channels = sr, ch
        stream.start()
        self.stream = stream

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        with self.lock:
            self.voices.clear()

    def _callback(self, outdata, frames, time_info, status):
        mix = np.zeros((frames, 2), dtype=np.float32)
        with self.lock:
            for v in self.voices:
                v.render(mix, frames)
            self.voices = [v for v in self.voices if not v.done]
        mix *= self.master
        np.clip(mix, -1.0, 1.0, out=mix)
        if self.channels == 1:
            outdata[:, 0] = mix.mean(axis=1)
        else:
            outdata[:] = mix

    def _fade_n(self):
        return int(FADE_SEC * self.samplerate)

    def play(self, pad, data, gain, loop):
        with self.lock:
            for v in self.voices:
                if v.pad == pad:
                    v.fade_out(self._fade_n())
            self.voices.append(Voice(pad, data, gain, loop))

    def stop(self, pad):
        with self.lock:
            for v in self.voices:
                if v.pad == pad:
                    v.fade_out(self._fade_n())

    def stop_all(self):
        with self.lock:
            for v in self.voices:
                v.fade_out(self._fade_n())

    def state(self, pad):
        """(position, longueur) de la voix active du pad, ou None."""
        with self.lock:
            for v in reversed(self.voices):
                if v.pad == pad and v.fade_left is None and not v.done:
                    return v.pos, len(v.data)
        return None


def audio_devices():
    out = []
    try:
        apis = sd.query_hostapis()
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0:
                api = apis[d["hostapi"]]["name"]
                if "WDM-KS" in api:  # peu fiable, on l'écarte
                    continue
                out.append((i, f"{d['name']} ({api})"))
    except Exception as e:
        print("Périphériques audio :", e)
    return out


# ----------------------------------------------------------------- MIDI
class MidiBridge(QtCore.QObject):
    message = QtCore.Signal(str, int, int, int)  # type, canal, numéro, valeur

    def __init__(self):
        super().__init__()
        self.inport = None
        self.outport = None

    @staticmethod
    def input_names():
        try:
            return mido.get_input_names()
        except Exception:
            return []

    @staticmethod
    def output_names():
        try:
            return mido.get_output_names()
        except Exception:
            return []

    def open_input(self, name):
        self.close_input()
        if name:
            self.inport = mido.open_input(name, callback=self._callback)

    def open_output(self, name):
        self.close_output()
        if name:
            self.outport = mido.open_output(name)

    def close_input(self):
        if self.inport:
            try:
                self.inport.close()
            except Exception:
                pass
            self.inport = None

    def close_output(self):
        if self.outport:
            try:
                self.outport.close()
            except Exception:
                pass
            self.outport = None

    def _callback(self, msg):  # thread MIDI -> signal Qt (file d'attente)
        t = msg.type
        if t == "note_on" and msg.velocity > 0:
            self.message.emit("note", msg.channel, msg.note, msg.velocity)
        elif t == "control_change" and msg.value > 0:
            self.message.emit("cc", msg.channel, msg.control, msg.value)
        elif t == "program_change":
            self.message.emit("pc", msg.channel, msg.program, 127)

    def send_led(self, key, on):
        if not self.outport or not key:
            return
        kind, ch, num = key
        try:
            if kind == "note":
                self.outport.send(mido.Message("note_on", channel=ch, note=num,
                                               velocity=127 if on else 0))
            elif kind == "cc":
                self.outport.send(mido.Message("control_change", channel=ch, control=num,
                                               value=127 if on else 0))
        except Exception:
            pass


def midi_label(key):
    if not key:
        return ""
    kind, ch, num = key
    prefix = {"note": "N", "cc": "CC", "pc": "PC"}.get(kind, "?")
    return f"{prefix}{num} c{ch + 1}"


# ----------------------------------------------------------------- pad
class PadWidget(QtWidgets.QWidget):
    clicked = QtCore.Signal(int)
    menuRequested = QtCore.Signal(int, QtCore.QPoint)
    filesDropped = QtCore.Signal(int, list)

    def __init__(self, index):
        super().__init__()
        self.index = index
        self.cfg = default_pad()
        self.loaded = False
        self.error = False
        self.playing = False
        self.progress = 0.0
        self.time_text = ""
        self.learn_target = False
        self.setAcceptDrops(True)
        self.setMinimumSize(140, 95)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.index)
        elif e.button() == Qt.RightButton:
            self.menuRequested.emit(self.index, e.globalPosition().toPoint())

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        files = [f for f in files if Path(f).suffix.lower() in AUDIO_EXTS]
        if files:
            self.filesDropped.emit(self.index, files)

    def display_name(self):
        if self.cfg["label"]:
            return self.cfg["label"]
        if self.cfg["file"]:
            return Path(self.cfg["file"]).stem
        return "Glisser un son ici"

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(3, 3, -3, -3)
        empty = not self.cfg["file"]
        base = QtGui.QColor("#262b33") if empty else QtGui.QColor(self.cfg["color"])
        bg = base.lighter(165) if self.playing else base

        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 7, 7)

        # barre de progression
        if self.playing and self.progress > 0:
            bar = QtCore.QRectF(r.left() + 6, r.bottom() - 10, (r.width() - 12) * self.progress, 5)
            p.setBrush(QtGui.QColor(255, 255, 255, 220))
            p.drawRoundedRect(bar, 2, 2)

        # contour
        if self.learn_target:
            p.setPen(QtGui.QPen(QtGui.QColor("#ffd23f"), 3, Qt.DashLine))
        elif self.playing:
            p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 3))
        elif self.error:
            p.setPen(QtGui.QPen(QtGui.QColor("#e05555"), 2))
        else:
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 30), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 7, 7)

        light = bg.lightness() > 150
        fg = QtGui.QColor("#111418") if light else QtGui.QColor("#f2f4f7")
        dim = QtGui.QColor(fg)
        dim.setAlpha(140)

        f = self.font()
        f.setPointSizeF(8.5)
        p.setFont(f)
        p.setPen(dim)
        inner = r.adjusted(8, 5, -8, -5)
        p.drawText(inner, Qt.AlignLeft | Qt.AlignTop, KEY_LABELS[self.index])
        p.drawText(inner, Qt.AlignRight | Qt.AlignTop, midi_label(self.cfg["midi"]))
        mode_txt = {"loop": "boucle", "restart": "relance"}.get(self.cfg["mode"], "")
        p.drawText(inner.adjusted(0, 0, 0, -6), Qt.AlignLeft | Qt.AlignBottom, mode_txt)

        f2 = self.font()
        f2.setPointSizeF(10.5 if empty else 12)
        f2.setBold(not empty)
        p.setFont(f2)
        p.setPen(dim if empty else fg)
        p.drawText(inner.adjusted(0, 14, 0, -18), Qt.AlignCenter | Qt.TextWordWrap,
                   "Fichier introuvable" if self.error else self.display_name())

        if self.time_text:
            f3 = self.font()
            f3.setPointSizeF(10)
            f3.setBold(self.playing)
            p.setFont(f3)
            p.setPen(fg)
            p.drawText(inner.adjusted(0, 0, 0, -6), Qt.AlignRight | Qt.AlignBottom, self.time_text)


# ----------------------------------------------------------------- fenêtre
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.engine = AudioEngine()
        self.engine.master = 10 ** (self.cfg["master_db"] / 20)
        self.midi = MidiBridge()
        self.midi.message.connect(self.on_midi)
        self.cache = {}       # pad -> np.ndarray
        self.learn_mode = False
        self.learn_target = None  # index de pad ou "stop"
        self.led_state = [None] * NPADS

        self.setWindowTitle(f"{self.cfg['title']} — Cartoucheur")
        self.resize(900, 640)
        self._build_ui()
        self._populate_devices()
        self.open_audio()
        self.open_midi()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(40)

        for w in self.findChildren(QtWidgets.QWidget):
            w.setFocusPolicy(Qt.NoFocus)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

    # ---------- interface
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 8)
        root.setSpacing(8)

        self.title_lbl = QtWidgets.QLabel(self.cfg["title"])
        tf = self.title_lbl.font()
        tf.setPointSize(18)
        tf.setBold(True)
        self.title_lbl.setFont(tf)
        self.title_lbl.setToolTip("Double-clic pour renommer l'émission")
        self.title_lbl.installEventFilter(self)
        root.addWidget(self.title_lbl)

        dev = QtWidgets.QGridLayout()
        dev.setHorizontalSpacing(8)
        self.audio_combo = QtWidgets.QComboBox()
        self.midi_in_combo = QtWidgets.QComboBox()
        self.midi_out_combo = QtWidgets.QComboBox()
        for c in (self.audio_combo, self.midi_in_combo, self.midi_out_combo):
            c.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(18)
        refresh = QtWidgets.QPushButton("Actualiser")
        refresh.setToolTip("Rechercher à nouveau les cartes son et contrôleurs MIDI")
        refresh.clicked.connect(self.on_refresh)
        dev.addWidget(QtWidgets.QLabel("Sortie audio"), 0, 0)
        dev.addWidget(self.audio_combo, 0, 1, 1, 3)
        dev.addWidget(refresh, 0, 4)
        dev.addWidget(QtWidgets.QLabel("Contrôleur MIDI"), 1, 0)
        dev.addWidget(self.midi_in_combo, 1, 1)
        dev.addWidget(QtWidgets.QLabel("Retour LED"), 1, 2)
        dev.addWidget(self.midi_out_combo, 1, 3, 1, 2)
        dev.setColumnStretch(1, 1)
        dev.setColumnStretch(3, 1)
        root.addLayout(dev)

        self.audio_combo.currentIndexChanged.connect(self.on_audio_changed)
        self.midi_in_combo.currentIndexChanged.connect(self.on_midi_in_changed)
        self.midi_out_combo.currentIndexChanged.connect(self.on_midi_out_changed)

        grid = QtWidgets.QGridLayout()
        grid.setSpacing(6)
        self.pads = []
        for i in range(NPADS):
            w = PadWidget(i)
            w.cfg = self.cfg["pads"][i]
            w.clicked.connect(lambda i: self.trigger(i, from_click=True))
            w.menuRequested.connect(self.pad_menu)
            w.filesDropped.connect(self.on_files_dropped)
            grid.addWidget(w, i // COLS, i % COLS)
            self.pads.append(w)
        root.addLayout(grid, 1)

        bottom = QtWidgets.QHBoxLayout()
        self.stop_btn = QtWidgets.QPushButton("Tout arrêter  (Échap)")
        self.stop_btn.setObjectName("stop")
        self.stop_btn.setMinimumHeight(44)
        self.stop_btn.clicked.connect(self.on_stop_clicked)
        self.learn_btn = QtWidgets.QPushButton("MIDI Learn")
        self.learn_btn.setObjectName("learn")
        self.learn_btn.setCheckable(True)
        self.learn_btn.setMinimumHeight(44)
        self.learn_btn.toggled.connect(self.set_learn_mode)

        self.vol = QtWidgets.QSlider(Qt.Horizontal)
        self.vol.setRange(-40, 6)
        self.vol.setValue(int(round(self.cfg["master_db"])))
        self.vol.setFixedWidth(180)
        self.vol_lbl = QtWidgets.QLabel()
        self.vol.valueChanged.connect(self.on_master)
        self.on_master(self.vol.value(), save=False)

        bottom.addWidget(self.stop_btn, 2)
        bottom.addWidget(self.learn_btn, 1)
        bottom.addSpacing(16)
        bottom.addWidget(QtWidgets.QLabel("Volume général"))
        bottom.addWidget(self.vol)
        bottom.addWidget(self.vol_lbl)
        root.addLayout(bottom)

        self.setStyleSheet("""
            QPushButton { padding: 6px 12px; border-radius: 5px; background: #333a45; color: #e8ebef; }
            QPushButton:hover { background: #3e4654; }
            QPushButton#stop { background: #b83232; font-size: 15px; font-weight: bold; }
            QPushButton#stop:hover { background: #cc3a3a; }
            QPushButton#learn:checked { background: #ffd23f; color: #111; font-weight: bold; }
        """)

    def eventFilter(self, obj, ev):
        if obj is self.title_lbl and ev.type() == QtCore.QEvent.MouseButtonDblClick:
            txt, ok = QtWidgets.QInputDialog.getText(self, "Nom de l'émission", "Nom :",
                                                     text=self.cfg["title"])
            if ok and txt.strip():
                self.cfg["title"] = txt.strip()
                self.title_lbl.setText(self.cfg["title"])
                self.setWindowTitle(f"{self.cfg['title']} — Cartoucheur")
                self.save()
            return True
        return super().eventFilter(obj, ev)

    def status(self, msg, ms=6000):
        self.statusBar().showMessage(msg, ms)

    def save(self):
        try:
            save_config(self.cfg)
        except Exception as e:
            self.status(f"Impossible d'enregistrer la configuration : {e}")

    # ---------- périphériques
    def _populate_devices(self):
        self._fill_combo(self.audio_combo, [n for _, n in audio_devices()],
                         self.cfg["audio_device"], none_label=None)
        self._fill_combo(self.midi_in_combo, MidiBridge.input_names(),
                         self.cfg["midi_in"], none_label="Aucun")
        self._fill_combo(self.midi_out_combo, MidiBridge.output_names(),
                         self.cfg["midi_out"], none_label="Aucun")

    @staticmethod
    def _fill_combo(combo, names, current, none_label):
        combo.blockSignals(True)
        combo.clear()
        if none_label:
            combo.addItem(none_label, None)
        for n in names:
            combo.addItem(n, n)
        if current and current not in names:
            combo.addItem(f"{current}  (absent)", current)
        idx = combo.findData(current) if current else (0 if none_label else -1)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def on_refresh(self):
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        self._populate_devices()
        self.open_audio()
        self.open_midi()

    def open_audio(self):
        devs = audio_devices()
        name = self.cfg["audio_device"]
        idx = next((i for i, n in devs if n == name), None)
        if idx is None:
            try:
                d = sd.default.device[1]
                idx = d if d is not None and d >= 0 else None
            except Exception:
                idx = None
            if idx is None and devs:
                idx = devs[0][0]
            if idx is not None:
                found = next((n for i, n in devs if i == idx), None)
                if found and not name:
                    self.cfg["audio_device"] = found
                    self._fill_combo(self.audio_combo, [n for _, n in devs], found, None)
        if idx is None:
            self.status("Aucune sortie audio trouvée.")
            return
        try:
            self.engine.open(idx)
            self.status(f"Sortie audio ouverte à {self.engine.samplerate} Hz.")
        except Exception as e:
            self.engine.close()
            QtWidgets.QMessageBox.warning(self, "Sortie audio",
                                          f"Impossible d'ouvrir cette sortie :\n{e}\n\n"
                                          "Essayez la même carte avec un autre pilote (WASAPI, MME…).")
        self.reload_all_pads()

    def open_midi(self):
        try:
            self.midi.open_input(self.cfg["midi_in"])
        except Exception as e:
            self.status(f"Contrôleur MIDI indisponible : {e}")
        try:
            self.midi.open_output(self.cfg["midi_out"])
        except Exception as e:
            self.status(f"Retour LED indisponible : {e}")
        self.led_state = [None] * NPADS

    def on_audio_changed(self, _):
        self.cfg["audio_device"] = self.audio_combo.currentData()
        self.save()
        self.open_audio()

    def on_midi_in_changed(self, _):
        self.cfg["midi_in"] = self.midi_in_combo.currentData()
        self.save()
        self.open_midi()

    def on_midi_out_changed(self, _):
        self.cfg["midi_out"] = self.midi_out_combo.currentData()
        self.save()
        self.open_midi()

    def on_master(self, val, save=True):
        self.cfg["master_db"] = float(val)
        self.engine.master = 10 ** (val / 20)
        self.vol_lbl.setText(f"{val:+d} dB")
        if save:
            self.save()

    # ---------- sons
    def reload_all_pads(self):
        self.cache.clear()
        for i in range(NPADS):
            self.load_pad(i)

    def load_pad(self, i):
        pad, w = self.cfg["pads"][i], self.pads[i]
        self.cache.pop(i, None)
        w.error = False
        if pad["file"]:
            path = resolve_path(pad["file"])
            try:
                self.cache[i] = load_audio(path, self.engine.samplerate)
            except Exception as e:
                w.error = True
                self.status(f"Pad {KEY_LABELS[i]} : impossible de lire {path.name} ({e})")
        w.update()

    def set_pad_file(self, i, path):
        pad = self.cfg["pads"][i]
        self.engine.stop(i)
        pad["file"] = store_path(path)
        pad["label"] = ""
        self.load_pad(i)

    def on_files_dropped(self, i, files):
        # plusieurs fichiers : on remplit les pads suivants
        for k, f in enumerate(files):
            if i + k >= NPADS:
                break
            self.set_pad_file(i + k, f)
        self.save()

    # ---------- lecture
    def trigger(self, i, from_click=False):
        if self.learn_mode:
            self.set_learn_target(i)
            return
        pad = self.cfg["pads"][i]
        data = self.cache.get(i)
        if data is None:
            if not pad["file"] and from_click:
                self.load_dialog(i)
            return
        if self.engine.stream is None:
            self.status("Aucune sortie audio ouverte.")
            return
        playing = self.engine.state(i) is not None
        if playing and pad["mode"] in ("toggle", "loop"):
            self.engine.stop(i)
            return
        gain = 10 ** (pad["gain_db"] / 20)
        self.engine.play(i, data, gain, loop=pad["mode"] == "loop")

    def on_stop_clicked(self):
        if self.learn_mode:
            self.set_learn_target("stop")
        else:
            self.engine.stop_all()

    def keyPressEvent(self, e):
        if e.isAutoRepeat():
            return
        if e.key() == Qt.Key_Escape:
            self.engine.stop_all()
            return
        sc = e.nativeScanCode()
        if sys.platform.startswith("linux"):
            sc -= 8
        if sc in SCANCODES:
            self.trigger(SCANCODES.index(sc))
            return
        super().keyPressEvent(e)

    def tick(self):
        sr = self.engine.samplerate
        for i, w in enumerate(self.pads):
            st = self.engine.state(i)
            data = self.cache.get(i)
            if st:
                pos, length = st
                w.playing = True
                w.progress = pos / length if length else 0
                w.time_text = "-" + fmt_time((length - pos) / sr)
            else:
                w.playing = False
                w.progress = 0
                w.time_text = fmt_time(len(data) / sr) if data is not None else ""
            w.learn_target = self.learn_mode and self.learn_target == i
            w.update()
            if self.led_state[i] != w.playing:
                self.led_state[i] = w.playing
                self.midi.send_led(self.cfg["pads"][i]["midi"], w.playing)

    # ---------- MIDI
    def set_learn_mode(self, on):
        self.learn_mode = on
        self.learn_target = None
        if on:
            self.status("MIDI Learn : cliquez un pad (ou « Tout arrêter »), puis touchez le contrôleur.", 0)
        else:
            self.status("MIDI Learn terminé.")

    def set_learn_target(self, target):
        self.learn_target = target
        name = "Tout arrêter" if target == "stop" else f"le pad {KEY_LABELS[target]}"
        self.status(f"Touchez maintenant le bouton du contrôleur pour {name}…", 0)

    def on_midi(self, kind, ch, num, val):
        key = [kind, ch, num]
        if self.learn_mode:
            if self.learn_target is None:
                self.status(f"Reçu {midi_label(key)} — cliquez d'abord un pad.", 0)
                return
            for p in self.cfg["pads"]:
                if p["midi"] == key:
                    p["midi"] = None
            if self.cfg["stop_all_midi"] == key:
                self.cfg["stop_all_midi"] = None
            if self.learn_target == "stop":
                self.cfg["stop_all_midi"] = key
                self.status(f"{midi_label(key)} → Tout arrêter. Cliquez un autre pad ou quittez MIDI Learn.", 0)
            else:
                self.cfg["pads"][self.learn_target]["midi"] = key
                self.status(f"{midi_label(key)} → pad {KEY_LABELS[self.learn_target]}. "
                            "Cliquez un autre pad ou quittez MIDI Learn.", 0)
            self.learn_target = None
            self.led_state = [None] * NPADS
            self.save()
            return
        if key == self.cfg["stop_all_midi"]:
            self.engine.stop_all()
            return
        for i, p in enumerate(self.cfg["pads"]):
            if p["midi"] == key:
                self.trigger(i)

    # ---------- menu de pad
    def load_dialog(self, i):
        start = str(APP_DIR / "sons") if (APP_DIR / "sons").is_dir() else str(APP_DIR)
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"Son pour le pad {KEY_LABELS[i]}", start,
            "Sons (*.wav *.mp3 *.flac *.ogg *.aif *.aiff)")
        if path:
            self.set_pad_file(i, path)
            self.save()

    def pad_menu(self, i, pos):
        pad = self.cfg["pads"][i]
        m = QtWidgets.QMenu(self)
        a_load = m.addAction("Charger un son…")
        a_name = m.addAction("Renommer…")
        a_color = m.addAction("Couleur…")
        sub = m.addMenu("Mode de lecture")
        group = QtGui.QActionGroup(sub)
        for key, lbl in MODES.items():
            act = sub.addAction(lbl)
            act.setCheckable(True)
            act.setChecked(pad["mode"] == key)
            act.setData(key)
            group.addAction(act)
        a_gain = m.addAction(f"Volume du pad ({pad['gain_db']:+.1f} dB)…")
        m.addSeparator()
        a_learn = m.addAction("Assigner au contrôleur MIDI…")
        a_unmidi = m.addAction("Retirer l'assignation MIDI")
        a_unmidi.setEnabled(bool(pad["midi"]))
        m.addSeparator()
        a_clear = m.addAction("Vider le pad")
        a_clear.setEnabled(bool(pad["file"]))

        act = m.exec(pos)
        if act is None:
            return
        if act is a_load:
            self.load_dialog(i)
        elif act is a_name:
            txt, ok = QtWidgets.QInputDialog.getText(
                self, "Renommer", "Nom affiché :", text=pad["label"] or self.pads[i].display_name())
            if ok:
                pad["label"] = txt.strip()
        elif act is a_color:
            c = QtWidgets.QColorDialog.getColor(QtGui.QColor(pad["color"]), self, "Couleur du pad")
            if c.isValid():
                pad["color"] = c.name()
        elif act.data() in MODES:
            pad["mode"] = act.data()
        elif act is a_gain:
            v, ok = QtWidgets.QInputDialog.getDouble(
                self, "Volume du pad", "Gain (dB) :", pad["gain_db"], -40.0, 12.0, 1)
            if ok:
                pad["gain_db"] = v
        elif act is a_learn:
            self.learn_btn.setChecked(True)
            self.set_learn_target(i)
        elif act is a_unmidi:
            self.midi.send_led(pad["midi"], False)
            pad["midi"] = None
        elif act is a_clear:
            self.engine.stop(i)
            self.cfg["pads"][i] = {**default_pad(), "color": pad["color"]}
            self.pads[i].cfg = self.cfg["pads"][i]
            self.load_pad(i)
        self.pads[i].update()
        self.save()

    def closeEvent(self, e):
        self.save()
        for i in range(NPADS):
            self.midi.send_led(self.cfg["pads"][i]["midi"], False)
        self.engine.close()
        self.midi.close_input()
        self.midi.close_output()
        super().closeEvent(e)


def dark_palette(app):
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    roles = {
        QtGui.QPalette.Window: "#1b1f25", QtGui.QPalette.WindowText: "#e8ebef",
        QtGui.QPalette.Base: "#14171c", QtGui.QPalette.AlternateBase: "#1f242b",
        QtGui.QPalette.Text: "#e8ebef", QtGui.QPalette.Button: "#2a3038",
        QtGui.QPalette.ButtonText: "#e8ebef", QtGui.QPalette.Highlight: "#4a7bd0",
        QtGui.QPalette.HighlightedText: "#ffffff", QtGui.QPalette.ToolTipBase: "#2a3038",
        QtGui.QPalette.ToolTipText: "#e8ebef",
    }
    for role, col in roles.items():
        pal.setColor(role, QtGui.QColor(col))
    app.setPalette(pal)


def main():
    app = QtWidgets.QApplication(sys.argv)
    dark_palette(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
