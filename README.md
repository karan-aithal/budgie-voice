# Budgie — Voice Typing Assistant

> Named after the budgie bird — listens intently, reproduces speech as clean text.

100 % local voice-to-text for Ubuntu 24.04 LTS (X11 and native Wayland/GNOME).  Speak → text appears in any focused window.  No audio ever leaves the machine.

**Stack:** Python 3.12 · Faster-Whisper · Silero VAD · ECAPA-TDNN · PyQt6 · ydotool/xdotool · systemd

---

## Quick Start (3 steps)

```bash
# 1. Clone + install
git clone https://github.com/you/budgie-voice && cd budgie-voice
python3.12 -m venv .venv && source .venv/bin/activate
bash scripts/install_deps.sh

# 2. Enroll your voice (optional — enables speaker verification)
python3.12 -m budgie_voice.speaker.enroller

# 3. Run
python3.12 -m budgie_voice.main
# or as a systemd service that starts on login:
bash scripts/install_service.sh
```
✓ Budgie installed and running.
  Status : systemctl --user status budgie-voice
  Logs   : journalctl --user -u budgie-voice -f
  Enroll : python3.12 -m budgie_voice.speaker.enroller
  
Press **Ctrl+Space** to start dictating.  Press again to stop → text appears at cursor.

---

## Configuration

Config file: `~/.config/budgie-voice/config.yaml`
Copy the example: `cp config.yaml.example ~/.config/budgie-voice/config.yaml`int8

### Key settings

| Key | Default | Description |
|---|---|---|
| `activation.hotkey` | `ctrl+space` | Global hotkey (pynput format) — used when `trigger` is `pynput` |
| `activation.mode` | `toggle` | `toggle` \| `push_to_hold` |
| `activation.trigger` | `auto` | Who owns the hotkey: `auto` \| `pynput` \| `dbus` — see below |
| `activation.debounce_ms` | `350` | Ignore a repeat from the same source within this window |
| `inference.model` | `small` | A model of the chosen backend (Settings lists them): Whisper `tiny`…`large-v3`, or `nemo-parakeet-*`. An unknown backend or model falls back to `faster_whisper`/`small` with a notification |
| `inference.backend` | `faster_whisper` | `faster_whisper` \| `parakeet` |
| `inference.device` | `auto` | `auto` \| `cpu` \| `cuda` |
| `inference.language` | `en` | BCP-47 language code |
| `inference.confidence_threshold` | `0.40` | Discard segments below this. Skipped for backends that do not measure confidence (Parakeet) |
| `inference.beam_size` | `3` | Whisper beam size (1 = fastest greedy) |
| `inference.finalize_timeout_s` | `15` | Max wait at stop for the last turn's transcription; on timeout nothing is typed |
| `inference.min_turn_s` | `15` | A pause closes a turn only once it holds this much audio, so short dictations are transcribed in one pass; longer ones stream turn by turn |
| `learning.dictionary_path` | `~/.config/budgie-voice/dictionary.txt` | Your names and jargon, one per line (`#` comments allowed), sent to the decoder as hotwords together with words you corrected in the preview. Whisper uses them; Parakeet ignores hotwords |
| `injection.backend` | `auto` | `auto` \| `ydotool` \| `xdotool` |
| `speaker.enabled` | `true` | Speaker verification on/off |
| `speaker.threshold` | `0.65` | Cosine similarity cutoff |
| `vad.aggressiveness` | `2` | Silero VAD 0 (permissive) – 3 (strict) |
| `vad.speech_pad_ms` | `300` | Trailing silence before segment ends |
| `audio.denoise` | `false` | noisereduce suppression. Off by default: on 30 ms chunks it can gate real speech to silence. Re-enroll your voiceprint after changing it |
| `audio.devices` | `[]` | Ordered mic preference (`bt:<card>`, `pa:<source>`, ALSA name); the first that works at session start wins. Empty = Auto: first real mic (not monitor, loopback, S/PDIF or Bluetooth), else the system default. A Bluetooth headset is only used when listed, since its mic drops playback to mSBC quality while you dictate |
| `audio.input_gain_pct` | `null` | BT mic gain 50–150 %, applied when a `bt:` device is used |
| `vad.pre_roll_ms` | `300` | Audio kept from before the first speech chunk so word onsets are not clipped |

Full reference: [`config.yaml.example`](config.yaml.example)

### Hotkey on Wayland vs X11

