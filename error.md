python -m assistant.main
2026-07-08 01:46:01 [INFO    ] __main__: Voice Assistant starting — mode=realtime
2026-07-08 01:46:01 [INFO    ] __main__: Hotkey registered: ctrl+space  →  <ctrl>+<space>
2026-07-08 01:46:01 [INFO    ] __main__: Loading RealtimeSTT — model=small  realtime=tiny
2026-07-08 01:46:01 [INFO    ] root: Initializing faster_whisper main transcription model small
ALSA lib pcm_dsnoop.c:601:(snd_pcm_dsnoop_open) unable to open slave
ALSA lib pcm_dmix.c:1032:(snd_pcm_dmix_open) unable to open slave
ALSA lib pcm.c:2664:(snd_pcm_open_noupdate) Unknown PCM cards.pcm.rear
ALSA lib pcm.c:2664:(snd_pcm_open_noupdate) Unknown PCM cards.pcm.center_lfe
ALSA lib pcm.c:2664:(snd_pcm_open_noupdate) Unknown PCM cards.pcm.side
ALSA lib pcm_oss.c:397:(_snd_pcm_oss_open) Cannot open device /dev/dsp
ALSA lib pcm_oss.c:397:(_snd_pcm_oss_open) Cannot open device /dev/dsp
ALSA lib confmisc.c:160:(snd_config_get_card) Invalid field card
ALSA lib pcm_usb_stream.c:482:(_snd_pcm_usb_stream_open) Invalid card 'card'
ALSA lib confmisc.c:160:(snd_config_get_card) Invalid field card
ALSA lib pcm_usb_stream.c:482:(_snd_pcm_usb_stream_open) Invalid card 'card'
ALSA lib pcm_dmix.c:1032:(snd_pcm_dmix_open) unable to open slave
[2026-07-08 01:46:01.876] [ctranslate2] [thread 195835] [warning] The compute type inferred from the saved model is float16, but the target device or backend do not support efficient float16 computation. The model weights have been automatically converted to use the float32 compute type instead.
2026-07-08 01:46:01 [INFO    ] faster_whisper: Processing audio with duration 00:01.620
2026-07-08 01:46:01 [INFO    ] faster_whisper: VAD filter removed 00:00.000 of audio
[2026-07-08 01:46:02.495] [ctranslate2] [thread 195864] [warning] The compute type inferred from the saved model is float16, but the target device or backend do not support efficient float16 computation. The model weights have been automatically converted to use the float32 compute type instead.
2026-07-08 01:46:02 [INFO    ] faster_whisper: Processing audio with duration 00:01.620
2026-07-08 01:46:02 [INFO    ] faster_whisper: VAD filter removed 00:00.000 of audio
2026-07-08 01:46:03 [INFO    ] assistant.speech.realtime: RealtimeSTT ready — model=small  realtime=tiny  inject_on_stable=True
2026-07-08 01:46:03 [INFO    ] assistant.speech.realtime: RealtimeSTT background thread started
2026-07-08 01:46:03 [INFO    ] __main__: Ready — press ctrl+space to start listening.  Ctrl+C or tray Quit to exit.
2026-07-08 01:46:15 [INFO    ] __main__: Listening started
2026-07-08 01:46:16 [INFO    ] faster_whisper: Processing audio with duration 00:01.120
2026-07-08 01:46:16 [INFO    ] faster_whisper: VAD filter removed 00:01.120 of audio