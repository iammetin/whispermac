import atexit
import json
import logging
import os
import signal
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
    _COLD_COMPILE_TIMEOUT = 900.0  # ANE-Erstkompilierung dauert ~10 Minuten
    _HEALTH_INTERVAL = 0.25

    def __init__(
        self,
        model_path: str,
        server_bin: str,
        host: str = "127.0.0.1",
        use_gpu: bool = False,
        threads: int | None = None,
    ):
        self.model_path = os.path.abspath(model_path)
        self.server_bin = os.path.abspath(server_bin)
        self.host = host
        self.use_gpu = use_gpu
        cpu_count = os.cpu_count() or 8
        self.threads = max(4, min(cpu_count, threads or 8))

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
        logging.info(f"Beende whisper.cpp Server pid={proc.pid}")
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            logging.warning(f"whisper.cpp Server pid={proc.pid} reagiert nicht auf SIGTERM, sende SIGKILL")
            proc.kill()
            proc.wait(timeout=4)
        logging.info(f"whisper.cpp Server pid={proc.pid} beendet")

    # Whisper wurde auf 30s-Segmente trainiert; 25s-Chunks geben genug Puffer.
    _CHUNK_SAMPLES    = 16000 * 25
    # Reste kürzer als 2s werden mit dem vorherigen Chunk zusammengeführt,
    # damit kein winziger Clip entsteht der Whisper zur Halluzination verleitet.
    _MIN_TAIL_SAMPLES = 16000 * 2

    def transcribe(self, audio: np.ndarray, language: str = None, task: str = "transcribe") -> str:
        """Einzelner Request – für Live-Passes, bei denen Lock-Zeit kritisch ist.
        whisper.cpp verwaltet >30s-Audio intern über ein Sliding Window."""
        self._ensure_server()
        return self._transcribe_chunk(audio, language, task)

    def transcribe_long(self, audio: np.ndarray, language: str = None, task: str = "transcribe") -> str:
        """Chunked-Variante für die finale Transkription langer Aufnahmen.
        Kurze Reste (<2s) werden mit dem vorherigen Chunk zusammengeführt,
        um Halluzinationen durch winzige Audio-Clips zu vermeiden."""
        self._ensure_server()

        if len(audio) <= self._CHUNK_SAMPLES:
            return self._transcribe_chunk(audio, language, task)

        texts = []
        start = 0
        while start < len(audio):
            end = min(start + self._CHUNK_SAMPLES, len(audio))
            remaining = len(audio) - end
            if 0 < remaining < self._MIN_TAIL_SAMPLES:
                end = len(audio)   # Rest absorbieren → max ~27s, sicher unter 30s
            chunk = audio[start:end]
            text = self._transcribe_chunk(chunk, language, task)
            if text:
                texts.append(text.strip())
            start = end

        return " ".join(t for t in texts if t)

    def _transcribe_chunk(self, audio: np.ndarray, language: str = None, task: str = "transcribe") -> str:
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

        return (data.get("text") or "").strip()

    def _ensure_server(self):
        with self._server_lock:
            if self._server_proc is not None and self._server_proc.poll() is None and self._is_server_ready():
                return

            if not os.path.isfile(self.server_bin):
                raise FileNotFoundError(f"whisper.cpp server nicht gefunden: {self.server_bin}")
            if not os.path.isfile(self.model_path):
                raise FileNotFoundError(f"whisper.cpp Modell nicht gefunden: {self.model_path}")

            self._cleanup_stale_servers()
            errors = []
            has_coreml = os.path.isdir(self._encoder_path())
            if self.use_gpu and has_coreml:
                self._protect_coreml_encoder()
                cache_ok = self.ane_cache_valid()
                start_timeout = self._READY_TIMEOUT if cache_ok else self._COLD_COMPILE_TIMEOUT
                if not cache_ok:
                    logging.warning(
                        "Kein valider ANE-Cache vorhanden. "
                        "Erstmalige CoreML-Kompilierung läuft (ca. 10 Min.) — bitte warten."
                    )
            else:
                start_timeout = self._READY_TIMEOUT
            attempts = [True, False] if (self.use_gpu and has_coreml) else [False]

            for use_gpu in attempts:
                self.close()
                self._port = self._find_free_port()
                cmd = [
                    self.server_bin,
                    "--host", self.host,
                    "--port", str(self._port),
                    "-t", str(self.threads),
                    "-bo", "1",
                    "-nf",
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
                    self._wait_until_ready(timeout=start_timeout if use_gpu else self._READY_TIMEOUT)
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

    def _cleanup_stale_servers(self):
        try:
            output = subprocess.check_output(
                ["ps", "-axo", "pid=,command="],
                text=True,
            )
        except Exception as e:
            logging.warning(f"Konnte laufende Prozesse nicht prüfen: {e}")
            return

        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            pid_str, _, cmd = line.partition(" ")
            if not pid_str.isdigit():
                continue
            pid = int(pid_str)
            if pid <= 0 or pid == os.getpid():
                continue
            if self.server_bin not in cmd or self.model_path not in cmd:
                continue

            logging.warning(f"Beende verwaisten whisper.cpp Server pid={pid}")
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except Exception as e:
                logging.warning(f"Konnte whisper.cpp Server pid={pid} nicht terminieren: {e}")
                continue

            deadline = time.time() + 4.0
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                try:
                    logging.warning(f"whisper.cpp Server pid={pid} lebt noch, sende SIGKILL")
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except Exception as e:
                    logging.warning(f"Konnte whisper.cpp Server pid={pid} nicht killen: {e}")

    def _log_server_output(self):
        proc = self._server_proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            logging.info("whisper.cpp: %s", line.rstrip())

    def _encoder_path(self) -> str:
        stem = os.path.basename(self.model_path)
        if stem.endswith(".bin"):
            stem = stem[:-4]
        for suf in ("_q5_0", "_q4_0", "_q8_0", "_q5_1", "_q4_1",
                    "_q2_k", "_q3_k", "_q4_k", "_q5_k", "_q6_k"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        return os.path.join(os.path.dirname(self.model_path), stem + "-encoder.mlmodelc")

    def _protect_coreml_encoder(self) -> None:
        """Entfernt .DS_Store aus dem .mlmodelc-Ordner und macht ihn read-only.

        .DS_Store ändert das mtime des Verzeichnisses → CoreML berechnet einen anderen
        ANE-Cache-Hash → Cache-Miss → langsamer/fehlgeschlagener Start.
        """
        encoder_dir = self._encoder_path()
        if not os.path.isdir(encoder_dir):
            return

        # .DS_Store und andere versteckte Schreibreste entfernen
        for name in os.listdir(encoder_dir):
            if name.startswith("."):
                try:
                    os.remove(os.path.join(encoder_dir, name))
                    logging.info("CoreML-Encoder: %s entfernt", name)
                except Exception as e:
                    logging.warning("CoreML-Encoder: %s konnte nicht entfernt werden: %s", name, e)

        # mtime an interne Dateien angleichen (stabilisiert den CoreML-Cache-Hash)
        try:
            inner_mtimes = [
                os.path.getmtime(os.path.join(encoder_dir, f))
                for f in os.listdir(encoder_dir)
                if not f.startswith(".")
            ]
            if inner_mtimes:
                stable = max(inner_mtimes)
                os.utime(encoder_dir, (stable, stable))
        except Exception as e:
            logging.warning("CoreML-Encoder: mtime konnte nicht gesetzt werden: %s", e)

        # Verzeichnis read-only machen → verhindert zukünftige .DS_Store-Erstellung
        try:
            mode = os.stat(encoder_dir).st_mode
            if mode & 0o222:
                os.chmod(encoder_dir, mode & ~0o222)
                logging.info("CoreML-Encoder: Verzeichnis auf read-only gesetzt")
        except Exception as e:
            logging.warning("CoreML-Encoder: chmod fehlgeschlagen: %s", e)

    def ane_cache_valid(self) -> bool:
        """True wenn ein vollständiger ANE-Bundle-Cache für das aktuelle macOS existiert."""
        if not self.use_gpu:
            return True
        try:
            build = subprocess.check_output(["sw_vers", "-buildVersion"], text=True).strip()
        except Exception:
            return False
        cache_root = os.path.join(
            os.path.expanduser("~/Library/Caches/whisper-server"),
            "com.apple.e5rt.e5bundlecache",
            build,
        )
        if not os.path.isdir(cache_root):
            return False
        for outer in os.listdir(cache_root):
            outer_path = os.path.join(cache_root, outer)
            if not os.path.isdir(outer_path):
                continue
            for bundle in os.listdir(outer_path):
                if ".tmp." in bundle or not bundle.endswith(".bundle"):
                    continue
                for root, _, files in os.walk(os.path.join(outer_path, bundle)):
                    if "weights1.bin" in files:
                        w = os.path.join(root, "weights1.bin")
                        if os.path.getsize(w) > 1_000_000:
                            return True
        return False

    def _wait_until_ready(self, timeout: float | None = None):
        deadline = time.time() + (timeout if timeout is not None else self._READY_TIMEOUT)
        while time.time() < deadline:
            if self._server_proc is None:
                raise RuntimeError("whisper.cpp Serverprozess fehlt")
            if self._server_proc.poll() is not None:
                raise RuntimeError(f"whisper.cpp Server beendet mit Code {self._server_proc.returncode}")
            if self._is_server_ready():
                return
            time.sleep(self._HEALTH_INTERVAL)
        active = timeout if timeout is not None else self._READY_TIMEOUT
        raise TimeoutError(f"whisper.cpp Server wurde nicht rechtzeitig bereit (Timeout: {active:.0f}s)")

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