Exactly **one** mechanism may own the hotkey, chosen by `activation.trigger`:

| `trigger` | Who fires the toggle | Works on |
|---|---|---|
| `pynput` | in-process global listener, using `activation.hotkey` | X11 only |
| `dbus` | external caller (desktop shortcut) invoking `Toggle` | X11 **and** Wayland |
| `auto` *(default)* | `pynput` on X11, `dbus` on Wayland — from `XDG_SESSION_TYPE` | both |

This matters because both can be live at once: pynput sees the key press *and* a GNOME
shortcut fires `Toggle`, so a single press toggles twice — starting a session and immediately
stopping it (`AudioAdapter opened` → `Mic capture stopped`, `chunks=0`). Whichever source
doesn't own the hotkey is rejected and logged, so the duplicate is harmless no matter how far
apart the two land. `auto` needs no configuration on either session type.

`activation.hotkey` is delivered by pynput, which can only observe global keys on **X11** —
the Wayland protocol has no client-side global grab. On native Wayland (or with
`trigger: dbus` on X11) the hotkey comes from a GNOME custom shortcut calling `Toggle`:

```bash
P=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/
S=org.gnome.settings-daemon.plugins.media-keys.custom-keybinding
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['$P']"
gsettings set $S:$P name 'Budgie Toggle'
gsettings set $S:$P binding '<Primary>space'
gsettings set $S:$P command 'dbus-send --session --type=method_call --dest=io.gitlab.karanaithal841.BudgieVoice /io/gitlab/karanaithal841/BudgieVoice io.gitlab.karanaithal841.BudgieVoice.Toggle'
```

Leaving this shortcut installed on X11 is safe: with `trigger: auto` pynput owns the key there
and the shortcut's `Toggle` is rejected (logged as `Toggle from 'dbus' ignored`). Set
`trigger: dbus` to hand ownership to the shortcut instead — the one setting that behaves
identically on X11 and Wayland.

### Tray icon states

| Icon | State | Meaning |
|---|---|---|
| 🔵 | IDLE | Running, not listening |
| 🟡 | STARTING | Session initialising |
| 🟢 | RECORDING | Actively capturing audio |
| 🟣 | TRANSCRIBING / FINALIZING | Inference in progress |
| 🟢 | COMPLETED | Text injected (2 s flash) |
| 🟠 | LOW_CONFIDENCE | Segment discarded (reverts after 3 s) |
| 🔴 | ERROR | Session failed, nothing typed — see log (2 s flash, then idle) |

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

1. **Create** `budgie_voice/inference/backends/mybackend.py`:

```python
from budgie_voice.inference.backends.base import InferenceBackend, BackendCapabilities
from budgie_voice.inference.protocol import TranscriptSegment

class MyBackend(InferenceBackend):
    async def load(self, model: str, device: str, compute_type: str) -> None: ...
    async def transcribe_stream(self, audio_frames, language) -> ...: ...
    async def unload(self) -> None: ...
    async def capabilities(self) -> BackendCapabilities: ...
    @property
    def is_ready(self) -> bool: ...
```

2. **Register** in `budgie_voice/inference/backends/registry.py`:

```python
from budgie_voice.inference.backends.mybackend import MyBackend
_REGISTRY["mybackend"] = MyBackend
```

3. **Set** in config: `inference.backend: "mybackend"`

---

## Adding a New Injection Backend

1. **Create** `budgie_voice/injection/mybackend.py`:

```python
from budgie_voice.injection.base import TextOutputBackend

class MyInjectionBackend(TextOutputBackend):
    async def start_session(self, target_window_id: int | None) -> None: ...
    async def commit_text(self, text: str) -> None: ...
    async def cancel_session(self) -> None: ...
    async def end_session(self) -> None: ...
    def is_secure_field(self, window_id: int | None) -> bool: ...
    @property
    def backend_name(self) -> str: return "mybackend"
```

2. **Register** in `budgie_voice/injection/selector.py` — add a `case "mybackend":` branch.

3. **Set** in config: `injection.backend: "mybackend"`

---

## Systemd Commands

```bash
# Install and auto-start on login
bash scripts/install_service.sh

# Status
systemctl --user status budgie-voice

# Live logs
journalctl --user -u budgie-voice -f
# — or —
tail -f ~/.local/share/budgie-voice/logs/budgie-voice.log

# Stop / restart / disable
systemctl --user stop    budgie-voice
systemctl --user restart budgie-voice
systemctl --user disable budgie-voice

# Full uninstall (preserves config + logs)
bash scripts/uninstall_service.sh
```

