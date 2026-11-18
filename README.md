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

## Settings, dashboard and history

Open **Settings…** from the tray icon. Pages, in order: Overview, Dictation History, Audio & Inference,
Activation, Speaker Verification, Post-processing, Appearance. Only Overview and Dictation History
scroll; every other page fits the window.

- **Overview** is the dashboard: whether Budgie is ready (speech model and microphone), the hotkey
  hint, four totals (words dictated, dictation sessions, average speed, recording time), a
  words-per-day chart (last 7 or 30 days), system health (microphone, speech recognition, LM Studio,
  speaker verification) and your last five dictations.
- **Dictation History** lists every saved dictation, newest first: date, time, duration, words,
  characters and result (completed, cancelled or failed). Filter by date range (`YYYY-MM-DD`) and
  result, sort newest or oldest first, "Load more" for older ones, and select a row for its details.
  It is read-only.

### What history stores

One row per dictation in `~/.local/share/budgie-voice/history.db` (SQLite): when it started, its local
calendar day, recording time, words typed, characters typed and how it ended. **Never the words you
said and never audio**, nothing about the window or application, no telemetry, nothing leaves the
machine. There is no delete button yet: stop Budgie and delete the file to clear it. If the file cannot
be opened, history is off for that run and dictation is unaffected.

- **Words** are the words actually typed into the target window. A cancelled preview, an empty
  dictation or failed typing counts 0; so does a command-mode rewrite, which is not words you dictated.
- **Sessions** are completed dictations that typed at least one word.
- **Average speed** is total words ÷ total minutes of recording (pauses included), so it reads lower
  than your speaking rate.
- **Days** are the local calendar day a dictation started.

---

## Configuration

Config file: `~/.config/budgie-voice/config.yaml`
Copy the example: `cp config.yaml.example ~/.config/budgie-voice/config.yaml`

### Key settings

