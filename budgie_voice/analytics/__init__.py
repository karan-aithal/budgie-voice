"""Local dictation history: one metadata row per dictation, in a SQLite file. Plain Python, no Qt.

Never stored: transcripts, recognised text, audio, clipboard, window or app names. A row holds
when, how long and how much (two integers), nothing derived from what was said. Nothing leaves the machine.
"""
