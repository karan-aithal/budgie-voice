# Budgie v2 — Phase Prompts

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
You are working in the Budgie voice dictation repo (Python 3.12, Linux, local-only speech to text).

Before any change:
1. Read CLAUDE.md and follow it.
2. Load the budgie-voice-architecture skill and respect the layer boundaries: Inference Backend
   decides segmentation and transcription, Speech Orchestrator is policy only (no audio
   processing), User Interaction owns hotkey, tray, widgets and injection.
3. Read every file the phase touches, and grep all callers of any function you change.
4. Verify each listed problem in the code first. If it is already fixed, say so in one line
   and skip it; never rebuild finished work.

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
  frameworks, no per-function suites. The existing 118 tests (current count) must stay green:
  .venv/bin/python -m pytest -q
- You cannot speak into the microphone. For anything needing real audio or a real desktop,
  give me an exact manual check (what to do, which log line to expect in
  ~/.local/share/budgie-voice/logs/budgie-voice.log) instead of claiming it works.
- My machine: X11 session, CPU only, Bluetooth mSBC headset, Parakeet backend
  (nemo-parakeet-tdt-0.6b-v3), toggle mode, denoise off. Do not break that setup.

Finish each phase with: what changed (file list), test result, manual check steps,
out-of-scope findings, then the line "✅ Phase <n> complete".
```

---

## Phase 1 — Session reliability (already built: verify only)

```
Phase 1 only. This was implemented earlier; do NOT rebuild it. Verify each item in the code
and tests/test_session_reliability.py, one line each, and fix only what is missing:
a. flush/reset messages on /ws/transcribe, protocol version 2, client waits for "flushed"
   within inference.finalize_timeout_s; no fixed sleep in _stop_session.
b. cancel sends reset; a second session never receives the first session's audio.
c. A failure in start or stop returns the session to IDLE through ERROR, with a traceback in
   the log and a notification.
d. utils/aio.dispatch is used at every run_coroutine_threadsafe call site.
e. The state callback fires on every transition (tray and overlay show transcribing and
   completed).
f. The capture queue is drained when a session opens.
g. No transcript text in log calls at INFO or above.

One new item, check it first and tell me what happens today: if the microphone stream dies
after audio was already accepted, the accepted audio must still be flushed and transcribed,
then the user is told audio was lost. Fix only if it currently drops the text.

Done when: the checklist is reported, tests green, "✅ Phase 1 complete".
```

---

## Phase 2 — Finish audio front end (denoise opt-in)

```
Phase 2 only. Most of the audio front end is already built; finish it.

Verify first, one line each, and do NOT redo any that is already true:
a. VADFilter re-buffers to exact 512-sample windows (audio/vad.py).
b. Pre-roll exists as vad.pre_roll_ms (default 300).
c. VADFilter.reset() resets the Silero state; AudioAdapter.open() calls it via load().
d. adapter.preload() runs in an executor at startup; adapter.open() and close() run in
   an executor from DictationService.
f. audio.input_gain_pct is clamped to 50-150 and applied once where the BT source is pinned
   (audio/capture.py). The "Session audio stats" line has windows and preroll_chunks.
If any of these is NOT true, tell me and fix only that item.

Still to do:
1. noisereduce stays, but opt-in: AudioSettings.denoise defaults to False (it gated isolated
   30 ms chunks and turned real speech into silence). Add one checkbox in the settings UI
   audio group, saved to audio.denoise. README and config.yaml.example show the new default
   and the warning. Do not delete denoiser.py or the requirement.
2. Enrollment (speaker/_workers.py) uses the same audio.denoise setting as runtime, so
   enroll and runtime always match. Say so in the checkbox label: re-enroll after changing.
   My config already had denoise off, so I do not need to re-enroll.
3. Conditional: time VADFilter.process per chunk. Measured p95 0.28 ms, so leave VAD on the
   event loop. Move it only if a later measurement exceeds about 5 ms.

Do not change speech_pad_ms (stays 300) unless the manual check shows clipped word endings.

Tests: AudioSettings().denoise is False; existing audio tests stay green.

Manual check for me: say a sentence starting with a soft consonant ("fifteen", "should")
ten times; the first word must not be clipped. Confirm "Session audio stats" peak stays
under 1.0 and the log has no DenoiseFilter line.

Done when: tests green, "✅ Phase 2 complete".
```

---

## Phase 3 — Transcript assembly: remaining cleanup

```
Phase 3 only. Turn assembly is already done (min_turn_s, one pipeline pass over the joined
text, dictionary.txt hotwords, corrected capability claims). Verify that quickly against
tests/test_transcript_assembly.py, then do only the leftovers:

