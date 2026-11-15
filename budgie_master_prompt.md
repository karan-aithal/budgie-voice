# Budgie — Master Build Prompt (All 5 Phases)
> Named after the budgie bird — listens intently, reproduces speech as clean text.
> Paste this in full into a new Claude conversation.

---

## Communication Style — Caveman Mode ON

- No filler (`the`, `a`, `an`, `is`, `are` — drop where meaning survives)
- No politeness (`sure`, `happy to help` → never)
- No repetition — once only
- Keywords + arrows + symbols → `→`, `=`, `vs`, `+`, `&`, `✓`, `✗`
- Compress aggressively. Smart user assumed.
- Long explanation only if asked

**Bad:** "Sure! I've gone ahead and created the audio capture module."
**Good:** "`audio/capture.py` done. sounddevice → 16kHz mono → asyncio.Queue."

---

## Architecture (from diagrams — implement exactly this)

### Three Layers

```
┌─────────────────────────────────────────────────────────┐
│  INFERENCE BACKEND                                       │
│  STT Server (OpenAI-compatible Transcription API)        │
│  Models: Faster-Whisper / Parakeet / NemoTron / Qwen3   │
│  Runtimes: GPU / CPU / NPU                              │
│  ← Control Channel (config, capabilities negotiation)   │
│  ↕ Data Channel (raw audio frames UP, text segments DOWN)│
└─────────────────────────────────────────────────────────┘
              ↕ Control    ↕ Data (pcm16/float32)
┌─────────────────────────────────────────────────────────┐
│  SPEECH ORCHESTRATOR                                     │
│  ┌─────────────────┐  ┌──────────────────────────────┐  │
│  │ Dictation        │  │ Audio Adapter                │  │
│  │ Service          │  │ denoise → vol norm → VAD     │  │
│  │ (policy only,    │  │ → pcm16/float32 frames       │  │
│  │  model-agnostic) │  │ ← Audio Server               │  │
│  │ • hotkey         │  │   (Pipewire/PulseAudio/DSP)  │  │
│  │ • session mgmt   │  └──────────────────────────────┘  │
│  │ • target lock    │                                     │
│  └─────────────────┘                                     │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ Text Finalization (Post-Processing)                  │ │
│  │ normalize → capitalize → punctuation → lang cleanup  │ │
│  │ (future: local LLM)                                  │ │
│  └─────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ Text Injection Layer (abstract)                      │ │
│  │ IBus (primary) | xdg | xdotool (X11 fallback)       │ │
│  │ future: Wayland input-method protocol                │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
              ↕ GRPC / DBus / Socket
┌─────────────────────────────────────────────────────────┐
│  USER INTERACTION                                        │
│  Settings UI          Wayland Compositor   Activity      │
│  • language           → Wayland text input  Indicator    │
│  • model size         → Text rendering                   │
│  • hotkey             widget                             │
│  • mic source                                            │
│  • install models                                        │
└─────────────────────────────────────────────────────────┘
```

### Key Design Rules (from diagrams — non-negotiable)

1. **Dictation Service is policy-only, model-agnostic.** It coordinates; it does NOT process audio or run inference.
2. **Audio Adapter owns audio processing:** device open → denoise → volume normalization → VAD → pcm16/float32 framing.
3. **Inference backend owns segmentation.** When a text segment is committed is decided by the model, not the orchestrator.
4. **Two channels to inference:**
   - **Control Channel** — capabilities query, model/language config, backend negotiation
   - **Data Channel** — bidirectional stream: audio frames UP, committed text segments DOWN
5. **Text Injection is abstract** — IBus primary; xdotool X11 fallback; xdg and Wayland as future backends.
6. **Segmentation note from diagram:** *"The inference snap owns recognition and segmentation. Segmentation decisions remain model-specific."*
7. **API and interfaces must be versioned.** Every backend interface carries a version.

---

## Fixed Decisions