Service unit installed at `~/.config/systemd/user/budgie-voice.service`, generated with this
checkout's own paths baked in — it runs in place via `.venv/bin/python3.12`, not a
copied install or the system interpreter:

```ini
[Unit]
Description=Budgie Voice Typing Assistant
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/path/to/budgie-voice/.venv/bin/python3.12 -m budgie_voice.main
WorkingDirectory=/path/to/budgie-voice
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus

[Install]
WantedBy=graphical-session.target
```

Moved the checkout? Re-run `bash scripts/install_service.sh` to regenerate the unit
with the new path — don't hand-edit the paths above.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `BudgieMicError` | `sudo apt install libportaudio2`; check `audio.devices` in config |
| `xdotool not found` | `sudo apt install xdotool` |
| No text injected on Wayland/GNOME | `sudo apt install ydotool`; add yourself to the `input` group (or add a udev rule for `/dev/uinput`), then re-login |
| Text injected into wrong window | Ensure focus is in target window **before** pressing hotkey. The window focused at session start is re-activated before typing |
| Nothing typed on X11 despite `Injecting:` in the log | Injection must not pass `xdotool --window` — that uses `XSendEvent`, which Electron/Firefox/GTK apps silently discard (see `xdotool(1)` SENDEVENT NOTES). Budgie activates the target window and types via XTEST instead |
| No text injected after hotkey | Lower `inference.confidence_threshold`; check `journalctl --user -u budgie-voice` |
| Session starts then stops instantly (`chunks=0`/`chunks=1` in the log) | Two triggers are firing for one press. Check `grep 'Toggle from' ~/.local/share/budgie-voice/logs/budgie-voice.log` — you should see exactly one `accepted` per press. Set `activation.trigger` explicitly to `pynput` or `dbus` |
| Hotkey does nothing on Wayland | `trigger` must resolve to `dbus` there; add the GNOME custom shortcut above |
| Speaker verify always fails | Re-enroll: `python3.12 -m budgie_voice.speaker.enroller` |
| Tray icon missing | `sudo apt install python3-pyqt6`; the service needs the session's `DISPLAY`/`WAYLAND_DISPLAY`: `systemctl --user import-environment DISPLAY XAUTHORITY WAYLAND_DISPLAY XDG_SESSION_TYPE`, then restart the service |
| Chrome/calls use the BT headset mic after dictation, or the headset stays in mSBC | Budgie makes the BT mic the system default while dictating and does not restore it; the mic source vanishes when the headset returns to A2DP, which only happens once no app records from it. Reset: `pactl set-default-source <your mic>`, then pick it once in Chrome's mic selector |
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
budgie_voice/
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
│   ├── xdotool.py                 # X11: windowactivate + XTEST (no-op on native Wayland)
│   ├── ydotool_backend.py         # /dev/uinput injection — Wayland-safe
│   ├── secure.py                  # SecureFieldDetector (xprop)
│   └── selector.py                # Auto-select ydotool (Wayland) | xdotool (X11)
├── speaker/
│   ├── verifier.py                # ECAPA-TDNN cosine-sim verification
│   ├── enroller.py                # PyQt6 QWizard enrollment
│   └── _workers.py                # RecordWorker + EnrollWorker (QThread)
├── indicator/
│   └── tray.py                    # QSystemTrayIcon + TrayState
├── settings_ui/
│   └── window.py                  # QDialog settings UI
├── dbus/
│   └── service.py                 # io.gitlab.karanaithal841.BudgieVoice DBus service
├── config/
│   └── settings.py                # YAML → Settings dataclass
└── utils/
    ├── exceptions.py              # BudgieMicError, BudgieInferenceError, …
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
| `~/.config/budgie-voice/config.yaml` | User configuration |
| `~/.config/budgie-voice/voiceprint.pt` | Enrolled speaker embedding |
| `~/.local/share/budgie-voice/logs/budgie-voice.log` | Rotating log (5 MB × 3) |
| `~/.local/share/budgie-voice/models/` | Speech models (Whisper weights, `parakeet/<model>-<precision>/`). Downloaded once, with a notification; loading is offline afterwards |
| `~/.local/share/budgie-voice/speechbrain/` | ECAPA-TDNN speaker model |
| `~/.local/share/budgie-voice/torch_hub/` | Silero VAD model |
