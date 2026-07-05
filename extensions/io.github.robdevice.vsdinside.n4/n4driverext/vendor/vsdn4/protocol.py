"""Packet builders derived from static analysis of VSD Craft's SDLibrary1.dll."""

from __future__ import annotations

DEFAULT_REPORT_ID = 0x00
DEFAULT_OUTPUT_SIZE = 1024


def output_report(payload: bytes, report_id: int = DEFAULT_REPORT_ID, output_size: int = DEFAULT_OUTPUT_SIZE) -> bytes:
    """Build a Linux hidraw output report.

    The N4 vendor HID descriptor advertises a 1024-byte output report and no
    explicit report IDs. Linux hidraw still expects byte 0 to be the report ID,
    so writes are 1025 bytes with report ID 0x00 followed by the payload.
    """
    if not 0 <= report_id <= 0xFF:
        raise ValueError("report_id must fit in one byte")
    if output_size <= 0:
        raise ValueError("output_size must be positive")
    if len(payload) > output_size:
        raise ValueError(f"payload is {len(payload)} bytes, max is {output_size}")

    packet = bytearray(output_size + 1)
    packet[0] = report_id
    packet[1 : 1 + len(payload)] = payload
    return bytes(packet)


def _command(tag: bytes, args: bytes = b"") -> bytes:
    payload = bytearray(DEFAULT_OUTPUT_SIZE)
    payload[0:3] = b"CRT"
    payload[5 : 5 + len(tag)] = tag
    payload[10 : 10 + len(args)] = args
    return bytes(payload)


def handshake() -> bytes:
    return output_report(b"HAN")


def heartbeat() -> bytes:
    return output_report(_command(b"CONNECT"))


def wake_up_screen() -> bytes:
    """Build CRT...DIS from SDDevice::sendWakeUpScreenPack.

    VSD Craft's write thread forces StreamDock[295] into a sleep-state flag,
    then calls this command before normal writes. On the N4 this appears to be
    the transition from standalone/default behavior into software-controlled
    runtime behavior.
    """
    return output_report(_command(b"DIS"))


def version_request() -> bytes:
    return output_report(_command(b"VER"))


def brightness(level: int) -> bytes:
    clamped = min(100, max(0, int(level)))
    return output_report(_command(b"LIG", bytes([clamped])))


def clear_key(index: int) -> bytes:
    """Build CRT...CLE for one key/location.

    VSD Craft's getClearCommand places the location byte at payload offset 0x0b,
    not 0x0a like the brightness command.
    """
    payload = bytearray(DEFAULT_OUTPUT_SIZE)
    payload[0:3] = b"CRT"
    payload[5:8] = b"CLE"
    payload[0x0B] = index & 0xFF
    return output_report(bytes(payload))


def clear_all() -> bytes:
    return clear_key(0xFF)


def mode(value: int) -> bytes:
    return output_report(_command(b"MOD", bytes([value & 0xFF])))


def set_direction(a: int, b: int) -> bytes:
    return output_report(_command(b"SET", bytes([a & 0xFF, b & 0xFF])))


def upload_finished() -> bytes:
    """Build CRT...ULEND from SDDevice::getUploadFinishedCommand."""
    payload = bytearray(DEFAULT_OUTPUT_SIZE)
    payload[0:3] = b"CRT"
    payload[5:10] = b"ULEND"
    return output_report(bytes(payload))


def settings_header(size: int, page: int, location: int, *, json_phase: bool = False) -> bytes:
    """Build SDDevice::sendSettingsPackHead for SDSettingPack uploads.

    VSD Craft sends one header before rendered key image bytes and another
    before the per-key JSON. The only visible distinction is byte 9 in the full
    report: 0x05 for image/icon data, 0x0f for JSON data.
    """
    if not 0 <= size <= 0xFFFF:
        raise ValueError("settings payload size must fit in two bytes")
    payload = bytearray(DEFAULT_OUTPUT_SIZE)
    payload[0:3] = b"CRT"
    payload[5:8] = b"ICO"
    payload[8] = 0x0F if json_phase else 0x05
    payload[9] = (size >> 8) & 0xFF
    payload[10] = size & 0xFF
    payload[11] = page & 0xFF
    payload[12] = location & 0xFF
    return output_report(bytes(payload))