| Concern | Decision |
|---|---|
| OS / Display | Ubuntu 24.04 LTS, X11 first; Wayland-abstracted from day one |
| Python | 3.12 |
| Inference | Local `InferenceServer` (FastAPI + WebSocket); OpenAI-compatible `/v1/audio/transcriptions` |
| Default backend | Faster-Whisper `small`/`medium`, auto GPU→CPU |
| Other backends | Parakeet, NemoTron, Qwen3-ASR — same interface, swap via YAML |
| Audio format | pcm16 (int16) primary; float32 accepted |
| Audio stack | sounddevice → Pipewire/PulseAudio via ALSA |
| Activation | Toggle hotkey (ON/OFF); push-to-hold mode also supported; both configurable in YAML |
| VAD | Silero VAD inside Audio Adapter |
| Speaker verification | SpeechBrain ECAPA-TDNN; PyQt6 enrollment wizard |
| Injection primary | IBus (`python-ibus`) |
| Injection fallback | `xdotool` (X11 only) |
| Injection future | xdg text input, Wayland `input-method-unstable-v2` |
| Config | `~/.config/budgie-voice/config.yaml` |
| Logging | Rotating `~/.local/share/budgie-voice/logs/budgie-voice.log` + stderr; **never log raw audio or full transcription at INFO+** |
| Low-confidence | Indicator → WARN state + `notify-send` → *"⚠️ Couldn't hear you — repeat?"* → discard |
| IPC | DBus between Settings UI ↔ Dictation Service (well-known name: `io.gitlab.karanaithal841.BudgieVoice`) |
| Audio persistence | **Zero disk writes of audio.** Bounded in-memory rolling buffer; discard on session end. |
| Packaging | Systemd user service |

---

## Session Lifecycle (Dictation Service owns this)

```
Idle → Starting → Recording → Transcribing → Finalizing → Completed → Idle

Any active state → Cancelled → Idle   (hotkey release / explicit cancel)
Any state        → Error → Idle       (mic lost, model crash, focus lost)
```

| State | Entry | Exit |
|---|---|---|
| `IDLE` | — | — |
| `STARTING` | Init Audio Adapter; connect inference client; lock target window | — |
| `RECORDING` | Open mic stream; start VAD; begin streaming frames to inference | — |
| `TRANSCRIBING` | Inference active; partial hypotheses internal only | — |
| `FINALIZING` | Stop mic; flush remaining frames; await final segments | Discard audio buffer |
| `COMPLETED` | Post-process → inject committed text | → IDLE |
| `CANCELLED` | Stop mic; discard uncommitted text; notify | → IDLE |
| `ERROR` | Log; notify user; release mic | → IDLE |

---

## Data Flow (complete)

```
[hotkey press]
      │
      ▼
DictationService.start_session()
  ├── lock target_window_id (captured NOW, never changed)
  ├── check secure_field → if yes: CANCEL + notify → IDLE
  └── → STARTING

AudioAdapter.open()
  ├── sounddevice.InputStream (16kHz, mono, pcm16)
  ├── DenoiseFilter (optional, RNNoise or noisereduce)
  ├── VolumeNormalizer
  └── SileroVAD
        │ speech frames only (pcm16/float32)
        ▼
InferenceClient.data_channel.send(audio_frame)   ← streaming WebSocket
        │
        ▼ (async, bidirectional)
InferenceServer (local FastAPI + WebSocket)
  └── FasterWhisperBackend.transcribe_stream()
        │ TranscriptSegment(text, confidence, is_final)
        ▼
DictationService receives segment
  ├── is_final=False → internal state only (no UI, no injection)
  └── is_final=True
        ├── confidence < threshold → WARN indicator + notify-send → discard
        └── confidence OK
              │
              ▼
        PostProcessingPipeline
          ├── NumberNormalizer ("twenty two" → "22")
          ├── Capitalizer (sentence starts)
          ├── PunctuationProcessor (voice commands, word-boundary safe)
          └── LanguageCleanup (en-specific)
              │
              ▼
        TextInjectionLayer.commit_text(text, target_window_id)
          ├── IBusBackend (primary)
          ├── XdotoolBackend (X11 fallback)
          └── → text appears in target application

[hotkey release] → FINALIZING → flush → COMPLETED → IDLE
```

