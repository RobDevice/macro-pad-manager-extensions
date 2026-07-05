"""VSD Craft-style live icon upload for the VSD N4."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import select
import time
from typing import Literal

from PIL import Image, ImageDraw

from . import hidraw, protocol


N4_ICON_SIZE = 112
N4_TOUCH_ICON_WIDTH = 176
N4_TOUCH_ICON_HEIGHT = 112
N4_CANVAS_WIDTH = 1024
N4_CANVAS_HEIGHT = 384
N4_TOUCHSTRIP_WIDTH = N4_CANVAS_WIDTH
N4_TOUCHSTRIP_HEIGHT = N4_CANVAS_HEIGHT
N4_TOUCHSCREEN_WIDTH = N4_TOUCHSTRIP_WIDTH
N4_TOUCHSCREEN_HEIGHT = N4_TOUCHSTRIP_HEIGHT
N4_BOOT_LOGO_WIDTH = 800
N4_BOOT_LOGO_HEIGHT = 480
VSD_LIVE_ICON_MAX_BYTES = 3000
VSD_N4_TOUCH_ICON_MAX_BYTES = 2040
VSD_LIVE_ICON_START_QUALITY = 100
VSD_LIVE_ICON_MIN_QUALITY = 2
N4_MAIN_BUTTON_DISPLAY_ADDRESSES = {
    1: 0x0B,
    2: 0x0C,
    3: 0x0D,
    4: 0x0E,
    5: 0x0F,
    6: 0x06,
    7: 0x07,
    8: 0x08,
    9: 0x09,
    10: 0x0A,
}
N4_TOUCHSTRIP_ICON_ADDRESSES = tuple(range(1, 5))
AI_ARTWORK_PATH = Path(__file__).resolve().parents[1] / "assets" / "vsdn4" / "ai-neon-control-panel.png"
AI_TOUCHBAR_IMAGE_PATH = AI_ARTWORK_PATH.with_name("ai-touchbar-background.png")
AI_TOUCHBAR_VIDEO_PATH = AI_ARTWORK_PATH.with_name("ai-touchbar-background.mp4")
AI_ARTWORK_BASE_SIZE = (1254, 1254)
AI_BUTTON_CROP_BOXES = (
    (88, 96, 374, 382),
    (392, 92, 676, 376),
    (690, 92, 974, 376),
    (978, 80, 1242, 344),
    (28, 322, 252, 546),
    (78, 370, 366, 658),
    (392, 370, 680, 658),
    (684, 370, 972, 658),
    (970, 370, 1244, 644),
    (72, 866, 356, 1150),
)
AI_TOUCHSTRIP_CROP_BOX = (24, 620, 1230, 1072)
TouchscreenBackgroundMode = Literal["dra", "video", "mv"]


@dataclass(frozen=True)
class IconPacketLog:
    name: str
    written: int
    preview: bytes


@dataclass(frozen=True)
class IconUploadTarget:
    button: int
    display_address: int
    image: bytes


@dataclass(frozen=True)
class TouchscreenUploadTarget:
    index: int
    x: int
    y: int
    width: int
    height: int
    image: bytes


@dataclass(frozen=True)
class BootLogoUploadTarget:
    width: int
    height: int
    image: bytes


def payload_reports(data: bytes) -> list[bytes]:
    return [
        protocol.data_chunk(data[offset : offset + protocol.DEFAULT_OUTPUT_SIZE])
        for offset in range(0, len(data), protocol.DEFAULT_OUTPUT_SIZE)
    ]


def is_ack_report(report: bytes) -> bool:
    decoded = hidraw.decode_vsd_report(report)
    return decoded is not None and decoded.kind == "ack"


class _UploadWriter:
    def __init__(self, fd: int, logs: list[IconPacketLog], *, delay: float, wait_for_ack: bool) -> None:
        self.fd = fd
        self.logs = logs
        self.delay = delay
        self.wait_for_ack_enabled = wait_for_ack

    def drain_pending(self, context: str) -> bool:
        saw_ack = False
        while True:
            rlist, _, _ = select.select([self.fd], [], [], 0)
            if not rlist:
                return saw_ack
            try:
                data = os.read(self.fd, 4096)
            except BlockingIOError:
                return saw_ack
            if not data:
                return saw_ack
            decoded = hidraw.decode_vsd_report(data)
            suffix = f" ({decoded.description})" if decoded else ""
            self.logs.append(IconPacketLog(f"read after {context}{suffix}", len(data), data[:32]))
            saw_ack = saw_ack or is_ack_report(data)

    def wait_for_ack(self, context: str, timeout: float) -> bool:
        deadline = time.time() + timeout
        saw_ack = self.drain_pending(context)
        while not saw_ack and time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            rlist, _, _ = select.select([self.fd], [], [], min(0.05, remaining))
            if not rlist:
                continue
            try:
                data = os.read(self.fd, 4096)
            except BlockingIOError:
                continue
            if not data:
                continue
            decoded = hidraw.decode_vsd_report(data)
            suffix = f" ({decoded.description})" if decoded else ""
            self.logs.append(IconPacketLog(f"read while waiting for ACK after {context}{suffix}", len(data), data[:32]))
            saw_ack = saw_ack or is_ack_report(data)
        if not saw_ack:
            self.logs.append(IconPacketLog(f"timeout waiting for ACK after {context}", 0, b""))
        return saw_ack

    def write(self, name: str, report: bytes, *, ack_timeout: float | None = None) -> bool:
        written = os.write(self.fd, report)
        self.logs.append(IconPacketLog(name, written, report[:32]))
        time.sleep(self.delay)
        if ack_timeout is None or not self.wait_for_ack_enabled:
            self.drain_pending(name)
            return True
        return self.wait_for_ack(name, ack_timeout)

    def write_chunks(self, name: str, data: bytes, *, ack_timeout: float | None = None) -> bool:
        for index, report in enumerate(payload_reports(data), 1):
            self.write(f"{name} chunk={index}", report)
        if ack_timeout is None or not self.wait_for_ack_enabled:
            self.drain_pending(name)
            return True
        return self.wait_for_ack(name, ack_timeout)


def _rotate_like_vsd_craft(image: Image.Image) -> Image.Image:
    """Mirror horizontally and vertically, matching the N4 branch in VSD Craft."""
    try:
        return image.transpose(Image.Transpose.ROTATE_180)
    except AttributeError:
        return image.transpose(Image.ROTATE_180)


def _smooth_resampling_filter() -> int:
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:
        return Image.LANCZOS


def encode_vsd_icon_jpeg(image: Image.Image, max_bytes: int = VSD_LIVE_ICON_MAX_BYTES) -> bytes:
    """Encode a live SDIcon JPEG the way VSD Craft prepares N4 key images."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    icon = image.convert("RGB").resize((N4_ICON_SIZE, N4_ICON_SIZE), _smooth_resampling_filter())
    icon = _rotate_like_vsd_craft(icon)

    last: bytes | None = None
    for quality in range(VSD_LIVE_ICON_START_QUALITY, VSD_LIVE_ICON_MIN_QUALITY - 1, -2):
        out = BytesIO()
        icon.save(out, format="JPEG", quality=quality)
        data = out.getvalue()
        last = data
        if len(data) <= max_bytes:
            return data

    out = BytesIO()
    icon.save(out, format="JPEG", quality=1)
    data = out.getvalue()
    return data if len(data) < len(last or b"") else (last or data)