| Key | Default | Description |
|---|---|---|
| `activation.hotkey` | `ctrl+space` | Global hotkey (pynput format) — used when `trigger` is `pynput` |
| `activation.evdev_hotkey` | `f9` | Same `a+b` format — used when `trigger` is `evdev`. Pick a key applications ignore, see below |
| `activation.command_hotkey` | `""` (off) | Command-mode key, e.g. `f10`. `evdev` trigger only; needs `llm.enabled`. From a desktop shortcut, call the DBus method `Command` instead |
| `activation.mode` | `toggle` | `toggle` \| `push_to_hold` |
| `activation.trigger` | `auto` | Who owns the hotkey: `auto` \| `pynput` \| `evdev` \| `dbus` — see below |
| `activation.debounce_ms` | `350` | Ignore a repeat from the same source within this window |
| `activation.pause_media` | `false` | Pause what is playing while you dictate and resume it after, via `playerctl` (only the players that were playing). Without `playerctl` it does nothing |
| `postprocessing.snippets` | `{}` | Spoken phrase → replacement text, e.g. `my email: "me@example.com"`. Whole phrase, case-insensitive, longest first, applied before punctuation commands. The text is inserted exactly as written — a snippet containing "comma" stays "comma" |
| `postprocessing.strip_fillers` | `false` | Remove um / uh / er / hmm |
| `postprocessing.spoken_edits` | `true` | Saying "scratch that" deletes the sentence before it. Only when the phrase ends its own sentence — "scratch that itch" is left alone |
| `postprocessing.strip_trailing_period` | `false` | Drop one final `.` (an ellipsis stays) |
| `postprocessing.profiles` | `[]` | Per-app overrides, **X11 only**: a list of `match` (regex tested against the target window's `WM_CLASS`) plus any of `normalize_numbers`, `auto_capitalize`, `punctuation_commands`, `language_cleanup`, `strip_fillers`, `spoken_edits`, `strip_trailing_period`. First match wins, resolved once at session start. On Wayland the default settings apply (logged once). Examples for terminals and code editors in [`config.yaml.example`](config.yaml.example) |
| `inference.model` | `small` | A model of the chosen backend (Settings lists them): Whisper `tiny`…`large-v3`, or `nemo-parakeet-*`. An unknown backend or model falls back to `faster_whisper`/`small` with a notification |
| `inference.backend` | `faster_whisper` | `faster_whisper` \| `parakeet` |
| `inference.device` | `auto` | `auto` \| `cpu` \| `cuda` |
| `inference.language` | `en` | BCP-47 language code, or `auto`: Whisper detects the language; Parakeet can't, so it logs that once and uses `en` |
| `inference.confidence_threshold` | `0.40` | Discard segments below this. Skipped for backends that do not measure confidence (Parakeet) |
| `inference.beam_size` | `3` | Whisper beam size (1 = fastest greedy) |
| `inference.finalize_timeout_s` | `15` | Max wait at stop for the last turn's transcription; on timeout nothing is typed |
| `inference.min_turn_s` | `15` | A pause closes a turn only once it holds this much audio, so short dictations are transcribed in one pass; longer ones stream turn by turn |
| `learning.dictionary_path` | `~/.config/budgie-voice/dictionary.txt` | Your names and jargon, one per line (`#` comments allowed), sent to the decoder as hotwords together with words you corrected in the preview. Whisper uses them; Parakeet ignores hotwords |
| `injection.backend` | `auto` | `auto` \| `ydotool` \| `xdotool` |
| `injection.progressive` | `false` | Type each turn while you speak instead of everything at stop. Needs the preview **and** speaker verification off, never bypasses either — see [Progressive typing](#progressive-typing-optional) |
| `llm.enabled` | `false` | Optional local LLM (LM Studio or Ollama): **polish** (drops fillers and false starts, applies spoken self-corrections, fixes punctuation) and **command mode**. Off = no import, no network call. If the server is down, slow or answers badly, the unpolished text is used |
| `llm.url` | `http://127.0.0.1:1234/v1` | OpenAI-style base URL, as LM Studio shows it (Ollama: `http://127.0.0.1:11434/v1`). Budgie calls `<url>/chat/completions`. **Dictated text must not leave the machine by accident:** a host that is not loopback (127.0.0.0/8, `::1`, `localhost`) is refused at startup, with a log line, unless `llm.allow_remote` is `true`. Redirects and proxies are never followed |
| `llm.model` | *(none)* | The model id your server lists (`curl <llm.url>/models`), e.g. `qwen/qwen3.5-4b`. Must be set. Budgie asks reasoning models (Qwen3.x) not to think (`reasoning_effort: none`); a server that ignores that will usually exceed `timeout_s` and fall back to the unpolished text |
| `llm.timeout_s` | `8` | Seconds before falling back to the original text |
| `llm.allow_remote` | `false` | Allow a non-loopback `llm.url`. Your dictation is then sent to that host |
| `llm.min_words` | `8` | Polish only dictations at least this long; short phrases gain nothing and pay the latency |
| `llm.max_len_ratio` | `0.5` | A polished answer whose length differs from the input by more than this fraction is rejected |
| `speaker.enabled` | `true` | Speaker verification on/off |
| `speaker.threshold` | `0.65` | Cosine similarity cutoff |
| `vad.aggressiveness` | `2` | Silero VAD 0 (permissive) – 3 (strict) |
| `vad.speech_pad_ms` | `300` | Trailing silence before segment ends |
| `audio.denoise` | `false` | noisereduce suppression. Off by default: on 30 ms chunks it can gate real speech to silence. Re-enroll your voiceprint after changing it |
| `audio.devices` | `[]` | Ordered mic preference (`bt:<card>`, `pa:<source>`, ALSA name); the first that works at session start wins. Empty = Auto: first real mic (not monitor, loopback, S/PDIF or Bluetooth), else the system default. A Bluetooth headset is only used when listed, since its mic drops playback to mSBC quality while you dictate |
| `audio.input_gain_pct` | `null` | BT mic gain 50–150 %, applied when a `bt:` device is used |
| `vad.pre_roll_ms` | `300` | Audio kept from before the first speech chunk so word onsets are not clipped |
| `ui.theme` | `system` | Look of the Settings and enrollment windows: `system` (follows your desktop's light/dark setting, live) \| `light` \| `dark`. Also under Settings → Appearance; a change applies at once, no restart, including after a hand edit plus a reload. Needs PyQt6 ≥ 6.8, otherwise the desktop's own look is kept and a warning is logged. The recording overlay and transcript preview keep their own look in every theme |

Full reference: [`config.yaml.example`](config.yaml.example)

### Local LLM (optional)

Set `llm.enabled: true` and `llm.model` to the model id your local server (LM Studio: start its server, `lms server start`) lists. Everything stays on
your machine: the URL must be loopback unless you set `llm.allow_remote: true`.
All three can be set under **Settings → Post-processing → LM Studio** (the switch, *Server URL* and
*Model*); `llm.allow_remote` stays config-file only. The Overview shows whether the server and model
respond.

- **Polish** runs after the rule-based cleanup and before the preview, for dictations of at least
  `llm.min_words` words. The preview shows the polished text. Preview edits are learned only when
  you change the polished text.
- **Command mode**: highlight text, press `activation.command_hotkey` (or call the DBus method
  `Command`), say what to do ("make this more formal"), press again. The rewrite appears in the
  preview and is typed over the selection on accept. Nothing highlighted → a notification, nothing
  else. It reads the *primary selection* (`xclip` on X11, `wl-paste` on Wayland), and typing
  replaces the highlighted text in most GUI apps; terminals keep it, so there you get the rewrite
  next to the original.
- Prompts and answers are transcript text: logged at `DEBUG` only. The log line `Stop-to-preview`
  gives the time from stop to preview, with `llm=on|off`.

### Progressive typing (optional)

`injection.progressive: true` types each turn as soon as it is transcribed, instead of everything
at stop. It is **off by default** and gated by safety checks that no setting can override:

- It stays off while `injection.preview_before_inject` is on: the preview is never bypassed.
- It stays off while `speaker.enabled` is on: text would be typed before the speaker is verified.
  To use it, set `speaker.enabled: false` and `injection.preview_before_inject: false` yourself.
- It never applies to command mode.

When a gate blocks it, Budgie logs the reason and shows one notification, then dictates as usual.

How it behaves:

- **Turn size.** A turn closes at a pause once it holds `inference.min_turn_s` of audio (15 s by
  default), so text arrives in chunks of at least that length, not word by word.
- **Append-only.** Typed text is never edited or retracted. Turns are typed in order, once each,
  joined by a single space. Stop types only what was not typed yet.
- **Cancel** keeps everything already typed (Budgie never deletes it) and tells you how many
  characters stay.
- **Per-turn processing.** Number, capitalisation and punctuation rules and known-mishear
  corrections run on each turn. "Scratch that" only reaches back within its own turn.
  `strip_trailing_period` applies to every turn. Preview-edit learning and LLM polish do not apply.
- **Focus.** Every turn is typed into the window that had focus when you started, and Budgie
  re-activates that window before typing. Switch windows mid-dictation and the next turn pulls
  focus back to the original one.
- **Typing fails** (window gone, tool error): you get a notification, later turns are not typed out
  of order, and the whole dictation stays available under tray → Copy Last Transcript.

### Hotkey on Wayland vs X11

Exactly **one** mechanism may own the hotkey, chosen by `activation.trigger`:

| `trigger` | Who fires the toggle | Works on |
|---|---|---|
| `pynput` | in-process global listener, using `activation.hotkey` | X11 only |
| `evdev` | in-process listener on `/dev/input`, using `activation.evdev_hotkey`; supports `push_to_hold` | X11 **and** Wayland |
| `dbus` | external caller (desktop shortcut) invoking `Toggle`; no key-release, so no `push_to_hold` | X11 **and** Wayland |
| `auto` *(default)* | `pynput` on X11; on Wayland `evdev` if a keyboard under `/dev/input` is readable, else `dbus` — from `XDG_SESSION_TYPE`. The choice and reason are logged | both |

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

#### The `evdev` trigger

Reads key events straight from `/dev/input/event*`, so it needs no X11 and no desktop shortcut,
and it is the only option besides pynput that sees the key *release* (hold-to-talk) — on Wayland
it is the only one.

- **Needs the `input` group.** The device nodes are `root:input`, mode `0660`. Budgie never
  changes group membership for you; run it yourself, then log out and back in:

  ```bash
  sudo usermod -aG input $USER
  ```

  (A udev rule granting your user read access to `/dev/input/event*` works too.) With
  `trigger: evdev` and no readable keyboard, Budgie logs an error and sends a notification
  naming this fix.
- **Never grabs a device** (no `EVIOCGRAB`, no uinput re-emit), so a hang can't take your keyboard
  away. The consequence: **the hotkey still reaches the focused application**, exactly as with
  pynput. The default is `f9`: it exists on laptop and full-size keyboards alike, and as a single
  key it needs no modifier handling for hold-to-talk. `ctrl+space` is not the default here
  (editor autocomplete, input-method switching). The catch: some applications bind F9 themselves,
  so a press can also do something there. If one does, pick another key — `pause` is ignored by
  applications and keeps no state, but many laptops lack it. `scroll_lock` toggles a keyboard LED;
  `right_ctrl` fires whenever you use it as a real Ctrl.
- **Reads only the hotkey.** Key codes that aren't part of `activation.evdev_hotkey` are never
  looked at, stored or logged. Each opened device is logged by name (`evdev: opened 'AT Translated
  Set 2 keyboard' (/dev/input/event3)`).
- **Check the log for your keyboard.** Budgie can't tell a drawing tablet's button pad from your
  keyboard — both are "devices with letter keys". If some devices are unreadable, it says so in the
  log and in a notification, listing which ones it *can* read. If your keyboard isn't listed, you
  need the group above.
- Keyboards plugged in or removed while running are picked up within ~2 s.
- Key names: letters, digits, `ctrl`, `right_ctrl`, `alt`, `shift`, `super`, `space`, `tab`, `esc`,
  `enter`, `pause`, `scroll_lock`, `f1`–`f12`. Left/right variants of `ctrl`/`alt`/`shift`/`super`
  both match; `right_ctrl` matches the right one only. These names work for pynput too.

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
│   ├── hotkey.py                  # pynput GlobalHotKey listener
│   ├── evdev_hotkey.py            # evdev listener (X11 + Wayland, hold-to-talk)
│   └── llm.py                     # optional LM Studio/Ollama client: polish + command mode (stdlib HTTP)
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
│   ├── language_cleanup.py        # "gonna" → "going to"; optional filler removal
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
│   ├── window.py                  # QDialog settings UI (sidebar + pages)
│   ├── overview.py                # Overview dashboard: readiness, totals, chart, health, recent
│   └── history.py                 # Dictation History page (filters, paging, details)
├── analytics/                     # local dictation history; plain Python, no Qt
│   ├── store.py                   # SQLite file (history.db), failure-isolated
│   ├── recorder.py                # state changes → one metadata row per dictation
│   ├── stats.py                   # totals / per-day sums, computed live
│   └── sessions.py                # filtered, sorted, keyset-paged session list
├── ui/
│   ├── widgets.py                 # cards, status rows, switch, readiness chip
│   ├── chart.py                   # native-Qt daily bar chart
│   ├── style.py, icons.py         # scoped stylesheet, Lucide icons
│   ├── theme.py                   # system / light / dark
│   └── status.py                  # runtime facts for the Overview
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
| `~/.local/share/budgie-voice/history.db` | Dictation history: one metadata row per dictation (times, counts, result), never text or audio. Owner-only (`0600`) |
| `~/.local/share/budgie-voice/models/` | Speech models (Whisper weights, `parakeet/<model>-<precision>/`). Downloaded once, with a notification; loading is offline afterwards |
| `~/.local/share/budgie-voice/speechbrain/` | ECAPA-TDNN speaker model |
| `~/.local/share/budgie-voice/torch_hub/` | Silero VAD model |