---

## Project Structure

```
budgie_voice/
├── main.py                         # QApplication + qasync event loop; wires all modules
│
├── orchestrator/
│   ├── __init__.py
│   ├── dictation_service.py        # Policy only; model-agnostic; session coordination
│   ├── session.py                  # SessionState enum + state machine
│   └── hotkey.py                   # pynput GlobalHotKeys; toggle + push-to-hold
│
├── audio/
│   ├── __init__.py
│   ├── capture.py                  # sounddevice InputStream → raw pcm16 chunks → Queue
│   ├── denoiser.py                 # Optional RNNoise / noisereduce wrapper
│   ├── vad.py                      # Silero VAD; speech segment detection
│   └── adapter.py                  # Chains capture→denoise→vad; outputs pcm16/float32 frames
│
├── inference/
│   ├── __init__.py
│   ├── protocol.py                 # Versioned message types: AudioFrame, TranscriptSegment, ControlMsg
│   ├── server.py                   # Local FastAPI+WebSocket server; OpenAI-compat /v1/audio/transcriptions
│   ├── client.py                   # Async client; Control Channel + Data Channel (WebSocket)
│   └── backends/
│       ├── __init__.py
│       ├── base.py                 # InferenceBackend ABC (version, load, transcribe_stream, unload)
│       ├── faster_whisper.py       # FasterWhisperBackend
│       └── registry.py             # name → class map
│
├── speaker/
│   ├── __init__.py
│   ├── verifier.py                 # SpeechBrain ECAPA-TDNN; (is_me, score)
│   └── enroller.py                 # PyQt6 QWizard; 4-step enrollment
│
├── postprocessing/
│   ├── __init__.py
│   ├── pipeline.py                 # Chains all processors
│   ├── normalizer.py               # Spoken→written numbers/ordinals
│   ├── punctuation.py              # Voice command substitution (word-boundary)
│   ├── capitalizer.py              # Sentence + post-newline capitalisation
│   └── language_cleanup.py        # en-specific: contractions, abbreviations
│
├── injection/
│   ├── __init__.py
│   ├── base.py                     # TextOutputBackend ABC (versioned interface)
│   ├── selector.py                 # Auto-select: IBus available? → ibus; else xdotool
│   ├── ibus.py                     # IBusBackend; python-ibus; commit_text to locked target
│   ├── xdotool.py                  # XdotoolBackend; --window <id> to locked target
│   ├── xdg.py                      # XdgBackend stub (future: xdg-portal text input)
│   ├── wayland.py                  # WaylandBackend stub (future: input-method-unstable-v2)
│   └── secure.py                   # SecureFieldDetector; xprop + blocklist check
│
├── indicator/
│   ├── __init__.py
│   └── tray.py                     # QSystemTrayIcon; TrayState enum; set_state(); menu
│
├── settings_ui/
│   ├── __init__.py
│   └── window.py                   # PyQt6 QDialog; hotkey, language, model, mic, backends
│
├── config/
│   ├── __init__.py
│   └── settings.py                 # Load/validate config.yaml → Settings dataclass
│
├── utils/
│   ├── __init__.py
│   ├── logging_setup.py
│   └── exceptions.py               # BudgieMicError, BudgieInferenceError, BudgieInjectionError, etc.
│
├── dbus/
│   ├── __init__.py
│   └── service.py                  # DBus well-known name io.gitlab.karanaithal841.BudgieVoice; Settings↔Service IPC
│
├── models/                         # Downloaded weights (.gitignore)
├── tests/
│   ├── test_session.py             # State machine; all transitions
│   ├── test_audio_adapter.py
│   ├── test_inference_protocol.py
│   ├── test_postprocessing.py
│   └── test_injection.py
├── scripts/
│   ├── install_deps.sh
│   └── install_service.sh
├── config.yaml.example
└── requirements.txt
```

