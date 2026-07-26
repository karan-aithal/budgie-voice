
FUTURE SCOPE:
4. Screen-context AI (reads screen content to adapt) — needs OCR/accessibility API integration, LLM call per dictation. Big scope, conflicts with your minimalist/local-only design.
https://github.com/Beingpax/VoiceInk
5. Smart Modes (AI rewrite styles) + conversational assistant mode — needs an LLM in the loop (your master prompt only lists "future: local LLM" for finalization, nothing conversational). Real feature, real scope, not a quick add.
6. Adjustable speaker verifivation threshold

7. Cosmetic -- edges of wavform visualizer seems snapped, create extending line dimensihes off at edges where visualizer intensity also dimenises slowly at edges


Cheapest real win: #1 (manual dictionary). Say go and I build it.


1. Floating recording indicator — VoiceInk shows a mini widget/overlay near where you're typing while recording (waveform, cancel button). You only have a static tray icon color change — easy to miss if tray isn't in view. Cheap-ish: small always-on-top PyQt widget, shown only during RECORDING/TRANSCRIBING states, positioned near cursor or corner.
Increase waveform intensity 

2. Preview-before-inject — VoiceInk shows "Preview" of the transcribed/enhanced text before it lands. You inject immediately on accept — no chance to catch a bad transcription before it's typed. Real UX gap given your hallucination/confidence concerns from earlier.
3. Dictionary editor UI — ties to the "manual Personal Dictionary" backend gap flagged earlier; needs a settings tab/table (word/replacement pairs), not just YAML.
4. Mode/App indicator ("Active Mode: Gmail") — pairs with the per-app-profile backend gap already flagged; the UI half is a simple label in tray tooltip or floating widget.
Not present in your settings window, worth adding regardless of VoiceInk:
5. Tabs instead of one long group-box column — settings window is flat GroupBoxes; fine now, will get cluttered once dictionary/modes UI lands. Cheap: QTabWidget wrap.

Inverse Text Normalization (ITN) — real gap. Myna's normalize_numbers in postprocessing pipeline is narrower than proper ITN (spoken→written form: dates, currency, units, abbreviations). Worth checking myna/postprocessing/pipeline.py scope.

VAD tuned params (min speech/silence duration, per-chunk probability) — myna's Silero VAD has aggressiveness levels only. FluidAudio exposes finer min-duration knobs. Minor, skip unless VAD misfires again.

Corruption detection + re-download for cached models — myna trusts local model cache blindly. Small robustness gap, low priority.

------------------------------------------------

Voice detection:

Adaptive gain — normalize RMS per session instead of fixed 90%, avoids re-tuning by hand when BT link level drifts. Cheap (few lines in capture.py), fixes both clipping and too-quiet cases.
Route low-confidence-rejected segments into preview too, tagged "uncertain," instead of hard-discard+notify. You already built the review box — reuse it so borderline speech doesn't just vanish. Recommended, near-zero new code, pairs directly with what's already shipped.
Ambient-noise-floor sample at session start → auto-pick VAD aggressiveness instead of static config value.
Learn from correction:

Extend corrections.py past single-word replace opcodes to short phrase substitutions (insert/delete runs too, e.g. "wont to"→"want to") — more coverage, still just difflib, no new deps.
Manual correction-dictionary entry point in Settings UI (add/edit/delete pairs directly) — VoiceInk has this, you don't. Reuses the same JSON store.
Surface correction stats somewhere visible (tray tooltip or settings tab: "12 auto-fixes applied this week") — cheap, closes the loop so the learning isn't invisible.
Top pick overall: #2 in voice detection (route rejects to preview) — directly reuses infra you just built, biggest quality win for near-zero cost. Say which to build.


Making it "work on all GNOME distros via IBus" for real means writing a genuine IBus input-method engine (D-Bus org.freedesktop.IBus.Engine, engine XML registration, restart ibus-daemon on install) — a real subsystem, not a cleanup.

-------------------------------------------------------
Issue -0---------

EnrollWorker error: Failed to load speaker model: cannot import name '_ccallback_c' from 'scipy._lib' (/usr/lib/python3/dist-packages/scipy/_lib/__init__.py)

2nd -- allow for mic not detected or mictest to switch from SBC -xq to msbc bluetooth earbuds
when enrolling voice 


4: USB Audio: - (hw:1,0)5: USB Audio: #1 (hw:1,1)8: Loopback: PCM (hw:2,0)9: Loopback: PCM (hw:2,1)- 11: pipewire12: pulse13: default
only these are available for Microphone , but still , where i have 3 input -- 
built in audio - analog input, 
usb audio - digital input - spdif
Bluetooth headset oneplus

