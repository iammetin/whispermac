# WhisperMac

**Local, private, always-ready speech-to-text for macOS — powered by Whisper on Apple Silicon.**

WhisperMac lives in your menu bar and lets you dictate into any app, at any time, with a single key press. Everything runs 100% on-device. No cloud, no subscription, no data leaves your Mac.

---

## Features

- **Hold `fn` to record, release to insert** — transcribed text is pasted directly at the cursor, in any app
- **Live transcription while speaking** — stable chunks can be inserted during dictation before you release the key
- **Fully local & private** — uses a project-local [whisper.cpp](https://github.com/ggml-org/whisper.cpp) runtime and model; no cloud, no external API
- **Workflows** — define trigger words that execute keyboard shortcuts or insert HTML (e.g. say *"bullet point"* → inserts `<ul><li></li></ul>` and positions the cursor)
- **Shortcuts** — automatic word/phrase replacements applied after every transcription
- **Multi-language** — supports German, English, Turkish, French, Spanish, Italian and auto-detection
- **Live translation** — transcribe in one language, insert in another
- **KI correction** — optional on-device LLM (Qwen via [mlx-lm](https://github.com/ml-explore/mlx-lm)) that grammatically corrects the transcription before inserting (editable prompt, toggle with `fn fn`)
- **Live smoothing** — optional local LLM can also clean up live chunks by adding punctuation and removing filler words
- **Smart spacing & capitalisation** — automatically adds a space before inserted text and capitalises after sentence-ending punctuation
- **Transcription retry** — if Whisper returns an all-lowercase result (a known model quirk), it retries up to 3 times automatically
- **Hallucination filter** — common Whisper hallucinations ("Danke schön.", "Thanks for watching.", …) are silently discarded
- **History** — last 5 transcriptions accessible from the menu bar for quick re-paste
- **Microphone selection** — pick any input device; list refreshes automatically when Bluetooth headphones connect
- **Project-local runtime** — `whisper.cpp` server binary, libraries, Core ML encoder and model all live inside this project
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
- The bundled `whisper.cpp` runtime and `ggml-large-v3-turbo` model inside this project

---

## Installation

### Quick setup (recommended)

```bash
git clone https://github.com/your-username/WhisperMac.git
cd WhisperMac
chmod +x setup.sh && ./setup.sh
```

The setup script will:
- Check all requirements (macOS, Apple Silicon, Python 3.11, Homebrew)
- Create a virtual environment and install all dependencies
- Verify that the project-local `whisper.cpp` runtime and model are in place
- Build the `.app` and install it to `/Applications`
- Add WhisperMac to your Dock automatically

---

### Manual setup

#### 1. Clone the repository

```bash
git clone https://github.com/your-username/WhisperMac.git
cd WhisperMac
```

#### 2. Create a virtual environment and install dependencies

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### 3. whisper.cpp runtime / model

This project now uses a **project-local `whisper.cpp` runtime** and a **`ggml-large-v3-turbo` model**.

Expected paths:

```text
vendor/whisper.cpp-runtime/build/bin/whisper-server
models/whisper-cpp/ggml-large-v3-turbo.bin
models/whisper-cpp/ggml-large-v3-turbo-encoder.mlmodelc/
```

If those files are present, nothing else is required for speech recognition.

#### 4. Build and run

```bash
./build.sh   # builds the .app, installs to /Applications and launches it
```

For development without building:

```bash
./start.sh
```

### 5. Grant permissions

On first launch macOS will ask for:
- **Microphone** — for recording
- **Accessibility** — for inserting text into other apps (`System Settings → Privacy & Security → Accessibility`)

---

## Optional: KI Correction (on-device LLM)

WhisperMac can pass every transcription through a local LLM to fix grammar, capitalisation and punctuation before inserting the text.

The LLM must also be in **MLX format**. Place the model files in `models/llm/`.

Recommended model (Qwen 3.5 2B, 8-bit):

```bash
pip install mlx-lm
huggingface-cli download NexVeridian/Qwen3.5-2B-8bit \
    --local-dir models/llm
```

Any instruction-tuned MLX model works. Browse options on [Hugging Face](https://huggingface.co/models?library=mlx&pipeline_tag=text-generation&sort=trending).

Then enable *KI-Korrektur* in the menu bar → **KI-Korrektur**, or double-tap `fn`. The correction prompt is fully editable in the same settings window.

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
