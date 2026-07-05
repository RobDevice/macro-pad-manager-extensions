"""Minimal Linux hidraw helpers using only the Python standard library."""

from __future__ import annotations

import binascii
import fcntl
import os
import select
import struct
import time
from dataclasses import dataclass
from pathlib import Path

VID = "5548"
PID = "1004"
SYS_HIDRAW = Path("/sys/class/hidraw")


@dataclass(frozen=True)
class HidrawDevice:
    node: Path
    sysfs: Path
    hid_id: str
    name: str
    phys: str
    uniq: str
    interface: int | None
    report_descriptor: bytes

    @property
    def is_vsd_n4(self) -> bool:
        return f"v0000{VID.upper()}p0000{PID.upper()}" in self.modalias.upper()

    @property
    def modalias(self) -> str:
        path = self.sysfs / "modalias"
        return read_text(path)


@dataclass(frozen=True)
class DecodedReport:
    kind: str
    description: str
    location: int | None = None
    state: int | None = None


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def parse_uevent(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in read_text(path).splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def interface_number(sysfs: Path) -> int | None:
    # Path segment examples include "1-1.4:1.0" and "1-1.4:1.1".
    for part in sysfs.parts:
        if ":1." in part:
            try:
                return int(part.rsplit(".", 1)[1])
            except ValueError:
                return None
    return None


def enumerate_hidraw() -> list[HidrawDevice]:
    devices: list[HidrawDevice] = []
    if not SYS_HIDRAW.exists():
        return devices

    for entry in sorted(SYS_HIDRAW.iterdir(), key=lambda p: p.name):
        sysfs = (entry / "device").resolve()
        uevent = parse_uevent(sysfs / "uevent")
        descriptor_path = sysfs / "report_descriptor"
        try:
            descriptor = descriptor_path.read_bytes()
        except OSError:
            descriptor = b""
        devices.append(
            HidrawDevice(
                node=Path("/dev") / entry.name,
                sysfs=sysfs,
                hid_id=uevent.get("HID_ID", ""),
                name=uevent.get("HID_NAME", ""),
                phys=uevent.get("HID_PHYS", ""),
                uniq=uevent.get("HID_UNIQ", ""),
                interface=interface_number(sysfs),
                report_descriptor=descriptor,
            )
        )
    return devices


def find_vendor_device() -> HidrawDevice | None:
    candidates = [
        dev
        for dev in enumerate_hidraw()
        if dev.hid_id.upper().endswith(f":0000{VID.upper()}:0000{PID.upper()}")
    ]
    for dev in candidates:
        if dev.interface == 0:
            return dev
    return candidates[0] if candidates else None


def find_vsd_devices() -> list[HidrawDevice]:
    return [
        dev
        for dev in enumerate_hidraw()
        if dev.hid_id.upper().endswith(f":0000{VID.upper()}:0000{PID.upper()}")
        and dev.node.exists()
    ]


def find_input_events() -> list[Path]:
    events: list[Path] = []
    for dev in find_vsd_devices():
        input_root = dev.sysfs / "input"
        if not input_root.exists():
            continue
        for event in input_root.glob("input*/event*"):
            events.append(Path("/dev/input") / event.name)
    return sorted(set(events))


def hexdump(data: bytes, width: int = 16) -> str:
    lines: list[str] = []
    for offset in range(0, len(data), width):
        chunk = data[offset : offset + width]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:08x}  {hex_part:<{width * 3}} {ascii_part}")
    return "\n".join(lines)


def open_rw(path: Path) -> int:
    return os.open(path, os.O_RDWR | os.O_NONBLOCK)


def write_report(path: Path, report: bytes) -> int:
    fd = open_rw(path)
    try:
        return os.write(fd, report)
    finally:
        os.close(fd)


def read_reports(path: Path, seconds: float) -> list[bytes]:
    fd = open_rw(path)
    reports: list[bytes] = []
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            rlist, _, _ = select.select([fd], [], [], min(0.25, remaining))
            if not rlist:
                continue
            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if data:
                reports.append(data)
    finally:
        os.close(fd)
    return reports


def hid_ioctl(direction: int, type_char: str, nr: int, size: int) -> int:
    nr_bits = 8
    type_bits = 8
    size_bits = 14
    nr_shift = 0
    type_shift = nr_shift + nr_bits
    size_shift = type_shift + type_bits
    dir_shift = size_shift + size_bits
    return (direction << dir_shift) | (ord(type_char) << type_shift) | (nr << nr_shift) | (size << size_shift)


def send_feature(path: Path, report: bytes) -> bytes:
    # Linux HIDIOCSFEATURE(len): _IOC(_IOC_READ|_IOC_WRITE, 'H', 0x06, len)
    ioctl_read = 2
    ioctl_write = 1
    request = hid_ioctl(ioctl_read | ioctl_write, "H", 0x06, len(report))
    buf = bytearray(report)
    fd = open_rw(path)
    try:
        fcntl.ioctl(fd, request, buf, True)
        return bytes(buf)
    finally:
        os.close(fd)