def encode_vsd_touch_icon_jpeg(image: Image.Image) -> bytes:
    """Encode an N4 touch-row SDIcon JPEG.

    VSD Craft's bundled N4 touch/page images are 176x112, while the physical
    key icons that are already confirmed working are square 112x112 images.
    """
    if VSD_N4_TOUCH_ICON_MAX_BYTES <= 0:
        raise ValueError("touch icon max size must be positive")

    icon = image.convert("RGB").resize((N4_TOUCH_ICON_WIDTH, N4_TOUCH_ICON_HEIGHT), _smooth_resampling_filter())
    icon = _rotate_like_vsd_craft(icon)

    last: bytes | None = None
    for quality in range(VSD_LIVE_ICON_START_QUALITY, VSD_LIVE_ICON_MIN_QUALITY - 1, -2):
        out = BytesIO()
        icon.save(out, format="JPEG", quality=quality)
        data = out.getvalue()
        last = data
        if len(data) <= VSD_N4_TOUCH_ICON_MAX_BYTES:
            return data

    out = BytesIO()
    icon.save(out, format="JPEG", quality=1)
    data = out.getvalue()
    return data if len(data) < len(last or b"") else (last or data)


def encode_vsd_touchscreen_jpeg(
    image: Image.Image,
    width: int = N4_TOUCHSTRIP_WIDTH,
    height: int = N4_TOUCHSTRIP_HEIGHT,
    quality: int = 88,
) -> bytes:
    """Encode a secondary-screen JPEG using the same N4 orientation handling."""
    icon = image.convert("RGB").resize((width, height), _smooth_resampling_filter())
    icon = _rotate_like_vsd_craft(icon)
    out = BytesIO()
    icon.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def encode_vsd_boot_logo_jpeg(
    image: Image.Image,
    width: int = N4_BOOT_LOGO_WIDTH,
    height: int = N4_BOOT_LOGO_HEIGHT,
    quality: int = 92,
) -> bytes:
    """Encode a likely boot-logo JPEG payload for the N4.

    The boot-logo transfer is a separate `CRT..LOG` path, but the app-side
    image conversion still appears to flow through the generic image helpers.
    We keep the proven N4 orientation handling and use the explicit 800x480
    size shown in the VSD Craft UI.
    """
    icon = image.convert("RGB").resize((width, height), _smooth_resampling_filter())
    icon = _rotate_like_vsd_craft(icon)
    out = BytesIO()
    icon.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def labeled_icon(key: int, rgb: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (N4_ICON_SIZE, N4_ICON_SIZE), rgb)
    draw = ImageDraw.Draw(image)
    text = str(key)
    bbox = draw.textbbox((0, 0), text)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = (N4_ICON_SIZE - width) // 2
    y = (N4_ICON_SIZE - height) // 2 - 4
    draw.text((x + 2, y + 2), text, fill=(0, 0, 0))
    draw.text((x, y), text, fill=(255, 255, 255))
    return encode_vsd_icon_jpeg(image)