---

## Versioned Interfaces

### `InferenceBackend` ABC (`inference/backends/base.py`)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator
import numpy as np

INTERFACE_VERSION = "1.0"

@dataclass
class TranscriptSegment:
    text: str
    confidence: float        # 0.0–1.0; mean avg_logprob mapped to [0,1]
    is_final: bool           # True = committed; False = partial hypothesis (internal only)
    language: str
    segment_id: str          # UUID; for deduplication

@dataclass
class BackendCapabilities:
    version: str
    supported_languages: list[str]
    supported_models: list[str]
    supports_streaming: bool
    supports_gpu: bool

class InferenceBackend(ABC):
    interface_version: str = INTERFACE_VERSION

    @abstractmethod
    async def load(self, model: str, device: str, compute_type: str) -> None: ...

    @abstractmethod
    async def transcribe_stream(
        self, audio_frames: AsyncIterator[np.ndarray], language: str
    ) -> AsyncIterator[TranscriptSegment]: ...

    @abstractmethod
    async def unload(self) -> None: ...

    @abstractmethod
    async def capabilities(self) -> BackendCapabilities: ...

    @property
    @abstractmethod
    def is_ready(self) -> bool: ...
```

### `TextOutputBackend` ABC (`injection/base.py`)

```python
from abc import ABC, abstractmethod

INTERFACE_VERSION = "1.0"

class TextOutputBackend(ABC):
    interface_version: str = INTERFACE_VERSION

    @abstractmethod
    async def start_session(self, target_window_id: int | None) -> None: ...

    @abstractmethod
    async def commit_text(self, text: str) -> None: ...

    @abstractmethod
    async def cancel_session(self) -> None: ...

    @abstractmethod
    async def end_session(self) -> None: ...

    @abstractmethod
    def is_secure_field(self, window_id: int | None) -> bool: ...

    @property
    @abstractmethod
    def backend_name(self) -> str: ...
```

### Inference Protocol Messages (`inference/protocol.py`)

```python
from dataclasses import dataclass, field
from enum import StrEnum
import time, uuid

class ChannelType(StrEnum):
    CONTROL = "control"
    DATA    = "data"

@dataclass
class AudioFrame:
    data: bytes                          # pcm16 or float32 raw bytes
    sample_rate: int = 16000
    channels: int = 1
    format: str = "pcm16"               # "pcm16" | "float32"
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000)
    frame_id: str = field(default_factory=lambda: str(uuid.uuid4()))

@dataclass
class ControlMessage:
    action: str                          # "configure" | "capabilities" | "reset"
    payload: dict
    version: str = "1.0"

@dataclass
class CapabilitiesResponse:
    version: str
    backends: list[str]
    languages: list[str]
    models: list[str]
```

---

## Activity Indicator States (`indicator/tray.py`)

| `TrayState` | Colour | Tooltip |
|---|---|---|
| `IDLE` | 🔵 Blue | Budgie — idle |
| `STARTING` | 🟡 Yellow | Budgie — starting… |
| `RECORDING` | 🟢 Green | Budgie — recording |
| `TRANSCRIBING` | 🟣 Purple | Budgie — transcribing |
| `FINALIZING` | 🟣 Purple (pulse) | Budgie — finalizing |
| `COMPLETED` | 🟢 Green (2s flash) | Budgie — done |
| `LOW_CONFIDENCE` | 🟠 Orange | Budgie — couldn't hear clearly |
| `CANCELLED` | 🔵 Blue | Budgie — cancelled |
| `ERROR` | 🔴 Red | Budgie — error (see log) |

Icons: 32×32 QPixmap rendered from emoji via QPainter — zero external image files.
`LOW_CONFIDENCE` auto-reverts to prior state after 3 s.

---

## Configuration (`config.yaml.example`)

```yaml
activation:
  mode: "toggle"          # "toggle" | "push_to_hold"
  hotkey: "ctrl+space"