def get_input_report(path: Path, report_id: int = 0, size: int = 512) -> bytes:
    # Linux HIDIOCGINPUT(len): _IOC(_IOC_READ|_IOC_WRITE, 'H', 0x0A, len)
    ioctl_read = 2
    ioctl_write = 1
    buf = bytearray(size + 1)
    buf[0] = report_id & 0xFF
    request = hid_ioctl(ioctl_read | ioctl_write, "H", 0x0A, len(buf))
    fd = open_rw(path)
    try:
        fcntl.ioctl(fd, request, buf, True)
        return bytes(buf)
    finally:
        os.close(fd)


def format_reports(reports: list[bytes]) -> str:
    lines: list[str] = []
    for index, report in enumerate(reports, 1):
        decoded = decode_vsd_report(report)
        suffix = f" - {decoded.description}" if decoded else ""
        lines.append(f"report {index}: {len(report)} bytes{suffix}")
        lines.append(hexdump(report, width=64))
        lines.append(f"hex: {binascii.hexlify(report).decode()}")
    return "\n".join(lines)


def decode_vsd_report(report: bytes) -> DecodedReport | None:
    """Decode the ACK-shaped reports VSD Craft uses for N4 ACKs and key events."""
    if len(report) < 11:
        return None
    if not (report.startswith(b"ACK") and report[5:7] == b"OK"):
        return None

    location = report[9]
    state = report[10]
    if location == 0:
        return DecodedReport("ack", f"upload ACK phase={state}", state=state)
    return DecodedReport(
        "key",
        f"N4 input location={location} state={state}",
        location=location,
        state=state,
    )


def format_decoded_report(label: str, data: bytes) -> str:
    decoded = decode_vsd_report(data)
    if decoded:
        return f"{label}: {decoded.description}"
    return f"{label}: report {len(data)} bytes"


def read_input_events(path: Path, seconds: float) -> list[str]:
    event_size = struct.calcsize("llHHI")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    events: list[str] = []
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            rlist, _, _ = select.select([fd], [], [], min(0.25, remaining))
            if not rlist:
                continue
            try:
                data = os.read(fd, event_size * 32)
            except BlockingIOError:
                continue
            for offset in range(0, len(data) - event_size + 1, event_size):
                sec, usec, event_type, code, value = struct.unpack(
                    "llHHI", data[offset : offset + event_size]
                )
                events.append(
                    f"{sec}.{usec:06d} type={event_type} code={code} value={value}"
                )
    finally:
        os.close(fd)
    return events


def capture_all_sources(
    hid_devices: list[HidrawDevice],
    event_devices: list[Path],
    seconds: float,
) -> list[str]:
    """Capture all supplied hidraw and evdev sources concurrently."""
    event_size = struct.calcsize("llHHI")
    fd_labels: dict[int, str] = {}
    fd_kinds: dict[int, str] = {}
    lines: list[str] = []

    for dev in hid_devices:
        try:
            fd = open_rw(dev.node)
        except OSError as exc:
            lines.append(f"error opening {dev.node}: {exc}")
            continue
        fd_labels[fd] = f"{dev.node} interface {dev.interface}"
        fd_kinds[fd] = "hidraw"
        lines.append(f"listening {fd_labels[fd]}")

    for event in event_devices:
        try:
            fd = os.open(event, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            lines.append(f"error opening {event}: {exc}")
            continue
        fd_labels[fd] = str(event)
        fd_kinds[fd] = "evdev"
        lines.append(f"listening {fd_labels[fd]}")

    if not fd_labels:
        return lines or ["no capture sources available"]

    deadline = time.time() + seconds
    seen = 0
    try:
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            readable, _, _ = select.select(list(fd_labels), [], [], min(0.25, remaining))
            for fd in readable:
                label = fd_labels[fd]
                try:
                    data = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    lines.append(f"error reading {label}: {exc}")
                    continue
                if not data:
                    continue
                seen += 1
                if fd_kinds[fd] == "hidraw":
                    decoded = decode_vsd_report(data)
                    if decoded:
                        lines.append(f"{label}: {decoded.description}")
                    else:
                        lines.append(f"{label}: report {len(data)} bytes")
                        lines.append(hexdump(data, width=64))
                        lines.append(f"hex: {binascii.hexlify(data).decode()}")
                else:
                    for offset in range(0, len(data) - event_size + 1, event_size):
                        sec, usec, event_type, code, value = struct.unpack(
                            "llHHI", data[offset : offset + event_size]
                        )
                        lines.append(
                            f"{label}: {sec}.{usec:06d} type={event_type} code={code} value={value}"
                        )
    finally:
        for fd in fd_labels:
            os.close(fd)

    if seen == 0:
        lines.append("no reports or input events captured")
    return lines
