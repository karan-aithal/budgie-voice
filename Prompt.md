 You are helping me build a **Linux desktop voice typing assistant** for Ubuntu 22.04 LTS (X11) using Python 3.12. The application continuously listens to my microphone, recognises only **my voice**, and automatically types the transcribed text wherever my keyboard cursor is currently focused — browser, VS Code, terminal, LibreOffice, etc.
 
 This is **Phase 1 of a larger personal AI assistant**. Keep the architecture open to future extension (adding LLM reasoning, tool use, etc.).
 
 ---
 
 ## Fixed Decisions (do not ask about these)
 
 | Concern | Decision |
 |---|---|
 | OS / Display | Ubuntu 22.04 LTS, X11 (Wayland later) | Also cross-platform Windows
 | Language | Python 3.12 |
 | STT Engine | Faster-Whisper (`small` or `medium` model, auto-select GPU if available, fall back to CPU) |
 | Spoken language | English only (`language="en"`) |
 | Activation mode | Hotkey **toggle** (listen ON / listen OFF) — key configurable in YAML |
 | VAD | Silero VAD inside each listening session (only transcribe when speech is detected) |
 | Punctuation | Whisper automatic punctuation **+** spoken commands: `"comma"` → `,`  `"period"` → `.`  `"new line"` → `\n`  `"new paragraph"` → `\n\n`  `"question mark"` → `?`  `"exclamation mark"` → `!`  `"open quote"` / `"close quote"` → `"` / `"` |
 | Speaker verification | SpeechBrain ECAPA-TDNN; enrollment via GUI wizard (PyQt6) |
 | Text injection | `xdotool type --clearmodifiers --delay 12 -- "<text>"` |
 | Audio backend | `sounddevice` (primary), `PyAudio` (fallback) |
 | Config file | `~/.config/voice-assistant/config.yaml` |
 | Logging | Python `logging` module → rotating file `~/.local/share/voice-assistant/logs/app.log` + stderr |
 | Low-confidence handling | Flash tray icon orange, show desktop notification: *"⚠️ I couldn't hear you clearly — could you repeat that?"* then discard the segment |
 | Confidence threshold | Configurable in YAML (`stt.confidence_threshold`, default `0.55`) |
 | Operation mode | Background service with **system tray icon** (PyQt6 + QSystemTrayIcon) |
 | Tray icon states | 🔵 idle/off, 🟢 listening, 🟠 low-confidence warning, 🔴 error |
 | Packaging | Systemd **user** service (`~/.config/systemd/user/voice-assistant.service`) |
 | Offline | 100% offline after installation |
 
 ---
 
 ## Project Structure
 
 Create this layout from the start. Keep it even if a directory is empty:
 
 ```
 assistant/
 ├── main.py                  # Entry point; wires all modules; runs asyncio event loop
 ├── audio/
 │   ├── __init__.py
 │   ├── capture.py           # Mic capture with sounddevice; emits audio chunks via asyncio.Queue
 │   └── vad.py               # Silero VAD wrapper; filters non-speech chunks
 ├── speech/
 │   ├── __init__.py
 │   └── recognizer.py        # Faster-Whisper wrapper; returns TranscriptResult(text, confidence)
 ├── speaker/
 │   ├── __init__.py
 │   ├── verifier.py          # SpeechBrain ECAPA-TDNN inference; returns (is_me: bool, score: float)
 │   └── enroller.py          # GUI wizard (PyQt6) to record samples and save voiceprint
 ├── injector/
 │   ├── __init__.py
 │   └── xdotool.py           # Calls xdotool; applies punctuation command substitution
 ├── config/
 │   ├── __init__.py
 │   └── settings.py          # Loads, validates, and exposes config.yaml via a Settings dataclass
 ├── tray/
 │   ├── __init__.py
 │   └── icon.py              # QSystemTrayIcon; exposes set_state(TrayState) enum method
 ├── utils/
 │   ├── __init__.py
 │   └── logging_setup.py     # Configures root logger with rotating file + stderr handlers
 ├── models/                  # Downloaded model files (gitignored)
 ├── logs/                    # Symlink or reference; actual logs in ~/.local/share/...
 ├── tests/
 │   ├── test_audio.py
 │   ├── test_recognizer.py
 │   ├── test_injector.py
 │   └── test_verifier.py
 ├── scripts/
 │   ├── install_deps.sh      # pip install + apt packages + model downloads
 │   └── install_service.sh   # Installs systemd user service
 ├── config.yaml.example      # Documented example config (copied to ~/.config/... on first run)
 └── requirements.txt
 ```
 
 ---
 
 ## Configuration File Schema (`config.yaml.example`)
 
 ```yaml
 hotkey:
   toggle_listen: "ctrl+space"   # Any combo parseable by pynput
 
 audio:
   device: null          # null = system default; or device name string
   sample_rate: 16000
   chunk_duration_ms: 30
 
 vad:
   enabled: true
   aggressiveness: 2             # Silero sensitivity 0–3
   speech_pad_ms: 300            # Pad silence before/after speech segment
 
 stt:
   model: "small"                # "tiny" | "small" | "medium" | "large-v3"
   device: "auto"                # "auto" | "cpu" | "cuda"
   compute_type: "int8"
   language: "en"
   confidence_threshold: 0.55    # Segments below this trigger low-confidence warning
   beam_size: 5
 
 speaker:
   enabled: true
   voiceprint_path: "~/.config/voice-assistant/voiceprint.pt"
   threshold: 0.75               # Cosine similarity threshold 0–1
   min_samples: 5                # Minimum enrollment recordings required
 
 injection:
   delay_ms: 12                  # xdotool keystroke delay
   clear_modifiers: true
 
 tray:
   enabled: true
 
 logging:
   level: "INFO"                 # DEBUG | INFO | WARNING | ERROR
   max_bytes: 5242880            # 5 MB
   backup_count: 3
 
 notifications:
   low_confidence_message: "⚠️ I couldn't hear you clearly — could you repeat that?"
   tool: "notify-send"           # "notify-send" or "libnotify"
 ```
 
 ---
 
 ## Data Flow (all phases active)
 
 ```
 Microphone
     │
     ▼
 [audio/capture.py]  ──►  asyncio.Queue(audio_chunks)
     │
     ▼
 [audio/vad.py]      ──►  filters non-speech, emits speech segments
     │
     ▼
 [speaker/verifier.py] ──►  is_me=True / False  (skip if False)
     │
     ▼
 [speech/recognizer.py] ──►  TranscriptResult(text, confidence)
     │
     ├── confidence < threshold  ──►  [tray: orange] + [notify-send warning]
     │
     └── confidence OK  ──►  [injector/xdotool.py]  ──►  xdotool types into focused window
 ```
 
 ---
 
 ## Phases — Build strictly in order. Do not skip ahead.
 
 ### Phase 1 — Microphone Capture + Speech-to-Text + Console Output
 
 **Deliverables:**
 - `audio/capture.py` — captures 16 kHz mono audio chunks from the default mic using `sounddevice`; pushes raw numpy arrays into an `asyncio.Queue`
 - `audio/vad.py` — wraps Silero VAD; accumulates chunks; emits complete speech segments (start→end of speech event)
 - `speech/recognizer.py` — wraps `faster_whisper.WhisperModel`; accepts a numpy audio array; returns `TranscriptResult(text: str, confidence: float, language: str)`; uses `language="en"`, `beam_size=5`; computes `confidence` as mean of segment `avg_logprob` mapped to [0,1]
 - `config/settings.py` — loads `config.yaml`; exposes typed `Settings` dataclass
 - `utils/logging_setup.py` — sets up rotating file + stderr logging
 - `main.py` — wires the above; prints each `TranscriptResult` to the terminal in format: `[HH:MM:SS] [conf=0.87] Hello world`
 - `config.yaml.example` and `requirements.txt`
 
 **Acceptance test:** Run `python main.py`, speak into the microphone, and see timestamped transcripts appear in the terminal. Ctrl+C exits cleanly.
 
 **Do not implement** speaker verification, text injection, or tray icon in this phase.
 
 ---
 
 ### Phase 2 — Text Injection into Active Application
 
 **Deliverables:**
 - `injector/xdotool.py` — `XdotoolInjector` class with a single public method `inject(text: str) -> None`
   - Calls: `xdotool type --clearmodifiers --delay 12 -- "<text>"`
   - Before injection, apply punctuation command substitution (see Fixed Decisions table)
   - Substitution must be **word-boundary aware** (e.g. `"My period is late"` must NOT become `"My . is late"`)
   - Substitution rules: only replace the spoken command when it appears as a **standalone word or phrase** at the end of a token group, or surrounded by whitespace / start/end of string
   - Log the pre- and post-substitution text at DEBUG level
   - Handle `xdotool` not installed: raise `InjectorError` with a helpful message
 - Update `main.py` to call `injector.inject(result.text)` after printing to terminal
 - Add `install_deps.sh` that runs: `sudo apt install -y xdotool` + pip installs
 
 **Acceptance test:** With cursor active in gedit, speak *"Hello world period new line This is a test"* → gedit receives `Hello world.\nThis is a test`.
 
 ---
 
 ### Phase 3 — Speaker Verification (Only My Voice)
 
 **Deliverables:**
 - `speaker/verifier.py` — `SpeakerVerifier` class
   - Loads a pre-saved voiceprint (torch tensor, shape `[1, embedding_dim]`) from disk
   - Uses SpeechBrain ECAPA-TDNN (`speechbrain/spkrec-ecapa-voxceleb`) to extract embeddings
   - `verify(audio_np: np.ndarray) -> tuple[bool, float]` — returns `(is_me, cosine_similarity_score)`
   - Threshold configurable from `Settings`
   - If voiceprint file does not exist, log a warning and return `(True, 1.0)` (disabled mode)
 - `speaker/enroller.py` — `EnrollmentWizard` (PyQt6 `QWizard`)
   - Step 1: Welcome screen, explains the process
   - Step 2: Records N audio clips (N from config, default 5), shows a waveform preview per clip using `pyqtgraph` or `matplotlib` embedded in Qt, allows re-record
   - Step 3: Computes mean embedding across all clips, saves as `voiceprint.pt`
   - Step 4: Confirmation screen with "Test your voice" button that runs one verification
   - Launch via `python -m assistant.speaker.enroller`
 - Update `main.py` pipeline: after VAD emits a segment, call `verifier.verify()`; if `is_me=False`, log at DEBUG and skip injection
 - Update `scripts/install_deps.sh` to install SpeechBrain
 
 **Acceptance test:** Enroll your voice. Then test that your voice triggers typing and a second person's voice is silently ignored.
 
 ---
 
 ### Phase 4 — Hotkey Toggle + System Tray + Low-Confidence Handling
 
 **Deliverables:**
 - `tray/icon.py` — `TrayIcon(QSystemTrayIcon)`
   - `TrayState` enum: `IDLE`, `LISTENING`, `LOW_CONFIDENCE_WARNING`, `ERROR`
   - `set_state(state: TrayState)` — changes icon and tooltip
   - Tray menu: **"Start Listening"**, **"Stop Listening"**, **"Enroll Voice"**, **"Open Log"**, **"Quit"**
   - Icons: use Unicode emoji rendered to QPixmap (🔵 🟢 🟠 🔴) — no external icon files required; generate 32×32 QPixmap from emoji text using QPainter at startup
 - Hotkey listener — use `pynput.keyboard.GlobalHotKeys` in a background thread; toggle `is_listening` flag; call `tray.set_state()` accordingly; key combo loaded from config
 - Low-confidence handling — when `result.confidence < settings.stt.confidence_threshold`:
   - Call `tray.set_state(TrayState.LOW_CONFIDENCE_WARNING)`
   - Run `notify-send` with the message from config (`notifications.low_confidence_message`)
   - After 3 seconds, restore tray to `LISTENING` state
   - Do **not** inject any text
 - Update `main.py` to run the Qt application (`QApplication`) and the asyncio event loop together using `qasync`
 
 **Acceptance test:** Toggle listening with the configured hotkey. Tray icon changes colour. Mumble into the mic → orange icon + desktop notification appears. Clear speech → text typed normally.
 
 ---
 
 ### Phase 5 — Systemd User Service + Packaging
 
 **Deliverables:**
 - `scripts/install_service.sh`:
   ```bash
   #!/usr/bin/env bash
   # Copies project to ~/.local/lib/voice-assistant/
   # Creates ~/.config/systemd/user/voice-assistant.service
   # Runs: systemctl --user daemon-reload && systemctl --user enable --now voice-assistant
   ```
 - `voice-assistant.service` template (embedded in the script):
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
 - `scripts/uninstall_service.sh` — stops and disables the service, removes service file
 - Update `README.md` with: installation steps, first-run enrollment instructions, hotkey configuration, log location, systemd commands (`status`, `stop`, `restart`, `logs`)
 - `requirements.txt` — pinned versions for all dependencies
 
 **Acceptance test:** After running `install_service.sh`, log out and back in. Verify `systemctl --user status voice-assistant` shows `active (running)`. Toggle hotkey and dictate text.
 
 ---
 
 ## Code Quality Rules (apply to every phase)
 
 - Python 3.12 — use `match/case`, `|` union types, `TypeAlias` where appropriate
 - All public functions and classes must have docstrings (Google style)
 - Full type hints on all function signatures
 - No `print()` — use `logging.getLogger(__name__)`
 - No global mutable state — pass dependencies via constructors or function arguments
 - Each module must be importable without side effects
 - Keep modules under ~150 lines; split if larger
 - Write one `pytest` test per phase covering the core happy path (mock audio, mock xdotool)
 - Raise custom exceptions from `utils/exceptions.py` instead of bare `Exception`
 - Use `dataclasses` or `pydantic` for all structured data (config, results)
 
 ---
 
 ## Dependency Reference
 
 ```
 faster-whisper>=1.0.0
 speechbrain>=1.0.0
 sounddevice>=0.4.6
 PyAudio>=0.2.14
 pynput>=1.7.6
 PyQt6>=6.6.0
 qasync>=0.27.0
 pyqtgraph>=0.13.3
 torch>=2.0.0
 torchaudio>=2.0.0
 numpy>=1.26.0
 pyyaml>=6.0
 pytest>=8.0.0
 ```
 
 System packages (apt): `xdotool`, `libportaudio2`, `python3-pyqt6`, `libnotify-bin`
 
 ---
 
 ## Important Constraints
 
 - Never use `subprocess.shell=True` — always pass a list of args
 - Never hardcode paths — use `pathlib.Path` and `os.path.expanduser()`
 - The app must exit cleanly on `SIGTERM` and `SIGINT` (release mic, stop Qt app, cancel asyncio tasks)
 - If `config.yaml` is missing, copy `config.yaml.example` to the config dir and log an `INFO` message
 - `models/` directory must be in `.gitignore`
 - Whisper model files download to `~/.cache/huggingface/` automatically — document this in README
 
 ---
 
 ## Communication Style — Caveman Mode ON
 
 Apply this to **all responses** throughout this project:
 
 - No filler words (`the`, `a`, `an`, `is`, `are` — drop where meaning survives)
 - No politeness (`sure`, `happy to help`, `great question` → never)
 - No repetition — say it once
 - Keywords + arrows + symbols over prose → `→`, `=`, `vs`, `+`, `&`, `✓`, `✗`
 - Compress aggressively — assume user is smart
 - Short sentences only
 - Long explanation only if explicitly asked
 - Dense, correct, minimum tokens
 
 **Example — bad:** "Sure! I've gone ahead and created the audio capture module for you. It should work great with your setup."
 **Example — good:** "`audio/capture.py` done. sounddevice → 16kHz mono → asyncio.Queue."
 
 ---
 
 ## Instruction for the AI building this
 
 Build one phase at a time. After each phase:
 1. File tree — created/modified only
 2. Full content of every new/changed file
 3. Exact terminal commands → install deps + run
 4. Acceptance test → expected output
 5. Ask confirm before next phase
 
 No placeholder stubs — every function fully implemented.