def labeled_touch_icon(key: int, rgb: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (N4_TOUCH_ICON_WIDTH, N4_TOUCH_ICON_HEIGHT), rgb)
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, N4_TOUCH_ICON_WIDTH - 6, N4_TOUCH_ICON_HEIGHT - 6), outline=(255, 255, 255), width=3)
    text = f"TOUCH {key}"
    bbox = draw.textbbox((0, 0), text)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = (N4_TOUCH_ICON_WIDTH - width) // 2
    y = (N4_TOUCH_ICON_HEIGHT - height) // 2 - 2
    draw.text((x + 2, y + 2), text, fill=(0, 0, 0))
    draw.text((x, y), text, fill=(255, 255, 255))
    return encode_vsd_touch_icon_jpeg(image)


def _scaled_crop_box(
    image: Image.Image,
    box: tuple[int, int, int, int],
    base_size: tuple[int, int] = AI_ARTWORK_BASE_SIZE,
) -> tuple[int, int, int, int]:
    scale_x = image.width / base_size[0]
    scale_y = image.height / base_size[1]
    left, top, right, bottom = box
    return (
        max(0, min(image.width - 1, round(left * scale_x))),
        max(0, min(image.height - 1, round(top * scale_y))),
        max(1, min(image.width, round(right * scale_x))),
        max(1, min(image.height, round(bottom * scale_y))),
    )


def _load_ai_artwork(path: Path | str = AI_ARTWORK_PATH) -> Image.Image:
    artwork_path = Path(path)
    if not artwork_path.exists():
        raise FileNotFoundError(f"AI artwork source not found: {artwork_path}")
    return Image.open(artwork_path).convert("RGB")


def _crop_ai_artwork(
    image: Image.Image,
    box: tuple[int, int, int, int],
    size: tuple[int, int],
) -> Image.Image:
    return image.crop(_scaled_crop_box(image, box)).resize(size, _smooth_resampling_filter())


def build_ai_touchstrip_source_image(path: Path | str = AI_ARTWORK_PATH) -> Image.Image:
    """Build one continuous full-width secondary-screen background image."""
    artwork = _load_ai_artwork(path)
    return _crop_ai_artwork(artwork, AI_TOUCHSTRIP_CROP_BOX, (N4_TOUCHSTRIP_WIDTH, N4_TOUCHSTRIP_HEIGHT))


