# Myna v2 — Phase Prompts

Paste the **Standing rules** block once at the start of a session, then one phase prompt at a time.
Wait for `✅ Phase <n> complete`, review the diff, run the manual check, then send the next phase.

Decisions already made (do not reopen):

- Hotkey: add an evdev listener so toggle and hold-to-talk work on X11 and Wayland.
- Injection: keep keystroke typing (xdotool / ydotool). No clipboard paste.
- Preview popup: stays on by default.
- LLM features: optional final phase, off by default, through a local Ollama endpoint.
- The in-process inference server and the three-layer architecture stay as they are.

---

## Standing rules (paste once per session)

```
You are working in the Myna voice dictation repo (Python 3.12, Linux, local-only speech to text).

Before any change:
1. Read CLAUDE.md and follow it.
2. Load the myna-architecture skill and respect the layer boundaries: Inference Backend
   decides segmentation and transcription, Speech Orchestrator is policy only (no audio
   processing), User Interaction owns hotkey, tray, widgets and injection.
3. Read every file the phase touches, and grep all callers of any function you change.

Hard constraints, every phase:
- Audio never touches disk. Transcript text is never logged at INFO or above (DEBUG only).
- No new dependency unless the phase names it.
- Strict YAGNI. Reuse existing helpers. Delete rather than add where possible.
- Do the named phase only. If you find a problem outside its scope, list it at the end,
  do not fix it.
- Before writing files, show me the minimal plan (files, functions, protocol or config
  changes) and wait for my OK.
- Where a phase says "Ask first", stop and ask me. Do not guess.
- Each behavioural fix gets one small pytest test that fails without the fix. No fixtures
  frameworks, no per-function suites. The existing 82 tests must stay green:
  .venv/bin/python -m pytest -q
- You cannot speak into the microphone. For anything needing real audio or a real desktop,
  give me an exact manual check (what to do, which log line to expect in
  ~/.local/share/myna/logs/myna.log) instead of claiming it works.
- My machine: X11 session, CPU only, Bluetooth mSBC headset, Parakeet backend
  (nemo-parakeet-tdt-0.6b-v3), toggle mode, denoise off. Do not break that setup.

Finish each phase with: what changed (file list), test result, manual check steps,
out-of-scope findings, then the line "✅ Phase <n> complete".
```

---

## Phase 1 — Session reliability (no lost text, no stuck sessions)

```
Phase 1 only. Make a dictation session impossible to lose or wedge.

Problems to fix (verify each in the code first, tell me if any is not real):
a. Stopping sends no end-of-speech. DictationService._stop_session cancels the send task,
   sleeps 0.4 s and reads self._pending. If the last VAD turn was still open, it is never
   transcribed, stays in the server's per-connection frame_queue (inference/server.py
   _handle_ws) and is prepended to the NEXT session. A turn that takes longer than 0.4 s
   to transcribe is dropped.
b. Cancel leaves the same stale audio in the server queue.
c. Any exception in _start_session (after to_starting) or in _stop_session (after
   to_finalizing) leaves the state machine in STARTING or FINALIZING forever, because
   toggle() then calls _stop_session, which raises MynaSessionError and returns.
d. Coroutines dispatched with asyncio.run_coroutine_threadsafe (hotkey, DBus, tray, cancel
   button) never have their result checked, so their exceptions vanish without a log line.
e. State callbacks fire only at RECORDING and at the final IDLE. FINALIZING, COMPLETED,
   CANCELLED and ERROR never reach the tray or the recorder overlay, so the overlay shows
   "recording" while transcription and the preview are running.
f. The capture queue is not drained between sessions; leftover chunks from the previous
   session are consumed at the start of the next.
g. dictation_service.py logs transcript text at INFO ("Injecting", "Segment accepted",
   "Segment rejected"). That violates the logging rule in utils/logging_setup.py.

Deliver:
- Protocol: two new client-to-server messages on /ws/transcribe and one reply.
  "flush" (with an id): server closes the open turn if there is one, waits for its
  transcription, sends any segments, then replies "flushed" with the same id.
  "reset": server discards the open turn without transcribing.
  Bump the protocol version and update inference/protocol.py docs and the client.
- _stop_session: send flush, await the matching "flushed" with a timeout taken from a new
  setting inference.finalize_timeout_s (default 15), then read segments. Remove the fixed
  sleep. On timeout: notify the user, go to ERROR then IDLE, inject nothing.
- _cancel_session: send reset.
- Wrap start and stop so that any failure closes the adapter, logs the exception with
  traceback, shows a desktop notification, and returns the session to IDLE through ERROR.
  Add the missing to_error transition to DictationSession if needed.
- One shared helper for run_coroutine_threadsafe that attaches a done-callback logging
  exceptions; use it at every existing call site.
- Fire the state callback on every transition. Simplest correct place wins; tell me where
  you put it and why.
- Drain the capture queue when a session opens.
- Move transcript text in log calls to DEBUG; keep confidence, length and timing at INFO.

Do not change VAD, segmentation policy, injection or settings UI in this phase.

Tests (minimum): stop with an open turn yields its text; a second session does not
receive the first session's audio; adapter.open raising leaves state IDLE and a later
toggle starts normally; injector raising leaves state IDLE; cancel then start is clean.

Manual check for me: dictate one sentence and press the hotkey the instant I stop
talking, five times in a row. Every sentence must appear whole, none must leak into the
next. Tray and overlay must show transcribing, then completed.

Done when: tests green, and the 0.4 s sleep is gone.
```

