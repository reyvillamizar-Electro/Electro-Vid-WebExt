from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


class MPVError(RuntimeError):
    pass


def find_mpv_executable() -> str | None:
    """Find mpv from PATH or common Windows install locations."""
    executable = shutil.which("mpv")
    if executable:
        return executable

    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "MPV Player" / "mpv.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "mpv" / "mpv.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "mpv" / "mpv.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "mpv" / "mpv.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

    return None


class MPVController:
    """Small controller around an external mpv process with JSON IPC."""

    def __init__(self, window_id: int) -> None:
        executable = find_mpv_executable()
        if not executable:
            raise MPVError(
                "No se encontró mpv.exe. Instala mpv o añádelo al PATH de Windows."
            )

        self.executable = executable
        self.window_id = int(window_id)
        self.process: subprocess.Popen[bytes] | None = None
        self.ipc_path = self._make_ipc_path()

    @staticmethod
    def available() -> bool:
        return find_mpv_executable() is not None

    @staticmethod
    def executable_path() -> str | None:
        return find_mpv_executable()

    def _make_ipc_path(self) -> str:
        if os.name == "nt":
            return rf"\\.\pipe\electro-vid-webext-{os.getpid()}-{id(self)}"
        return str(Path(tempfile.gettempdir()) / f"electro-vid-webext-{os.getpid()}-{id(self)}.sock")

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = [
            self.executable,
            f"--wid={self.window_id}",
            "--idle=yes",
            "--force-window=yes",
            "--keep-open=yes",
            "--input-default-bindings=no",
            f"--input-ipc-server={self.ipc_path}",
            "--terminal=no",
            "--msg-level=all=warn",
            "--vo=gpu-next",
            "--gpu-api=auto",
            "--hwdec=auto-safe",
            "--vd-lavc-dr=no",
            "--audio-client-name=Electro Vid-WebExt",
            "--volume=70",
        ]

        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._wait_for_ipc()

    def _wait_for_ipc(self, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                raise MPVError("mpv terminó inesperadamente al iniciar.")

            if os.name == "nt":
                try:
                    with open(self.ipc_path, "r+b", buffering=0):
                        return
                except OSError:
                    pass
            else:
                if Path(self.ipc_path).exists():
                    return
            time.sleep(0.05)

        raise MPVError("mpv inició, pero no respondió por IPC.")

    def command(self, *args: Any) -> Any:
        self.start()
        payload = (json.dumps({"command": list(args)}) + "\n").encode("utf-8")

        if os.name == "nt":
            return self._command_windows(payload)
        return self._command_unix(payload)

    def _command_windows(self, payload: bytes) -> Any:
        last_error: OSError | None = None
        for _ in range(8):
            try:
                with open(self.ipc_path, "r+b", buffering=0) as pipe:
                    pipe.write(payload)
                    line = pipe.readline()
                    if not line:
                        return None
                    response = json.loads(line.decode("utf-8", errors="replace"))
                    if response.get("error") not in {None, "success"}:
                        raise MPVError(str(response.get("error")))
                    return response.get("data")
            except OSError as exc:
                last_error = exc
                time.sleep(0.03)
        raise MPVError(f"No se pudo comunicar con mpv: {last_error}")

    def _command_unix(self, payload: bytes) -> Any:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1.0)
                client.connect(self.ipc_path)
                client.sendall(payload)
                buffer = b""
                while b"\n" not in buffer:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
        except OSError as exc:
            raise MPVError(f"No se pudo comunicar con mpv: {exc}") from exc

        if not buffer:
            return None
        response = json.loads(buffer.split(b"\n", 1)[0].decode("utf-8", errors="replace"))
        if response.get("error") not in {None, "success"}:
            raise MPVError(str(response.get("error")))
        return response.get("data")

    def load(
        self,
        url: str,
        *,
        referer: str | None = None,
        user_agent: str | None = None,
        cookie_header: str | None = None,
        origin_header: str | None = None,
        audio_url: str | None = None,
    ) -> None:
        options: dict[str, str] = {"hls-bitrate": "max"}

        if user_agent:
            options["user-agent"] = user_agent

        header_fields: list[str] = []
        if referer:
            header_fields.append(f"Referer: {referer}")
        if cookie_header:
            header_fields.append(f"Cookie: {cookie_header}")
        if origin_header:
            header_fields.append(f"Origin: {origin_header}")
        if header_fields:
            # mpv's loadfile option map requires string values. The previous
            # list value was accepted by JSON but rejected by mpv as
            # "invalid parameter".
            options["http-header-fields"] = ",".join(header_fields)
        if audio_url:
            options["audio-file"] = audio_url

        self.command("loadfile", url, "replace", -1, options)

    def toggle_pause(self) -> None:
        self.command("cycle", "pause")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.command("stop")

    def seek_absolute(self, seconds: float) -> None:
        self.command("seek", float(seconds), "absolute", "exact")

    def set_property(self, name: str, value: Any) -> None:
        self.command("set_property", name, value)

    def set_volume(self, value: int) -> None:
        self.set_property("volume", max(0, min(int(value), 100)))

    def set_mute(self, muted: bool) -> None:
        self.set_property("mute", bool(muted))

    def set_file_loop(self, enabled: bool) -> None:
        self.set_property("loop-file", "inf" if enabled else "no")

    def set_ab_loop(self, start: float | None, end: float | None) -> None:
        self.set_property("ab-loop-a", "no" if start is None else float(start))
        self.set_property("ab-loop-b", "no" if end is None else float(end))

    def get_property(self, name: str) -> Any:
        try:
            return self.command("get_property", name)
        except (MPVError, json.JSONDecodeError):
            return None

    def close(self) -> None:
        process = self.process
        if not process:
            return

        if process.poll() is None:
            try:
                self.command("quit")
                process.wait(timeout=1.5)
            except Exception:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()

        self.process = None
        if os.name != "nt":
            try:
                Path(self.ipc_path).unlink(missing_ok=True)
            except OSError:
                pass
