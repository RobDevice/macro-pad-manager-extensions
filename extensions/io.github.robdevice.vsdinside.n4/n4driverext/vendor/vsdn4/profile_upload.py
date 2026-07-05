"""Minimal VSD N4 profile/settings upload based on VSD Craft's upload path."""

from __future__ import annotations

import json
from io import BytesIO
import os
import select
import time
from dataclasses import dataclass

from PIL import Image, ImageDraw

from . import hidraw, protocol


N4_PROFILE_COORDINATES = [
    "0,0",
    "0,1",
    "0,2",
    "1,0",
    "1,1",
    "1,2",
    "2,0",
    "2,1",
    "2,2",
    "3,0",
    "3,1",
    "3,2",
    "4,1",
    "4,2",
]


@dataclass(frozen=True)
class ProfilePacketLog:
    name: str
    written: int
    preview: bytes


VSD_CRAFT_JPEG_QUALITY = 1
N4_IMAGE_WIDTH = 176
N4_IMAGE_HEIGHT = 112


def solid_jpeg(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (width, height), rgb)
    out = BytesIO()
    image.save(out, format="JPEG", quality=VSD_CRAFT_JPEG_QUALITY)
    return out.getvalue()


def labeled_jpeg(width: int, height: int, rgb: tuple[int, int, int], lines: list[str]) -> bytes:
    image = Image.new("RGB", (width, height), rgb)
    draw = ImageDraw.Draw(image)
    text_color = (255, 255, 255)
    shadow_color = (0, 0, 0)
    line_height = 22
    total_height = line_height * len(lines)
    y = max(8, (height - total_height) // 2)
    for line in lines:
        bbox = draw.textbbox((0, 0), line)
        x = max(4, (width - (bbox[2] - bbox[0])) // 2)
        draw.text((x + 1, y + 1), line, fill=shadow_color)
        draw.text((x, y), line, fill=text_color)
        y += line_height
    out = BytesIO()
    image.save(out, format="JPEG", quality=VSD_CRAFT_JPEG_QUALITY)
    return out.getvalue()


def n4_location_from_coordinates(coordinates: str) -> int:
    """Map VSD profile coordinates using Utilities::getLocationByCoordinates for StreamDock[295]."""
    location_table = {
        0: {0: 0x01, 1: 0x02, 2: 0x03, 3: 0x04, 4: 0x05, 5: 0x10},
        1: {0: 0x06, 1: 0x07, 2: 0x08, 3: 0x09, 4: 0x0A, 5: 0x11},
        2: {0: 0x0B, 1: 0x0C, 2: 0x0D, 3: 0x0E, 4: 0x0F, 5: 0x12},
    }
    left, right = coordinates.split(",", 1)
    column = int(left)
    row = int(right)
    try:
        return location_table[row][column]
    except KeyError:
        raise ValueError(f"unexpected N4 coordinates: {coordinates!r}")


def qt_json_bytes(data: dict) -> bytes:
    """Approximate QJsonDocument::toJson(QJsonDocument::Indented)."""
    return (json.dumps(data, indent=4, ensure_ascii=False) + "\n").encode("utf-8")


def key_json(coordinates: str, name: str, image_name: str) -> bytes:
    data = {
        "Position": coordinates,
        "Name": name,
        "Settings": {},
        "State": 0,
        "States": [{"Image": image_name}],
        "UUID": "com.hotspot.streamdock.system.hotkey",
    }
    return qt_json_bytes(data)


def build_basic_profile(*, labeled: bool = False) -> list[tuple[int, int, bytes, bytes]]:
    # Coordinates from the stock VSD N4 profile manifest. The app maps these
    # through Utilities::getLocationByCoordinates before uploading.
    colors = [
        (210, 64, 74),
        (236, 146, 48),
        (240, 203, 74),
        (85, 176, 112),
        (62, 158, 190),
        (72, 116, 210),
        (132, 91, 202),
        (198, 82, 150),
        (90, 90, 90),
        (220, 220, 220),
        (32, 42, 54),
        (142, 188, 72),
        (224, 94, 64),
        (74, 165, 151),
    ]
    entries: list[tuple[int, int, bytes, bytes]] = []
    for index, coordinate in enumerate(N4_PROFILE_COORDINATES):
        location = n4_location_from_coordinates(coordinate)
        if labeled:
            image = labeled_jpeg(
                N4_IMAGE_WIDTH,
                N4_IMAGE_HEIGHT,
                colors[index],
                [f"POC {index + 1}", f"coord {coordinate}", f"loc {location:02x}"],
            )
        else:
            image = solid_jpeg(N4_IMAGE_WIDTH, N4_IMAGE_HEIGHT, colors[index])
        payload = key_json(coordinate, f"POC {index + 1}", f"POC_{index + 1:02}.jpg")
        entries.append((1, location, image, payload))
    return entries


def build_labeled_profile() -> list[tuple[int, int, bytes, bytes]]:
    return build_basic_profile(labeled=True)


def n4_control_locations() -> list[tuple[str, int]]:
    return [
        (coordinate, n4_location_from_coordinates(coordinate))
        for coordinate in N4_PROFILE_COORDINATES
    ]


def payload_reports(data: bytes) -> list[bytes]:
    return [
        protocol.data_chunk(data[offset : offset + protocol.DEFAULT_OUTPUT_SIZE])
        for offset in range(0, len(data), protocol.DEFAULT_OUTPUT_SIZE)
    ]


def is_ack_report(report: bytes) -> bool:
    decoded = hidraw.decode_vsd_report(report)
    return decoded is not None and decoded.kind == "ack"


def upload_basic_profile(path, delay: float = 0.01, *, labeled: bool = False) -> list[ProfilePacketLog]:
    logs: list[ProfilePacketLog] = []

    try:
        input_report = hidraw.get_input_report(path)
        logs.append(ProfilePacketLog("open input report", len(input_report), input_report[:32]))
    except OSError as exc:
        logs.append(ProfilePacketLog(f"open input report failed: {exc}", 0, b""))

    def drain_pending(fd: int, context: str) -> bool:
        saw_ack = False
        while True:
            rlist, _, _ = select.select([fd], [], [], 0)
            if not rlist:
                return saw_ack
            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                return saw_ack
            if not data:
                return saw_ack
            decoded = hidraw.decode_vsd_report(data)
            suffix = f" ({decoded.description})" if decoded else ""
            logs.append(ProfilePacketLog(f"read after {context}{suffix}", len(data), data[:32]))
            saw_ack = saw_ack or is_ack_report(data)

    def wait_for_ack(fd: int, context: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        saw_ack = drain_pending(fd, context)
        while not saw_ack and time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            rlist, _, _ = select.select([fd], [], [], min(0.05, remaining))
            if not rlist:
                continue
            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if not data:
                continue
            decoded = hidraw.decode_vsd_report(data)
            suffix = f" ({decoded.description})" if decoded else ""
            logs.append(ProfilePacketLog(f"read while waiting for ACK after {context}{suffix}", len(data), data[:32]))
            saw_ack = saw_ack or is_ack_report(data)
        if not saw_ack:
            logs.append(ProfilePacketLog(f"timeout waiting for ACK after {context}", 0, b""))
        return saw_ack

    def write(fd: int, name: str, report: bytes, *, ack_timeout: float | None = None) -> None:
        written = os.write(fd, report)
        logs.append(ProfilePacketLog(name, written, report[:32]))
        time.sleep(delay)
        if ack_timeout is None:
            drain_pending(fd, name)
        else:
            wait_for_ack(fd, name, ack_timeout)

    def write_chunks(fd: int, name: str, reports: list[bytes], ack_timeout: float) -> None:
        for index, report in enumerate(reports, 1):
            write(fd, f"{name} chunk={index}", report)
        wait_for_ack(fd, name, ack_timeout)

    fd = hidraw.open_rw(path)
    try:
        write(fd, "HAN", protocol.handshake(), ack_timeout=0.5)
        write(fd, "CONNECT", protocol.heartbeat(), ack_timeout=0.5)
        write(fd, "DIS wake/software-control transition", protocol.wake_up_screen(), ack_timeout=0.5)
        for page, location, image, payload in build_basic_profile(labeled=labeled):
            write(
                fd,
                f"settings image header page={page} location={location} size={len(image)}",
                protocol.settings_header(len(image), page, location, json_phase=False),
                ack_timeout=0.5,
            )
            write_chunks(
                fd,
                f"settings image data page={page} location={location}",
                payload_reports(image),
                ack_timeout=1.0,
            )
            write(
                fd,
                f"settings json header page={page} location={location} size={len(payload)}",
                protocol.settings_header(len(payload), page, location, json_phase=True),
                ack_timeout=0.5,
            )
            write_chunks(
                fd,
                f"settings json data page={page} location={location}",
                payload_reports(payload),
                ack_timeout=1.0,
            )

        write(fd, "ULEND", protocol.upload_finished(), ack_timeout=1.0)
        write(fd, "STP", protocol.finish_command(), ack_timeout=1.0)
    finally:
        os.close(fd)
    return logs