---

## Phase 2 — Audio front end (VAD correctness, no UI freezes)

```
Phase 2 only. Fix the capture and VAD chain.

Problems to fix (verify first):
a. Chunks are 30 ms = 480 samples, Silero needs 512. VADFilter.is_speech zero-pads every
   chunk, which feeds the stateful model discontinuous audio.
b. No pre-roll: everything before the first chunk that crosses the threshold is dropped,
   clipping word onsets.
c. The Silero model's internal state is not reset between sessions.
d. AudioAdapter.open() runs on the Qt/asyncio loop: pactl calls (up to several seconds on
   Bluetooth) and the first Silero load (torch.hub, may hit the network). VAD inference
   and denoise also run on that loop for every chunk.
e. noisereduce runs spectral gating on isolated 30 ms chunks. On my headset it reduced
   real speech to silence, so it is already off in my config.
f. Bluetooth mic gain has no config knob; I re-apply it by hand with
   pactl set-source-volume.

Deliver:
- VADFilter re-buffers internally to exact 512-sample windows regardless of chunk size.
  No padding inside a stream.
- Pre-roll ring buffer, new setting vad.pre_roll_ms (default 300), emitted ahead of the
  first speech chunk of each turn.
- Reset model state when a session opens.
- Load the VAD model at application startup, in an executor, not on first hotkey press.
- Run the blocking parts of adapter.open() (and close, if it blocks) in an executor.
  Keep the open cue ordering comment in adapter.py true: cue before the BT profile switch.
- Remove noisereduce: DenoiseFilter, the audio.denoise setting, the requirement, the
  settings UI control, README and config.yaml.example entries. An existing config that
  still has audio.denoise must load without error (the unknown-key warning is fine).
  Check myna/speaker/enroller.py: enrollment must run through the same processing as
  runtime audio. Tell me whether I need to re-enroll.
- New setting audio.input_gain_pct (default null = leave alone). When set and the device
  is a "bt:" card, apply it with pactl set-source-volume once, where the BT source is
  pinned. Clamp to 50-150 and log the value.
- Keep the "Session audio stats" log line and add the pre-roll and window counts.

Ask first: whether to raise the default vad.speech_pad_ms. Phase 3 changes how turns are
closed, so propose a value only if it still matters after reading Phase 3.

Tests: VAD called with 480-sample chunks sees only 512-sample windows (use a fake model);
pre-roll audio precedes the first speech frame; state reset is called on open.

Manual check for me: say a sentence starting with a soft consonant ("fifteen", "should")
ten times; first word must not be clipped. Press the hotkey: the overlay must appear
without the tray freezing. Confirm "Session audio stats" peak stays under 1.0.

Done when: tests green, noisereduce no longer imported anywhere.
```

---

## Phase 3 — Transcript assembly and personal dictionary

