---
name: myna-architecture
description: Architectural rules for the Myna voice dictation app (this repo) — the three-layer design (Inference Backend / Speech Orchestrator / User Interaction), which module owns what, and the fixed technical decisions (protocol versioning, audio format, injection backend chain, zero-disk-audio, logging redaction). Consult this before adding or moving code anywhere under myna/ — new features, new backends, new injection targets, changes to session/audio/inference flow, config schema changes, or logging changes. Also use when reviewing a diff in this repo to check it doesn't violate a layer boundary (e.g. orchestrator doing audio processing, or segmentation logic in the orchestrator instead of the inference backend).
---

# Myna Architecture

Myna is a Linux voice-dictation app with three layers. The layering is
deliberate — it lets the inference backend (Faster-Whisper today; Parakeet /
NemoTron / Qwen3 later) and the injection target (X11 today; Wayland later)
both be swapped without touching orchestration logic. When adding or
reviewing code, first work out **which layer it belongs to**, then check it
against that layer's rules below. Violating a boundary (e.g. the orchestrator
doing its own VAD, or an injection backend deciding when a segment is final)
is the most common way this codebase drifts from its design.

## The three layers → real module paths

```
myna/inference/          INFERENCE BACKEND (owns recognition + segmentation)
  backends/base.py         backend ABC, versioned interface
  backends/faster_whisper.py, registry.py   swappable backends
  protocol.py               versioned wire messages (Control + Data channel)
  client.py, server.py      WebSocket client / FastAPI+WS server

myna/orchestrator/       SPEECH ORCHESTRATOR — policy only
  dictation_service.py     session coordinator (NO audio/inference logic)
  hotkey.py, session.py    activation + session state machine
myna/audio/               Audio Adapter (owns ALL audio processing)
  capture.py → denoiser.py → vad.py → adapter.py (frames out)
myna/postprocessing/       Text Finalization
  normalizer.py → capitalizer.py → punctuation.py → language_cleanup.py → pipeline.py
myna/injection/            Text Injection Layer (abstract)
  base.py (TextOutputBackend ABC) → selector.py → ibus.py / xdotool.py / xdg.py / wayland.py

myna/settings_ui/, myna/indicator/, myna/dbus/   USER INTERACTION
  window.py (Settings UI) · tray.py (Activity Indicator) · service.py (DBus, com.canonical.Myna)
```

## Non-negotiable rules (check every diff against these)

1. **`dictation_service.py` stays policy-only and model-agnostic.**
   It coordinates `AudioAdapter`, `InferenceClient`, `PostProcessingPipeline`,
   and the injector — it must never touch raw audio samples, call a model
   directly, or contain backend-specific logic. If you find yourself adding
   `import` of a specific ASR library or numpy DSP code here, it belongs in
   `myna/audio/` or `myna/inference/backends/` instead.

2. **`myna/audio/` owns the entire processing chain**: device open → denoise
   → volume normalization → VAD (Silero) → pcm16/float32 framing into
   `AudioFrame` (see `inference/protocol.py`). Nothing outside this package
   should read raw samples off the mic.

3. **Segmentation belongs to the inference backend, not the orchestrator.**
   `AudioFrame.is_end_of_speech` is only a *hint* from the Audio Adapter's
   VAD; the backend decides `TranscriptSegment.is_final` independently. Don't
   add orchestrator-side logic that finalizes a segment based on silence
   timing — that decision has to come back down the Data Channel.

4. **Two channels, two message shapes** (`myna/inference/protocol.py`):
   - Control Channel — `ControlMessage` / `CapabilitiesResponse` (config,
     capability negotiation). HTTP.
   - Data Channel — `AudioFrame` UP, `TranscriptSegment` DOWN. WebSocket,
     bidirectional, streaming.
   Don't blend the two (e.g. don't send audio frames over the control path,
   don't put capability info in a transcript message).

5. **Text injection is abstract.** Every backend implements
   `TextOutputBackend` (`myna/injection/base.py`) and declares
   `interface_version`. Target window is locked once in `start_session()` and
   never changes mid-session. `selector.py` does auto-probing
   (IBus → xdotool); explicit backend names bypass probing. New backends
   (Wayland input-method, xdg) go in their own module and get registered in
   `selector.py` — never special-cased inside `dictation_service.py`.

6. **Every wire/backend interface is versioned.** `ControlMessage.version`,
   `TextOutputBackend.interface_version`, backend registry entries — when you
   change a message shape or ABC method signature, bump the version and keep
   the old one negotiable if backends may be on different versions.

7. **Zero disk writes of audio, ever.** Audio lives in a bounded in-memory
   rolling buffer and is discarded at session end. If you're adding
   buffering, caching, or debug-dump code for audio, it must stay in memory
   and never hit `~/.local/share/myna/` or any tempfile.

8. **Logging must never include raw audio or full transcript text at INFO+.**
   Rotating log at `~/.local/share/myna/logs/myna.log` (see
   `myna/utils/logging_setup.py`). Transcript content, if logged at all, is
   DEBUG-only and should be truncated/redacted — treat it like a credential.

9. **Low-confidence handling is a UI concern, not a silent drop.** Route
   through the Activity Indicator (`myna/indicator/tray.py`) → WARN state +
   `notify-send` ("⚠️ Couldn't hear you — repeat?") → discard. Don't just
   swallow low-confidence segments in the orchestrator without surfacing
   them.

10. **IPC to the Settings UI is DBus only**, well-known name
    `com.canonical.Myna` (`myna/dbus/service.py`). Don't add a second IPC
    mechanism (sockets, files) for UI ↔ service communication.

## Fixed technical decisions (don't relitigate without asking)

| Concern | Decision |
|---|---|
| Python | 3.12 |
| Audio format | pcm16 (int16) primary; float32 accepted |
| Audio stack | sounddevice → Pipewire/PulseAudio via ALSA |
| VAD | Silero VAD, inside Audio Adapter only |
| Speaker verification | SpeechBrain ECAPA-TDNN; PyQt6 enrollment wizard |
| Injection order | IBus primary → xdotool (X11 fallback) → xdg/Wayland (future) |
| Activation | Toggle hotkey (ON/OFF) and push-to-hold, both YAML-configurable |
| Config | `~/.config/myna/config.yaml` (`myna/config/settings.py`) |
| Packaging | Systemd user service |
| Display target | X11 first; Wayland-abstracted from day one (don't hardcode X11 assumptions outside `injection/xdotool.py`) |

## When adding a new feature

1. Identify the layer (table above), then check which of the 10 rules
   constrains it.
2. If it needs a new inference backend: implement against
   `myna/inference/backends/base.py`, register in `registry.py`, keep the
   protocol messages (`protocol.py`) unchanged unless the capability genuinely
   doesn't fit — then version-bump.
3. If it needs a new injection target: implement `TextOutputBackend`, wire
   into `selector.py`'s probing order, don't touch `dictation_service.py`
   beyond passing config through.
4. If it touches audio processing: stays inside `myna/audio/`, exposed only
   as `AudioFrame` objects to the rest of the system.
5. If it's a UI/config surface: Settings UI talks to the Dictation Service
   only over DBus; no direct imports of orchestrator internals from
   `myna/settings_ui/`.

For the full original design rationale (diagrams, IPC details, model
runtime notes), see `myna_master_prompt.md` and `Speech.md` at the repo root
— this skill is the condensed, enforceable checklist version of those.
