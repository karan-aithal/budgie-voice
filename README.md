# myna-voice — Linux Desktop Voice Typing Assistant

100 % offline voice-to-text that types into any focused window (browser, VS Code, terminal, LibreOffice, …).
Python 3.12 · Ubuntu 22.04 LTS (X11) · cross-platform headless mode.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Installation](#installation)
3. [First-Run Enrollment](#first-run-enrollment)
4. [Hotkey Configuration](#hotkey-configuration)
5. [Configuration Reference](#configuration-reference)
6. [Model File Locations](#model-file-locations)
7. [Systemd Service](#systemd-service)
8. [Log Location](#log-location)
9. [Project Structure](#project-structure)
10. [Roadmap](#roadmap)

---

## Quick Start

```bash
git clone <repo> myna-voice && cd myna-voice
python3.12 -m venv .venv && source .venv/bin/activate
bash scripts/install_deps.sh          # installs apt + pip packages
python -m assistant.speaker.enroller  # enroll your voice (one-time)
python assistant/main.py              # run the assistant
```

Press the configured hotkey (default **Ctrl+Space**) to start listening.  
Press it again to stop. Ctrl+C or tray → Quit to exit.

---

## Installation

### System packages (Ubuntu 22.04)

```bash
sudo apt install -y \
    libportaudio2 portaudio19-dev \
    python3-dev \
    xdotool \
    python3-pyqt6 \
    libnotify-bin \
    rsync
```

### Python packages

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**PyTorch — choose one:**

```bash
# CPU only
pip install torch==2.4.0 torchaudio==2.4.0 \
    --index-url https://download.pytorch.org/whl/cpu

# CUDA 12 (GPU)
pip install torch==2.4.0 torchaudio==2.4.0 \
    --index-url https://download.pytorch.org/whl/cu121
```

### Pre-cache models (optional but recommended)

Runs on first use anyway; doing it now avoids a wait when the assistant starts.

```bash
bash scripts/install_deps.sh   # includes Silero VAD pre-cache step
# Whisper model downloads automatically on first transcription (~250 MB for "small")
```

---

## First-Run Enrollment

Before speaker verification works you must record a voiceprint.  
If you skip this step the assistant runs in **open-mic mode** (everyone's voice triggers typing).

```bash
python -m assistant.speaker.enroller
```

The wizard walks you through recording **5 short clips** (~3.5 s each).  
Your voiceprint is saved to `~/.config/voice-assistant/voiceprint.pt`.

To re-enroll at any time: run the wizard again, or click **Enroll Voice** in the tray menu.

---

## Hotkey Configuration

Default toggle hotkey: **Ctrl+Space**

Change it in `~/.config/voice-assistant/config.yaml`:

```yaml
hotkey:
  toggle_listen: "ctrl+shift+v"   # any combo parseable by pynput
```

**Supported key names** (pynput format — no angle brackets needed in config):

| Config value | Key |
|---|---|
| `ctrl` | Left/Right Ctrl |
| `shift` | Left/Right Shift |
| `alt` | Left Alt |
| `space` | Space bar |
| `f1`…`f12` | Function keys |
| `a`…`z` | Letter keys |

Examples: `"ctrl+space"`, `"ctrl+shift+f"`, `"alt+f4"`.

---

## Configuration Reference

Config file: `~/.config/voice-assistant/config.yaml`  
Auto-created from `config.yaml.example` on first run.

```yaml
hotkey:
  toggle_listen: "ctrl+space"

audio:
  device: null            # null = OS default mic; or device name/index string
  sample_rate: 16000
  chunk_duration_ms: 30

vad:
  enabled: true
  aggressiveness: 2       # 0 (least sensitive) – 3 (most sensitive)
  speech_pad_ms: 300

stt:
  model: "small"          # tiny | small | medium | large-v3
  device: "auto"          # auto | cpu | cuda
  compute_type: "int8"
  language: "en"
  confidence_threshold: 0.55
  beam_size: 5

speaker:
  enabled: true
  voiceprint_path: "~/.config/voice-assistant/voiceprint.pt"
  threshold: 0.75         # cosine similarity threshold 0–1
  min_samples: 5

injection:
  delay_ms: 12
  clear_modifiers: true

tray:
  enabled: true

logging:
  level: "INFO"           # DEBUG | INFO | WARNING | ERROR
  max_bytes: 5242880      # 5 MB
  backup_count: 3

notifications:
  low_confidence_message: "⚠️ I couldn't hear you clearly — could you repeat that?"
  tool: "notify-send"
```

### Tray icon states

| Icon | State | Meaning |
|---|---|---|
| 🔵 | IDLE | Running, not listening |
| 🟢 | LISTENING | Actively transcribing |
| 🟠 | LOW_CONFIDENCE | Last segment discarded; restores after 3 s |
| 🔴 | ERROR | Fatal error |

### Punctuation commands

Speak these words to insert punctuation:

| Say | Inserts |
|---|---|
| `"comma"` | `,` |
| `"period"` | `.` (phrase-boundary aware) |
| `"question mark"` | `?` |
| `"exclamation mark"` | `!` |
| `"new line"` | newline |
| `"new paragraph"` | blank line |
| `"open quote"` | `"` |
| `"close quote"` | `"` |

---

## Model File Locations

All models download automatically on first use and are cached locally.

| Model | Default cache path | Size |
|---|---|---|
| Whisper `small` | `~/.cache/huggingface/hub/` | ~250 MB |
| Whisper `medium` | `~/.cache/huggingface/hub/` | ~770 MB |
| Silero VAD | `~/.cache/torch/hub/snakers4_silero-vad_master/` | ~2 MB |
| ECAPA-TDNN | `~/.cache/speechbrain/spkrec-ecapa-voxceleb/` | ~80 MB |

After installation the assistant is **100 % offline**.

---

## Systemd Service

### Install (auto-start on login)

```bash
bash scripts/install_service.sh
```

This copies the project to `~/.local/lib/voice-assistant/` and installs  
`~/.config/systemd/user/voice-assistant.service`.

### Service management

```bash
# Status
systemctl --user status voice-assistant

# Stop
systemctl --user stop voice-assistant

# Restart
systemctl --user restart voice-assistant

# Live logs (journal)
journalctl --user -u voice-assistant -f

# Disable auto-start (keep installed)
systemctl --user disable voice-assistant

# Full uninstall
bash scripts/uninstall_service.sh
```

### Service unit file (for reference)

```ini
[Unit]
Description=Voice Typing Assistant
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3.12 -m assistant.main
WorkingDirectory=%h/.local/lib/voice-assistant
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus

[Install]
WantedBy=graphical-session.target
```

> **Note:** If you use a virtual environment, change `ExecStart` to  
> `/path/to/.venv/bin/python3.12 -m assistant.main`.

---

## Log Location

| File | Purpose |
|---|---|
| `~/.local/share/voice-assistant/logs/app.log` | Main rotating log (5 MB × 3) |

```bash
# Tail the log
tail -f ~/.local/share/voice-assistant/logs/app.log

# Or via journalctl (when running as a service)
journalctl --user -u voice-assistant -f
```

Change log level in config:
```yaml
logging:
  level: "DEBUG"   # DEBUG | INFO | WARNING | ERROR
```

---

## Tests

```bash
pytest          # all tests
pytest -v       # verbose
pytest tests/test_injector.py   # single file
```

---

## Project Structure

```
assistant/
├── main.py                  # Entry point (qasync + pipeline orchestration)
├── audio/
│   ├── capture.py           # sounddevice → asyncio.Queue
│   └── vad.py               # Silero VAD streaming async generator
├── speech/
│   └── recognizer.py        # Faster-Whisper → TranscriptResult
├── speaker/
│   ├── verifier.py          # ECAPA-TDNN cosine-similarity verification
│   ├── enroller.py          # PyQt6 enrollment wizard
│   └── _workers.py          # RecordThread + EnrollThread (QThread)
├── injector/
│   └── xdotool.py           # xdotool injection + punctuation substitution
├── config/
│   └── settings.py          # YAML → Settings dataclass
├── tray/
│   └── icon.py              # QSystemTrayIcon + TrayState enum
└── utils/
    ├── exceptions.py        # Custom exception hierarchy
    └── logging_setup.py     # Rotating file + stderr handlers
tests/
scripts/
├── install_deps.sh
├── install_service.sh
└── uninstall_service.sh
config.yaml.example
requirements.txt
```

---

## Roadmap

| Phase | Feature | Status |
|---|---|---|
| 1 | Mic + VAD + Whisper + console output | ✓ |
| 2 | xdotool text injection + punctuation commands | ✓ |
| 3 | Speaker verification (ECAPA-TDNN) + enrollment wizard | ✓ |
| 4 | Hotkey toggle + system tray + low-confidence notifications | ✓ |
| 5 | Systemd user service + packaging + full docs | ✓ |
| 6+ | LLM reasoning, tool use, multi-language, Wayland support | planned |

---

## Phase 1 — Mic Capture + VAD + Whisper STT (current)

Pipeline: `microphone → Silero VAD → Faster-Whisper → stdout`

### Quick start

```bash
# 1. Clone + enter project
git clone <repo> myna-voice && cd myna-voice

# 2. Create venv (recommended)
python3.12 -m venv .venv && source .venv/bin/activate

# 3. Install deps (Ubuntu)
bash scripts/install_deps.sh

# 4. Run
python assistant/main.py
# or: python -m assistant.main
```

### Expected output

```
2026-07-07 14:32:01 [INFO    ] assistant.main: Voice Assistant Phase 1 — starting
2026-07-07 14:32:08 [INFO    ] assistant.speech.recognizer: Whisper model ready
2026-07-07 14:32:08 [INFO    ] assistant.main: Listening — speak into the microphone.  Ctrl+C to quit.
[14:32:12] [conf=0.84] Hello world, this is a test.
[14:32:16] [conf=0.79] The quick brown fox jumps over the lazy dog.
```

Ctrl+C exits cleanly.

---

## Configuration

Default path: `~/.config/voice-assistant/config.yaml`
Auto-copied from `config.yaml.example` on first run.

| Key | Default | Notes |
|---|---|---|
| `stt.model` | `small` | `tiny` \| `small` \| `medium` \| `large-v3` |
| `stt.device` | `auto` | Auto-selects CUDA if available |
| `stt.confidence_threshold` | `0.55` | Below this → segment discarded |
| `vad.aggressiveness` | `2` | 0 (least) – 3 (most) |
| `audio.device` | `null` | `null` = OS default mic |

---

## Model locations (auto-downloaded)

| Model | Path |
|---|---|
| Whisper | `~/.cache/huggingface/hub/` |
| Silero VAD | `~/.cache/torch/hub/snakers4_silero-vad_master/` |

~250 MB for `small`. 100% offline after first run.

---

## Logs

`~/.local/share/voice-assistant/logs/app.log` — rotates at 5 MB, 3 backups.

```bash
tail -f ~/.local/share/voice-assistant/logs/app.log
```

---

## Tests

```bash
pytest
```

---

## Project structure

```
assistant/
├── main.py                  # Entry point
├── audio/
│   ├── capture.py           # sounddevice → asyncio.Queue
│   └── vad.py               # Silero VAD streaming
├── speech/
│   └── recognizer.py        # Faster-Whisper → TranscriptResult
├── config/
│   └── settings.py          # YAML → Settings dataclass
├── utils/
│   ├── exceptions.py        # Custom exception hierarchy
│   └── logging_setup.py     # Rotating file + stderr
├── speaker/                 # Phase 3 — ECAPA-TDNN
├── injector/                # Phase 2 — xdotool
└── tray/                    # Phase 4 — QSystemTrayIcon
tests/
scripts/
config.yaml.example
requirements.txt
```

---

## Roadmap

| Phase | Feature | Status |
|---|---|---|
| 1 | Mic + VAD + Whisper + console output | ✓ |
| 2 | xdotool text injection + punctuation commands | ✓ |
| 3 | Speaker verification (ECAPA-TDNN) + enrollment wizard | ✓ |
| 4 | Hotkey toggle + system tray + low-confidence notifications | ✓ |
| 5 | Systemd user service + packaging + full docs | ✓ |
| 6+ | LLM reasoning, tool use, multi-language, Wayland support | planned |