```
Phase 3 only. Stop producing fragments like "I think. We should go." and replace the
frequency-based hotwords with something useful.

Problems to fix (verify first):
a. VAD closes a turn after 300 ms of silence. Each turn is transcribed alone and each
   result is post-processed alone (_stop_session runs pipeline.run per segment, then
   joins with a space), so a mid-sentence pause gives a period and a capital in the
   middle of a sentence.
b. PersonalVocab feeds the 50 most frequent 5+ letter words as hotwords. Those are common
   words, they are learned from unverified ASR output, and Parakeet ignores hotwords.
c. PostProcessingPipeline's docstring says Whisper output is lowercase. It is not.
d. FasterWhisperBackend.capabilities reports supports_streaming=True but yields no
   partial results.

Deliver:
- Segmentation stays in the inference layer. In the server, treat the adapter's
  is_end_of_speech as a hint: close the current turn on a hint only when the turn already
  holds at least inference.min_turn_s seconds of audio (new setting, default 15). A flush
  always closes it. Short dictations are therefore transcribed in one pass at stop; long
  ones still stream turn by turn.
- In DictationService, join accepted segment texts first, then run the post-processing
  pipeline once on the joined text.
- User dictionary: plain text file ~/.config/myna/dictionary.txt, one term per line,
  edited by hand (add an "Open dictionary" tray entry next to the existing log entry if
  that is one line of reuse; otherwise skip). Hotwords sent to the backend = dictionary
  terms plus the corrected words already stored by CorrectionMemory (those were confirmed
  by me in the preview). Delete PersonalVocab, its settings and its file handling.
- Fix the two wrong claims (c, d).

Ask first: for backends without confidence (Parakeet returns a fixed 0.9), should the
low-confidence path be skipped entirely instead of compared against a placeholder?

Tests: three segments "i think" / "we should go" / "tomorrow" produce one sentence with
one capital and no inner period after the pipeline; server keeps a turn open across a
hint below min_turn_s and closes it above; hotwords contain dictionary terms and
correction targets only.

Manual check for me: dictate a 10 second sentence with two deliberate one-second pauses.
Output must read as one sentence. Then dictate about 40 seconds; text must still arrive
and stop-to-text delay must be reported from the log.

Done when: tests green, vocab.py removed.
```

---

## Phase 4 — Injection hardening and transcript safety net

```
Phase 4 only. Typing stays the injection method; make it reliable for any length.

Problems to fix (verify first):
a. XdotoolBackend and YdotoolBackend use a fixed 10 s subprocess timeout. At 12 ms per
   character that is roughly 800 characters; beyond that the process is killed mid-text.
b. Injection return codes are ignored (check=False, output discarded), so a failed
   injection looks like success.
c. Punctuation commands emit curly quotes and an em dash. Check whether the installed
   ydotool can type non-ASCII at all; report what you find before changing anything.
d. SecureFieldDetector blocks any window whose title contains "password", "sudo",
   "secret", "unlock" and similar, which hits browser tabs, terminals and editors. On
   Wayland the check cannot see native windows and silently passes.
e. If injection fails or lands in the wrong place, the text is gone.

Deliver:
- Type in bounded pieces (split on whitespace, about 200 characters each) with a timeout
  derived from piece length and delay_ms plus a margin. Stop on the first failed piece.
- Treat a non-zero exit as failure: raise MynaInjectionError with stderr, which Phase 1
  turns into a notification and a clean return to IDLE.
- Lower the default injection.delay_ms only if you can show on X11 that characters are
  not dropped; otherwise leave it and say so.
- ydotool non-ASCII: if it cannot type them, map the handful of symbols the pipeline can
  emit to ASCII inside the ydotool backend only. No general transliteration.
- Secure fields: keep the WM_CLASS blocklist. Apply the title patterns only to dialog
  windows (_NET_WM_WINDOW_TYPE_DIALOG). On Wayland log once at startup that secure-field
  detection is unavailable.
- Last transcript: keep the most recent final text in memory only (never on disk, cleared
  on exit) and add a tray action "Copy last transcript" using Qt's clipboard. On injection
  failure the notification tells me the text can be recovered from the tray.

Tests: a 3000-character text is typed in several calls and none exceeds its timeout
(mock subprocess); non-zero exit raises; a normal window titled "Reset password - docs"
is not blocked while a dialog with that title is; last transcript is replaced per session.

Manual check for me: dictate about 2 minutes into a text editor; full text must arrive.
Kill xdotool mid-typing once; I must get a notification and be able to copy the text
from the tray, and the next dictation must work.

Done when: tests green.
```

