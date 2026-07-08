python3.12 -m myna.speaker.enroller
EnrollWorker error: SpeakerVerifier not loaded

python3.12 -m myna.main
2026-07-08T15:37:55 __main__                     INFO     Myna starting (Phase 4)
2026-07-08T15:37:56 myna.inference.backends.faster_whisper INFO     FasterWhisperBackend ready: small on auto (int8)
2026-07-08T15:37:56 myna.inference.server        INFO     InferenceServer running on 127.0.0.1:18000
2026-07-08T15:37:56 myna.inference.client        INFO     InferenceClient connected → ws://127.0.0.1:18000/ws/transcribe
2026-07-08T15:37:56 myna.audio.denoiser          INFO     DenoiseFilter enabled (noisereduce)
2026-07-08T15:37:57 myna.injection.selector      INFO     Injection backend selected: ibus
2026-07-08T15:37:57 myna.speaker.verifier        INFO     Voiceprint not found at /home/xlr8/.config/myna/voiceprint.pt — speaker verify disabled
2026-07-08T15:37:57 myna.dbus.service            INFO     dbus-python / gi not available — DBus service disabled
2026-07-08T15:37:57 __main__                     INFO     Tray unavailable (setIcon(self, icon: QIcon): argument 1 has unexpected type 'QPixmap') — headless mode
2026-07-08T15:37:57 myna.orchestrator.hotkey     INFO     HotkeyManager started — mode=toggle hotkey=ctrl+space
Myna ready — ctrl+space (toggle).  Ctrl+C to exit.
2026-07-08T15:38:00 myna.audio.vad               INFO     Silero VAD loaded (threshold=0.50, pad=300 ms)
2026-07-08T15:38:00 myna.audio.capture           INFO     Mic capture started — 16000 Hz, 30 ms chunks, device=None
2026-07-08T15:38:00 myna.audio.adapter           INFO     AudioAdapter opened
2026-07-08T15:38:00 __main__                     INFO     State → recording
/home/xlr8/2CODE/myna-voice/myna/inference/server.py:152: RuntimeWarning: coroutine '_transcribe' was never awaited
  async for seg in _transcribe(backend, combined, language):
RuntimeWarning: Enable tracemalloc to get the object allocation traceback
2026-07-08T15:38:02 myna.inference.server        WARNING  WebSocket error: 'async for' requires an object with __aiter__ method, got coroutine
2026-07-08T15:38:02 myna.orchestrator.dictation_service WARNING  Recv loop error: no close frame received or sent
2026-07-08T15:38:10 myna.orchestrator.dictation_service WARNING  Send loop error: no close frame received or sent
2026-07-08T15:38:20 myna.audio.capture           INFO     Mic capture stopped
2026-07-08T15:38:20 myna.audio.adapter           INFO     AudioAdapter closed
2026-07-08T15:38:20 __main__                     INFO     State → idle
^C2026-07-08T15:38:21 myna.inference.client        INFO     InferenceClient disconnected
2026-07-08T15:38:21 myna.inference.server        INFO     InferenceServer stopping
2026-07-08T15:38:21 __main__                     INFO     Shutdown complete