def device_address_header(size: int, page: int = 0, location: int = 0) -> bytes:
    """Build SDDevice::sendSettingsPackHead for SDDeviceAddressJsonPack."""
    if not 0 <= size <= 0xFFFF:
        raise ValueError("device-address payload size must fit in two bytes")
    payload = bytearray(DEFAULT_OUTPUT_SIZE)
    payload[0:3] = b"CRT"
    payload[5:8] = b"DEV"
    payload[9] = (size >> 8) & 0xFF
    payload[10] = size & 0xFF
    payload[11] = page & 0xFF
    payload[12] = location & 0xFF
    return output_report(bytes(payload))


def picture_size_header(size: int, controller: int) -> bytes:
    """Build SDDevice::sendPicSizeCommand for live SDIcon uploads.

    VSD Craft sends this before the JPEG bytes for normal per-key icon updates.
    The controller byte is the target display address. On the N4 these are not
    identical to the decoded physical input IDs.
    """
    if not 0 <= size <= 0xFFFF:
        raise ValueError("picture payload size must fit in two bytes")
    if not 0 <= controller <= 0xFF:
        raise ValueError("controller must fit in one byte")
    payload = bytearray(DEFAULT_OUTPUT_SIZE)
    payload[0:3] = b"CRT"
    payload[5:8] = b"BAT"
    payload[10] = (size >> 8) & 0xFF
    payload[11] = size & 0xFF
    payload[12] = controller & 0xFF
    return output_report(bytes(payload))


def logo_size_header(size: int, logo_type: int = 0x02) -> bytes:
    """Build CRT...LOG from SDDevice::sendLogoSizeCommand.

    Static analysis of VSD Craft shows the boot-logo path uses the `LOG`
    command and writes a trailing mode/type byte. The WinUSB helper hard-codes
    `0x02` for the boot-logo upload path.
    """
    if not 0 <= size <= 0xFFFFFFFF:
        raise ValueError("logo payload size must fit in four bytes")
    if not 0 <= logo_type <= 0xFF:
        raise ValueError("logo type must fit in one byte")

    payload = bytearray(DEFAULT_OUTPUT_SIZE)
    payload[0:3] = b"CRT"
    payload[5:8] = b"LOG"
    payload[8] = (size >> 24) & 0xFF
    payload[9] = (size >> 16) & 0xFF
    payload[10] = (size >> 8) & 0xFF
    payload[11] = size & 0xFF
    payload[12] = logo_type
    return output_report(bytes(payload))


def secondary_screen_info_report(
    size: int,
    index: int,
    width: int,
    height: int,
    x: int = 0,
    y: int = 0,
) -> bytes:
    """Build SDDevice::sendSecondaryScreenPicInfo for DRA uploads."""
    if not 0 <= size <= 0xFFFFFF:
        raise ValueError("secondary-screen payload size must fit in three bytes")
    for name, value in (
        ("index", index),
        ("width", width),
        ("height", height),
        ("x", x),
        ("y", y),
    ):
        limit = 0xFF if name == "index" else 0xFFFF
        if not 0 <= value <= limit:
            raise ValueError(f"{name} must fit in {'one' if name == 'index' else 'two'} byte(s)")

    payload = bytearray(DEFAULT_OUTPUT_SIZE)
    payload[0:3] = b"CRT"
    payload[5:8] = b"DRA"
    payload[9] = (size >> 16) & 0xFF
    payload[10] = (size >> 8) & 0xFF
    payload[11] = size & 0xFF
    payload[12] = index & 0xFF
    payload[13] = (width >> 8) & 0xFF
    payload[14] = width & 0xFF
    payload[15] = (height >> 8) & 0xFF
    payload[16] = height & 0xFF
    payload[17] = (x >> 8) & 0xFF
    payload[18] = x & 0xFF
    payload[19] = (y >> 8) & 0xFF
    payload[20] = y & 0xFF
    return output_report(bytes(payload))