---

## Phase 5 — Settings, reload, install, docs

```
Phase 5 only. Make configuration and installation trustworthy.

Problems to fix (verify first):
a. settings_ui/window.py offers backends "nemotron" and "qwen3_asr", which are not in
   inference/backends/registry.py. Selecting one makes get_backend raise KeyError at
   startup, and systemd restarts the service in a loop.
b. The model list mixes Whisper and Parakeet names regardless of the chosen backend.
c. Changing inference.backend through Reload calls load() on the old backend instance
   (InferenceServer.reload_backend), so the switch fails.
d. main._reload_settings propagates only audio and inference settings. Pipeline,
   injection, speaker, VAD, activation and indicator changes silently need a restart.
   It also runs on the DBus/GLib thread and reaches into private attributes.
e. scripts/install_service.sh hardcodes DISPLAY=:0 and an unused IBUS variable.
   scripts/install_deps.sh has no shebang, installs ibus packages nothing uses and does
   not install ydotool.
f. README defaults (model, confidence_threshold, beam_size, speaker.threshold) differ
   from config/settings.py. README also describes tray emoji that were replaced by SVG
   icons.
g. The DBus name com.canonical.Myna uses a namespace that is not ours.

Deliver:
- Settings UI builds the backend list from available_backends() and the model list from
  the selected backend's capabilities.
- Unknown backend or model at startup: log an error, notify, fall back to the default
  backend. Never crash-loop.
- Reload: propose the minimal design first. My preferred option is that saving settings
  triggers the existing graceful restart path (the one behind the tray's Restart action)
  and the partial hot-reload code is deleted. Tell me the restart cost you measure and
  any reason this is wrong before implementing.
- Service unit: no hardcoded DISPLAY; rely on the session environment imported into the
  systemd user manager, and say in the script output what to do if it is missing.
- install_deps.sh: shebang, drop unused packages, add ydotool, print (do not run) the
  group or udev step needed for /dev/uinput.
- README and config.yaml.example match settings.py exactly. Add a test that loads
  config.yaml.example and asserts every key exists in the Settings dataclasses.

Ask first: the new DBus name. Renaming breaks my existing GNOME shortcut and
settings_ui's Reload call, so list every place that must change and wait for my choice
(or my decision to keep the current name).

Tests: UI backend list equals the registry; unknown backend falls back; example config
has no unknown keys.

Done when: tests green, and starting with inference.backend set to a bogus value leaves
a running app and a clear notification.
```

---

## Phase 6 — evdev hotkey (toggle and hold-to-talk on X11 and Wayland)

```
Phase 6 only. Add a hotkey source that works on both session types.

Context: pynput sees global keys on X11 only. Wayland currently needs a desktop shortcut
calling the DBus Toggle method, which has no key-release event, so hold-to-talk is
impossible there. activation.trigger currently accepts auto | pynput | dbus, and
DictationService.toggle(source) lets only the owning source through.

Deliver:
- New dependency: evdev (python-evdev). Nothing else.
- New trigger value "evdev": a listener that reads key events from every keyboard device
  under /dev/input, supports both activation modes, and dispatches through the same
  service calls and ownership check as pynput (source name "evdev").
- Handle keyboards being plugged in or removed while running.
- Do NOT grab devices (no EVIOCGRAB, no uinput re-emit): a hang must never be able to
  take my keyboard away. The hotkey will therefore still reach the focused application,
  exactly as with pynput today. Document that in the README and recommend a key that
  applications ignore.
- Reuse the hotkey string format of activation.hotkey; extend the key-name map so
  function keys beyond F4 and keys such as pause, scroll_lock, right_ctrl work for both
  listeners.
- auto resolution: X11 keeps pynput. Wayland uses evdev when at least one keyboard device
  is readable, otherwise dbus. Log which one was chosen and why.
- No readable device with trigger "evdev": clear error in the log and a notification
  naming the fix (input group membership or a udev rule). Do not change group membership
  from code or scripts; print the command.
- The listener never logs or stores key codes other than its own hotkey match.

Ask first: the default hotkey for evdev mode, since ctrl+space collides with editor
autocomplete and input-method switching. Give me three candidates with trade-offs.

Tests: with fake devices, a press/release sequence produces push_start then push_stop in
hold mode; one toggle per press in toggle mode despite auto-repeat events; modifier
left/right variants match; a device disappearing does not stop the listener.

Manual check for me: set trigger: evdev and mode: push_to_hold on X11, hold, speak,
release. Then the same in a Wayland session if I have one available.

Done when: tests green, and trigger: auto on my X11 machine still selects pynput.
```

