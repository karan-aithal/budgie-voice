# Myna — Voice Typing Assistant

> Named after the myna bird — listens intently, reproduces speech as clean text.

100 % local voice-to-text for Ubuntu 24.04 LTS (X11).  Speak → text appears in any focused window.  No audio ever leaves the machine.

**Stack:** Python 3.12 · Faster-Whisper · Silero VAD · ECAPA-TDNN · PyQt6 · xdotool/IBus · systemd

---

## Quick Start (3 steps)

```bash
# 1. Clone + install
git clone https://github.com/you/myna-voice && cd myna-voice
python3.12 -m venv .venv && source .venv/bin/activate
bash scripts/install_deps.sh

# 2. Enroll your voice (optional — enables speaker verification)
python3.12 -m myna.speaker.enroller

# 3. Run
python3.12 -m myna.main
# or as a systemd service that starts on login:
bash scripts/install_service.sh
```

Press **Ctrl+Space** to start dictating.  Press again to stop → text appears at cursor.

   
---

## Configuration

Config file: `~/.config/myna/config.yaml`
Copy the example: `cp config.yaml.example ~/.config/myna/config.yaml`int8

### Key settings

| Key | Default | Description |
|---|---|---|
| `activation.hotkey` | `ctrl+space` | Global hotkey (pynput format) |
| `activation.mode` | `toggle` | `toggle` \| `push_to_hold` |
| `inference.model` | `small` | `tiny` \| `small` \| `medium` \| `large-v3` |
| `inference.backend` | `faster_whisper` | STT backend name |
| `inference.device` | `auto` | `auto` \| `cpu` \| `cuda` |
| `inference.language` | `en` | BCP-47 language code |
| `inference.confidence_threshold` | `0.55` | Discard segments below this |
| `inference.beam_size` | `5` | Whisper beam size (1 = fastest greedy) |
| `injection.backend` | `auto` | `auto` \| `ibus` \| `xdotool` |
| `speaker.enabled` | `true` | Speaker verification on/off |
| `speaker.threshold` | `0.75` | Cosine similarity cutoff |
| `vad.aggressiveness` | `2` | Silero VAD 0 (permissive) – 3 (strict) |
| `vad.speech_pad_ms` | `300` | Trailing silence before segment ends |
| `audio.denoise` | `true` | noisereduce suppression |

Full reference: [`config.yaml.example`](config.yaml.example)

### Tray icon states

| Icon | State | Meaning |
|---|---|---|
| 🔵 | IDLE | Running, not listening |
| 🟡 | STARTING | Session initialising |
| 🟢 | RECORDING | Actively capturing audio |
| 🟣 | TRANSCRIBING / FINALIZING | Inference in progress |
| 🟢 | COMPLETED | Text injected (2 s flash) |
| 🟠 | LOW_CONFIDENCE | Segment discarded (reverts after 3 s) |
| 🔴 | ERROR | Fatal error — see log |

### Punctuation voice commands

| Say | Inserts |
|---|---|
| `"comma"` | `,` |
| `"period"` | `.` (phrase-boundary only) |
| `"question mark"` | `?` |
| `"exclamation mark"` | `!` |
| `"new line"` | `\n` |
| `"new paragraph"` | `\n\n` |
| `"open quote"` / `"close quote"` | `"` / `"` |
| `"colon"` / `"semicolon"` | `:` / `;` |
| `"dash"` | `—` |

---

## Adding a New STT Backend

1. **Create** `myna/inference/backends/mybackend.py`:

```python
from myna.inference.backends.base import InferenceBackend, BackendCapabilities
from myna.inference.protocol import TranscriptSegment

class MyBackend(InferenceBackend):
    async def load(self, model: str, device: str, compute_type: str) -> None: ...
    async def transcribe_stream(self, audio_frames, language) -> ...: ...
    async def unload(self) -> None: ...
    async def capabilities(self) -> BackendCapabilities: ...
    @property
    def is_ready(self) -> bool: ...
```

2. **Register** in `myna/inference/backends/registry.py`:

```python
from myna.inference.backends.mybackend import MyBackend
_REGISTRY["mybackend"] = MyBackend
```

3. **Set** in config: `inference.backend: "mybackend"`

---

## Adding a New Injection Backend

1. **Create** `myna/injection/mybackend.py`:

```python
from myna.injection.base import TextOutputBackend

class MyInjectionBackend(TextOutputBackend):
    async def start_session(self, target_window_id: int | None) -> None: ...
    async def commit_text(self, text: str) -> None: ...
    async def cancel_session(self) -> None: ...
    async def end_session(self) -> None: ...
    def is_secure_field(self, window_id: int | None) -> bool: ...
    @property
    def backend_name(self) -> str: return "mybackend"
```

2. **Register** in `myna/injection/selector.py` — add a `case "mybackend":` branch.

3. **Set** in config: `injection.backend: "mybackend"`

---

## Systemd Commands

