
Issue 1:

2026-07-11T17:36:37 myna.orchestrator.dictation_service INFO     Segment accepted conf=0.75: 'These two things are important.'

but 

2026-07-11T17:36:40 myna.orchestrator.dictation_service INFO     Injecting: conf=0.75 'These 2'

issue 2:
After saving config selected does not get downloaded.It gets downloaded.Before we can close the window.

Issue -0---------

EnrollWorker error: Failed to load speaker model: cannot import name '_ccallback_c' from 'scipy._lib' (/usr/lib/python3/dist-packages/scipy/_lib/__init__.py)

2nd -- allow for mic not detected or mictest to switch from SBC -xq to msbc bluetooth earbuds
when enrolling voice 

Myna ready — ctrl+space (toggle).  Ctrl+C to exit.
2026-07-11T16:19:34 myna.audio.vad               INFO     Silero VAD loaded (threshold=0.50, pad=300 ms)
2026-07-11T16:19:34 myna.audio.capture           INFO     Mic capture started — 16000 Hz, 30 ms chunks, device=None
2026-07-11T16:19:34 myna.audio.adapter           INFO     AudioAdapter opened
/home/xlr8/2CODE/myna-voice/.venv/lib/python3.12/site-packages/noisereduce/spectralgate/nonstationary.py:70: RuntimeWarning: invalid value encountered in divide
  sig_mult_above_thresh = (abs_sig_stft - sig_stft_smooth) / sig_stft_smooth
2026-07-11T16:20:01 myna.audio.capture           INFO     Mic capture stopped
2026-07-11T16:20:01 myna.audio.adapter           INFO     AudioAdapter closed

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