---

## Phase 7 — Snippets, spoken edits, per-app profiles, small conveniences

```
Phase 7 only. Rule-based features, no model involved.

Deliver, each behind its own setting, each off or empty by default unless stated:
- Snippets: postprocessing.snippets, a map of spoken phrase to replacement text
  ("my email" -> an address). Whole-phrase, case-insensitive matches, longest first,
  applied before punctuation commands. Reuse the matching approach already in
  postprocessing/punctuation.py rather than writing a second engine.
- Filler removal: LanguageCleanup already implements strip_fillers but nothing can turn
  it on. Expose it as postprocessing.strip_fillers.
- "scratch that": removes the sentence before it in the same dictation. Exact phrase
  only, as a pipeline step with its own toggle postprocessing.spoken_edits (default on).
- Per-app profiles: a profiles list, each entry a WM_CLASS regex plus overrides for
  existing postprocessing toggles and one new one, strip_trailing_period. Resolved once
  at session start from the target window. Reuse the xprop helper in injection/secure.py.
  X11 only; on Wayland the default profile applies, logged once. Ship two commented
  examples in config.yaml.example: terminals and code editors with capitalisation and
  trailing period off.
- Pause media while dictating: activation.pause_media (default off). If playerctl exists
  and something is playing at session start, pause it and resume at session end. Missing
  playerctl is a silent no-op.
- Language auto-detect: inference.language "auto" passes no language to backends that
  support detection; backends that do not log it once and use their default.

Keep each feature to the smallest code that works. If any of these needs more than one
new module, stop and tell me why first.

Tests: one per feature, including "my period is late" still not becoming a full stop,
and a snippet whose text contains a punctuation command word.

Done when: tests green, README documents each setting in the existing table style.
```

---

## Phase 8 — Optional local LLM: polish and command mode (Ollama)

```
Phase 8 only. Optional, off by default, never required for dictation to work.

Constraints specific to this phase:
- No new Python dependency. Talk to Ollama over HTTP with the standard library.
- Endpoint from a new settings section llm: enabled (false), url
  (http://127.0.0.1:11434), model (no default; must be set by me), timeout_s (8).
- Refuse to start the feature if the url host is not loopback, unless
  llm.allow_remote is explicitly true. Dictated text must not leave the machine by
  accident. Say so in the README.
- Never block the event loop; run requests in an executor.
- Any failure (Ollama not running, timeout, empty or oversized answer) falls back to the
  un-polished text and logs one line. Dictation must not get slower when llm.enabled is
  false: no import cost, no network call.
- Prompts and responses are transcript text: DEBUG logging only.

Deliver:
- Polish: after rule-based post-processing and before the preview, send the text with a
  fixed instruction to remove fillers and false starts, apply spoken self-corrections,
  and fix punctuation, while changing no wording otherwise. Reject the answer and keep
  the original if its length differs from the input by more than a set ratio. The
  preview shows the polished text; CorrectionMemory keeps learning from my edits against
  the raw ASR text, not the polished text.
- Command mode: a new DBus method and a second hotkey setting. It reads the current
  primary selection, records a spoken instruction through the normal session path, asks
  the model for the rewritten text, shows it in the existing preview, and types it over
  the selection on accept. If nothing is selected, notify and do nothing.
- Tell me which layer each piece lives in according to the architecture skill before
  writing it.

Ask first: which Ollama model I have pulled, and whether polish should run for every
dictation or only above a minimum word count (short phrases gain nothing and pay the
latency).

Tests: with a fake HTTP endpoint: polished text is used; timeout falls back; a wildly
longer answer is rejected; non-loopback url is refused; with llm.enabled false the client
is never constructed.

Manual check for me: time stop-to-preview with polish off and on for a 15 second
dictation and report both numbers from the log.

Done when: tests green, and with Ollama stopped dictation behaves exactly as in Phase 7.
```