```bash
# Install and auto-start on login
bash scripts/install_service.sh

# Status
systemctl --user status myna

# Live logs
journalctl --user -u myna -f
# — or —
tail -f ~/.local/share/myna/logs/myna.log

# Stop / restart / disable
systemctl --user stop    myna
systemctl --user restart myna
systemctl --user disable myna

# Full uninstall (preserves config + logs)
bash scripts/uninstall_service.sh
```

Service unit installed at `~/.config/systemd/user/myna.service`:

```ini
[Unit]
Description=Myna Voice Typing Assistant
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3.12 -m myna.main
WorkingDirectory=%h/.local/lib/myna
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus
Environment=IBUS_ADDRESS_FILE=%h/.config/ibus/bus

[Install]
WantedBy=graphical-session.target
```

> **Venv:** change `ExecStart` to `/path/to/.venv/bin/python3.12 -m myna.main`

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `MynaMicError` | `sudo apt install libportaudio2`; check `audio.device` in config |
| `xdotool not found` | `sudo apt install xdotool` |
| Text injected into wrong window | Ensure focus is in target window **before** pressing hotkey |
| No text injected after hotkey | Lower `inference.confidence_threshold`; check `journalctl --user -u myna` |
| Speaker verify always fails | Re-enroll: `python3.12 -m myna.speaker.enroller` |
| Tray icon missing | `sudo apt install python3-pyqt6`; verify `DISPLAY=:0` in service env |
| DBus `GetState` not found | `sudo apt install python3-dbus python3-gi` |
| Whisper very slow | `inference.device: cuda` (GPU) or `inference.model: tiny` (fastest CPU) |
| `ModuleNotFoundError` | Activate venv; run `pip install -r requirements.txt` |
| Session starts but no transcription | Check VAD aggressiveness; try `vad.aggressiveness: 1` |

### GPU setup (CUDA 12)

```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
```

Then set `inference.device: cuda` and `inference.compute_type: float16`.

### Tests

```bash
pytest tests/ -v
```

---

## Project Structure

```
myna/
├── main.py                        # QApplication + qasync entry point
├── orchestrator/
│   ├── session.py                 # SessionState enum + DictationSession
│   ├── dictation_service.py       # Policy-only session coordinator
│   └── hotkey.py                  # pynput GlobalHotKey listener
├── audio/
│   ├── capture.py                 # sounddevice → asyncio.Queue
│   ├── denoiser.py                # noisereduce wrapper
│   ├── vad.py                     # Silero VAD stream
│   └── adapter.py                 # capture → denoise → VAD → AudioFrame
├── inference/
│   ├── protocol.py                # AudioFrame, TranscriptSegment, …
│   ├── server.py                  # FastAPI + WebSocket inference server
│   ├── client.py                  # Async WebSocket + HTTP client
│   └── backends/
│       ├── base.py                # InferenceBackend ABC (versioned)
│       ├── faster_whisper.py      # FasterWhisperBackend
│       └── registry.py            # name → class map
├── postprocessing/
│   ├── normalizer.py              # "twenty two" → "22"
│   ├── punctuation.py             # "period" → "."
│   ├── capitalizer.py             # Sentence-start capitalisation
│   ├── language_cleanup.py        # "gonna" → "going to"
│   └── pipeline.py                # Chains all processors
├── injection/
│   ├── base.py                    # TextOutputBackend ABC (versioned)
│   ├── xdotool.py                 # X11 fallback
│   ├── ibus.py                    # IBus-aware injection
│   ├── secure.py                  # SecureFieldDetector (xprop)
│   ├── selector.py                # Auto-select ibus | xdotool
│   ├── xdg.py                     # Stub (future)
│   └── wayland.py                 # Stub (future)
├── speaker/
│   ├── verifier.py                # ECAPA-TDNN cosine-sim verification
│   ├── enroller.py                # PyQt6 QWizard enrollment
│   └── _workers.py                # RecordWorker + EnrollWorker (QThread)
├── indicator/
│   └── tray.py                    # QSystemTrayIcon + TrayState
├── settings_ui/
│   └── window.py                  # QDialog settings UI
├── dbus/
│   └── service.py                 # com.canonical.Myna DBus service
├── config/
│   └── settings.py                # YAML → Settings dataclass
└── utils/
    ├── exceptions.py              # MynaMicError, MynaInferenceError, …
    └── logging_setup.py           # Rotating file + stderr
tests/
├── test_audio_adapter.py
├── test_postprocessing.py
├── test_injection.py
├── test_session.py
└── test_inference_protocol.py
scripts/
├── install_deps.sh
├── install_service.sh
└── uninstall_service.sh
config.yaml.example
requirements.txt
```

---

## File locations

| Path | Content |
|---|---|
| `~/.config/myna/config.yaml` | User configuration |
| `~/.config/myna/voiceprint.pt` | Enrolled speaker embedding |
| `~/.local/share/myna/logs/myna.log` | Rotating log (5 MB × 3) |
| `~/.cache/myna/models/` | Whisper model weights |
| `~/.cache/myna/speechbrain/` | ECAPA-TDNN weights |

