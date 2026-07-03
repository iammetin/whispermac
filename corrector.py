"""
WhisperMac – Text-Korrektor
Grammatikalische Korrektur des transkribierten Textes per lokalem Gemma-MTP-Modell.
Läuft über llama-server mit Multi-Token-Prediction (speculative decoding),
Reasoning/Thinking ist deaktiviert.
"""
import atexit
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx

_GLOBAL_PREFIX = (
    "Antworte ausschließlich mit dem Ergebnis – ohne Einleitung, Erklärung, Kommentar oder Begründung. "
    "Kein Markdown, keine Sternchen, keine Aufzählungszeichen."
)

_SYSTEM_PROMPT = (
    "Du bist ein Grammatik-Korrekturdienst für Spracherkennung. "
    "Korrigiere den deutschen Text grammatikalisch (Groß-/Kleinschreibung, "
    "Zeichensetzung, Beugung). Entferne reine Füllwörter wie 'äh', 'ähm' oder "
    "'hm' sowie offensichtliche Selbstkorrekturen, wenn der Satz dadurch "
    "natürlicher wird. Behalte den Originalwortlaut sonst so weit wie möglich. "
    "Antworte ausschließlich mit dem korrigierten Text – keine Erklärungen, "
    "keine Anmerkungen."
)

_BASE_DIR = Path(__file__).resolve().parent

# Reste eines eventuellen Denkblocks entfernen (Thinking ist deaktiviert,
# aber das Modell könnte Marker trotzdem ausgeben)
_THINK_BLOCK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL | re.IGNORECASE)
_GEMMA_THOUGHT_RE = re.compile(
    r"(?:<\|channel\|>thought|<\|think\|>).*?(?:<\|?channel\|?>|$)",
    re.DOTALL | re.IGNORECASE,
)
_GEMMA_MARKER_RE = re.compile(r"<\|?(?:channel\|?)?(?:think|thought)?\|?>", re.IGNORECASE)


def _resolve_server_bin() -> Path:
    configured = (
        os.getenv("GGUF_MTP_SERVER_BIN")
        or os.getenv("LLAMA_CPP_SERVER_BIN")
        or ""
    ).strip()
    if configured:
        return Path(os.path.expanduser(configured))
    return _BASE_DIR / "vendor" / "llama.cpp-runtime" / "build" / "bin" / "llama-server"


def _find_base_and_draft_models(model_dir: Path) -> tuple[Path, Path]:
    gguf_files = sorted(model_dir.glob("*.gguf"))
    base_candidates = [
        path for path in gguf_files
        if not path.name.startswith("mtp-") and not path.name.startswith("mmproj")
    ]
    draft_candidates = [path for path in gguf_files if path.name.startswith("mtp-")]

    if not base_candidates:
        raise FileNotFoundError(
            f"Keine Basis-GGUF-Datei in {model_dir} gefunden. Erwartet wird eine normale *.gguf-Datei neben dem MTP-Modell."
        )
    if len(base_candidates) > 1:
        raise RuntimeError(
            f"Mehrere Basis-GGUF-Dateien in {model_dir} gefunden: {', '.join(path.name for path in base_candidates)}"
        )
    if not draft_candidates:
        raise FileNotFoundError(
            f"Keine MTP-GGUF-Datei in {model_dir} gefunden. Erwartet wird eine Datei mit Prefix 'mtp-'."
        )
    if len(draft_candidates) > 1:
        raise RuntimeError(
            f"Mehrere MTP-GGUF-Dateien in {model_dir} gefunden: {', '.join(path.name for path in draft_candidates)}"
        )

    return base_candidates[0], draft_candidates[0]


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _strip_thinking_remnants(text: str) -> str:
    text = _THINK_BLOCK_RE.sub("", text)
    text = _GEMMA_THOUGHT_RE.sub("", text)
    text = _GEMMA_MARKER_RE.sub("", text)
    return text.strip()


