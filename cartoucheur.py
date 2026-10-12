#!/usr/bin/env python3
"""
Cartoucheur 4x4 Transistoc'h — lecteur de jingles pour la radio (V2).

- 16 pads nommés A1…D4 (colonnes A jaune, B rose, C vert, D mauve)
- horloge, alerte « FIN DE SON », export/import de banques (.cartouche)
- mapping Akai MPD218 automatique
- pads, glisser-déposer des sons (wav, mp3, flac, ogg, aiff)
- choix de la sortie carte son
- mapping contrôleur MIDI (MIDI Learn), retour LED optionnel
- raccourcis clavier : 1 2 3 4 / A Z E R / Q S D F / W X C V, Échap = tout arrêter
- une configuration par .exe : "La Matinale.exe" lit/écrit "La Matinale.json" à côté de lui
"""
import base64
import datetime
import json
import os
import re
import sys
import threading
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf
import sounddevice as sd
import mido
from PySide6 import QtCore, QtGui, QtSvg, QtWidgets
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
COL_NAMES = "ABCD"
COL_COLORS = ["#f2c230", "#ec6fae", "#4dbb66", "#9d7fd8"]  # A jaune, B rose, C vert, D mauve
END_ALERT_SEC = 4.0      # durée de l'alerte « FIN DE SON »
WARN_SEC = 5.0           # temps restant affiché en rouge sur les dernières secondes
BANK_EXT = ".cartouche"


def pad_name(index):
    row, col = divmod(index, COLS)
    return f"{COL_NAMES[col]}{row + 1}"


def pad_color(index):
    return COL_COLORS[index % COLS]