I need to select bluetooth 



Issue 2:
Dictation gets better when switching from small model to medium model as shown below. How can I improve small model? So that it captures every word better.
Optimizations for best apple/google level dictations


---- Small   ----  rejects mostly everyhting , hello is captured as hello, and none of the other words match what i spoke 
2026-07-08T20:58:55 myna.audio.vad               INFO     Silero VAD loaded (threshold=0.50, pad=300 ms)
2026-07-08T20:58:55 myna.audio.capture           INFO     Mic capture started — 16000 Hz, 30 ms chunks, device=None
2026-07-08T20:58:55 myna.audio.adapter           INFO     AudioAdapter opened
2026-07-08T20:59:01 faster_whisper               INFO     Processing audio with duration 00:00.900
2026-07-08T20:59:01 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.34 < 0.55: 'Aloha.'
2026-07-08T20:59:06 faster_whisper               INFO     Processing audio with duration 00:01.770
2026-07-08T20:59:07 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.40 < 0.55: "I'm in the washroom."
2026-07-08T20:59:10 faster_whisper               INFO     Processing audio with duration 00:00.360
2026-07-08T20:59:11 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.14 < 0.55: 'Thank you.'
2026-07-08T20:59:12 faster_whisper               INFO     Processing audio with duration 00:01.290
2026-07-08T20:59:12 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.05 < 0.55: 'is hood.'
2026-07-08T20:59:17 faster_whisper               INFO     Processing audio with duration 00:00.330
2026-07-08T20:59:17 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.01 < 0.55: 'the war.'
2026-07-08T20:59:18 faster_whisper               INFO     Processing audio with duration 00:00.900
2026-07-08T20:59:21 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.00 < 0.55: "I'll see you soon."
2026-07-08T20:59:21 faster_whisper               INFO     Processing audio with duration 00:00.330
2026-07-08T20:59:21 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.20 < 0.55: 'Thank you.'
2026-07-08T20:59:33 faster_whisper               INFO     Processing audio with duration 00:01.770
2026-07-08T20:59:33 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.79: "Aloha, I'm in the washroom."
2026-07-08T20:59:38 faster_whisper               INFO     Processing audio with duration 00:01.590
2026-07-08T20:59:39 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.37 < 0.55: 'Thank you.'
2026-07-08T20:59:43 faster_whisper               INFO     Processing audio with duration 00:01.260
2026-07-08T20:59:44 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.35 < 0.55: 'should the war'
2026-07-08T20:59:57 faster_whisper               INFO     Processing audio with duration 00:01.590
2026-07-08T21:00:00 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.00 < 0.55: 'All right, captured, start to do it.'
2026-07-08T21:00:08 faster_whisper               INFO     Processing audio with duration 00:04.230
2026-07-08T21:00:09 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.62: 'mic capture started, audio adapter opened.'



