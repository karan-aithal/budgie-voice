python3.12 -m myna.main
2026-07-08T15:47:59 __main__                     INFO     Myna starting (Phase 4)
2026-07-08T15:48:00 myna.inference.backends.faster_whisper INFO     FasterWhisperBackend ready: small on auto (int8)
2026-07-08T15:48:00 myna.inference.server        INFO     InferenceServer running on 127.0.0.1:18000
2026-07-08T15:48:01 myna.inference.client        INFO     InferenceClient connected → ws://127.0.0.1:18000/ws/transcribe
2026-07-08T15:48:01 myna.audio.denoiser          INFO     DenoiseFilter enabled (noisereduce)
2026-07-08T15:48:01 myna.injection.selector      INFO     Injection backend selected: ibus
2026-07-08T15:48:01 speechbrain.utils.quirks     INFO     Applied quirks (see `speechbrain.utils.quirks`): [disable_jit_profiling, allow_tf32]
2026-07-08T15:48:01 speechbrain.utils.quirks     INFO     Excluded quirks specified by the `SB_DISABLE_QUIRKS` environment (comma-separated list): []
2026-07-08T15:48:01 speechbrain.utils.fetching   INFO     Fetch hyperparams.yaml: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
2026-07-08T15:48:02 speechbrain.utils.fetching   INFO     Fetch custom.py: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
/home/xlr8/2CODE/myna-voice/.venv/lib/python3.12/site-packages/speechbrain/utils/autocast.py:68: FutureWarning: `torch.cuda.amp.custom_fwd(args...)` is deprecated. Please use `torch.amp.custom_fwd(args..., device_type='cuda')` instead.
  wrapped_fwd = torch.cuda.amp.custom_fwd(fwd, cast_inputs=cast_inputs)
2026-07-08T15:48:02 speechbrain.utils.fetching   INFO     Fetch embedding_model.ckpt: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
2026-07-08T15:48:02 speechbrain.utils.fetching   INFO     Fetch mean_var_norm_emb.ckpt: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
2026-07-08T15:48:02 speechbrain.utils.fetching   INFO     Fetch classifier.ckpt: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
2026-07-08T15:48:03 speechbrain.utils.fetching   INFO     Fetch label_encoder.txt: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
2026-07-08T15:48:03 speechbrain.utils.parameter_transfer INFO     Loading pretrained files for: embedding_model, mean_var_norm_emb, classifier, label_encoder
/home/xlr8/2CODE/myna-voice/.venv/lib/python3.12/site-packages/speechbrain/utils/checkpoints.py:200: FutureWarning: You are using `torch.load` with `weights_only=False` (the current default value), which usesthe default pickle module implicitly. It is possible to construct malicious pickle data which will execute arbitrary code during unpickling (See https://github.com/pytorch/pytorch/blob/main/SECURITY.md#untrusted-models for more details). In a future release, the default value for `weights_only` will be flipped to `True`. This limits the functions that could be executed during unpickling. Arbitrary objects will no longer be allowed to be loaded via this mode unless they are explicitly allowlisted by the user via `torch.serialization.add_safe_globals`. We recommend you start setting `weights_only=True` for any use case where you don't have full control of the loaded file. Please open an issue on GitHub for any issues related to this experimental feature.
  state_dict = torch.load(path, map_location=device)
/home/xlr8/2CODE/myna-voice/.venv/lib/python3.12/site-packages/speechbrain/processing/features.py:1311: FutureWarning: You are using `torch.load` with `weights_only=False` (the current default value), which uses the default pickle module implicitly. It is possible to construct malicious pickle data which will execute arbitrary code during unpickling (See https://github.com/pytorch/pytorch/blob/main/SECURITY.md#untrusted-models for more details). In a future release, the default value for `weights_only` will be flipped to `True`. This limits the functions that could be executed during unpickling. Arbitrary objects will no longer be allowed to be loaded via this mode unless they are explicitly allowlisted by the user via `torch.serialization.add_safe_globals`. We recommend you start setting `weights_only=True` for any use case where you don't have full control of the loaded file. Please open an issue on GitHub for any issues related to this experimental feature.
  stats = torch.load(path, map_location=device)
2026-07-08T15:48:03 myna.speaker.verifier        INFO     SpeakerVerifier model loaded
2026-07-08T15:48:03 myna.speaker.verifier        INFO     SpeakerVerifier loaded (threshold=0.75)
2026-07-08T15:48:03 myna.dbus.service            INFO     dbus-python / gi not available — DBus service disabled
2026-07-08T15:48:03 __main__                     INFO     Tray unavailable (setIcon(self, icon: QIcon): argument 1 has unexpected type 'QPixmap') — headless mode
2026-07-08T15:48:03 myna.orchestrator.hotkey     INFO     HotkeyManager started — mode=toggle hotkey=ctrl+space
Myna ready — ctrl+space (toggle).  Ctrl+C to exit.
2026-07-08T15:48:09 myna.audio.vad               INFO     Silero VAD loaded (threshold=0.50, pad=300 ms)
2026-07-08T15:48:10 myna.audio.capture           INFO     Mic capture started — 16000 Hz, 30 ms chunks, device=None
2026-07-08T15:48:10 myna.audio.adapter           INFO     AudioAdapter opened
2026-07-08T15:48:10 __main__                     INFO     State → recording
2026-07-08T15:48:11 faster_whisper               INFO     Processing audio with duration 00:00.330
2026-07-08T15:48:16 faster_whisper               INFO     Processing audio with duration 00:03.810
2026-07-08T15:48:17 faster_whisper               INFO     Processing audio with duration 00:01.080
2026-07-08T15:48:21 faster_whisper               INFO     Processing audio with duration 00:01.110
2026-07-08T15:48:24 faster_whisper               INFO     Processing audio with duration 00:03.270
2026-07-08T15:48:27 faster_whisper               INFO     Processing audio with duration 00:01.830
2026-07-08T15:48:28 faster_whisper               INFO     Processing audio with duration 00:00.390
2026-07-08T15:48:31 faster_whisper               INFO     Processing audio with duration 00:01.560
2026-07-08T15:48:33 faster_whisper               INFO     Processing audio with duration 00:01.320
2026-07-08T15:48:36 faster_whisper               INFO     Processing audio with duration 00:02.010
2026-07-08T15:48:36 faster_whisper               INFO     Processing audio with duration 00:00.330
2026-07-08T15:48:37 faster_whisper               INFO     Processing audio with duration 00:00.330
^C2026-07-08T15:48:40 myna.audio.capture           INFO     Mic capture stopped
2026-07-08T15:48:40 myna.audio.adapter           INFO     AudioAdapter closed
2026-07-08T15:48:40 __main__                     INFO     State → idle
2026-07-08T15:48:40 myna.inference.client        INFO     InferenceClient disconnected
2026-07-08T15:48:40 myna.inference.server        INFO     InferenceServer stopping
2026-07-08T15:48:40 __main__                     INFO     Shutdown complete