def build_ai_demo_icons(path: Path | str = AI_ARTWORK_PATH) -> list[IconUploadTarget]:
    artwork = _load_ai_artwork(path)
    return [
        IconUploadTarget(
            button=button,
            display_address=n4_main_button_display_address(button),
            image=encode_vsd_icon_jpeg(_crop_ai_artwork(artwork, crop_box, (N4_ICON_SIZE, N4_ICON_SIZE))),
        )
        for button, crop_box in enumerate(AI_BUTTON_CROP_BOXES, 1)
    ]


def build_ai_touchscreen_background(path: Path | str = AI_ARTWORK_PATH) -> TouchscreenUploadTarget:
    """Build the AI artwork as a single secondary-screen background target."""
    image = build_ai_touchstrip_source_image(path)
    return TouchscreenUploadTarget(
        index=0,
        x=0,
        y=0,
        width=N4_TOUCHSTRIP_WIDTH,
        height=N4_TOUCHSTRIP_HEIGHT,
        image=encode_vsd_touchscreen_jpeg(image),
    )


def build_ai_touchscreen_background_video(path: Path | str = AI_TOUCHBAR_VIDEO_PATH) -> TouchscreenUploadTarget:
    """Build the AI artwork as VSD Craft's secondary-screen background video payload."""
    video_path = Path(path)
    if not video_path.exists():
        raise FileNotFoundError(f"AI touchbar MP4 source not found: {video_path}")
    return TouchscreenUploadTarget(
        index=0,
        x=0,
        y=0,
        width=N4_TOUCHSTRIP_WIDTH,
        height=N4_TOUCHSTRIP_HEIGHT,
        image=video_path.read_bytes(),
    )


def build_ai_boot_logo(path: Path | str = AI_ARTWORK_PATH) -> BootLogoUploadTarget:
    image = build_ai_touchstrip_source_image(path).resize(
        (N4_BOOT_LOGO_WIDTH, N4_BOOT_LOGO_HEIGHT), _smooth_resampling_filter()
    )
    return BootLogoUploadTarget(
        width=N4_BOOT_LOGO_WIDTH,
        height=N4_BOOT_LOGO_HEIGHT,
        image=encode_vsd_boot_logo_jpeg(image),
    )


def n4_main_button_display_address(button: int) -> int:
    try:
        return N4_MAIN_BUTTON_DISPLAY_ADDRESSES[button]
    except KeyError as exc:
        raise ValueError(f"unexpected N4 main button: {button!r}") from exc


def build_demo_icons() -> list[IconUploadTarget]:
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
        (42, 42, 42),
    ]
    return [
        IconUploadTarget(
            button=button,
            display_address=n4_main_button_display_address(button),
            image=labeled_icon(button, colors[button - 1]),
        )
        for button in range(1, 11)
    ]