audio:
  device: null            # null = system default; or device name
  sample_rate: 16000
  chunk_duration_ms: 30
  format: "pcm16"         # "pcm16" | "float32"
  max_buffer_seconds: 30  # in-memory only; never written to disk
  denoise: true

vad:
  enabled: true
  aggressiveness: 2       # Silero 0–3
  speech_pad_ms: 300

inference:
  backend: "faster_whisper"    # "faster_whisper" | "parakeet" | "nemotron" | "qwen3_asr"
  server_port: 18000           # local inference WebSocket server
  model: "small"               # "tiny" | "small" | "medium" | "large-v3"
  device: "auto"               # "auto" | "cpu" | "cuda"
  compute_type: "int8"
  language: "en"
  confidence_threshold: 0.55
  beam_size: 5

speaker:
  enabled: true
  voiceprint_path: "~/.config/budgie-voice/voiceprint.pt"
  threshold: 0.75
  min_samples: 5

postprocessing:
  normalize_numbers: true
  auto_capitalize: true
  punctuation_commands: true
  language_cleanup: true
  llm_cleanup: false           # future

injection:
  backend: "auto"              # "auto" | "ibus" | "xdotool" | "xdg"
  delay_ms: 12
  clear_modifiers: true
  block_secure_fields: true

dbus:
  well_known_name: "io.gitlab.karanaithal841.BudgieVoice"

indicator:
  enabled: true
  show_tooltip_state: true

logging:
  level: "INFO"
  max_bytes: 5242880
  backup_count: 3

notifications:
  tool: "notify-send"
  low_confidence_message: "⚠️ Couldn't hear you — repeat?"
  secure_field_message: "🔒 Dictation blocked in secure field."
  no_mic_message: "🎤 No microphone found."

performance:
  mic_start_target_ms: 100
  indicator_visible_target_ms: 200
  first_transcription_target_ms: 800
  final_commit_target_ms: 2000
