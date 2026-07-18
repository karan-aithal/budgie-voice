To train a voice biometric system or voice-to-text profile effectively, you need phonetically balanced text scripts that capture every sound in the English language.Here are the best standard scripts and sentences you can read aloud to enroll your voice.

🎙️ Standard Phonetic Scripts (Recommended)
The "Stella" Paragraph (Best for Voice Training):"Please call Stella. 
Ask her to bring these things with her from the store: Six spoons of fresh snow peas, five thick slabs of blue cheese, and maybe a snack for her brother Bob. We also need a small plastic snake and a big toy frog for the kids. She can scoop these things into three red bags, and we will go meet her Wednesday at the train station.

"The Rainbow Passage (Best for Speech Patterns):"When the sunlight strikes raindrops in the air, they act as a prism and form a rainbow. The rainbow is a division of white light into many beautiful colors. These take the shape of a long round arch, with its path high above, and its two ends apparently beyond the horizon."


⏱️ Short Multi-Phrase ScriptsIf your system requires multiple distinct, short prompts, 
read these sentences sequentially:

"The quick brown fox jumps over the lazy dog." (Contains every letter in the alphabet)

"The soft cushion broke the fall of the heavy ceramic vase."

"Yellow jazz bells ring out clearly on a bright, frosty morning."

"Pack my box with five dozen liquor jugs."

"Bright blue waves crashed violently against the steep, rocky cliffs."

 
-------------------------------------------------------------


Done — Myna now learns your voice two ways, both persisted, both on by default:

1. Voiceprint adapts as you dictate (verifier.py:139-161, dictation_service.py). Every session that passes verification with a comfortable margin (score ≥ threshold + 0.05) nudges the saved voiceprint 10% toward your current voice and writes it back to voiceprint.pt. So as you use it, the profile tracks your actual voice through the BT headset and scores climb. The margin guard stops borderline audio (a possible impostor) from drifting the print.

2. Personal vocabulary biases recognition (new vocab.py). Words you successfully dictate (5+ letters, seen at least twice) are counted in ~/.config/myna/vocab.json, and the top 50 are fed to faster-whisper as hotwords — so words like "cryptography" that you use often get recognized more reliably over time. Only word→count pairs are stored, never full transcripts, keeping the privacy rules intact. The hotwords travel client → WebSocket frame → backend, the same way language already does, and the backend interface version was bumped 1.0 → 1.1 for the new optional parameter.

Both are switchable in config under learning: (adapt_voiceprint, vocab_enabled, vocab_path).

Verified with a runnable check: vocab round-trips through disk and filters correctly, the EMA adaptation keeps the embedding normalized and saves it, and your installed faster-whisper version accepts the hotwords argument.

One honest limit: Whisper itself isn't retrained — that's not feasible on your CPU. These two mechanisms (speaker profile + vocabulary bias) are what realistically improves on-device. The re-enrollment via the BT headset from last time is still the prerequisite for the voiceprint part to start passing and adapting.