# Logo Transistoc'h (SVG blanc sur fond transparent), intégré pour un exe autonome
LOGO_SVG_B64 = (
    "PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0idXRmLTgiPz4KPCEtLSBHZW5lcmF0b3I6IEFkb2JlIElsbHVzdHJhdG9y"
    "IDE2LjAuMCwgU1ZHIEV4cG9ydCBQbHVnLUluIC4gU1ZHIFZlcnNpb246IDYuMDAgQnVpbGQgMCkgIC0tPgo8IURPQ1RZUEUg"
    "c3ZnIFBVQkxJQyAiLS8vVzNDLy9EVEQgU1ZHIDEuMS8vRU4iICJodHRwOi8vd3d3LnczLm9yZy9HcmFwaGljcy9TVkcvMS4x"
    "L0RURC9zdmcxMS5kdGQiPgo8c3ZnIHZlcnNpb249IjEuMSIgaWQ9IkNhbHF1ZV8xIiB4bWxucz0iaHR0cDovL3d3dy53My5v"
    "cmcvMjAwMC9zdmciIHhtbG5zOnhsaW5rPSJodHRwOi8vd3d3LnczLm9yZy8xOTk5L3hsaW5rIiB4PSIwcHgiIHk9IjBweCIK"
    "CSB3aWR0aD0iMTAwOC4xMXB4IiBoZWlnaHQ9IjM2MHB4IiB2aWV3Qm94PSIwIDAgMTAwOC4xMSAzNjAiIGVuYWJsZS1iYWNr"
    "Z3JvdW5kPSJuZXcgMCAwIDEwMDguMTEgMzYwIiB4bWw6c3BhY2U9InByZXNlcnZlIj4KCjxnPgoJCgkKCTxwYXRoIGZpbGw9"
    "IiNGRkZGRkYiIGQ9Ik01NTguMjAzLDI5Mi42MTJjLTEuNTg4LTIuMjE0LTMuMTc2LTQuNDI5LTQuNzY1LTYuNjQzCgkJYy0w"
    "LjA2NCwwLjAxNy0wLjEzMSwwLjAzMy0wLjE5NSwwLjA1YzAuMjU1LDEuODM2LDAuNTAyLDMuNjcyLDAuNzY3LDUuNTA3YzAu"
    "NDkxLDMuNDEzLDAuOTgsNi44MjcsMS41MDEsMTAuMjM2CgkJYzAuMTA3LDAuNzAxLTAuMDUxLDEuMDg2LTAuNzUzLDEuMzdj"
    "LTEuMzY1LDAuNTUyLTIuNjg1LDEuMjE2LTQuMDI5LDEuODM2Yy0xLjIwNS0zLjQwOS0zLjkyNC0zMy4wMTMtMy4xNDktMzUu"
    "MDUxCgkJYzEuODczLDAuNzg1LDMuNzU3LDEuNTM1LDUuNTk2LDIuMzgyYzAuMzI3LDAuMTQ5LDAuNTM5LDAuNjgsMC42OCwx"
    "LjA3OGMwLjg1NiwyLjQxMywxLjY3Nyw0LjgzOSwyLjc0NSw3LjI5MQoJCWMwLjc3My00LjczMSwxLjU0Ni05LjQ2MSwyLjM0"
    "NC0xNC4zNDhjMS4zMDMsMC41NjYsMi40NzYsMC45ODIsMy41NDYsMS41ODNjMC40NTksMC4yNTgsMC44NjgsMC44NTIsMS4w"
    "MTYsMS4zNzIKCQljMS4wODEsMy44NTQsMi4xLDcuNzI3LDMuMTIsMTEuNTk4YzEuMTAyLDQuMTgzLDIuMTg2LDguMzcyLDMu"
    "Mjc0LDEyLjU1OGMwLjM1NCwxLjM1NywwLjY4OSwyLjcxOSwxLjA1NSw0LjA3MgoJCWMwLjIsMC43NC0wLjAzNywxLjIwNC0w"
    "LjczOSwxLjUyMmMtMS4zNDYsMC42MTEtMi42NjksMS4yNzEtNC4xNjYsMS45ODljLTIuMjc4LTcuMjA1LTIuODUxLTE0LjY3"
    "LTUuMDc2LTIxLjgwNAoJCWMtMC44MDIsNC40NS0xLjYwMyw4Ljg5OS0yLjQwNCwxMy4zNDlDNTU4LjQ0NiwyOTIuNTc5LDU1"
    "OC4zMjUsMjkyLjU5Niw1NTguMjAzLDI5Mi42MTIiLz4KCTxwYXRoIGZpbGw9IiNGRkZGRkYiIGQ9Ik00NzYuMzY1LDI4NC40"
    "MWMwLjQzNi0yLjc0OCwwLjc0OS01Ljg2NywxLjQ4Mi04Ljg4NAoJCWMwLjQwMy0xLjY1NywxLjQwMy0zLjIxNSwyLjMzNy00"
    "LjY4OGMwLjg2OC0xLjM2NywyLjIyNi0xLjgyOSwzLjg4Ni0xLjQxYzEuOTAzLDAuNDgxLDMuNDYsMS41MDIsNC43NTksMi44"
    "OTkKCQljNS4yOTMsNS42OTQsNy4xMzcsMTIuNTYxLDYuNzMzLDIwLjE2NWMtMC4xODksMy41NjktMSw2LjkzNy0yLjgzNCwx"
    "MC4wNzdjLTMuMDgsNS4yNzgtOC41MzQsNC4xMTItMTEuMDgzLDAuNjA3CgkJYy0yLjM5Ni0zLjI5NS0zLjU1LTcuMDc2LTQu"
    "MjktMTAuOTkxQzQ3Ni44OTYsMjg5Ljc1NSw0NzYuNzE4LDI4Ny4yNjksNDc2LjM2NSwyODQuNDEgTTQ5MC4zNTQsMjkzLjY0"
    "MQoJCWMtMC4xMjItNC41NTktMC44NTctOC45NzQtMi41NDktMTMuMTgyYy0wLjYxOS0xLjU0MS0xLjUwNC0zLjAwNy0yLjQ3"
    "LTQuMzY0Yy0wLjQzLTAuNjA0LTEuMzYxLTAuODUxLTIuMDY0LTEuMjYKCQljLTAuMjkxLDAuNjY2LTAuNzIsMS4zMDYtMC44"
    "NTEsMi4wMDJjLTEuMTQ5LDYuMDkzLTAuNDU4LDEyLjA3MiwxLjUyOCwxNy44NzRjMC41NzcsMS42ODQsMS43MTMsMy4yNTYs"
    "Mi44ODEsNC42MzQKCQljMS4xNTgsMS4zNjUsMi4zMiwxLjAxNSwyLjc1LTAuNzM2QzQ4OS45NzgsMjk2Ljk4Niw0OTAuMTA2"
    "LDI5NS4zLDQ5MC4zNTQsMjkzLjY0MSIvPgoJPHBhdGggZmlsbD0iI0ZGRkZGRiIgZD0iTTQ0Ni42MjksMjc4LjU2NmMtMC4y"
    "NiwwLjQ0MS0wLjQ1OCwwLjc2NC0wLjY0MywxLjA5MwoJCWMtMS40NzIsMi42MTgtMi45NTUsNS4yMzEtNC40MDYsNy44NjJj"
    "LTAuNDkzLDAuODk0LTAuOTk3LDEuMDM2LTEuNzU0LDAuMjg0Yy0wLjk2Ny0wLjk2Mi0yLjAyNy0xLjgzMS0yLjk1My0yLjgy"
    "NwoJCWMtMC4yNi0wLjI4LTAuNDEtMC45NzktMC4yNDQtMS4yOTNjMy4xMjEtNS45MzUsNi44Mi0xMS41MDMsMTAuODI4LTE2"
    "Ljg3YzAuMDctMC4wOTMsMC4xNzktMC4xNTYsMC40MTUtMC4zNTUKCQljMS40OTEsMS41ODUsMy4xNzUsMy4wNDEsNC4zOTYs"
    "NC44MTZjMC41OTQsMC44NjQsMC4zNzYsMi4zMiwwLjQzOCwzLjUxM2MwLjUyNSwxMC4wOTEsMS4wMzEsMjAuMTgzLDEuNTQs"
    "MzAuMjc0CgkJYzAuMDEyLDAuMjMyLDAuMDAyLDAuNDY3LDAuMDAyLDAuNzk1Yy0xLjY4MSwwLjE4My0zLjMwNCwwLjM0Mi00"
    "LjkyMiwwLjU0NWMtMC42ODksMC4wODctMC45MzQtMC4yMDYtMC45ODMtMC44NDYKCQljLTAuMjE0LTIuNzk4LTAuNTA0LTUu"
    "NTktMC42Ni04LjM5Yy0wLjMzNy02LjA0MS0wLjYwNy0xMi4wODctMC45MDktMTguMTNDNDQ2Ljc2OSwyNzguOTM1LDQ0Ni43"
    "MTMsMjc4LjgzMiw0NDYuNjI5LDI3OC41NjYiLz4KCTxwYXRoIGZpbGw9IiNGRkZGRkYiIGQ9Ik00NTcuMjE1LDI4Mi44NWMt"
    "MC4wMjEtMi44MzQsMC4yNjktNS42MDQsMS4yOC04LjI3MgoJCWMwLjYxMi0xLjYxNCwxLjU4Mi0yLjk1LDIuOTYzLTQuMDA1"
    "YzEuMDk4LTAuODQsMi4yMjgtMC45NzcsMy40ODctMC40MTdjMi4wMTMsMC44OTUsMy41MDMsMi4zODIsNC43MzMsNC4xNQoJ"
    "CWMyLjA2MSwyLjk2MywyLjk0Miw2LjM4MiwzLjY2NSw5Ljg0MWMxLjExNCw1LjMyNywxLjQwOSwxMC42NTMtMC4zNTksMTUu"
    "OTE0Yy0wLjMxNiwwLjk0Mi0wLjcyOCwxLjg3Ni0xLjI0OCwyLjcyMQoJCWMtMS4zMjksMi4xNTMtMy40OTEsMi44OTYtNS42"
    "ODksMS42NWMtMS40NTQtMC44MjQtMi44MjQtMi4wMTgtMy44NDQtMy4zNDRjLTIuOTQ2LTMuODI2LTQuMTg2LTguMzM1LTQu"
    "NjM0LTEzLjA4MQoJCUM0NTcuNDA4LDI4Ni4yOTIsNDU3LjMzMiwyODQuNTY5LDQ1Ny4yMTUsMjgyLjg1IE00NjkuOTM4LDI5"
    "My42MzNjLTAuNTUzLTUuMjU1LTEuNjIxLTEwLjMzLTMuNzA3LTE1LjE1MwoJCWMtMC40Ny0xLjA4Ny0xLjEzOC0yLjExOS0x"
    "Ljg3NS0zLjA1MWMtMC43NTgtMC45NTgtMS40OC0wLjg0Ny0yLjA0NSwwLjIyMmMtMC4zNywwLjY5OS0wLjcxNiwxLjQ1NS0w"
    "Ljg1OCwyLjIyNgoJCWMtMS4zLDcuMDY2LDAuMDk1LDEzLjY2NywzLjU2LDE5Ljg4NGMwLjI2OCwwLjQ4MSwwLjY1NSwwLjkx"
    "NywxLjA1OSwxLjI5OWMxLjQwNSwxLjMzLDIuNzU5LDEuMDQxLDMuMjI5LTAuODEzCgkJQzQ2OS42ODEsMjk2Ljc1LDQ2OS43"
    "MzgsMjk1LjE3Myw0NjkuOTM4LDI5My42MzMiLz4KCTxwYXRoIGZpbGw9IiNGRkZGRkYiIGQ9Ik01MTQuNzcsMjg4Ljg4N2Mx"
    "LjU5OSwwLjc4NSwzLjAxOCwxLjQwNSw0LjM0MiwyLjE4NwoJCWMwLjMzMywwLjE5NiwwLjQyMywwLjg5NiwwLjUxNSwxLjM4"
    "N2MwLjEwOSwwLjU4MywwLjEwMywxLjE4OSwwLjE0NiwxLjc5YzQuMjI1LTAuNjkzLDMuNDkzLTEuMDI2LDQuNzkxLDMuMzc2"
    "CgkJYy0wLjc3MywwLjI3Ny0xLjU1OCwwLjU0My0yLjMzLDAuODM4Yy0wLjc0MywwLjI4My0xLjQ3NSwwLjU5OS0yLjI4Miww"
    "LjkyOWMwLjI5NywxLjc2MywwLjU2LDMuNDk2LDAuODg5LDUuMjE5CgkJYzAuMjI1LDEuMTc2LDAuMTIyLDIuMDYyLTEuMTkz"
    "LDIuNTgzYy0wLjk4OCwwLjM5MS0xLjg2NiwxLjA2MS0zLjA1MiwxLjc1OWMtMC4zODYtMi44Ni0wLjc0NS01LjUzLTEuMTEy"
    "LTguMjU2CgkJYy0zLjA1NCwwLjc4OS02LjEwMiwxLjU3NS05LjE5NywyLjM3NGMtMC4yNTMtMTEuMTUyLDEuMDg4LTIyLjAx"
    "NiwzLjE2Ny0zMi45NTZjMS4wMDcsMC44MzYsMS45NzgsMS41OTYsMi44OCwyLjQyOQoJCWMwLjE5NCwwLjE4LDAuMjMsMC42"
    "MzIsMC4xOTcsMC45NDNjLTAuNzg4LDcuMzc4LTEuNTk1LDE0Ljc1My0yLjM5NCwyMi4xM2MtMC4wMjUsMC4yMzQtMC4wMDQs"
    "MC40NzQtMC4wMDQsMC43NAoJCWMxLjQ4Mi0wLjU2MywyLjk0MS0xLjA4Myw0LjM2NC0xLjY4OWMwLjIwOC0wLjA4OSwwLjM0"
    "Ny0wLjU4MywwLjM0MS0wLjg4NmMtMC4wMjMtMS4xNjEtMC4xMzMtMi4zMi0wLjE4MS0zLjQ4MQoJCUM1MTQuNjQsMjg5Ljky"
    "NSw1MTQuNzEyLDI4OS41NDQsNTE0Ljc3LDI4OC44ODciLz4KCTxwYXRoIGZpbGw9IiNGRkZGRkYiIGQ9Ik01NDQuNjA0LDI2"
    "Ny4wMTljLTAuMjM5LDEuOTQyLTAuNDA5LDMuNjA3LTAuNjgzLDUuMjU0CgkJYy0wLjA0NCwwLjI2OC0wLjUxOSwwLjU2Ni0w"
    "Ljg0OSwwLjY2NGMtMi4xODgsMC42NDgtNC4zODEsMS4yODEtNi41OTMsMS44NGMtMC44MzMsMC4yMS0xLjIyNCwwLjUyNi0x"
    "LjEyNywxLjQ1MgoJCWMwLjI3NCwyLjYyMywwLjQ3Nyw1LjI1NSwwLjcyMSw4LjAyOWMxLjM3NS0wLjI4NywyLjY5NS0wLjUw"
    "MywzLjk4LTAuODUzYzAuODAxLTAuMjE5LDEuMjcxLTAuMTA0LDEuNTg2LDAuNzEKCQljMC4yNzEsMC43MDQsMC42NjcsMS4z"
    "NiwxLjA3MywyLjE2NmMtMS40OTcsMC42NDUtMi45MzEsMS4zMDQtNC4zOTcsMS44NzRjLTAuNzQxLDAuMjg3LTEuMDEyLDAu"
    "NTc5LTAuODc4LDEuNDc5CgkJYzAuNTc5LDMuODg3LDEuMDM0LDcuNzkyLDEuNTMxLDExLjY4OWMwLjAzMSwwLjIzOSwwLjA1"
    "OCwwLjQ4LDAuMDYxLDAuNzIxYzAuMDIyLDEuNjIxLTAuMDcsMS43MjItMS43MDEsMS45NDYKCQljLTAuNTk1LDAuMDgyLTEu"
    "MjEzLDAuMTA0LTEuNzc1LDAuMjg4Yy0xLjAyMywwLjMzNS0xLjMtMC4yMS0xLjQwMi0xLjA0Yy0wLjYzOC01LjE2OS0xLjMw"
    "OS0xMC4zMzQtMS45LTE1LjUwOAoJCWMtMC41OTctNS4yMTktMS4wOTUtMTAuNDUtMS42ODUtMTUuNjdjLTAuMS0wLjg3OSww"
    "LjEwNi0xLjI5LDAuOTktMS41MjFDNTM1LjgzNCwyNjkuNDE3LDU0MC4wOTksMjY4LjIzOSw1NDQuNjA0LDI2Ny4wMTkiLz4K"
    "CTxwYXRoIGZpbGw9IiNGRkZGRkYiIGQ9Ik00OTkuMTQ3LDI5OS43M2MtMS4zMDMsMC4wMjItMi4wOC0wLjczMy0yLjExMS0y"
    "LjA1NgoJCWMtMC4wMzMtMS4zMzUsMC43MzgtMi4yODYsMS44ODUtMi4zMjZjMS4wNzgtMC4wMzgsMi4yNCwxLjE3OCwyLjI2"
    "NSwyLjM2OUM1MDEuMjA5LDI5OC44OTEsNTAwLjM4LDI5OS43MSw0OTkuMTQ3LDI5OS43MyIvPgoJPHBhdGggZmlsbD0iI0ZG"
    "RkZGRiIgZD0iTTY5MC40ODEsODEuODY4Yy0zLjAyMiwxNC40NzEtNS40OSwyOC43NzQtNy4yLDQzLjQxMgoJCWM4LjUwNi0x"
    "LjYyOCwxNi43ODMtMy4yMTIsMjUuMTk5LTQuODIyYzAuMDM3LDAuNzg1LDAuMDczLDEuMzcyLDAuMDkzLDEuOTZjMC4xMzcs"
    "NC4xNzEsMC4yNDMsOC4zNDIsMC40MjcsMTIuNTEKCQljMC4wNDQsMC45OTMtMC4yNDYsMS40MDMtMS4yNDMsMS42MDljLTgu"
    "MTY4LDEuNjg4LTE2LjMxOSwzLjQ1NC0yNC40OSw1LjEyOGMtMS4xNTksMC4yMzctMS42NzMsMC42MTctMS43NDksMS44ODYK"
    "CQljLTAuODkxLDE0Ljc1NC0xLjM3MiwyOS40OTgsMC44ODQsNDQuMTg0YzAuNzI2LDQuNzI2LDEuNzI5LDkuMzk1LDMuOTQ5"
    "LDEzLjY5NmMwLjQyMSwwLjgxNSwwLjg5OCwxLjYyMywxLjQ3NiwyLjMzMgoJCWMyLjQxOSwyLjk3LDUuMTM0LDMuMzg0LDgu"
    "Mzc0LDEuMzE3YzIuODk4LTEuODUsNS00LjQ4Myw3LjAyMi03LjE5MmMwLjIxNi0wLjI4OCwwLjQzNS0wLjU3NSwwLjY2LTAu"
    "ODU0CgkJYzAuMDQ3LTAuMDU3LDAuMTM1LTAuMDc5LDAuMjk2LTAuMTY4YzAuMjE0LDAuMjQxLDAuNDU2LDAuNDg5LDAuNjcx"
    "LDAuNzZjMy4xODEsMy45NzksNi4zNTQsNy45NjYsOS41NDEsMTEuOTQKCQljMC4zNzEsMC40NjQsMC43MzIsMC44MjQsMC4x"
    "NDIsMS40MTRjLTUuNjc4LDUuNjc2LTExLjY2NiwxMC45MTgtMTkuMjQ4LDEzLjk2NmMtOC41NzEsMy40NDMtMTYuMzg3LDEu"
    "MTc2LTIxLjc0Mi02LjM0NQoJCWMtMy41My00Ljk1OS01LjQzNy0xMC42MTMtNi44NC0xNi40NTdjLTIuMzcxLTkuODgtMy4x"
    "MTItMTkuOTQ4LTMuMzQzLTMwLjA3Yy0wLjI3MS0xMS44NDMsMC4wOTktMjMuNjcyLDEuMjktMzUuNDUzCgkJYzEuMTg2LTEx"
    "LjcyNiwyLjYzNC0yMy40MjUsMy45NzctMzUuMTM1YzAuMTQyLTEuMjM1LDAuNDM2LTIuNDU5LDAuNDg5LTMuNjk1YzAuMDUz"
    "LTEuMjE1LDAuNTkxLTEuOTY4LDEuNTcyLTIuNjI4CgkJYzYuMzI2LTQuMjUzLDEyLjYyMy04LjU1LDE4LjkzMi0xMi44MjlD"
    "Njg5LjgwNSw4Mi4yMDgsNjkwLjAxNSw4Mi4xMTksNjkwLjQ4MSw4MS44NjgiLz4KCTxwYXRoIGZpbGw9IiNGRkZGRkYiIGQ9"
    "Ik01OTguMTYzLDIwNC45NzdjMi4yNCwwLjg4NSw0LjI4NywxLjk3OSw2LjQ3LDIuNTAzCgkJYzcuMjMxLDEuNzM0LDEzLjQ0"
    "OS0yLjMzNiwxNS4xMzgtOS43MDRjMC45OTEtNC4zMjYsMC4zODMtOC40OTktMS4wNDEtMTIuNjQzYy0xLjY3Ny00Ljg4MS00"
    "LjY2Ni04LjY0OS04Ljg0Ny0xMS42NjkKCQljLTMuNjctMi42NTEtNy4wNTUtNS42OTItMTAuNjM2LTguNDdjLTUuMjM3LTQu"
    "MDYyLTguNDMyLTkuNDExLTEwLjQxMS0xNS42NTRjLTQuNzg3LTE1LjEwNS01LjM2MS0zMC4yMzEtMC4zNjMtNDUuMzg3CgkJ"
    "YzEuNjMzLTQuOTUxLDQuMDU3LTkuNTA2LDcuNjc0LTEzLjM0NGM0LjExNC00LjM2Niw4Ljk5Ny02Ljk1NSwxNS4xNjktNi4z"
    "NDJjNC41NzYsMC40NTQsOC4zNjYsMi42NDIsMTEuNjY3LDUuNzE1CgkJYzUuNjA5LDUuMjIsOS4wMTUsMTEuODAzLDExLjQ1"
    "NywxOC45NDJjMC43MjQsMi4xMTgsMS4yMjksNC4zMTEsMS44NzYsNi40NTdjMC4yNiwwLjg2MiwwLjA4NSwxLjMyOC0wLjc4"
    "LDEuNzgKCQljLTQuNjkzLDIuNDUtOS4zNDMsNC45ODMtMTQuMDA4LDcuNDg1Yy0wLjIwNCwwLjEwOS0wLjQyMywwLjE5MS0w"
    "LjU1NCwwLjI0OWMtMC43MzEtMy4xNjQtMS4zNTMtNi4zMDItMi4xOTgtOS4zNzkKCQljLTAuODExLTIuOTQzLTEuOTg4LTUu"
    "NzYtMy45MTQtOC4xODljLTIuMjM1LTIuODE4LTQuMjE0LTIuNzQyLTYuMTM2LDAuMjY5Yy0xLjgzMSwyLjg2OS0yLjYwNiw2"
    "LjEyMi0zLjA2Myw5LjQyMQoJCWMtMS4zMzgsOS42Ny0wLjYyMiwxOS4xODksMi4zMzYsMjguNTI2YzEuNDY4LDQuNjMxLDQu"
    "Mjg5LDguMzI5LDcuODY3LDExLjQ0OWMzLjY1NywzLjE4OCw3LjM5LDYuMzA5LDExLjI2NCw5LjIyNwoJCWM3LjM4LDUuNTU4"
    "LDExLjAyLDEyLjkxNywxMS43MzQsMjIuMDYzYzAuODQ2LDEwLjgxNS0yLjMsMjAuMzItOC45ODEsMjguNjI4Yy02LjE3OSw3"
    "LjY4NS0xNC4xNzMsMTIuMDc2LTI0LjMxMywxMS42NDQKCQljLTYuNzQzLTAuMjg4LTEyLjQzOC0zLjExNi0xNy4zMjMtNy42"
    "NjJjLTAuMjQ5LTAuMjMyLTAuNDE0LTAuOTM0LTAuMjU0LTEuMTY5QzU5MS4zNzYsMjE0Ljc1LDU5NC44MTMsMjA5LjgxMiw1"
    "OTguMTYzLDIwNC45NzciLz4KCTxwYXRoIGZpbGw9IiNGRkZGRkYiIGQ9Ik0xODcuMzM5LDIwOS4xN2MtMy45MzctMTAuNDE4"
    "LTcuNzk3LTIwLjYzMS0xMS42NTYtMzAuODQyCgkJYy0wLjA5OSwwLjAwOC0wLjE5OSwwLjAxNS0wLjI5OCwwLjAyM2MtMC4x"
    "NzgsMS4zOTgtMC4zNjMsMi43OTUtMC41MzMsNC4xOTNjLTEuMDU0LDguNzIxLTIuMTA5LDE3LjQ0MS0zLjE1MiwyNi4xNjIK"
    "CQljLTAuMDk1LDAuNzk0LTAuMjIsMS4zMi0xLjI5LDEuMjA0Yy01LjYyNC0wLjYwNi0xMS4yNTMtMS4xNjctMTYuODg4LTEu"
    "NjYzYy0xLjIxMi0wLjEwNi0xLjM4Ny0wLjUtMS4yMTUtMS42NDcKCQljMS43NTUtMTEuNzQzLDMuNDQ1LTIzLjQ5NSw1LjE1"
    "My0zNS4yNDVjMS44ODctMTIuOTgsMy43NzUtMjUuOTYsNS42NjItMzguOTRjMC4yMy0xLjU4OCwwLjQ2MS0zLjE3NiwwLjY4"
    "NC00Ljc2NQoJCWMwLjMzNy0yLjQwOCwwLjYyNy0yLjcwNSwyLjk2Ni0zLjIzMWM0LjEwMi0wLjkyMyw4LjI1My0xLjIyNiwx"
    "Mi40MzYtMC45MDJjNi43NywwLjUyNCwxMy4wOSwyLjQ1NSwxOC41OTEsNi41MzQKCQljNi42MSw0LjkwNCwxMC41MzksMTEu"
    "MzM1LDEwLjE3NywxOS43ODVjLTAuMzA5LDcuMjI4LTMuNzkxLDEyLjk4MS05LjEyNiwxNy42N2MtMS4zODEsMS4yMTQtMi45"
    "MDEsMi4yODUtNC40NDMsMy4yODkKCQljLTAuNzY5LDAuNTAxLTAuODY5LDAuOTI4LTAuNjA0LDEuNzU2YzIuNTA4LDcuODI5"
    "LDQuOTUsMTUuNjc5LDcuNDc3LDIzLjUwMmMwLjM2NywxLjEzNSwwLjE3OSwxLjc3MS0wLjc2NiwyLjUxNgoJCUMxOTYuMTk2"
    "LDIwMS45NjUsMTkxLjk0NywyMDUuNDQ3LDE4Ny4zMzksMjA5LjE3IE0xNzcuNDE2LDE2MS4zNjFjMy43NzktMC43MjIsNi43"
    "MTItMi40MTksOS4wMzMtNS4xMTUKCQljMi4xMjQtMi40NjcsMy4yNjEtNS4zMSwyLjgyOS04LjYzN2MtMC41NDItNC4xNzQt"
    "NS40MjUtNy44ODEtOS4zNDctNy4wMjRDMTc5LjEwMywxNDcuNDI0LDE3OC4yNzIsMTU0LjI5MSwxNzcuNDE2LDE2MS4zNjEi"
    "Lz4KCTxwYXRoIGZpbGw9IiNGRkZGRkYiIGQ9Ik0yNjguMTExLDIyMC44NzVjLTEuMjQyLTMuOTMxLTIuNDU4LTcuNzgzLTMu"
    "Njc1LTExLjYzNAoJCWMtMi4yNy03LjE3OS00LjU1Ni0xNC4zNS02Ljc4OC0yMS41MzhjLTAuMjUxLTAuODA5LTAuNTk0LTEu"
    "MDYzLTEuNDI0LTEuMDMxYy01LjMzMiwwLjItMTAuNjY1LDAuMzQ5LTE1Ljk5OCwwLjUyMwoJCWMtMC4zMSwwLjAxLTAuNjE4"
    "LDAuMS0xLjAyMSwwLjE2OGMtMC4xMjcsMy4wNzktMC4yNTIsNi4xMTUtMC4zNzYsOS4xNTFjLTAuMjQ1LDYuMDExLTAuNDkz"
    "LDEyLjAyLTAuNzI1LDE4LjAyOQoJCWMtMC4wMjMsMC42MDQtMC4wOTksMC45OTgtMC44MDgsMS4yMzNjLTUuODk2LDEuOTU5"
    "LTExLjc3MywzLjk3My0xNy42NTcsNS45NjhjLTAuMTExLDAuMDM4LTAuMjM0LDAuMDQtMC41MTYsMC4wODQKCQljMi4zOTUt"
    "NDYuMDY4LDQuNzg4LTkyLjA4OSw3LjE4OS0xMzguMjljMC40NDcsMC4yNDcsMC43MjMsMC4zNzMsMC45NzEsMC41NDFjNi4x"
    "NzcsNC4xODYsMTIuMzQ0LDguMzg1LDE4LjUzMSwxMi41NTUKCQljMC45MTUsMC42MTcsMS40NTIsMS40MDcsMS43OTksMi40"
    "NDRjMTAuOTk1LDMyLjkyNSwyMi4wMSw2NS44NDMsMzMuMDIsOTguNzYyYzEuMzA4LDMuOTExLDIuNTk3LDcuODI3LDMuOTI3"
    "LDExLjczCgkJYzAuMTk2LDAuNTczLDAuMjE5LDAuOTEzLTAuMzcxLDEuMjgxYy01LjIwMywzLjI1NC0xMC4zODYsNi41NDMt"
    "MTUuNTc2LDkuODE4QzI2OC41MTUsMjIwLjczMiwyNjguMzk1LDIyMC43NjIsMjY4LjExMSwyMjAuODc1CgkJIE0yNDEuNDkz"
    "LDEzNi41NDdjLTAuMDkxLDAuMDA5LTAuMTgxLDAuMDE4LTAuMjcyLDAuMDI3Yy0wLjUzOCwxMy41LTEuMDc2LDI3LjAwMS0x"
    "LjYyNSw0MC43ODkKCQljNC44ODUtMC4zMDUsOS42NDQtMC42MDMsMTQuNDkxLTAuOTA1QzI0OS45MDgsMTYzLjIxNCwyNDUu"
    "NzAxLDE0OS44ODEsMjQxLjQ5MywxMzYuNTQ3Ii8+Cgk8cGF0aCBmaWxsPSIjRkZGRkZGIiBkPSJNMzA0LjYzMSwxMTkuNTg4"
    "aDAuOTYzYzAuMzk4LDAuMjM5LDAuNzcsMC41NDQsMS4xOTcsMC43MDUKCQljMy4wMzMsMS4xNSw2LjA1LDIuMzU1LDkuMTMz"
    "LDMuMzU0YzEuMDg5LDAuMzUzLDEuNDksMC45MjksMS44MDksMS45MTVjMy44MDYsMTEuNzgsNy42NDksMjMuNTQ3LDExLjQ4"
    "OCwzNS4zMTYKCQljMC4xNCwwLjQzMSwwLjMzLDAuODQ1LDAuNDk2LDEuMjY3YzAuNDU4LTUuMzQxLDAuNjg4LTEwLjYyOCww"
    "LjkyNy0xNS45MTVjMC4yNzYtNi4wOTIsMC41NjktMTIuMTgzLDAuODMtMTguMjc1CgkJYzAuMDMyLTAuNzUxLDAuMTk2LTEu"
    "MTQ5LDEuMDctMS4xOTRjNS44MS0wLjI5OSwxMS42MTktMC42MzYsMTcuNDI1LTAuOTk0YzAuODktMC4wNTQsMS4wOTksMC4y"
    "NDksMS4wMTMsMS4xMDEKCQljLTAuNDksNC44NjktMC45MzUsOS43NDQtMS4zOTIsMTQuNjE3Yy0xLjc0NSwxOC41ODktMi42"
    "NjgsMzcuMjEzLTIuMzkzLDU1Ljg4OWMwLjAyOCwxLjg5MywwLjAxNywxLjg2Mi0xLjkyMiwyLjE0CgkJYy00LjUyMywwLjY0"
    "OC05LjA0NywxLjI5OC0xMy41NTcsMi4wMjdjLTAuODAyLDAuMTI5LTAuOTc0LTAuMjgyLTEuMjM2LTAuNzgyYy0yLjgxMi01"
    "LjM2MS01LjYyNS0xMC43MjMtOC40NC0xNi4wODQKCQljLTEuMDAyLTEuOTA3LTIuMDA5LTMuODExLTMuMDE0LTUuNzE2Yy0w"
    "LjExMSwwLjAyMS0wLjIyMiwwLjA0Mi0wLjMzMywwLjA2M2MwLDAuNDk3LDAuMDAxLDAuOTk1LDAsMS40OTIKCQljLTAuMDIy"
    "LDcuMTQyLTAuMDU3LDE0LjI4NC0wLjA0NywyMS40MjVjMC4wMDEsMC45MDMtMC4yMTcsMS4zNDYtMS4xNzksMS41NTZjLTUu"
    "OTU3LDEuMzAxLTExLjg5OCwyLjY3Mi0xNy44NDYsNC4wMTIKCQljLTAuOTYxLDAuMjE2LTEuNDc2LTAuMTczLTEuNDc0LTEu"
    "MTU2YzAuMDAxLTAuNzE4LDAuMTMtMS40MzYsMC4xNzctMi4xNTRjMC4zNTMtNS4zNTgsMC42ODUtMTAuNzE4LDEuMDU0LTE2"
    "LjA3NAoJCWMwLjM1Mi01LjExNiwwLjc1OC0xMC4yMjksMS4xMS0xNS4zNDVjMC4zNjktNS4zNTYsMC42ODQtMTAuNzE3LDEu"
    "MDU0LTE2LjA3NGMwLjQzNS02LjI3NSwwLjkxOS0xMi41NDcsMS4zNTMtMTguODIyCgkJYzAuMzctNS4zNTYsMC42NzctMTAu"
    "NzE3LDEuMDctMTYuMDcyQzMwNC4wMjEsMTIxLjA1NiwzMDQuNDAxLDEyMC4zMjgsMzA0LjYzMSwxMTkuNTg4Ii8+Cgk8cGF0"
    "aCBmaWxsPSIjRkZGRkZGIiBkPSJNMzgwLjk4OSwxOTcuNzA2YzIuMjEzLDEuNjcxLDQuNTk2LDIuOTY5LDcuMjk3LDMuNTgz"
    "CgkJYzcuMjQyLDEuNjQ2LDEzLjUzMS0yLjM0MywxNS4xNTgtOS43MTNjMS41MDMtNi44MTMtMC4yNC0xMi45OS00LjI0NC0x"
    "OC41MjFjLTMuMzY0LTQuNjQ2LTcuODgtOC4xOTktMTIuMTEyLTEyLjAwOAoJCWMtMi40NzQtMi4yMjYtNS4wMS00LjM4OS03"
    "LjM5MS02LjcxMmMtNC44MDgtNC42OTEtNy4zNjctMTAuNTYyLTguODU0LTE3LjAyOWMtMi44NzctMTIuNTA4LTMuMDM4LTI0"
    "Ljk1NiwwLjU5LTM3LjMzNwoJCWMxLjY5Ni01Ljc5MSw0LjI0Ni0xMS4xNjEsOC40MzctMTUuNjE3YzQuMTEtNC4zNyw5LjAw"
    "OS02LjkzOSwxNS4xNzktNi4zMTFjNC41NzMsMC40NjUsOC4zNjMsMi42NTcsMTEuNjU2LDUuNzM4CgkJYzUuOTcxLDUuNTg5"
    "LDkuNTY1LDEyLjYzMywxMS44NSwyMC4zMzNjMS4xOSw0LjAxLDEuNzc5LDguMiwyLjU3MiwxMi4zMjFjMC4wNzQsMC4zODEt"
    "MC4yMTcsMS4wNS0wLjU0NiwxLjI1NwoJCWMtNC45OSwzLjE0My0xMC4wMTMsNi4yMzEtMTUuMDMxLDkuMzI3Yy0wLjA5NCww"
    "LjA1OC0wLjIxOCwwLjA2NC0wLjQ1MiwwLjEyOGMtMC4wMzgtMC40NzEtMC4wOTEtMC44OTYtMC4xMDYtMS4zMjIKCQljLTAu"
    "MjUzLTcuMDEzLTEuMDktMTMuOTI5LTMuOTc2LTIwLjQwOGMtMC44MTgtMS44MzYtMS45NTYtMy42MDUtMy4yNjktNS4xMjZj"
    "LTEuNjIyLTEuODc4LTMuMy0xLjY0OS00LjgwNSwwLjM0MQoJCWMtMS45MTUsMi41MzItMi43NjksNS41MDctMy4yODYsOC41"
    "NjFjLTEuODMzLDEwLjg0My0xLjA5MiwyMS40ODYsMi43MjEsMzEuODU0YzEuMTQ2LDMuMTE2LDIuODk5LDUuODIyLDUuNjY4"
    "LDcuNzM0CgkJYzEuODQxLDEuMjcxLDMuODA0LDIuMzY2LDUuNjU3LDMuNjIxYzQuODY5LDMuMjk2LDkuMzI0LDcuMDExLDEy"
    "Ljk4MSwxMS43MDhjNC44MTIsNi4xODIsNi41OTYsMTMuMTYzLDYuMjgxLDIwLjgyOQoJCWMtMC4zMzMsOC4wOTgtMi40NTMs"
    "MTUuNjY2LTYuOTM4LDIyLjQ2M2MtNC45NDMsNy40ODYtMTEuNTEzLDEyLjg3My0yMC41MjgsMTQuNTFjLTEwLjAzNCwxLjgx"
    "OS0xOC4zNjQtMS42NDUtMjUuMjI4LTguOTYKCQljLTAuMjA1LTAuMjItMC4yNzMtMC44NjUtMC4xMDYtMS4wODljMy40NzIt"
    "NC42NjgsNi45ODctOS4zMDQsMTAuNDk3LTEzLjk0MkMzODAuNzA3LDE5Ny44NiwzODAuNzkyLDE5Ny44MzIsMzgwLjk4OSwx"
    "OTcuNzA2Ii8+Cgk8cGF0aCBmaWxsPSIjRkZGRkZGIiBkPSJNNzU3LjUwMiwxNzkuNzEyYy0xMi44OTYtMC40MjMtMjMuMTgy"
    "LTUuMDg2LTMwLjQxNC0xNS42MTIKCQljLTguMjYyLTEyLjAyNC05LjA0OS0yNC45MTMtMS45NDUtMzcuODY1YzQuODg5LTgu"
    "OTE3LDEyLjUxNi0xNC4zMTEsMjEuOTUyLTE2LjUzMmMxMi44NDctMy4wMjQsMjQuMzc0LDAuMTM1LDM0LjA3NSw5LjU3NAoJ"
    "CWMxMS41MjksMTEuMjE2LDE0LjA5OCwyOC45LDYuMDAxLDQyLjg1N2MtNS4zMzUsOS4xOTYtMTMuMjU4LDE0LjU3NC0yMy4x"
    "NTgsMTYuNzE0CgkJQzc2MS42MzcsMTc5LjM2Miw3NTkuMTg4LDE3OS40OTYsNzU3LjUwMiwxNzkuNzEyIE03NzEuODMxLDE0"
    "My42NWMtMC4yMy0xLjUwNC0wLjM2Ni0zLjAzMy0wLjcwOS00LjUwOAoJCWMtMS42NzYtNy4yMjgtNy45NTgtMTMuNjYxLTE2"
    "LjI3MS0xNC41ODNjLTEwLjYzMi0xLjE3OS0xOS4yNzEsOC42NjYtMTcuMTY0LDE5Ljc1MWMxLjY1NCw4LjcwNSw4LjIzNSwx"
    "NS4wMiwxNi43ODgsMTUuODI1CgkJQzc2My43NDUsMTYxLjAwNyw3NzEuNjU5LDE1My43ODIsNzcxLjgzMSwxNDMuNjUiLz4K"
    "CTxwYXRoIGZpbGw9IiNGRkZGRkYiIGQ9Ik04ODYuNDAzLDE0OC4yMjRjMi44NjUtMC41MjIsNS41NzQtMS4wMSw4LjI4LTEu"
    "NTExCgkJYzUuMTMtMC45NSwxMC4yNTMtMS45NDMsMTUuMzk0LTIuODMzYzEuMDE1LTAuMTc1LDEuMTktMC41OCwxLjEyNy0x"
    "LjQ4OGMtMC41MzItNy42OTYtMS4wMjctMTUuMzk0LTEuNTM2LTIzLjA5MgoJCWMtMC4xODEtMi43NTQtMC4zNjktNS41MDgt"
    "MC41NDMtOC4yNjNjLTAuMDY4LTEuMTA3LDAuMjY0LTEuMzQsMS4yNDgtMC43OTJjNS4zNTEsMi45NzUsMTAuNjgzLDUuOTgz"
    "LDE2LjAzMyw4Ljk1NwoJCWMwLjYzOSwwLjM1NiwwLjg4MSwwLjgwNSwwLjkxNiwxLjUyOWMwLjM2MSw3LjQ0MSwwLjc1Nywx"
    "NC44NzksMS4xMzYsMjIuMzE5YzAuNDYxLDkuMDYzLDAuOTE0LDE4LjEyNywxLjM3MiwyNy4xOQoJCWMwLjM2OSw3LjI4OCww"
    "LjczOCwxNC41NzcsMS4xMSwyMS44NjVjMC4wNzMsMS40MzUsMC4xOTYsMi44NjgsMC4yMDcsNC4zMDNjMC4wMDMsMC4zMzct"
    "MC4zMDgsMC45MzUtMC41NTIsMC45NzkKCQljLTUuMTI5LDAuOTM0LTEwLjI3MSwxLjgwNi0xNS42MTcsMi43MjZjLTAuODYz"
    "LTEzLjA4MS0xLjcyMS0yNi4xMDQtMi41OTItMzkuMzE5Yy0xLjgzNSwwLjIyNC0zLjU3OSwwLjQyMy01LjMxOSwwLjY1MwoJ"
    "CWMtNi4zMzUsMC44MzktMTIuNjY1LDEuNzE2LTE5LjAwNiwyLjUwOGMtMS4xNTYsMC4xNDUtMS40NTEsMC41NTEtMS40MTgs"
    "MS43MDNjMC4xODYsNi4zODgsMC4yNzEsMTIuNzc5LDAuMzk4LDE5LjE2OAoJCWMwLjA4Niw0LjMxMSwwLjE1Miw4LjYyLDAu"
    "Mjk4LDEyLjkyOWMwLjAzNCwxLjAzLTAuMjE4LDEuMzY5LTEuMjk0LDEuMzc2Yy00LjQ2MiwwLjAyOS04LjkyNCwwLjEyMi0x"
    "My4zODQsMC4yNzEKCQljLTEuMTIxLDAuMDM3LTEuMzkxLTAuMzU0LTEuNDI2LTEuNDAzYy0wLjMtOS4xMDgtMC42NDYtMTgu"
    "MjE1LTAuOTg3LTI3LjMyMmMtMC40ODEtMTIuODg3LTAuOTcyLTI1Ljc3NC0xLjQ1Ni0zOC42NjEKCQljLTAuMjM3LTYuMzQ5"
    "LTAuNDU2LTEyLjY5OS0wLjcyMS0xOS4wNDZjLTAuMDM4LTAuOTEzLDAuMjg2LTEuMjY3LDEuMTY2LTEuNDA2YzUuMDA1LTAu"
    "NzkyLDEwLjAwNi0xLjYxMSwxNS0yLjQ2NgoJCWMwLjg4NC0wLjE1MSwxLjE5MywwLjE3OSwxLjIyLDAuOTdjMC4wOTUsMi43"
    "NTgsMC4yMSw1LjUxNSwwLjI2OSw4LjI3NGMwLjIwMSw5LjU2NSwwLjM4MSwxOS4xMzEsMC41NzMsMjguNjk3CgkJQzg4Ni4z"
    "MDcsMTQ3LjM3MSw4ODYuMzU2LDE0Ny43MDQsODg2LjQwMywxNDguMjI0Ii8+Cgk8cGF0aCBmaWxsPSIjRkZGRkZGIiBkPSJN"
    "MTMzLjQxNywxNDEuMjI1YzAsMi43NDYsMC4wMzksNS4yNjctMC4wMSw3Ljc4NwoJCWMtMC4wNTYsMi44ODgtMC4yMTUsNS43"
    "NzQtMC4yNzUsOC42NjFjLTAuMDE4LDAuODc4LTAuMjg3LDEuMjQ2LTEuMjIsMS4zNjNjLTkuNzUzLDEuMjIzLTE5LjQ5Niwy"
    "LjUyNC0yOS4yNTIsMy43MgoJCWMtMS4yNjMsMC4xNTUtMS42OTgsMC42NjYtMS44MjMsMS44MjZjLTAuOTU2LDguODUzLTIu"
    "MDc3LDE3LjY5MS0yLjg4MSwyNi41NTdjLTAuNzIzLDcuOTU5LTAuNzc0LDE1Ljk1LTAuMDUyLDIzLjkyNQoJCWMwLjQ2MSw1"
    "LjA4NCwxLjEzOCwxMC4xMzcsMy4xNjcsMTQuODg3YzAuNDIzLDAuOTg5LDAuOTMyLDEuOTY4LDEuNTYxLDIuODM2YzIuMzk2"
    "LDMuMzA2LDUuMzEyLDMuOTUzLDguOTQyLDIuMDcxCgkJYzMuMDExLTEuNTYxLDUuNDUzLTMuODMzLDcuNjE5LTYuMzc0YzIu"
    "MDUyLTIuNDA3LDMuOTA4LTQuOTgzLDUuODUtNy40ODZjMC4yMzktMC4zMDcsMC40NzYtMC42MTYsMC43NjEtMC45ODIKCQlj"
    "MC4yMjksMC4xODUsMC40LDAuMjgyLDAuNTIsMC40MjVjNC4wNDMsNC43NzcsOC4wNjQsOS41NzQsMTIuMTM2LDE0LjMyNmMw"
    "LjcyNSwwLjg0NiwwLjc0NiwxLjM0Ni0wLjA5OCwyLjE1MgoJCWMtNi41MTMsNi4yMjktMTMuMjc5LDEyLjEyMi0yMS4yMzMs"
    "MTYuNDg2Yy00LjQ3MiwyLjQ1NC05LjE0Nyw0LjMzNC0xNC4zMjIsNC41NzJjLTcuODczLDAuMzYxLTEzLjQ5NC0zLjM2NC0x"
    "Ny41LTkuODU4CgkJYy0zLjQ3Ni01LjYzNC01LjA5LTExLjkyMy02LjE4NC0xOC4zNjNjLTEuNTM5LTkuMDU4LTEuNzUzLTE4"
    "LjE5Ni0xLjQ5OC0yNy4zNTRjMC40NDEtMTUuNzk0LDIuMzYyLTMxLjQzNCw0Ljg5Ny00Ny4wMTIKCQljMi43OTEtMTcuMTU0"
    "LDYuMzk1LTM0LjE0LDEwLjQ4Ny01MS4wMjZjMC40NDQtMS44MzIsMC44NDItMy42NzcsMS4zNy01LjQ4NWMwLjEyNy0wLjQz"
    "NCwwLjYzMi0wLjk3MSwxLjA1Ny0xLjA3NAoJCWM3LjA5NS0xLjcxOSwxNC4yMDUtMy4zNzgsMjEuMzEzLTUuMDQ2YzAuMTUy"
    "LTAuMDM1LDAuMzE2LTAuMDE4LDAuODA2LTAuMDQxYy01LjEyMywxNy4wMTYtOS41OTksMzQuMDY4LTEzLjA5OCw1MS42NjQK"
    "CQlDMTE0LjI4MiwxNDMuMzEyLDEyMy44NTgsMTQyLjI2NywxMzMuNDE3LDE0MS4yMjUiLz4KCTxwYXRoIGZpbGw9IiNGRkZG"
    "RkYiIGQ9Ik00NDMuNjksMjMzLjU0MWMtMC4yNS05LjE4OC0wLjQ2Mi0xNy4zLTAuNjk1LTI1LjQxMgoJCWMtMC4yMDUtNy4x"
    "MjMtMC40MzUtMTQuMjQ2LTAuNjUzLTIxLjM2OWMtMC4yMjEtNy4yMjUtMC40NDItMTQuNDUtMC42NjMtMjEuNjc1Yy0wLjIx"
    "Ny03LjEyNC0wLjQzNS0xNC4yNDctMC42NTMtMjEuMzcKCQljLTAuMjk1LTkuNjQ1LTAuNTktMTkuMjktMC44ODUtMjguOTM2"
    "Yy0wLjIxNy03LjA4OS0wLjQzNy0xNC4xNzgtMC42NTItMjEuMjY3Yy0wLjE0OS00Ljk0Mi0wLjM4Ni05Ljg4NC0wLjM4OS0x"
    "NC44MjcKCQljLTAuMDAxLTEuOTgtMC40MDQtNC4wMTIsMC40MTQtNS45NzJjMC43MzQtMS43NjIsMS42MjgtMi40NTMsMy42"
    "MjEtMi41NzJjNi42MS0wLjM5NywxMy4yMi0wLjc4LDE5LjgyOC0xLjE5NQoJCWM3LjM3NC0wLjQ2MiwxNC43NDYtMC45NTMs"
    "MjIuMTE5LTEuNDMzYzYuMjc4LTAuNDA4LDEyLjU1Ni0wLjgxOCwxOC44MzQtMS4yMjZjNy4zNzItMC40OCwxNC43NDUtMC45"
    "NTksMjIuMTE4LTEuNDM4CgkJYzYuMjc3LTAuNDA4LDEyLjU1NS0wLjgyMywxOC44MzQtMS4yMmMzLjUwNC0wLjIyMiw3LjAw"
    "OS0wLjQyMywxMC41MTQtMC42MThjMi4yMy0wLjEyNSwzLjc2NywwLjg0Niw0LjM3NCwyLjgyOQoJCWMwLjI2LDAuODQ1LDAu"
    "NDQzLDEuNzMzLDAuNDg5LDIuNjA5YzAuMzI4LDYuMzY0LDAuNjAzLDEyLjczLDAuOTE1LDE5LjA5NWMwLjI4OCw1Ljg1NCww"
    "LjU5NCwxMS43MDcsMC45MDYsMTcuNTYKCQljMC4yOCw1LjI3NCwwLjU4LDEwLjU0OCwwLjg3MywxNS44MjFjMC4zNjgsNi42"
    "NjksMC43MzcsMTMuMzM4LDEuMTA2LDIwLjAwN2MwLjI5LDUuMjQsMC41ODIsMTAuNDc5LDAuODcyLDE1LjcxOQoJCWMwLjM2"
    "OSw2LjY2OSwwLjczNiwxMy4zMzgsMS4xMDUsMjAuMDA3YzAuMjg5LDUuMjQsMC41ODQsMTAuNDc5LDAuODczLDE1LjcxOWMw"
    "LjM3LDYuNzAzLDAuNzM4LDEzLjQwNiwxLjEwOSwyMC4xMDkKCQljMC4yODgsNS4yMDYsMC42MzcsMTAuNDA4LDAuODUsMTUu"
    "NjE3YzAuMTUyLDMuNzc0LDAuMjUyLDcuNTU5LDAuMTE0LDExLjMyOWMtMC4wNTQsMS40NzktMC43MzIsMi45NjItMS4yNzUs"
    "NC4zOTUKCQljLTAuNjU1LDEuNzI3LTIuMTQxLDIuNjc4LTQuMDEzLDMuMDAyYy0yLjQzOSwwLjQyMi00Ljg5NCwwLjgzNy03"
    "LjM2MiwxLjA0OGMtNy4wMywwLjYtMTQuMDcsMS4xMDQtMjEuMTA4LDEuNjI5CgkJYy01LjAzMiwwLjM3Ni0xMC4wNjgsMC43"
    "MTQtMTUuMSwxLjA5NWMtNC44ODYsMC4zNy05Ljc2NywwLjc4Ni0xNC42NTEsMS4xNTZjLTQuOTk2LDAuMzc5LTkuOTk1LDAu"
    "NzE2LTE0Ljk5MSwxLjA5NAoJCWMtNC44ODUsMC4zNy05Ljc2OCwwLjc3OC0xNC42NTMsMS4xNDljLTQuMTIxLDAuMzE0LTgu"
    "MjQ0LDAuNjA2LTEyLjM2NiwwLjg5NmMtMi45OTEsMC4yMS01Ljk4MywwLjQxOS04Ljk3NiwwLjU5OAoJCWMtMi44ODcsMC4x"
    "NzMtNS4yLTAuODYtNy4wMjItMi45MzljLTIuMDc5LTIuMzczLTMuMzIzLTQuOTk2LTMuMzY0LTguMTNDNDQ0LjAzNCwyNDAu"
    "NDM4LDQ0My44MDMsMjM2LjQ1Miw0NDMuNjksMjMzLjU0MSIvPgoJPHBhdGggZmlsbD0iI0ZGRkZGRiIgZD0iTTg1MS41MTUs"
    "MTI0LjM3Yy0zLjA4OCwzLjM2My02LjA2OCw2LjYxLTkuMDgxLDkuODkyCgkJYy0xLjI2My0xLjA3NS0yLjQzMi0yLjE3Mi0z"
    "LjcwNS0zLjEzN2MtMy4wMDgtMi4yNzgtNi4zMjYtMy44OTMtMTAuMjM3LTMuOTIxYy00LjkyNS0wLjAzNi04LjQwNywyLjQ1"
    "OS0xMS4wNTQsNi4yMzYKCQljLTIuNzA0LDMuODU4LTQuMDI0LDguMjM2LTQuNTAzLDEyLjgxMmMtMS4zMTcsMTIuNTgxLDEu"
    "NDk1LDI0LjI3NSw4LjQ2NSwzNC45NTljMi45OTgsNC41OTUsNi43NDksOC41MDQsMTEuOTExLDEwLjkwOQoJCWM2LjA1NCwy"
    "LjgyLDExLjk3NiwxLjUwNSwxNi4yMjYtMy41OTJjMC4xOTQtMC4yMzQsMC40MTgtMC40NDUsMC41OTEtMC42MjdjMy4wOTQs"
    "My44OTEsNi4xNTEsNy43MzYsOS4yNjcsMTEuNjU1CgkJYy0zLjMxOSwzLjk5LTcuMzk0LDcuMDM3LTEyLjUxLDguNjA5Yy04"
    "LjA1MSwyLjQ3My0xNS41NjMsMC44ODQtMjIuNjQ2LTMuMTU2Yy03LjgyNS00LjQ2My0xMy41MzQtMTAuOTM3LTE3Ljk3NC0x"
    "OC41CgkJYy02Ljk0Ni0xMS44MzUtMTAuMTY0LTI0LjU5OS05LjUzNS0zOC4yMTdjMC40MDEtOC43MDMsMi41MDUtMTYuOTcz"
    "LDcuNTctMjQuMzEzYzQuNTgxLTYuNjM1LDEwLjc0Mi0xMS4wMDMsMTkuMDk0LTExLjk3NgoJCWM0LjgxMy0wLjU2LDkuNDE1"
    "LDAuMzQxLDEzLjg0MywyLjE4N0M4NDIuNzc2LDExNi41MDEsODQ3LjM5MywxMjAuMDI5LDg1MS41MTUsMTI0LjM3Ii8+Cgk8"
    "cGF0aCBmaWxsPSIjRkZGRkZGIiBkPSJNODUzLjAxOCwxMzcuMzA1YzAuMzI0LTguMDEyLDEuODc2LTE1Ljc5NCwzLjQ2Ny0y"
    "My43NjEKCQljMC40OCwwLjM1NywwLjg1MywwLjYxNywxLjIwNywwLjg5OWMxLjY0NCwxLjMwNCwzLjI3MywyLjYyNSw0Ljky"
    "OCwzLjkxNmMwLjQ3NSwwLjM3LDAuNTc4LDAuNjkyLDAuMjY0LDEuMjU0CgkJYy0zLjIzMSw1Ljc5OS02LjQzOCwxMS42MTIt"
    "OS42NTQsMTcuNDE5Qzg1My4xNzMsMTM3LjEzMiw4NTMuMDg4LDEzNy4yMTUsODUzLjAxOCwxMzcuMzA1Ii8+CgkKCQk8cGF0"
    "aCBmaWxsPSJub25lIiBzdHJva2U9IiNGRkZGRkYiIHN0cm9rZS13aWR0aD0iNCIgc3Ryb2tlLWxpbmVjYXA9InNxdWFyZSIg"
    "c3Ryb2tlLWxpbmVqb2luPSJiZXZlbCIgc3Ryb2tlLW1pdGVybGltaXQ9IjEwIiBkPSIKCQlNMzYuNzY4LDE2Ni42NTljMjcu"
    "MzMtNy44MDIsNTEuODU1LTEyLjg5MSw4MC4zMTItMTQuNDk5YzEyLjk3Ni0wLjczMywyNS41NDYsMS41OTMsMzcuNTM3LDYu"
    "NjM4CgkJYzE3LjY5NSw3LjQ0NCwzNS45NjUsMTQuNTc0LDU0LjU1NiwxOS40NDRjNDIuNzc3LDExLjIwNyw4My43NzQtNS41"
    "MjQsMTI0LjYzOC0xNi43MjdjNDMuMTM2LTExLjgyNCw4Ny4zNTktMTQuMjY4LDEzMS44NzktMTQuMjY4CgkJIi8+Cgk8cGF0"
    "aCBmaWxsPSIjRkZGRkZGIiBkPSJNNTQyLjQ0MywxNjYuNTM3YzE2Ljk4NCwwLjA2NywzMy45NzUsMS44NDUsNTAuOTE1LTAu"
    "MTM1CgkJYzE3LjY5NS0yLjA2OCwzMy4xODgtMTAuNjAzLDQ5LjEwOC0xOC4wMDVjMjYuNjQyLTEyLjM4OCw2MC4wMzEtMjIu"
    "ODE1LDg4Ljc5OC0xMC43MzJjMi45NzIsMS4yNDgsNC4yNTUtMy41OTIsMS4zMjktNC44MjEKCQljLTI2LjcyOS0xMS4yMjgt"
    "NTkuMDU0LTMuNTg1LTg0LjQxNiw3LjUwM2MtMTcuMDU1LDcuNDU1LTMzLjQ1LDE3LjQ3OC01MS45OTcsMjAuNjQ3Yy0xNy42"
    "ODgsMy4wMjItMzUuOTI3LDAuNjE0LTUzLjczNywwLjU0MwoJCUM1MzkuMjE5LDE2MS41MjQsNTM5LjIyLDE2Ni41MjQsNTQy"
    "LjQ0MywxNjYuNTM3Ii8+Cgk8cGF0aCBmaWxsPSJub25lIiBzdHJva2U9IiNGRkZGRkYiIHN0cm9rZS13aWR0aD0iMyIgc3Ry"
    "b2tlLW1pdGVybGltaXQ9IjEwIiBkPSJNNzczLjk1OSwxNDcuNTQ3CgkJYzU2LjM2NiwzNy4xNzgsMTM5LjE0OC05Ljg5NCwx"
    "OTcuOTEzLTcuNDk2Ii8+Cgk8cGF0aCBkPSJNNTE5LjAwNiwxMzUuODM0Yy0wLjIzNSwyLjMxMi0wLjQ1MSw0LjQ0My0wLjY3"
    "LDYuNTc0Yy0wLjg3OSw4LjUzLTEuOTIsMTcuMDQ3LTIuNTk4LDI1LjU5MwoJCWMtMC43NDgsOS40MjctMS4yOTcsMTguODc1"
    "LTEuNjc3LDI4LjMyMmMtMC4yNDgsNi4xNjItMC4yMSwxMi4zNSwwLjAyNSwxOC41MTNjMC4xNDgsMy45MDQsMC44MjQsNy43"
    "OTgsMS40MDgsMTEuNjc0CgkJYzAuMTg2LDEuMjM4LDAuNzEzLDIuNDcxLDEuMzA0LDMuNTljMC44NDIsMS41OTcsMS45NTQs"
    "MS43NiwzLjI4NywwLjUxN2MwLjgxMi0wLjc1NywxLjQyMy0xLjcyNiwyLjE0MS0yLjU4NgoJCWMwLjE5Mi0wLjIzLDAuNDQ0"
    "LTAuNDEzLDAuODMxLTAuNzY2YzAsMC41MjcsMC4xMTQsMC44OTEtMC4wMTcsMS4xMTFjLTIuOTE5LDQuOTE4LTUuOTkzLDku"
    "Ny0xMS4wNjIsMTIuNzY3CgkJYy0yLjM4LDEuNDQtNC44ODcsMi40NDEtNy43MDYsMi4zNTFjLTMuNDMtMC4xMDctNi4wOTkt"
    "MS43NzQtOC4zMDItNC4yODJjLTIuODYtMy4yNTQtNC41ODktNy4xMjYtNS45NzEtMTEuMTY3CgkJYy0yLjQwMS03LjAxOS0z"
    "LjU1Mi0xNC4yOTctNC4zMjUtMjEuNjQxYy0wLjg5OC04LjU0NS0xLjA5NC0xNy4xMi0wLjk4NS0yNS43MDdjMC4xMzItMTAu"
    "NDMxLDAuOTM2LTIwLjgxOCwxLjkwNS0zMS4xOTkKCQljMC4xMjItMS4zMTUsMC44MzUtMS43NTIsMS44NjMtMi4xNTJjNi45"
    "NDctMi43MDEsMTMuODgtNS40MzgsMjAuODI3LTguMTM3YzIuNzY0LTEuMDc0LDUuNTUtMi4wOSw4LjMzMy0zLjExMwoJCUM1"
    "MTcuOTY5LDEzNS45NjcsNTE4LjM2MSwxMzUuOTUyLDUxOS4wMDYsMTM1LjgzNCIvPgoJPHBhdGggZD0iTTQ3OS4wMzYsMTA3"
    "LjY3N2MxNi4xMTctMi42NDksMzIuMDk3LTUuMjQxLDQ4LjQ3NC03LjE4MmMwLDEuNTQ5LDAuMTMsMi44MTMtMC4wMjEsNC4w"
    "NDMKCQljLTAuOTMsNy41ODYtMy45MDIsMTQuMTU5LTkuODEzLDE5LjJjLTguNzgsNy40ODgtMjEuMjMsNy40NDEtMjkuOTEx"
    "LTAuMTQ3QzQ4My4wMTcsMTE5LjQ0MSw0ODAuMjUzLDExNC4xNTgsNDc5LjAzNiwxMDcuNjc3Ii8+Cgk8cGF0aCBkPSJNNDc4"
    "LjQ5LDEwMi41MjJjLTAuMTE2LTIuNTQ3LDAuNC00LjcyMSwwLjkwMi02Ljg2OGMxLjYxMS02LjksNS4xNzQtMTIuNTU4LDEx"
    "LjA3MS0xNi41ODcKCQljMTEuNTA2LTcuODYxLDI2LjU3OC0zLjkzMSwzMy40OTMsOC43MDVjMC45NDgsMS43MzUsMS44Nzks"
    "My41MDEsMi4xOTMsNS44QzUxMC4xMjksOTUuNzIyLDQ5NC4yNjIsOTguMTg5LDQ3OC40OSwxMDIuNTIyIi8+CjwvZz4KPC9z"
    "dmc+Cg=="
)
# Akai MPD218, preset d'usine, banque A : pads 1-16 = notes 36-51.
# Le pad 1 est en bas à gauche, comme sur l'écran : la grille reproduit le contrôleur.
# Canal -1 = n'importe quel canal (le MPD218 envoie sur le canal 10 par défaut).
MPD218_NAME = "MPD218"