def secondary_screen_data_header(
    image_size: int,
    index: int,
    width: int,
    height: int,
    x: int = 0,
    y: int = 0,
) -> bytes:
    """Build the 32-byte DRA header prepended by getSecondaryScreenPicInfo."""
    total_size = image_size + 0x20
    if not 0 <= total_size <= 0xFFFFFFFF:
        raise ValueError("secondary-screen data size must fit in four bytes")
    for name, value in (
        ("index", index),
        ("width", width),
        ("height", height),
        ("x", x),
        ("y", y),
    ):
        limit = 0xFF if name == "index" else 0xFFFF
        if not 0 <= value <= limit:
            raise ValueError(f"{name} must fit in {'one' if name == 'index' else 'two'} byte(s)")

    payload = bytearray(0x20)
    payload[0:3] = b"CRT"
    payload[5:8] = b"DRA"
    payload[8] = (total_size >> 24) & 0xFF
    payload[9] = (total_size >> 16) & 0xFF
    payload[10] = (total_size >> 8) & 0xFF
    payload[11] = total_size & 0xFF
    payload[12] = index & 0xFF
    payload[13] = (width >> 8) & 0xFF
    payload[14] = width & 0xFF
    payload[15] = (height >> 8) & 0xFF
    payload[16] = height & 0xFF
    payload[17] = (x >> 8) & 0xFF
    payload[18] = x & 0xFF
    payload[19] = (y >> 8) & 0xFF
    payload[20] = y & 0xFF
    return bytes(payload)


def secondary_screen_video_data_header(
    image_size: int,
    index: int,
    width: int,
    height: int,
    x: int = 0,
    y: int = 0,
) -> bytes:
    """Build the 1024-byte M_V header used by VSD's background-video branch."""
    total_size = image_size + 0x20
    if not 0 <= total_size <= 0xFFFFFFFF:
        raise ValueError("secondary-screen data size must fit in four bytes")
    for name, value in (
        ("index", index),
        ("width", width),
        ("height", height),
        ("x", x),
        ("y", y),
    ):
        limit = 0xFF if name == "index" else 0xFFFF
        if not 0 <= value <= limit:
            raise ValueError(f"{name} must fit in {'one' if name == 'index' else 'two'} byte(s)")

    payload = bytearray(DEFAULT_OUTPUT_SIZE)
    payload[0:3] = b"M_V"
    payload[8] = (total_size >> 24) & 0xFF
    payload[9] = (total_size >> 16) & 0xFF
    payload[10] = (total_size >> 8) & 0xFF
    payload[11] = total_size & 0xFF
    payload[12] = index & 0xFF
    payload[13] = (width >> 8) & 0xFF
    payload[14] = width & 0xFF
    payload[15] = (height >> 8) & 0xFF
    payload[16] = height & 0xFF
    payload[17] = (x >> 8) & 0xFF
    payload[18] = x & 0xFF
    payload[19] = (y >> 8) & 0xFF
    payload[20] = y & 0xFF
    return bytes(payload)


def data_chunk(data: bytes) -> bytes:
    return output_report(data)


def finish_command() -> bytes:
    """Build CRT...STP from SDDevice::addFinishCommand."""
    payload = bytearray(DEFAULT_OUTPUT_SIZE)
    payload[0:3] = b"CRT"
    payload[5:8] = b"STP"
    return output_report(bytes(payload))


def generic_qucmd(a: int, b: int, c: int, d: int, e: int) -> bytes:
    values = bytes(x & 0xFF for x in (a, b, c, d, e))
    return output_report(_command(b"QUCMD", values))


def feature_initializer(report_id: int = DEFAULT_REPORT_ID, feature_size: int = 64) -> bytes:
    """Build the feature report sent by SDDevice::sendHidFeatureReport."""
    if feature_size <= 0:
        raise ValueError("feature_size must be positive")
    packet = bytearray(feature_size + 1)
    packet[0] = report_id
    packet[1] = 0xA0
    packet[2] = 0x0A
    return bytes(packet)