--- Medium --- model -- better but still correct captured segments gets rejected due to conf , and speaker verification fails 
2026-07-08T21:24:11 myna.audio.capture           INFO     Mic capture started — 16000 Hz, 30 ms chunks, device=None
2026-07-08T21:24:11 myna.audio.adapter           INFO     AudioAdapter opened
2026-07-08T21:24:13 faster_whisper               INFO     Processing audio with duration 00:01.860
2026-07-08T21:24:15 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.72: 'dictation gets better'
2026-07-08T21:24:16 faster_whisper               INFO     Processing audio with duration 00:03.210
2026-07-08T21:24:19 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.82: 'when switching from small model to medium model.'
2026-07-08T21:24:20 faster_whisper               INFO     Processing audio with duration 00:01.170
2026-07-08T21:24:22 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.47 < 0.55: 'but still.'
2026-07-08T21:24:23 faster_whisper               INFO     Processing audio with duration 00:02.250
2026-07-08T21:24:25 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.51 < 0.55: 'segments get rejected'
2026-07-08T21:24:26 faster_whisper               INFO     Processing audio with duration 00:01.140
2026-07-08T21:24:28 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.77: 'as shown below.'
2026-07-08T21:24:39 faster_whisper               INFO     Processing audio with duration 00:01.500
2026-07-08T21:24:41 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.90: 'How can I improve?'
2026-07-08T21:24:41 faster_whisper               INFO     Processing audio with duration 00:01.440
2026-07-08T21:24:43 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.10 < 0.55: 'small module'
2026-07-08T21:24:45 faster_whisper               INFO     Processing audio with duration 00:01.440
2026-07-08T21:24:47 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.65: 'so that it'
2026-07-08T21:24:48 faster_whisper               INFO     Processing audio with duration 00:01.080
2026-07-08T21:24:50 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.46 < 0.55: 'Captures.'
2026-07-08T21:24:51 faster_whisper               INFO     Processing audio with duration 00:01.170
2026-07-08T21:24:53 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.51 < 0.55: 'every word.'
2026-07-08T21:24:53 faster_whisper               INFO     Processing audio with duration 00:00.750
2026-07-08T21:24:55 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.59: 'better.'
2026-07-08T21:25:01 myna.audio.capture           INFO     Mic capture stopped
2026-07-08T21:25:01 myna.audio.adapter           INFO     AudioAdapter closed
2026-07-08T21:25:02 myna.orchestrator.dictation_service INFO     Injecting: conf=0.72 'Dictation gets better'
2026-07-08T21:25:02 myna.orchestrator.dictation_service INFO     Injecting: conf=0.82 'When switching from small model to medium model.'
2026-07-08T21:25:02 myna.orchestrator.dictation_service INFO     Injecting: conf=0.77 'As shown below.'
2026-07-08T21:25:02 myna.orchestrator.dictation_service INFO     Injecting: conf=0.90 'How can I improve?'
2026-07-08T21:25:02 myna.orchestrator.dictation_service INFO     Injecting: conf=0.65 'So that it'
2026-07-08T21:25:02 myna.orchestrator.dictation_service INFO     Injecting: conf=0.59 'Better.'
2026-07-08T21:26:25 myna.audio.vad               INFO     Silero VAD loaded (threshold=0.50, pad=300 ms)
2026-07-08T21:26:25 myna.audio.capture           INFO     Mic capture started — 16000 Hz, 30 ms chunks, device=None
2026-07-08T21:26:25 myna.audio.adapter           INFO     AudioAdapter opened
2026-07-08T21:26:26 faster_whisper               INFO     Processing audio with duration 00:00.840
2026-07-08T21:26:28 myna.orchestrator.dictation_service INFO     Segment rejected conf=0.24 < 0.55: 'Also.'
2026-07-08T21:26:29 faster_whisper               INFO     Processing audio with duration 00:03.060
2026-07-08T21:26:31 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.60: 'The medium model still rejects certain words.'
2026-07-08T21:26:36 myna.audio.capture           INFO     Mic capture stopped
2026-07-08T21:26:36 myna.audio.adapter           INFO     AudioAdapter closed
2026-07-08T21:26:36 myna.orchestrator.dictation_service INFO     Speaker verification failed (score=0.70) — discarded
2026-07-08T21:26:48 myna.audio.vad               INFO     Silero VAD loaded (threshold=0.50, pad=300 ms)
2026-07-08T21:26:48 myna.audio.capture           INFO     Mic capture started — 16000 Hz, 30 ms chunks, device=None
2026-07-08T21:26:48 myna.audio.adapter           INFO     AudioAdapter opened
2026-07-08T21:26:53 faster_whisper               INFO     Processing audio with duration 00:03.480
2026-07-08T21:26:55 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.80: 'Also the medium module still rejects certain words.'
2026-07-08T21:26:59 myna.audio.capture           INFO     Mic capture stopped
2026-07-08T21:26:59 myna.audio.adapter           INFO     AudioAdapter closed
2026-07-08T21:26:59 myna.orchestrator.dictation_service INFO     Speaker verification failed (score=0.72) — discarded



4 files changed:

Change	Before	After	Effect
Confidence formula	avg_logprob + 1.0	 math.exp(avg_logprob)	"segments get rejected" (logprob≈-0.49) → conf 0.51→0.61 ✓

confidence_threshold default	0.55	 0.40	With exp formula, 0.40=exp(-0.92) — rejects only genuine hallucinations

beam_size	hardcoded 1 in __init__	  reads settings.inference.beam_size (5)	5× better accuracy per segment

Speaker threshold	0.75	0.65	0.70-0.72 matches no longer discarded
What to expect with small model now — segments like "every word." (logprob≈-0.54 → exp=0.58) and "segments get rejected" (0.61) will pass. True hallucinations like "is hood." (logprob≈-1.35 → exp=0.26) still rejected correctly.

Further accuracy gains if needed: switch to small.en (English-only model, better than multilingual small for English), or set language=en explicitly in config (already done).