1. Delete PersonalVocab: orchestrator/vocab.py, learning.vocab_enabled, learning.vocab_path,
   its construction and use in main.py and DictationService (_refresh_hotwords still appends
   vocab words), README and config.yaml.example rows. A config that still has the old keys
   must load (unknown-key warning is fine). Tell me whether the file
   ~/.config/budgie-voice/vocab.json should be deleted by me; do not delete it yourself.
2. Hotwords sent to the backend = dictionary terms + CorrectionMemory targets, de-duplicated,
   capped at 100 entries of at most 64 characters each. Drop the rest and log the count once
   at DEBUG.

Ask first: for backends without a real confidence (Parakeet returns a fixed 0.9), should
the confidence_threshold check be skipped for them (e.g. a capability flag) instead of
comparing against a placeholder?

Tests: hotwords contain only dictionary terms and correction targets; a 150-term dictionary
yields 100; an old config with vocab keys loads.

Done when: tests green, `grep -ri "vocab" budgie_voice` finds nothing except dictionary or
corrections code, "✅ Phase 3 complete".
```

---

## Phase 4 — Injection hardening (already built: verify only)

```
Phase 4 only. This was implemented earlier; do NOT rebuild it. Verify each item in the code
and tests/test_injection_hardening.py, one line each, and fix only what is missing:
a. Typing is split into bounded pieces (injection/base.py chunk_text) with a timeout derived
   from piece length and delay_ms; it stops at the first failed piece.
b. A non-zero exit raises BudgieInjectionError carrying stderr but never the typed text.
c. ydotool non-ASCII symbols are mapped to ASCII inside the ydotool backend only.
d. Title patterns apply to dialog windows only; the WM_CLASS blocklist is unchanged;
   Wayland logs once that secure-field detection is unavailable.
e. The last transcript lives in memory only and the tray's "Copy last transcript" works;
   the failure notification points to it.

Done when: the checklist is reported, tests green, "✅ Phase 4 complete".
```

---

## Phase 5 — Settings, reload, install, docs: remaining items

```
Phase 5 only. Mostly implemented (backend list from the registry, unknown-backend fallback,
service unit without DISPLAY, install_deps.sh fixes, example-config test). Verify against
tests/test_phase5.py, then do only the leftovers:

1. README and config.yaml.example must match config/settings.py exactly: defaults, every
   key, no audio.denoise row after Phase 2, tray icons described as SVG. Extend the existing
   example-config test to also assert that every README settings-table key exists in the
   Settings dataclasses.
2. Reload design differs from what was first asked: the code keeps partial hot-reload
   (swap_backend, hotkey apply, a "restart needed for X" message) instead of restart on
   save. Do not change it. Tell me which settings still need a restart, and whether the
   graceful restart path would be simpler. I decide.
3. Starting with inference.backend set to a bogus value must leave a running app and a
   notification. Confirm with a test or a manual log line.

Ask first: the DBus name io.gitlab.karanaithal841.BudgieVoice. List every place that
references it (dbus/service.py, main.py, settings_ui/window.py, scripts/*.sh, README) and
wait for my choice to rename or keep it.

Done when: tests green, "✅ Phase 5 complete".
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

---

## Phase 9 — Optional: progressive commit (type while speaking)

```
Phase 9 only. Optional; ask me before starting because it changes the default feel.

Model (from canonical/myna specs 007 and 008): committed text is append-only and never
retracted; unstable partials are never injected. Today Budgie transcribes long dictations
turn by turn but injects only at stop.

Deliver:
- New setting injection.progressive (default false). When true and the preview popup is
  off, each turn the server closes is typed as soon as it is transcribed; stop commits and
  types the rest. When the preview is on, behaviour is unchanged.
- Committed segments are typed in order, once, with a single space join; the final text is
  the concatenation of what was typed. Never edit text already typed.
- Cancel after some text was typed: tell me, do not try to delete it.
- Reuse the Phase 1 flush path and the Phase 4 bounded typing. No new protocol message
  unless you can show one is needed.

Ask first: whether progressive mode should also bypass the preview popup for confirmed
speakers only, and how corrections (CorrectionMemory) should behave when text is already
typed.

Tests: segments typed in order exactly once; stop types only the remainder; cancel leaves
state clean; progressive off = Phase 8 behaviour.

Manual check for me: dictate 40 seconds with preview off and progressive on; text must
appear during speech without duplicates.

Done when: tests green.
```
