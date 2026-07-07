python -m assistant.main
2026-07-08 01:30:54 [INFO    ] __main__: Voice Assistant starting — mode=realtime
2026-07-08 01:30:54 [INFO    ] __main__: Hotkey registered: ctrl+space  →  <ctrl>+<space>
2026-07-08 01:30:54 [INFO    ] __main__: Loading RealtimeSTT — model=small  realtime=tiny
2026-07-08 01:30:55 [ERROR   ] __main__: AudioToTextRecorder initialisation failed: AudioToTextRecorder.__init__() got an unexpected keyword argument 'fast_sentence_end' — falling back to batch mode
2026-07-08 01:30:55 [INFO    ] __main__: Loading Whisper 'small' …
2026-07-08 01:30:55 [INFO    ] assistant.speech.recognizer: Loading Whisper 'small' on cpu (int8) — first run downloads model files
2026-07-08 01:30:56 [INFO    ] assistant.speech.recognizer: Whisper model ready
2026-07-08 01:30:56 [INFO    ] speechbrain.utils.quirks: Applied quirks (see `speechbrain.utils.quirks`): [allow_tf32, disable_jit_profiling]
2026-07-08 01:30:56 [INFO    ] speechbrain.utils.quirks: Excluded quirks specified by the `SB_DISABLE_QUIRKS` environment (comma-separated list): []
2026-07-08 01:30:56 [INFO    ] speechbrain.utils.fetching: Fetch hyperparams.yaml: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
2026-07-08 01:30:56 [INFO    ] speechbrain.utils.fetching: Fetch custom.py: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
/home/xlr8/2CODE/myna-voice/.venv/lib/python3.12/site-packages/speechbrain/utils/autocast.py:68: FutureWarning: `torch.cuda.amp.custom_fwd(args...)` is deprecated. Please use `torch.amp.custom_fwd(args..., device_type='cuda')` instead.
  wrapped_fwd = torch.cuda.amp.custom_fwd(fwd, cast_inputs=cast_inputs)
2026-07-08 01:30:56 [INFO    ] speechbrain.utils.fetching: Fetch embedding_model.ckpt: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
2026-07-08 01:30:57 [INFO    ] speechbrain.utils.fetching: Fetch mean_var_norm_emb.ckpt: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
2026-07-08 01:30:57 [INFO    ] speechbrain.utils.fetching: Fetch classifier.ckpt: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
2026-07-08 01:30:57 [INFO    ] speechbrain.utils.fetching: Fetch label_encoder.txt: Fetching from HuggingFace Hub 'speechbrain/spkrec-ecapa-voxceleb' if not cached
2026-07-08 01:30:57 [INFO    ] speechbrain.utils.parameter_transfer: Loading pretrained files for: embedding_model, mean_var_norm_emb, classifier, label_encoder
/home/xlr8/2CODE/myna-voice/.venv/lib/python3.12/site-packages/speechbrain/utils/checkpoints.py:200: FutureWarning: You are using `torch.load` with `weights_only=False` (the current default value), which uses the default pickle module implicitly. It is possible to construct malicious pickle data which will execute arbitrary code during unpickling (See https://github.com/pytorch/pytorch/blob/main/SECURITY.md#untrusted-models for more details). In a future release, the default value for `weights_only` will be flipped to `True`. This limits the functions that could be executed during unpickling. Arbitrary objects will no longer be allowed to be loaded via this mode unless they are explicitly allowlisted by the user via `torch.serialization.add_safe_globals`. We recommend you start setting `weights_only=True` for any use case where you don't have full control of the loaded file. Please open an issue on GitHub for any issues related to this experimental feature.
  state_dict = torch.load(path, map_location=device)
/home/xlr8/2CODE/myna-voice/.venv/lib/python3.12/site-packages/speechbrain/processing/features.py:1311: FutureWarning: You are using `torch.load` with `weights_only=False` (the current default value), which uses the default pickle module implicitly. It is possible to construct malicious pickle data which will execute arbitrary code during unpickling (See https://github.com/pytorch/pytorch/blob/main/SECURITY.md#untrusted-models for more details). In a future release, the default value for `weights_only` will be flipped to `True`. This limits the functions that could be executed during unpickling. Arbitrary objects will no longer be allowed to be loaded via this mode unless they are explicitly allowlisted by the user via `torch.serialization.add_safe_globals`. We recommend you start setting `weights_only=True` for any use case where you don't have full control of the loaded file. Please open an issue on GitHub for any issues related to this experimental feature.
  stats = torch.load(path, map_location=device)
2026-07-08 01:30:57 [INFO    ] assistant.speaker.verifier: ECAPA-TDNN loaded from speechbrain/spkrec-ecapa-voxceleb
2026-07-08 01:30:57 [INFO    ] assistant.speaker.verifier: Voiceprint loaded — shape=(192,)  path=/home/xlr8/.config/voice-assistant/voiceprint.pt
2026-07-08 01:30:57 [INFO    ] assistant.audio.capture: Audio capture started — device=default  rate=16000 Hz  chunk=320 samples (20 ms)
2026-07-08 01:30:57 [INFO    ] __main__: Ready — press ctrl+space to start listening.  Ctrl+C or tray Quit to exit.
2026-07-08 01:30:58 [INFO    ] assistant.audio.vad: Silero VAD loaded — threshold=0.50  pad=150 ms  min_speech=300 ms
2026-07-08 01:31:05 [INFO    ] __main__: Listening started
2026-07-08 01:33:00 [INFO    ] __main__: Listening stopped