def mpd218_key(index):
    row, col = divmod(index, COLS)
    return ["note", -1, 36 + (ROWS - 1 - row) * COLS + col]


def apply_mpd218(cfg):
    for i, pad in enumerate(cfg["pads"]):
        pad["midi"] = mpd218_key(i)


def same_port(a, b):
    """Windows ajoute un numéro au nom du port (« MPD218 0 », « MPD218 1 »…)."""
    import re
    strip = lambda n: re.sub(r"\s+\d+$", "", n or "").strip().lower()
    return strip(a) == strip(b)


def find_port(names, wanted):
    if not wanted:
        return None
    if wanted in names:
        return wanted
    return next((n for n in names if same_port(n, wanted)), None)


def midi_match(stored, kind, ch, num):
    return bool(stored) and stored[0] == kind and stored[2] == num and stored[1] in (-1, ch)
FADE_SEC = 0.025  # micro-fondu pour éviter les clics à l'arrêt


def default_pad():
    return {"file": None, "label": "", "mode": "toggle", "gain_db": 0.0, "midi": None}


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
    if not any(p["midi"] for p in cfg["pads"]) and not cfg["stop_all_midi"]:
        apply_mpd218(cfg)
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
        self.ended = False  # arrivée au bout du son (pas un arrêt manuel)

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
                self.ended = self.fade_left is None
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
        self.ended = []  # pads arrivés au bout, lus par l'interface

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
                if v.ended:
                    self.ended.append(v.pad)
                    v.ended = False
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

    def pop_ended(self):
        with self.lock:
            out, self.ended = self.ended, []
        return out

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
        ch = 9 if ch < 0 else ch
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
    return f"{prefix}{num}" if ch < 0 else f"{prefix}{num} c{ch + 1}"


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
        self.warn = False
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
        col = QtGui.QColor(pad_color(self.index))
        if empty:
            bg = QtGui.QColor("#22262d")
        elif self.playing:
            bg = col.lighter(118)
        else:
            bg = col.darker(150)

        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 7, 7)
        # liseré de couleur de colonne en haut du pad
        p.setBrush(col)
        p.drawRoundedRect(QtCore.QRectF(r.left(), r.top(), r.width(), 5), 2, 2)

        light = bg.lightness() > 140
        fg = QtGui.QColor("#15181c") if light else QtGui.QColor("#f2f4f7")
        dim = QtGui.QColor(fg)
        dim.setAlpha(150)

        if self.playing and self.progress > 0:
            bar = QtCore.QRectF(r.left() + 6, r.bottom() - 10, (r.width() - 12) * self.progress, 5)
            p.setBrush(fg)
            p.drawRoundedRect(bar, 2, 2)

        if self.learn_target:
            p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 3, Qt.DashLine))
        elif self.playing:
            p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 3))
        elif self.error:
            p.setPen(QtGui.QPen(QtGui.QColor("#e05555"), 2))
        else:
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 25), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 7, 7)

        inner = r.adjusted(9, 9, -9, -6)
        # nom de la case : A1, B3…
        fn = self.font()
        fn.setPointSizeF(13)
        fn.setBold(True)
        p.setFont(fn)
        p.setPen(col if empty else fg)
        p.drawText(inner, Qt.AlignLeft | Qt.AlignTop, pad_name(self.index))

        f = self.font()
        f.setPointSizeF(8.5)
        p.setFont(f)
        p.setPen(dim)
        p.drawText(inner, Qt.AlignRight | Qt.AlignTop, midi_label(self.cfg["midi"]))
        mode_txt = {"loop": "boucle", "restart": "relance"}.get(self.cfg["mode"], "")
        bottom_left = "  ".join(t for t in (f"[{KEY_LABELS[self.index]}]", mode_txt) if t)
        p.drawText(inner.adjusted(0, 0, 0, -6), Qt.AlignLeft | Qt.AlignBottom, bottom_left)

        f2 = self.font()
        f2.setPointSizeF(10 if empty else 12)
        f2.setBold(not empty)
        p.setFont(f2)
        p.setPen(dim if empty else fg)
        p.drawText(inner.adjusted(0, 18, 0, -18), Qt.AlignCenter | Qt.TextWordWrap,
                   "Fichier introuvable" if self.error else self.display_name())

        if self.time_text:
            f3 = self.font()
            f3.setPointSizeF(11 if self.playing else 10)
            f3.setBold(self.playing)
            p.setFont(f3)
            p.setPen(QtGui.QColor("#c81e1e" if light else "#ff5a5a") if self.warn else fg)
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

        self.setWindowTitle(f"{self.cfg['title']} — Cartoucheur Transistoc'h")
        self.resize(1100, 760)
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

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(14)
        logo = QtWidgets.QLabel()
        logo.setPixmap(self._logo_pixmap(46))
        header.addWidget(logo)

        self.title_lbl = QtWidgets.QLabel(self.cfg["title"])
        tf = self.title_lbl.font()
        tf.setPointSize(18)
        tf.setBold(True)
        self.title_lbl.setFont(tf)
        self.title_lbl.setToolTip("Double-clic pour renommer l'émission")
        self.title_lbl.installEventFilter(self)
        header.addWidget(self.title_lbl)
        header.addStretch(1)

        self.end_lbl = QtWidgets.QLabel("")
        self.end_lbl.setAlignment(Qt.AlignCenter)
        self.end_lbl.setMinimumWidth(260)
        self.end_lbl.setMinimumHeight(44)
        ef = self.end_lbl.font()
        ef.setPointSize(15)
        ef.setBold(True)
        self.end_lbl.setFont(ef)
        self._end_until = 0.0
        self._end_text = ""
        header.addWidget(self.end_lbl)
        header.addStretch(1)

        self.clock_lbl = QtWidgets.QLabel("00:00:00")
        cf = QtGui.QFont("Consolas")
        cf.setStyleHint(QtGui.QFont.Monospace)
        cf.setPointSize(34)
        cf.setBold(True)
        self.clock_lbl.setFont(cf)
        self.clock_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(self.clock_lbl)
        root.addLayout(header)

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
        for c in range(COLS):
            h = QtWidgets.QLabel(COL_NAMES[c])
            h.setAlignment(Qt.AlignCenter)
            h.setStyleSheet(f"background:{COL_COLORS[c]}; color:#15181c; font-weight:bold;"
                            "font-size:16px; border-radius:5px; padding:3px;")
            grid.addWidget(h, 0, c + 1)
        for r_ in range(ROWS):
            n = QtWidgets.QLabel(str(r_ + 1))
            n.setAlignment(Qt.AlignCenter)
            n.setFixedWidth(26)
            n.setStyleSheet("color:#aab2bd; font-weight:bold; font-size:18px;")
            grid.addWidget(n, r_ + 1, 0)
        self.pads = []
        for i in range(NPADS):
            w = PadWidget(i)
            w.cfg = self.cfg["pads"][i]
            w.clicked.connect(lambda i: self.trigger(i, from_click=True))
            w.menuRequested.connect(self.pad_menu)
            w.filesDropped.connect(self.on_files_dropped)
            grid.addWidget(w, i // COLS + 1, i % COLS + 1)
            self.pads.append(w)
        root.addLayout(grid, 1)

        bottom = QtWidgets.QHBoxLayout()
        self.stop_btn = QtWidgets.QPushButton("TOUT ARRÊTER")
        self.stop_btn.setObjectName("stop")
        self.stop_btn.setMinimumHeight(44)
        self.stop_btn.setMinimumWidth(170)
        self.stop_btn.setToolTip("Arrête tous les sons (touche Échap)")
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
        mpd_btn = QtWidgets.QPushButton("Mapping MPD218")
        mpd_btn.setMinimumHeight(44)
        mpd_btn.setToolTip("Remettre l'assignation d'usine du MPD218 sur les 16 pads")
        mpd_btn.clicked.connect(self.on_mpd218)
        bottom.addWidget(mpd_btn, 1)
        exp_btn = QtWidgets.QPushButton("Exporter la banque")
        exp_btn.setMinimumHeight(44)
        exp_btn.setToolTip("Enregistrer les 16 pads et leurs sons dans un fichier .cartouche")
        exp_btn.clicked.connect(self.export_bank)
        imp_btn = QtWidgets.QPushButton("Importer une banque")
        imp_btn.setMinimumHeight(44)
        imp_btn.setToolTip("Recharger une banque .cartouche exportée")
        imp_btn.clicked.connect(self.import_bank)
        bottom.addWidget(exp_btn, 1)
        bottom.addWidget(imp_btn, 1)
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
        self.setMinimumSize(1000, 660)

    @staticmethod
    def _logo_pixmap(height):
        renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(base64.b64decode("".join(LOGO_SVG_B64))))
        size = renderer.defaultSize()
        ratio = 2.0
        w = int(size.width() * height / size.height())
        pm = QtGui.QPixmap(int(w * ratio), int(height * ratio))
        pm.fill(Qt.transparent)
        painter = QtGui.QPainter(pm)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        renderer.render(painter)
        painter.end()
        pm.setDevicePixelRatio(ratio)
        return pm

    def eventFilter(self, obj, ev):
        if obj is self.title_lbl and ev.type() == QtCore.QEvent.MouseButtonDblClick:
            txt, ok = QtWidgets.QInputDialog.getText(self, "Nom de l'émission", "Nom :",
                                                     text=self.cfg["title"])
            if ok and txt.strip():
                self.cfg["title"] = txt.strip()
                self.title_lbl.setText(self.cfg["title"])
                self.setWindowTitle(f"{self.cfg['title']} — Cartoucheur Transistoc'h")
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
        names = MidiBridge.input_names()
        port = find_port(names, self.cfg["midi_in"])
        if port is None and not self.cfg["midi_in"]:
            # aucun contrôleur choisi : on prend le MPD218 s'il est branché
            port = next((n for n in names if MPD218_NAME.lower() in n.lower()), None)
        if port and port != self.cfg["midi_in"]:
            self.cfg["midi_in"] = port
            self.save()
        self._fill_combo(self.midi_in_combo, names, self.cfg["midi_in"], none_label="Aucun")
        out_names = MidiBridge.output_names()
        out_port = find_port(out_names, self.cfg["midi_out"])
        if out_port and out_port != self.cfg["midi_out"]:
            self.cfg["midi_out"] = out_port
            self.save()
        self._fill_combo(self.midi_out_combo, out_names, self.cfg["midi_out"], none_label="Aucun")
        try:
            self.midi.open_input(port)
        except Exception as e:
            self.status(f"Contrôleur MIDI indisponible : {e}")
        try:
            self.midi.open_output(out_port)
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
                self.status(f"Pad {pad_name(i)} : impossible de lire {path.name} ({e})")
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

    def show_end_alert(self, i):
        self._end_text = f"FIN DE SON · {pad_name(i)}"
        self._end_until = QtCore.QDateTime.currentMSecsSinceEpoch() / 1000 + END_ALERT_SEC

    def tick(self):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        if self.clock_lbl.text() != now:
            self.clock_lbl.setText(now)

        for i in self.engine.pop_ended():
            self.show_end_alert(i)
        t = QtCore.QDateTime.currentMSecsSinceEpoch() / 1000
        if t < self._end_until:
            blink = int(t * 3) % 2 == 0
            self.end_lbl.setText(self._end_text)
            self.end_lbl.setStyleSheet(
                "border-radius:8px; padding:4px 16px; color:white;"
                f"background:{'#d42b2b' if blink else '#7a1c1c'};")
        elif self.end_lbl.text():
            self.end_lbl.setText("")
            self.end_lbl.setStyleSheet("")

        sr = self.engine.samplerate
        for i, w in enumerate(self.pads):
            st = self.engine.state(i)
            data = self.cache.get(i)
            if st:
                pos, length = st
                w.playing = True
                w.progress = pos / length if length else 0
                remaining = (length - pos) / sr
                w.time_text = "-" + fmt_time(remaining)
                w.warn = remaining <= WARN_SEC and self.cfg["pads"][i]["mode"] != "loop"
            else:
                w.playing = False
                w.progress = 0
                w.warn = False
                w.time_text = fmt_time(len(data) / sr) if data is not None else ""
            w.learn_target = self.learn_mode and self.learn_target == i
            w.update()
            if self.led_state[i] != w.playing:
                self.led_state[i] = w.playing
                self.midi.send_led(self.cfg["pads"][i]["midi"], w.playing)

    # ---------- MIDI
    def on_mpd218(self):
        if QtWidgets.QMessageBox.question(
                self, "Mapping MPD218",
                "Remplacer les assignations MIDI des 16 pads par celles du MPD218 ?") \
                != QtWidgets.QMessageBox.Yes:
            return
        for i in range(NPADS):
            self.midi.send_led(self.cfg["pads"][i]["midi"], False)
        apply_mpd218(self.cfg)
        self.led_state = [None] * NPADS
        self.save()
        self.status("Pads assignés au MPD218 (banque A).")

    def set_learn_mode(self, on):
        self.learn_mode = on
        self.learn_target = None
        if on:
            self.status("MIDI Learn : cliquez un pad (ou « Tout arrêter »), puis touchez le contrôleur.", 0)
        else:
            self.status("MIDI Learn terminé.")

    def set_learn_target(self, target):
        self.learn_target = target
        name = "Tout arrêter" if target == "stop" else f"le pad {pad_name(target)}"
        self.status(f"Touchez maintenant le bouton du contrôleur pour {name}…", 0)

    def on_midi(self, kind, ch, num, val):
        key = [kind, ch, num]
        if self.learn_mode:
            if self.learn_target is None:
                self.status(f"Reçu {midi_label(key)} — cliquez d'abord un pad.", 0)
                return
            for p in self.cfg["pads"]:
                if midi_match(p["midi"], kind, ch, num):
                    p["midi"] = None
            if midi_match(self.cfg["stop_all_midi"], kind, ch, num):
                self.cfg["stop_all_midi"] = None
            if self.learn_target == "stop":
                self.cfg["stop_all_midi"] = key
                self.status(f"{midi_label(key)} → Tout arrêter. Cliquez un autre pad ou quittez MIDI Learn.", 0)
            else:
                self.cfg["pads"][self.learn_target]["midi"] = key
                self.status(f"{midi_label(key)} → pad {pad_name(self.learn_target)}. "
                            "Cliquez un autre pad ou quittez MIDI Learn.", 0)
            self.learn_target = None
            self.led_state = [None] * NPADS
            self.save()
            return
        if midi_match(self.cfg["stop_all_midi"], kind, ch, num):
            self.engine.stop_all()
            return
        for i, p in enumerate(self.cfg["pads"]):
            if midi_match(p["midi"], kind, ch, num):
                self.trigger(i)

    # ---------- menu de pad
    def load_dialog(self, i):
        start = str(APP_DIR / "sons") if (APP_DIR / "sons").is_dir() else str(APP_DIR)
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"Son pour le pad {pad_name(i)}", start,
            "Sons (*.wav *.mp3 *.flac *.ogg *.aif *.aiff)")
        if path:
            self.set_pad_file(i, path)
            self.save()

    def pad_menu(self, i, pos):
        pad = self.cfg["pads"][i]
        m = QtWidgets.QMenu(self)
        a_load = m.addAction("Charger un son…")
        a_name = m.addAction("Renommer…")
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
            self.cfg["pads"][i] = {**default_pad(), "midi": pad["midi"]}
            self.pads[i].cfg = self.cfg["pads"][i]
            self.load_pad(i)
        self.pads[i].update()
        self.save()

    # ---------- banques
    def export_bank(self):
        default = str(APP_DIR / f"{self.cfg['title']}{BANK_EXT}")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Exporter la banque", default, f"Banque cartoucheur (*{BANK_EXT})")
        if not path:
            return
        if not path.lower().endswith(BANK_EXT):
            path += BANK_EXT
        pads, missing, count = [], [], 0
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
                for i, pad in enumerate(self.cfg["pads"]):
                    entry = dict(pad)
                    if pad["file"]:
                        src = resolve_path(pad["file"])
                        if src.is_file():
                            arc = f"sons/{pad_name(i)} - {src.name}"
                            z.write(src, arc)
                            entry["file"] = arc
                            entry["label"] = pad["label"] or src.stem
                            count += 1
                        else:
                            missing.append(pad_name(i))
                            entry["file"] = None
                    pads.append(entry)
                manifest = {"format": "cartoucheur-banque", "version": 2,
                            "title": self.cfg["title"],
                            "stop_all_midi": self.cfg["stop_all_midi"], "pads": pads}
                z.writestr("banque.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Export", f"Export impossible :\n{e}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        msg = f"Banque exportée : {count} son(s) dans {Path(path).name}."
        if missing:
            msg += f" Introuvables : {', '.join(missing)}."
        self.status(msg, 10000)

    def import_bank(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Importer une banque", str(APP_DIR), f"Banque cartoucheur (*{BANK_EXT} *.zip)")
        if not path:
            return
        try:
            z = zipfile.ZipFile(path)
            manifest = json.loads(z.read("banque.json").decode("utf-8"))
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Import", f"Ce fichier n'est pas une banque valide :\n{e}")
            return
        name = manifest.get("title") or Path(path).stem
        if QtWidgets.QMessageBox.question(
                self, "Importer une banque",
                f"Remplacer les 16 pads actuels par la banque « {name} » ?") \
                != QtWidgets.QMessageBox.Yes:
            return
        safe = re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "banque"
        dest = APP_DIR / "sons" / safe
        k = 2
        while dest.exists():
            dest = APP_DIR / "sons" / f"{safe} ({k})"
            k += 1
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            dest.mkdir(parents=True)
            new_pads = []
            src_pads = manifest.get("pads") or []
            for i in range(NPADS):
                pad = {**default_pad(), **(src_pads[i] if i < len(src_pads) else {})}
                if pad["file"]:
                    target = dest / Path(pad["file"]).name  # nom seul : pas d'écriture hors du dossier
                    with z.open(pad["file"]) as fsrc, open(target, "wb") as fdst:
                        fdst.write(fsrc.read())
                    pad["file"] = store_path(target)
                new_pads.append(pad)
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Import", f"Import impossible :\n{e}")
            return
        finally:
            z.close()
        self.engine.stop_all()
        for i in range(NPADS):
            self.midi.send_led(self.cfg["pads"][i]["midi"], False)
        self.cfg["pads"] = new_pads
        self.cfg["stop_all_midi"] = manifest.get("stop_all_midi")
        if not any(p["midi"] for p in new_pads) and not self.cfg["stop_all_midi"]:
            apply_mpd218(self.cfg)
        for i, w in enumerate(self.pads):
            w.cfg = self.cfg["pads"][i]
        self.led_state = [None] * NPADS
        self.reload_all_pads()
        QtWidgets.QApplication.restoreOverrideCursor()
        self.save()
        self.status(f"Banque « {name} » importée dans sons/{dest.name}.", 10000)

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