def build_touchscreen_demo() -> TouchscreenUploadTarget:
    """Build a full-canvas DRA diagnostic image.

    This is intentionally not part of the normal touch-zone upload path because
    current N4 hardware tests accept DRA packets but do not show them visibly.
    """
    image = Image.new("RGB", (N4_TOUCHSTRIP_WIDTH, N4_TOUCHSTRIP_HEIGHT), (12, 14, 20))
    draw = ImageDraw.Draw(image)
    for y in range(N4_TOUCHSTRIP_HEIGHT):
        shade = int(22 + (y / max(1, N4_TOUCHSTRIP_HEIGHT - 1)) * 32)
        draw.line((0, y, N4_TOUCHSTRIP_WIDTH, y), fill=(shade, shade + 2, shade + 8))

    colors = [(220, 70, 95), (240, 186, 55), (75, 190, 135), (70, 155, 225)]
    segment_width = N4_TOUCHSTRIP_WIDTH // len(colors)
    touch_row_top = 18
    touch_row_bottom = touch_row_top + N4_ICON_SIZE
    for index, color in enumerate(colors, 1):
        x0 = (index - 1) * segment_width
        x1 = N4_TOUCHSTRIP_WIDTH if index == len(colors) else index * segment_width
        draw.rectangle((x0, 0, x1, N4_TOUCHSTRIP_HEIGHT), outline=(color[0] // 2, color[1] // 2, color[2] // 2), width=3)
        draw.rectangle((x0 + 12, touch_row_top, x1 - 12, touch_row_bottom), fill=color)
        draw.rectangle((x0 + 18, touch_row_top + 8, x1 - 18, touch_row_bottom - 8), outline=(255, 255, 255), width=3)
        label = f"TOUCH {index}"
        bbox = draw.textbbox((0, 0), label)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        tx = x0 + ((x1 - x0) - text_width) // 2
        ty = touch_row_top + ((N4_ICON_SIZE - text_height) // 2)
        draw.text((tx + 2, ty + 2), label, fill=(0, 0, 0))
        draw.text((tx, ty), label, fill=(255, 255, 255))

    draw.rectangle((8, 152, N4_TOUCHSTRIP_WIDTH - 8, N4_TOUCHSTRIP_HEIGHT - 10), outline=(200, 210, 225), width=3)
    draw.text((28, 178), "DRA DIAGNOSTIC BACKGROUND", fill=(255, 255, 255))
    draw.text((28, 214), "not sent by Upload Touch Zones", fill=(190, 205, 220))
    return TouchscreenUploadTarget(
        index=0,
        x=0,
        y=0,
        width=N4_TOUCHSTRIP_WIDTH,
        height=N4_TOUCHSTRIP_HEIGHT,
        image=encode_vsd_touchscreen_jpeg(image),
    )


def build_touchstrip_icons() -> list[IconUploadTarget]:
    colors = [
        (225, 80, 100),
        (245, 190, 60),
        (85, 195, 135),
        (70, 165, 225),
    ]
    return [
        IconUploadTarget(
            button=index,
            display_address=address,
            image=labeled_touch_icon(index, colors[index - 1]),
        )
        for index, address in enumerate(N4_TOUCHSTRIP_ICON_ADDRESSES, 1)
    ]


def _write_startup(writer: _UploadWriter) -> None:
    writer.write("HAN", protocol.handshake(), ack_timeout=0.5)
    writer.write("CONNECT", protocol.heartbeat(), ack_timeout=0.5)
    writer.write("DIS wake/software-control transition", protocol.wake_up_screen(), ack_timeout=0.5)


def _write_sdicon_target(
    writer: _UploadWriter,
    target: IconUploadTarget,
    label: str,
) -> None:
    writer.write(
        (
            f"{label} BAT slot={target.button} "
            f"address=0x{target.display_address:02x} size={len(target.image)}"
        ),
        protocol.picture_size_header(len(target.image), target.display_address),
        ack_timeout=0.5,
    )
    writer.write_chunks(
        (
            f"{label} JPEG slot={target.button} "
            f"address=0x{target.display_address:02x}"
        ),
        target.image,
        ack_timeout=1.0,
    )
    writer.write(
        f"STP {label} slot={target.button} address=0x{target.display_address:02x}",
        protocol.finish_command(),
        ack_timeout=0.2,
    )


def _write_secondary_screen_background(
    writer: _UploadWriter,
    target: TouchscreenUploadTarget,
    label: str,
    mode: TouchscreenBackgroundMode,
) -> None:
    if mode == "dra":
        header = protocol.secondary_screen_data_header(
            len(target.image),
            target.index,
            target.width,
            target.height,
            target.x,
            target.y,
        )
        mode_label = "DRA JPEG"
    elif mode in ("video", "mv"):
        header = protocol.secondary_screen_video_data_header(
            len(target.image),
            target.index,
            target.width,
            target.height,
            target.x,
            target.y,
        )
        mode_label = "M_V MP4"
    else:
        raise ValueError(f"unknown secondary-screen background mode: {mode!r}")

    writer.write_chunks(
        (
            f"{label} {mode_label} background index={target.index} "
            f"rect={target.x},{target.y} {target.width}x{target.height} "
            f"payload_size={len(target.image)} declared_size={len(target.image) + 0x20} "
            f"wire_size={len(header) + len(target.image)}"
        ),
        header + target.image,
        ack_timeout=1.0,
    )
    writer.write(f"STP {label} {mode_label} background", protocol.finish_command(), ack_timeout=0.2)


def _write_boot_logo(
    writer: _UploadWriter,
    target: BootLogoUploadTarget,
    label: str,
) -> None:
    writer.write(
        f"{label} LOG size={len(target.image)} dims={target.width}x{target.height} type=0x02",
        protocol.logo_size_header(len(target.image), logo_type=0x02),
        ack_timeout=0.5,
    )
    writer.write_chunks(
        f"{label} JPEG payload dims={target.width}x{target.height}",
        target.image,
        ack_timeout=1.0,
    )
    writer.write(f"STP {label} LOG", protocol.finish_command(), ack_timeout=0.2)


def upload_live_icons(
    path,
    delay: float = 0.005,
    retries: int = 1,
    wait_for_ack: bool = False,
) -> list[IconPacketLog]:
    """Upload ten key icons using VSD Craft's live SDIcon path."""
    if retries < 1:
        raise ValueError("retries must be at least 1")

    logs: list[IconPacketLog] = []
    fd = hidraw.open_rw(path)
    try:
        writer = _UploadWriter(fd, logs, delay=delay, wait_for_ack=wait_for_ack)
        _write_startup(writer)

        for target in build_demo_icons():
            sent = False
            attempts = retries if wait_for_ack else 1
            for attempt in range(1, attempts + 1):
                writer.write(
                    (
                        f"live icon BAT button={target.button} "
                        f"address=0x{target.display_address:02x} "
                        f"size={len(target.image)} attempt={attempt}"
                    ),
                    protocol.picture_size_header(len(target.image), target.display_address),
                    ack_timeout=0.5,
                )
                if writer.write_chunks(
                    (
                        f"live icon JPEG button={target.button} "
                        f"address=0x{target.display_address:02x} attempt={attempt}"
                    ),
                    target.image,
                ):
                    sent = True
                    break
            if not sent:
                logs.append(
                    IconPacketLog(
                        (
                            f"live icon button={target.button} "
                            f"address=0x{target.display_address:02x} failed after {retries} attempts"
                        ),
                        0,
                        b"",
                    )
                )
            writer.write(
                f"STP button={target.button} address=0x{target.display_address:02x}",
                protocol.finish_command(),
                ack_timeout=0.2,
            )
    finally:
        os.close(fd)

    return logs


def upload_ai_artwork(
    path,
    artwork_path: Path | str = AI_ARTWORK_PATH,
    delay: float = 0.005,
    wait_for_ack: bool = False,
) -> list[IconPacketLog]:
    """Upload generated AI artwork to the ten main keys only."""
    logs: list[IconPacketLog] = []
    fd = hidraw.open_rw(path)
    try:
        # Keep key uploads on the known fast BAT/JPEG path.
        writer = _UploadWriter(fd, logs, delay=delay, wait_for_ack=False)
        _write_startup(writer)
        for target in build_ai_demo_icons(artwork_path):
            _write_sdicon_target(writer, target, "AI key artwork")
    finally:
        os.close(fd)

    return logs


def upload_boot_logo(
    path,
    artwork_path: Path | str = AI_ARTWORK_PATH,
    delay: float = 0.005,
    wait_for_ack: bool = True,
) -> list[IconPacketLog]:
    """Upload one 800x480 AI-derived boot logo via the `CRT..LOG` path."""
    logs: list[IconPacketLog] = []
    fd = hidraw.open_rw(path)
    try:
        writer = _UploadWriter(fd, logs, delay=delay, wait_for_ack=wait_for_ack)
        _write_startup(writer)
        _write_boot_logo(writer, build_ai_boot_logo(artwork_path), "AI boot logo")
    finally:
        os.close(fd)

    return logs


def upload_touchscreen_test(
    path,
    delay: float = 0.005,
    wait_for_ack: bool = False,
) -> list[IconPacketLog]:
    """Upload visible rectangular touch-strip slot icons."""
    logs: list[IconPacketLog] = []
    fd = hidraw.open_rw(path)
    try:
        writer = _UploadWriter(fd, logs, delay=delay, wait_for_ack=wait_for_ack)
        _write_startup(writer)
        for target in build_touchstrip_icons():
            writer.write(
                (
                    f"touchstrip BAT slot={target.button} "
                    f"address=0x{target.display_address:02x} size={len(target.image)}"
                ),
                protocol.picture_size_header(len(target.image), target.display_address),
                ack_timeout=0.5,
            )
            writer.write_chunks(
                (
                    f"touchstrip JPEG slot={target.button} "
                    f"address=0x{target.display_address:02x}"
                ),
                target.image,
                ack_timeout=1.0,
            )
            writer.write(
                f"STP touchstrip slot={target.button} address=0x{target.display_address:02x}",
                protocol.finish_command(),
                ack_timeout=0.2,
            )
    finally:
        os.close(fd)

    return logs
