from __future__ import annotations

import os
import select
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .vendor.vsdn4 import hidraw, protocol
from .vendor.vsdn4.icon_upload import payload_reports


@dataclass
class SessionSnapshot:
    connected: bool
    node: str | None
    last_input: dict | None
    heartbeat_at: float | None
    error: str | None


class N4DeviceSession:
    """Own the live HID session for the N4.

    This is intentionally conservative. It keeps the device alive and records
    decoded inputs without letting GUI work or action logic block the polling
    loop.
    """

    def __init__(self, event_callback: Callable[[dict], None] | None = None) -> None:
        self._fd: int | None = None
        self._node: Path | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._last_input: dict | None = None
        self._last_heartbeat_at: float | None = None
        self._error: str | None = None
        self._event_callback = event_callback

    def set_event_callback(self, callback: Callable[[dict], None] | None) -> None:
        with self._state_lock:
            self._event_callback = callback

    def start(self, node: str | Path, brightness: int | None = None) -> None:
        node_path = Path(node)
        with self._state_lock:
            if self._node == node_path and self._thread and self._thread.is_alive():
                if brightness is not None:
                    self.set_brightness(brightness)
                return
        self.stop()
        fd = hidraw.open_rw(node_path)
        self._fd = fd
        self._node = node_path
        self._stop.clear()
        self._error = None
        self._send_startup(brightness)
        self._thread = threading.Thread(target=self._run, name="n4-device-session", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        fd = self._fd
        self._fd = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        with self._state_lock:
            self._node = None

    def snapshot(self) -> SessionSnapshot:
        with self._state_lock:
            return SessionSnapshot(
                connected=self._fd is not None and not self._stop.is_set(),
                node=str(self._node) if self._node else None,
                last_input=self._last_input,
                heartbeat_at=self._last_heartbeat_at,
                error=self._error,
            )

    def set_brightness(self, level: int) -> None:
        self._write_packet(protocol.brightness(level))

    def force_heartbeat(self) -> bool:
        if self._fd is None:
            return False
        self._write_packet(protocol.heartbeat())
        with self._state_lock:
            self._last_heartbeat_at = time.time()
        return True

    def upload_button_image(self, display_address: int, image_bytes: bytes) -> None:
        self._write_packet(protocol.picture_size_header(len(image_bytes), display_address))
        for report in payload_reports(image_bytes):
            self._write_packet(report)
        self._write_packet(protocol.finish_command())

    def upload_boot_logo(self, image_bytes: bytes, logo_type: int = 0x02) -> None:
        self._write_packet(protocol.logo_size_header(len(image_bytes), logo_type=logo_type))
        for report in payload_reports(image_bytes):
            self._write_packet(report)
        self._write_packet(protocol.finish_command())

    def _send_startup(self, brightness: int | None = None) -> None:
        self._write_packet(protocol.handshake())
        self._write_packet(protocol.heartbeat())
        self._write_packet(protocol.wake_up_screen())
        if brightness is not None:
            self._write_packet(protocol.brightness(brightness))

    def _write_packet(self, packet: bytes) -> None:
        fd = self._fd
        if fd is None:
            return
        with self._write_lock:
            try:
                os.write(fd, packet)
            except OSError as exc:
                with self._state_lock:
                    self._error = f"write failed: {exc}"

    def _run(self) -> None:
        heartbeat_interval = 5.0
        last_heartbeat = 0.0
        while not self._stop.is_set():
            fd = self._fd
            if fd is None:
                return
            now = time.time()
            if now - last_heartbeat >= heartbeat_interval:
                self._write_packet(protocol.heartbeat())
                last_heartbeat = now
                with self._state_lock:
                    self._last_heartbeat_at = now
            try:
                rlist, _, _ = select.select([fd], [], [], 0.2)
            except (ValueError, OSError) as exc:
                with self._state_lock:
                    self._error = f"poll failed: {exc}"
                return
            if not rlist:
                continue
            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                continue
            except OSError as exc:
                with self._state_lock:
                    self._error = f"read failed: {exc}"
                return
            if not data:
                continue
            decoded = hidraw.decode_vsd_report(data)
            if decoded and decoded.kind == "key":
                event = {
                    "kind": decoded.kind,
                    "description": decoded.description,
                    "location": decoded.location,
                    "state": decoded.state,
                    "timestamp": time.time(),
                }
                with self._state_lock:
                    self._last_input = event
                if self._event_callback is not None:
                    try:
                        self._event_callback(event)
                    except Exception as exc:  # noqa: BLE001
                        with self._state_lock:
                            self._error = f"event callback failed: {exc}"
