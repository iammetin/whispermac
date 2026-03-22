# WhisperMac

**Local, private, always-ready speech-to-text for macOS — powered by Whisper on Apple Silicon.**

WhisperMac lives in your menu bar and lets you dictate into any app, at any time, with a single key press. Everything runs 100% on-device. No cloud, no subscription, no data leaves your Mac.

---

## Features

- **Hold `fn` to record, release to insert** — transcribed text is pasted directly at the cursor, in any app
- **Fully local & private** — uses [mlx-whisper](https://github.com/ml-explore/mlx-examples) on Apple Silicon; no internet connection needed
- **Workflows** — define trigger words that execute keyboard shortcuts or insert HTML (e.g. say *"bullet point"* → inserts `<ul><li></li></ul>` and positions the cursor)
- **Shortcuts** — automatic word/phrase replacements applied after every transcription
- **Multi-language** — supports German, English, Turkish, French, Spanish, Italian and auto-detection
- **Live translation** — transcribe in one language, insert in another
- **KI correction** — optional on-device LLM (Qwen via [mlx-lm](https://github.com/ml-explore/mlx-lm)) that grammatically corrects the transcription before inserting (editable prompt, toggle with `fn fn`)
- **Smart spacing & capitalisation** — automatically adds a space before inserted text and capitalises after sentence-ending punctuation
- **Transcription retry** — if Whisper returns an all-lowercase result (a known model quirk), it retries up to 3 times automatically
- **Hallucination filter** — common Whisper hallucinations ("Danke schön.", "Thanks for watching.", …) are silently discarded
- **History** — last 5 transcriptions accessible from the menu bar for quick re-paste
- **Microphone selection** — pick any input device; list refreshes automatically when Bluetooth headphones connect
- **Hotkeys**
  - `F13` tap — delete last word
  - `F13` hold — delete current line
  - `F14` — undo last action
  - `F15` — open Shortcuts editor
  - `F15 F15` — open Workflows editor
  - `fn fn` (double-tap) — toggle KI correction on/off with a notification in the menu bar

---

## Requirements

- macOS 13 Ventura or later
- Apple Silicon Mac (M1 / M2 / M3 / M4)
- Python 3.11
- A Whisper model in MLX format (see below)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/your-username/WhisperMac.git
cd WhisperMac
```

### 2. Create a virtual environment and install dependencies

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Download a Whisper model

The recommended model is `whisper-large-v3-turbo` in 4-bit quantisation — fast and accurate:

```bash
pip install huggingface_hub
huggingface-cli download mlx-community/whisper-large-v3-turbo \
    --local-dir models/whisper-large-v3-turbo
```

Other supported sizes: `whisper-large-v3-turbo-4bit`, `whisper-large-v3-turbo-8bit`, `whisper-large-v3`.

### 4. Run

```bash
./start.sh
```

Or build a standalone `.app` for `/Applications`:

```bash
./build.sh
```

### 5. Grant permissions

On first launch macOS will ask for:
- **Microphone** — for recording
- **Accessibility** — for inserting text into other apps (`System Settings → Privacy & Security → Accessibility`)

---

## Optional: KI Correction (on-device LLM)

If you want WhisperMac to automatically correct grammar after transcription, download an MLX-compatible LLM and place it in `models/llm/`:

```bash
huggingface-cli download NexVeridian/Qwen3.5-2B-8bit \
    --local-dir models/llm
pip install mlx-lm
```

Then enable *KI-Korrektur* in the menu bar icon → **KI-Korrektur**, or double-tap `fn`. The correction prompt is fully editable in the same settings window.

---

## Workflows

Workflows let you map spoken trigger words to actions. Open the editor with `F15 F15` or from the menu.

| Field | Example | Description |
|-------|---------|-------------|
| Trigger | `bullet point` | Word or phrase Whisper must recognise |
| Action | `html:<ul><li>\|</li></ul>` | What happens before the transcribed text |
| After | `delete` | Optional key action after inserting text |

**Action types:**
- `enter` / `tab` / `delete` / `escape` — send a key
- `cmd+b`, `cmd+shift+k`, … — key combinations
- `html:<pre>|</pre>` — wrap the spoken text in HTML (the `|` marks where text goes)

---

## Shortcuts

Shortcuts are simple word replacements applied to every transcription. Open the editor with `F15`.

Example: `mfg` → `Mit freundlichen Grüßen`

---

## Project Structure

```
WhisperMac/
├── app.py               # Main app, menu bar, event handling
├── transcriber.py       # Whisper transcription wrapper
├── corrector.py         # LLM grammar correction wrapper
├── recorder.py          # Audio capture (sounddevice)
├── workflows.py         # Workflow engine
├── shortcuts.py         # Shortcut replacement engine
├── overlay.py           # Recording overlay animation
├── permissions.py       # macOS permission checks
├── ki_window.py         # KI correction settings window
├── shortcuts_window.py  # Shortcuts editor window
├── workflows_window.py  # Workflows editor window
├── models/              # Local model storage (not included in repo)
├── assets/              # Icons
├── start.sh             # Development launcher
└── build.sh             # .app builder
```

---

## Privacy

All processing happens on your device. WhisperMac does not make any network requests. Audio is recorded in memory, transcribed immediately, and never written to disk.

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first.

---

## License

MIT License — see [LICENSE](LICENSE) for details.