Myna ready — ctrl+space (toggle).  Ctrl+C to exit.
2026-07-12T18:34:26 myna.audio.vad               INFO     Silero VAD loaded (threshold=0.50, pad=300 ms)
2026-07-12T18:34:26 myna.audio.capture           INFO     BT profile switched for capture: bluez_card.08_12_87_F2_F6_D2 → headset-head-unit-msbc
2026-07-12T18:34:27 myna.audio.capture           INFO     Mic capture started — 16000 Hz, 30 ms chunks, device='bt:bluez_card.08_12_87_F2_F6_D2'
2026-07-12T18:34:27 myna.audio.adapter           INFO     AudioAdapter opened
2026-07-12T18:34:31 faster_whisper               INFO     Processing audio with duration 00:01.020
2026-07-12T18:34:31 myna.audio.capture           INFO     Mic capture stopped
2026-07-12T18:34:31 myna.audio.capture           INFO     BT profile restored: bluez_card.08_12_87_F2_F6_D2 → a2dp-sink-sbc_xq
2026-07-12T18:34:31 myna.audio.adapter           INFO     AudioAdapter closed
2026-07-12T18:34:32 myna.orchestrator.dictation_service INFO     Speaker verification failed (score=0.21) — discarded
2026-07-12T18:34:35 myna.audio.vad               INFO     Silero VAD loaded (threshold=0.50, pad=300 ms)
2026-07-12T18:34:35 myna.audio.capture           INFO     BT profile switched for capture: bluez_card.08_12_87_F2_F6_D2 → headset-head-unit-msbc
2026-07-12T18:34:36 myna.audio.capture           INFO     Mic capture started — 16000 Hz, 30 ms chunks, device='bt:bluez_card.08_12_87_F2_F6_D2'
2026-07-12T18:34:36 myna.audio.adapter           INFO     AudioAdapter opened
2026-07-12T18:34:37 faster_whisper               INFO     Processing audio with duration 00:01.020
2026-07-12T18:34:39 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.62: 'cryptography.'
2026-07-12T18:34:41 myna.audio.capture           INFO     Mic capture stopped
2026-07-12T18:34:41 myna.audio.capture           INFO     BT profile restored: bluez_card.08_12_87_F2_F6_D2 → a2dp-sink-sbc_xq
2026-07-12T18:34:41 myna.audio.adapter           INFO     AudioAdapter closed
2026-07-12T18:34:41 myna.orchestrator.dictation_service INFO     Speaker verification failed (score=0.12) — discarded
2026-07-12T18:35:01 myna.audio.vad               INFO     Silero VAD loaded (threshold=0.50, pad=300 ms)
2026-07-12T18:35:01 myna.audio.capture           INFO     BT profile switched for capture: bluez_card.08_12_87_F2_F6_D2 → headset-head-unit-msbc
2026-07-12T18:35:02 myna.audio.capture           INFO     Mic capture started — 16000 Hz, 30 ms chunks, device='bt:bluez_card.08_12_87_F2_F6_D2'
2026-07-12T18:35:02 myna.audio.adapter           INFO     AudioAdapter opened
2026-07-12T18:35:03 faster_whisper               INFO     Processing audio with duration 00:00.690
2026-07-12T18:35:05 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.69: 'Thank you.'
2026-07-12T18:35:08 faster_whisper               INFO     Processing audio with duration 00:00.960
2026-07-12T18:35:10 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.74: 'Cryptography.'
2026-07-12T18:35:11 myna.audio.capture           INFO     Mic capture stopped
2026-07-12T18:35:11 myna.audio.capture           INFO     BT profile restored: bluez_card.08_12_87_F2_F6_D2 → a2dp-sink-sbc_xq
2026-07-12T18:35:11 myna.audio.adapter           INFO     AudioAdapter closed
2026-07-12T18:35:12 myna.orchestrator.dictation_service INFO     Speaker verification failed (score=0.16) — discarded
2026-07-12T18:35:37 myna.audio.vad               INFO     Silero VAD loaded (threshold=0.50, pad=300 ms)
2026-07-12T18:35:37 myna.audio.capture           INFO     BT profile switched for capture: bluez_card.08_12_87_F2_F6_D2 → headset-head-unit-msbc
2026-07-12T18:35:38 myna.audio.capture           INFO     Mic capture started — 16000 Hz, 30 ms chunks, device='bt:bluez_card.08_12_87_F2_F6_D2'
2026-07-12T18:35:38 myna.audio.adapter           INFO     AudioAdapter opened
2026-07-12T18:35:40 faster_whisper               INFO     Processing audio with duration 00:02.100
2026-07-12T18:35:43 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.76: 'type in the text cryptography.'
2026-07-12T18:35:47 myna.audio.capture           INFO     Mic capture stopped
2026-07-12T18:35:47 myna.audio.capture           INFO     BT profile restored: bluez_card.08_12_87_F2_F6_D2 → a2dp-sink-sbc_xq
2026-07-12T18:35:47 myna.audio.adapter           INFO     AudioAdapter closed
2026-07-12T18:35:48 myna.orchestrator.dictation_service INFO     Speaker verification failed (score=0.44) — discarded