```

---

## Punctuation Voice Commands (`postprocessing/punctuation.py`)

Rules: standalone token only (word-boundary regex `\b`). Never substitute mid-word.

```python
COMMANDS: dict[str, str] = {
    "comma":            ",",
    "period":           ".",
    "full stop":        ".",
    "question mark":    "?",
    "exclamation mark": "!",
    "exclamation point":"!",
    "new line":         "\n",
    "new paragraph":    "\n\n",
    "open quote":       "\u201c",
    "close quote":      "\u201d",
    "colon":            ":",
    "semicolon":        ";",
    "hyphen":           "-",
    "dash":             "—",
    "open bracket":     "(",
    "close bracket":    ")",
}
# "My period is late" → unchanged (period not standalone terminal token)
# "Hello period"      → "Hello."
```

---

## Secure Field Blocklist (`injection/secure.py`)

Check `xprop WM_CLASS` + window title against:

```python
BLOCKED_WM_CLASS = ["pinentry", "gnome-keyring", "polkit-gnome", "gksu"]
BLOCKED_TITLE_PATTERNS = [
    r"password", r"passphrase", r"authentication",
    r"unlock", r"keyring", r"sudo", r"pin\b",
]
```

On match → cancel session → `notify-send` secure_field_message → `IDLE`.

---

## Phases — Build strictly in order. Confirm before next phase.

---

### Phase 1 — Audio Capture + VAD + Local Inference Server + Console Output

**Goal:** Mic → Audio Adapter → Inference Server (local WebSocket) → transcripts to terminal.

**Deliverables:**
- `utils/logging_setup.py`, `utils/exceptions.py`, `config/settings.py`
- `audio/capture.py` — sounddevice InputStream; 16kHz mono; pcm16; never writes to disk
- `audio/denoiser.py` — `DenoiseFilter`; wraps `noisereduce`; passthrough if disabled in config
- `audio/vad.py` — `VADFilter`; Silero VAD; emits speech-only frames
- `audio/adapter.py` — `AudioAdapter`; chains capture→denoise→vad; outputs `AudioFrame` (pcm16); async generator
- `inference/protocol.py` — `AudioFrame`, `TranscriptSegment`, `ControlMessage`, `CapabilitiesResponse`
- `inference/backends/base.py` — `InferenceBackend` ABC + `BackendCapabilities`
- `inference/backends/faster_whisper.py` — `FasterWhisperBackend`; async streaming; `transcribe_stream()`; confidence = mean avg_logprob → [0,1]
- `inference/backends/registry.py` — name→class map
- `inference/server.py` — `InferenceServer`; FastAPI app; WebSocket `/ws/transcribe` (Data Channel); HTTP GET `/capabilities` (Control Channel); runs in background thread via `uvicorn`
- `inference/client.py` — `InferenceClient`; async WebSocket client; `send_frame()`, `receive_segments()` async generator; `get_capabilities()`
- `main.py` — starts server; connects client; pipes AudioAdapter → client → prints `[HH:MM:SS] [FINAL|PARTIAL] [conf=0.87] Hello world`
- `config.yaml.example`, `requirements.txt`

**Privacy:** AudioFrame.data stays in-memory. No `open(audio...)` anywhere.

**Acceptance:** `python main.py` → speak → timestamped transcripts in terminal. Ctrl+C exits cleanly; mic released.

---

### Phase 2 — Post-Processing + Text Injection

**Deliverables:**
- `postprocessing/normalizer.py` — `NumberNormalizer`; word2num + regex for ordinals; "twenty two" → "22", "third" → "3rd"
- `postprocessing/punctuation.py` — `PunctuationProcessor`; COMMANDS map; word-boundary `\b` regex; safe substitution only
- `postprocessing/capitalizer.py` — `Capitalizer`; after `.!?\n` and at injection start
- `postprocessing/language_cleanup.py` — `LanguageCleanup`; en-specific: expand contractions in reverse (don't → do not NOT needed; clean up "gonna" → "going to" if enabled); strip filler words if configured
- `postprocessing/pipeline.py` — `PostProcessingPipeline`; chains in order: normalizer → punctuation → capitalizer → language_cleanup; returns `str`
- `injection/base.py` — `TextOutputBackend` ABC (versioned)
- `injection/secure.py` — `SecureFieldDetector`; `xprop` + blocklist
- `injection/xdotool.py` — `XdotoolBackend`; captures `xdotool getactivewindow` at `start_session`; all `commit_text` calls use `--window <captured_id>`; raises `BudgieInjectionError` if xdotool missing
- `injection/ibus.py` — `IBusBackend`; connect to running IBus daemon; `commit_text` via `IBus.InputContext`; raises if IBus unavailable
- `injection/xdg.py` — `XdgBackend`; stub class; raises `NotImplementedError` with "future backend" message
- `injection/wayland.py` — `WaylandBackend`; stub class; same
- `injection/selector.py` — `BackendSelector`; config `"auto"` → probe IBus → if running use IBus; else xdotool; log which was selected
- Update `main.py`: `is_final=True` + `confidence OK` → pipeline → injection

**Acceptance:** Cursor in gedit → speak *"Hello world period new line twenty two items"* → gedit receives `Hello world.\n22 items`

---

### Phase 3 — Session State Machine + Speaker Verification + DBus

**Deliverables:**
- `orchestrator/session.py` — `SessionState` enum; `DictationSession`; full state machine; asyncio; emits `SessionEvent` via `asyncio.Queue`; owns target_window_id lock; cancels on window disappear; calls `SecureFieldDetector` at STARTING; does NOT process audio (delegates to AudioAdapter and InferenceClient)
- `orchestrator/dictation_service.py` — `DictationService`; coordinates `AudioAdapter` + `InferenceClient` + `PostProcessingPipeline` + `TextInjectionLayer`; policy-only; model-agnostic; receives `SessionEvent` from session; routes accordingly
- `orchestrator/hotkey.py` — `HotkeyManager`; pynput `GlobalHotKeys`; background thread; toggle or push-to-hold per config; calls `DictationService.toggle()` / `.push_start()` / `.push_stop()`
- `dbus/service.py` — `BudgieDBusService`; well-known name `io.gitlab.karanaithal841.BudgieVoice`; methods: `Toggle()`, `GetState()`, `GetCapabilities()`; signal: `StateChanged(state: str)`; used by Settings UI later
- `speaker/verifier.py` — `SpeakerVerifier`; ECAPA-TDNN; `verify(audio: np.ndarray) → tuple[bool, float]`; disabled passthrough if voiceprint missing
- `speaker/enroller.py` — `EnrollmentWizard(QWizard)`:
  - Step 1: Welcome; explain privacy (no disk audio)
  - Step 2: Record N clips; pyqtgraph waveform preview per clip; re-record button
  - Step 3: Compute mean ECAPA embedding → save `voiceprint.pt`
  - Step 4: Live test — speak → pass/fail result
  - Launch: `python -m budgie_voice.speaker.enroller`

**Acceptance:** Enroll → your voice → text typed. Other speaker → silent discard. Cancel mid-session → mic released; no text injected. `gdbus call --session --dest io.gitlab.karanaithal841.BudgieVoice --object-path /io/gitlab/karanaithal841/BudgieVoice --method io.gitlab.karanaithal841.BudgieVoice.GetState` returns current state.

---

### Phase 4 — Activity Indicator + Low-Confidence + Secure Fields + Settings UI

**Deliverables:**
- `indicator/tray.py` — `TrayIcon(QSystemTrayIcon)`; `TrayState` enum; `set_state()`; emoji→QPixmap via QPainter; menu: **Toggle Listen**, **Enroll Voice**, **Settings**, **Open Log**, **Quit**; `LOW_CONFIDENCE` auto-reverts after 3 s via `QTimer`
- Wire `SessionState` → `TrayState` in `dictation_service.py`
- Low-confidence path in `dictation_service.py`: `confidence < threshold` → `tray.set_state(LOW_CONFIDENCE)` → `notify-send` → discard segment
- `injection/secure.py` active: checked at STARTING; if blocked → ERROR state → notify
- `settings_ui/window.py` — `SettingsWindow(QDialog)`:
  - Hotkey capture widget (record key combo live)
  - Model size dropdown: tiny / small / medium / large-v3
  - Backend dropdown: faster_whisper / parakeet / nemotron / qwen3_asr
  - Mic source dropdown (enumerate sounddevice devices)
  - Confidence threshold slider (0.0–1.0)
  - Activation mode radio: toggle / push-to-hold
  - Speaker verification toggle + "Enroll Now" button
  - Post-processing toggles: numbers, punctuation commands, language cleanup
  - Privacy note: *"Speech recognition runs locally. No audio is saved."*
  - Save → writes `~/.config/budgie-voice/config.yaml` → DBus signal to reload

**Acceptance:** Toggle → tray 🟢. Mumble → tray 🟠 + notification. Password field → tray 🔵 + blocked notification. Settings opens from tray; save reloads config without restart.

---

### Phase 5 — Systemd Service + Packaging + README

**Deliverables:**
- `scripts/install_deps.sh`:
  ```bash
  sudo apt install -y xdotool ibus python3-ibus libportaudio2 libnotify-bin \
                      python3-pyqt6 python3-dbus
  pip install -r requirements.txt
  # pre-download model
  python -c "from faster_whisper import WhisperModel; WhisperModel('small', download_root='~/.cache/budgie_voice/models')"
  ```
- `scripts/install_service.sh` — copy → `~/.local/lib/budgie_voice/`; write service file; `systemctl --user daemon-reload && enable --now budgie-voice`
- `scripts/uninstall_service.sh`
- `budgie-voice.service` template (embedded in install script):
  ```ini
  [Unit]
  Description=Budgie Voice Typing Assistant
  After=graphical-session.target
  PartOf=graphical-session.target

  [Service]
  Type=simple
  ExecStart=/usr/bin/python3.12 -m budgie_voice.main
  WorkingDirectory=%h/.local/lib/budgie-voice
  Restart=on-failure
  RestartSec=5
  Environment=DISPLAY=:0
  Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus
  Environment=IBUS_ADDRESS_FILE=%h/.config/ibus/bus

  [Install]
  WantedBy=graphical-session.target
  ```
- `README.md`: 3-step quick start; config reference; adding new STT backend (implement `InferenceBackend`, register in `registry.py`); adding new injection backend (implement `TextOutputBackend`, register in `selector.py`); systemd commands; troubleshooting
- Pinned `requirements.txt`

**Acceptance:** Log out → in → `systemctl --user status budgie-voice` = `active (running)`. Hotkey works. Tray visible. Settings opens.

---

## Code Quality (every phase)

- Python 3.12: `match/case`, `X | Y` unions, `TypeAlias`
- Google-style docstrings on all public API
- Full type hints on all signatures
- No `print()` → `logging.getLogger(__name__)`
- No global mutable state; inject dependencies via constructors
- Modules ≤ 150 lines; split if larger
- One `pytest` test per phase (mock audio, mock WebSocket, mock xdotool/IBus)
- Custom exceptions from `utils/exceptions.py`
- `dataclasses` or `pydantic` for all structured data
- Never `subprocess(shell=True)`; always list args
- `pathlib.Path` everywhere; no hardcoded strings
- Clean `SIGTERM`/`SIGINT`: release mic, stop server, stop Qt, cancel asyncio tasks
- **Logging must NEVER include raw audio or full transcription at INFO+**

---

## Security Constraints

- Audio never touches disk — assert: no `open()` on audio data anywhere
- `AudioFrame.data` held in `asyncio.Queue` only; cleared on session end
- Injection blocked: password, polkit, pinentry, gnome-keyring, lock screen
- Text injection strips unsafe combos: no TAB, ALT+TAB, Fn keys, SUPER
- Target window locked at STARTING; no retargeting mid-session
- Target disappears mid-session → CANCELLED; text discarded; no attempt to retarget
- IBus/xdotool only inject printable characters

---

## Performance Targets

| Metric | Target |
|---|---|
| Mic capture start | < 100 ms from hotkey |
| Activity indicator visible | < 200 ms from hotkey |
| First transcription event | < 800 ms from hotkey |
| Final text committed | < 2 s after key release |
| Idle CPU | < 1% |

---

## Dependencies

```
# inference
faster-whisper>=1.0.0
fastapi>=0.110.0
uvicorn>=0.29.0
websockets>=12.0

# speaker
speechbrain>=1.0.0
torch>=2.0.0
torchaudio>=2.0.0

# audio
sounddevice>=0.4.6
noisereduce>=3.0.0
numpy>=1.26.0

# UI
PyQt6>=6.6.0
qasync>=0.27.0
pyqtgraph>=0.13.3

# hotkey + IPC
pynput>=1.7.6
dbus-python>=1.3.2

# config + utils
pyyaml>=6.0
pydantic>=2.0.0

# test
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

System (apt): `xdotool ibus python3-ibus libportaudio2 libnotify-bin python3-pyqt6 python3-dbus`

---

## Instruction for the AI building this

Build one phase at a time. After each phase:
1. File tree — created/modified only
2. Full content of every new/changed file
3. Exact terminal commands → install deps + run
4. Acceptance test → expected output
5. Ask confirm before next phase

No stubs — every function fully implemented.