class TextCorrector:
    def __init__(self, model_path: str):
        self.model_path    = model_path
        self.system_prompt = _SYSTEM_PROMPT
        self._model        = None  # llama-server Popen; None = nicht geladen (app.py prüft darauf)
        self._tokenizer    = None  # nur noch für Kompatibilität
        self._base_url     = None
        self._lock         = threading.Lock()
        self._log_handle   = None

    def preload(self):
        with self._lock:
            if self._model is not None and self._model.poll() is None:
                return

            server_bin = _resolve_server_bin()
            if not server_bin.exists():
                raise FileNotFoundError(f"llama-server nicht gefunden: {server_bin}")

            model_dir = Path(os.path.expanduser(self.model_path))
            base_model_path, draft_model_path = _find_base_and_draft_models(model_dir)

            port = _reserve_port()
            base_url = f"http://127.0.0.1:{port}"
            gpu_layers = (os.getenv("GGUF_MTP_N_GPU_LAYERS", "auto") or "auto").strip()
            n_ctx = int(os.getenv("GGUF_MTP_N_CTX", "32000"))
            spec_draft_n_max = int(os.getenv("GGUF_MTP_SPEC_DRAFT_N_MAX", "2"))

            command = [
                str(server_bin),
                "--host", "127.0.0.1",
                "--port", str(port),
                "-m", str(base_model_path),
                "-c", str(n_ctx),
                "--spec-type", "draft-mtp",
                "--spec-draft-model", str(draft_model_path),
                "--spec-draft-n-max", str(spec_draft_n_max),
                "-ngl", gpu_layers,
            ]

            logging.info(f"Lade Korrektor-Modell (MTP): {base_model_path.name} + {draft_model_path.name}")

            log_dir = _BASE_DIR / "logs"
            log_dir.mkdir(exist_ok=True)
            self._log_handle = open(log_dir / "llama-server.log", "a", encoding="utf-8")

            process = subprocess.Popen(
                command,
                stdout=self._log_handle,
                stderr=self._log_handle,
                text=True,
            )
            try:
                self._wait_for_server_ready(process, base_url)
            except Exception:
                self._terminate_process(process)
                raise

            self._model = process
            self._base_url = base_url
            atexit.register(self.shutdown)
            logging.info(f"Korrektor-Modell geladen (llama-server auf {base_url}).")

    @staticmethod
    def _wait_for_server_ready(process: subprocess.Popen, base_url: str, timeout: float = 300.0):
        deadline = time.monotonic() + timeout
        with httpx.Client(timeout=5.0) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"llama-server beendete sich unerwartet (Exit-Code {process.returncode}). "
                        "Details in logs/llama-server.log."
                    )
                for path in ("/health", "/v1/models"):
                    try:
                        response = client.get(f"{base_url}{path}")
                    except Exception:
                        continue
                    if response.status_code == 200:
                        return
                time.sleep(0.5)
        raise TimeoutError("llama-server wurde nicht rechtzeitig bereit.")

    @staticmethod
    def _terminate_process(process: subprocess.Popen):
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=10)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def shutdown(self):
        with self._lock:
            if self._model is not None:
                self._terminate_process(self._model)
                self._model = None
                self._base_url = None
            if self._log_handle is not None:
                try:
                    self._log_handle.close()
                except Exception:
                    pass
                self._log_handle = None

    def correct(self, text: str, system_prompt: str = None, max_tokens: int = 16000) -> str:
        if self._model is None or self._model.poll() is not None or not self._base_url:
            return text
        try:
            individual = system_prompt if system_prompt is not None else self.system_prompt
            # System: nur Ausgabe-Verhalten (kurz, kein "Gib den Text aus")
            # User: Anweisung + Text zusammen → Modell versteht Aufgabe korrekt
            messages = [
                {"role": "system", "content": _GLOBAL_PREFIX},
                {"role": "user",   "content": f"{individual}\n\n{text}"},
            ]
            request_payload = {
                "messages": messages,
                "stream": False,
                "temperature": 1.0,
                "top_p": 0.95,
                "top_k": 64,
                "min_p": 0.0,
                "max_tokens": max_tokens,
                "presence_penalty": 0.0,
                "repeat_penalty": 1.0,
                "chat_template_kwargs": {
                    "enable_thinking": False,
                },
            }
            with httpx.Client(timeout=300.0) as client:
                response = client.post(
                    f"{self._base_url}/v1/chat/completions",
                    json=request_payload,
                )
            if response.status_code != 200:
                logging.warning(
                    f"Korrektor-Fehler: llama-server antwortete mit {response.status_code}: {response.text[:500]}"
                )
                return text
            payload = response.json()
            message = (payload.get("choices") or [{}])[0].get("message", {})
            result = _strip_thinking_remnants(message.get("content") or "")
            logging.debug(f"Korrektor: '{text}' → '{result}'")
            return result if result else text
        except Exception as e:
            logging.warning(f"Korrektor-Fehler: {e}")
            return text
