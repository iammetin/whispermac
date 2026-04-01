import atexit
import json
import logging
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np


class Transcriber:
    _READY_TIMEOUT = 90.0
    _HEALTH_INTERVAL = 0.25

    def __init__(self, model_path: str, server_bin: str, host: str = "127.0.0.1", use_gpu: bool = False):
        self.model_path = os.path.abspath(model_path)
        self.server_bin = os.path.abspath(server_bin)
        self.host = host
        self.use_gpu = use_gpu

        self._model_loaded = False
        self._port = None
        self._server_proc = None
        self._server_lock = threading.Lock()

        atexit.register(self.close)

    def preload(self):
        """Startet den projektlokalen whisper.cpp-Server und wartet bis er bereit ist."""
        self._ensure_server()
        self._model_loaded = True

    def close(self):
        proc = self._server_proc
        self._server_proc = None
        self._port = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=4)

    def transcribe(self, audio: np.ndarray, language: str = None, task: str = "transcribe") -> str:
        self._ensure_server()

        wav_path = self._write_temp_wav(audio)
        try:
            with open(wav_path, "rb") as f:
                audio_bytes = f.read()
            payload, content_type = self._build_multipart_payload(
                fields={
                    "language": language or "auto",
                    "translate": "true" if task == "translate" else "false",
                    "response_format": "json",
                    "temperature": "0.0",
                    "temperature_inc": "0.0",
                    "no_timestamps": "true",
                    "split_on_word": "true",
                    "suppress_non_speech": "true",
                    "no_context": "true",
                },
                file_field="file",
                filename=os.path.basename(wav_path),
                file_bytes=audio_bytes,
                file_content_type="audio/wav",
            )

            req = urllib.request.Request(
                self._base_url("/inference"),
                data=payload,
                headers={"Content-Type": content_type},
                method="POST",
            )

            timeout = max(30.0, min(180.0, len(audio) / 16000.0 * 6.0))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            details = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"whisper.cpp HTTP {e.code}: {details}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"whisper.cpp Verbindung fehlgeschlagen: {e}") from e
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass

        text = (data.get("text") or "").strip()
        if text.endswith("."):
            text = text[:-1].rstrip()
        return text

    def _ensure_server(self):
        with self._server_lock:
            if self._server_proc is not None and self._server_proc.poll() is None and self._is_server_ready():
                return

            if not os.path.isfile(self.server_bin):
                raise FileNotFoundError(f"whisper.cpp server nicht gefunden: {self.server_bin}")
            if not os.path.isfile(self.model_path):
                raise FileNotFoundError(f"whisper.cpp Modell nicht gefunden: {self.model_path}")

            errors = []
            attempts = [True, False] if self.use_gpu else [False]

            for use_gpu in attempts:
                self.close()
                self._port = self._find_free_port()
                cmd = [
                    self.server_bin,
                    "--host", self.host,
                    "--port", str(self._port),
                    "-m", self.model_path,
                    "-l", "auto",
                ]
                if not use_gpu:
                    cmd.append("-ng")

                env = os.environ.copy()
                env["NO_COLOR"] = "1"

                self._server_proc = subprocess.Popen(
                    cmd,
                    cwd=os.path.dirname(self.model_path),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                threading.Thread(target=self._log_server_output, daemon=True).start()
                try:
                    self._wait_until_ready()
                    if use_gpu:
                        logging.info("whisper.cpp läuft mit GPU/Metal")
                    else:
                        logging.info("whisper.cpp läuft im CPU-Fallback")
                    return
                except Exception as e:
                    errors.append(str(e))
                    logging.warning(f"whisper.cpp Start fehlgeschlagen (use_gpu={use_gpu}): {e}")
                    self.close()

            raise RuntimeError(" | ".join(errors))

    def _log_server_output(self):
        proc = self._server_proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            logging.info("whisper.cpp: %s", line.rstrip())

    def _wait_until_ready(self):
        deadline = time.time() + self._READY_TIMEOUT
        while time.time() < deadline:
            if self._server_proc is None:
                raise RuntimeError("whisper.cpp Serverprozess fehlt")
            if self._server_proc.poll() is not None:
                raise RuntimeError(f"whisper.cpp Server beendet mit Code {self._server_proc.returncode}")
            if self._is_server_ready():
                return
            time.sleep(self._HEALTH_INTERVAL)
        raise TimeoutError("whisper.cpp Server wurde nicht rechtzeitig bereit")

    def _is_server_ready(self) -> bool:
        if self._port is None:
            return False
        try:
            with urllib.request.urlopen(self._base_url("/health"), timeout=1.0) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload.get("status") == "ok"
        except Exception:
            return False

    def _base_url(self, path: str) -> str:
        return f"http://{self.host}:{self._port}{path}"

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((self.host, 0))
            sock.listen(1)
            return int(sock.getsockname()[1])

    def _write_temp_wav(self, audio: np.ndarray) -> str:
        clipped = np.clip(audio.astype(np.float32), -1.0, 1.0)
        pcm16 = (clipped * 32767.0).astype(np.int16)

        with tempfile.NamedTemporaryFile(prefix="whispermac_", suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        with wave.open(wav_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(pcm16.tobytes())

        return wav_path

    def _build_multipart_payload(
        self,
        *,
        fields: dict[str, str],
        file_field: str,
        filename: str,
        file_bytes: bytes,
        file_content_type: str,
    ) -> tuple[bytes, str]:
        boundary = f"----WhisperMac{uuid.uuid4().hex}"
        body = bytearray()

        for key, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")

        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                f"Content-Type: {file_content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        body.extend(file_bytes)
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        return bytes(body), f"multipart/form-data; boundary={boundary}"
