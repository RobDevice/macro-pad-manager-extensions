from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from macro_pad_manager.core.svg_assets import is_svg_path, load_svg_as_image
from .vendor.vsdn4.icon_upload import (
    N4_ICON_SIZE,
    N4_TOUCH_ICON_HEIGHT,
    N4_TOUCH_ICON_WIDTH,
    encode_vsd_icon_jpeg,
    encode_vsd_touch_icon_jpeg,
    n4_main_button_display_address,
)

TEXT_SIZE_MAP = {"small": 0.72, "medium": 1.0, "large": 1.28}
TOUCH_READOUT_PREVIEWS = {
    "device_brightness": ("Brightness", "80%", "Device"),
    "system_volume": ("Volume", "42%", "System"),
    "system_battery": ("Battery", "91%", "Charging"),
    "current_time": ("Time", "14:37", "Wednesday"),
    "current_date": ("Date", "27 Jun", "2026"),
    "active_page": ("Page", "3", "Current"),
    "active_profile": ("Profile", "Work", "Active"),
}


def _safe_color(value: str, fallback: str) -> tuple[int, int, int]:
    try:
        return ImageColor.getrgb(value)
    except ValueError:
        return ImageColor.getrgb(fallback)


def _visual_color(visual: dict, key: str, fallback: str = "#ffffff") -> str:
    value = visual.get(key) or visual.get("foreground") or fallback
    return str(value)


def _load_visual_asset(asset_path: str | None, asset_loader=None, *, tint: str | None = None, size: tuple[int, int] | None = None) -> Image.Image | None:
    if not asset_path or asset_loader is None:
        return None
    resolved = asset_loader(asset_path)
    if resolved is None:
        return None
    path = Path(resolved)
    if not path.exists():
        return None
    if is_svg_path(path):
        target_size = size or (256, 256)
        return load_svg_as_image(path, target_size, tint=tint)
    return Image.open(path).convert("RGBA")


def _draw_asset_background(image: Image.Image, asset: Image.Image | None) -> None:
    if asset is None:
        return
    background = asset.convert("RGB").resize(image.size)
    image.paste(background, (0, 0))


def _draw_asset_icon(image: Image.Image, asset: Image.Image | None) -> None:
    if asset is None:
        return
    icon = asset.copy()
    icon.thumbnail((int(image.width * 0.62), int(image.height * 0.62)))
    x = (image.width - icon.width) // 2
    y = (image.height - icon.height) // 2
    image.alpha_composite(icon, (x, y))


def _text_metrics(base_size: int, scale_name: str) -> tuple[int, int]:
    scale = TEXT_SIZE_MAP.get(scale_name, 1.0)
    stroke = 0
    padding = max(8, int(base_size * 0.08))
    return stroke, padding


def _load_font(base_size: int, scale_name: str) -> ImageFont.ImageFont:
    scale = TEXT_SIZE_MAP.get(scale_name, 1.0)
    font_size = max(14, int(base_size * 0.20 * scale))
    for font_name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(font_name, font_size)
        except OSError:
            continue
    return ImageFont.load_default()


def _resolved_toggle_visual(module_payload: dict) -> dict:
    if module_payload.get("type") != "builtin.toggle_switch":
        return dict(module_payload.get("visual", {}))
    visual = dict(module_payload.get("visual", {}))
    settings = module_payload.get("settings", {})
    state_prefix = "toggle_on" if bool(settings.get("state", False)) else "toggle_off"
    text = visual.get(f"{state_prefix}_text", visual.get("text", ""))
    background = visual.get(f"{state_prefix}_background", visual.get("background", "#1f2937"))
    foreground = visual.get(f"{state_prefix}_foreground", visual.get("foreground", "#ffffff"))
    image_path = visual.get(f"{state_prefix}_image_path", visual.get("image_path", ""))
    icon_path = visual.get(f"{state_prefix}_icon_path", visual.get("icon_path", ""))
    asset_mode = visual.get(f"{state_prefix}_asset_mode", visual.get("asset_mode", "image"))
    icon_foreground = visual.get(
        f"{state_prefix}_icon_foreground",
        visual.get(f"{state_prefix}_foreground", visual.get("icon_foreground", visual.get("foreground", "#ffffff"))),
    )
    text_foreground = visual.get(
        f"{state_prefix}_text_foreground",
        visual.get(f"{state_prefix}_foreground", visual.get("text_foreground", visual.get("foreground", "#ffffff"))),
    )
    text_position = visual.get(f"{state_prefix}_text_position", visual.get("text_position", "center"))
    text_size = visual.get(f"{state_prefix}_text_size", visual.get("text_size", "medium"))
    icon_source = visual.get(f"{state_prefix}_icon_source", visual.get("icon_source", ""))
    icon_pack = visual.get(f"{state_prefix}_icon_pack", visual.get("icon_pack", ""))
    icon_name = visual.get(f"{state_prefix}_icon_name", visual.get("icon_name", ""))
    icon_weight = visual.get(f"{state_prefix}_icon_weight", visual.get("icon_weight", ""))
    visual.update(
        {
            "text": text,
            "background": background,
            "foreground": foreground,
            "image_path": image_path,
            "icon_path": icon_path,
            "asset_mode": asset_mode,
            "icon_foreground": icon_foreground,
            "text_foreground": text_foreground,
            "text_position": text_position,
            "text_size": text_size,
            "icon_source": icon_source,
            "icon_pack": icon_pack,
            "icon_name": icon_name,
            "icon_weight": icon_weight,
        }
    )
    return visual


def _draw_module_text(image: Image.Image, module_payload: dict) -> None:
    visual = _resolved_toggle_visual(module_payload)
    foreground = _safe_color(_visual_color(visual, "text_foreground"), "#ffffff")
    text = str(visual.get("text") or module_payload.get("settings", {}).get("key") or "")
    if not text:
        return
    position = str(visual.get("text_position", "center"))
    size_name = str(visual.get("text_size", "medium"))
    stroke, padding = _text_metrics(min(image.width, image.height), size_name)
    font = _load_font(min(image.width, image.height), size_name)
    text_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_layer)
    bbox = draw.multiline_textbbox((0, 0), text, font=font, align="center", stroke_width=stroke)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = ((image.width - width) // 2) - bbox[0]
    if position == "top":
        y = padding - bbox[1]
    elif position == "bottom":
        y = (image.height - height - padding) - bbox[1]
    else:
        y = ((image.height - height) // 2) - bbox[1]
    draw.multiline_text((x, y), text, fill=foreground + (255,), font=font, align="center", stroke_width=stroke)
    image.alpha_composite(text_layer)


def _touch_readout_content(module_payload: dict) -> tuple[str, str, str]:
    runtime_readout = module_payload.get("runtime_readout", {})
    if isinstance(runtime_readout, dict):
        title = str(runtime_readout.get("title", "")).strip()
        value = str(runtime_readout.get("value", "")).strip()
        subtitle = str(runtime_readout.get("subtitle", "")).strip()
        if title or value or subtitle:
            return title, value, subtitle
    source = str(module_payload.get("settings", {}).get("source", "device_brightness")).strip()
    return TOUCH_READOUT_PREVIEWS.get(source, ("Readout", "--", "Unavailable"))


def _touch_readout_percent(value: str) -> float | None:
    raw = value.strip()
    if not raw.endswith("%"):
        return None
    try:
        return float(raw[:-1].strip())
    except ValueError:
        return None


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, preferred_size: int, minimum_size: int = 14) -> ImageFont.ImageFont:
    for size in range(preferred_size, minimum_size - 1, -2):
        for font_name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(font_name, size)
                break
            except OSError:
                font = None
        if font is None:
            continue
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return font
    return ImageFont.load_default()


def _render_readout_icon(module_payload: dict, width: int, height: int, *, touch: bool) -> bytes:
    visual = _resolved_toggle_visual(module_payload)
    background = _safe_color(str(visual.get("background", "#000000")), "#000000")
    text_color = _safe_color(_visual_color(visual, "text_foreground"), "#ffffff")
    accent_color = _safe_color(_visual_color(visual, "icon_foreground"), "#ffffff")
    image = Image.new("RGBA", (width, height), background + (255,))
    draw = ImageDraw.Draw(image)
    title, value, subtitle = _touch_readout_content(module_payload)
    label_override = str(visual.get("text", "")).strip()
    if label_override:
        title = label_override
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=16 if touch else 24, fill=background + (255,))
    stack_position = str(visual.get("text_position", "middle")).strip().lower()
    size_name = str(visual.get("text_size", "medium")).strip().lower()
    value_size_map = {"small": 26 if touch else 32, "medium": 34 if touch else 42, "large": 42 if touch else 50}
    title_size_map = {"small": 13 if touch else 16, "medium": 15 if touch else 18, "large": 17 if touch else 21}
    subtitle_size_map = {"small": 11 if touch else 13, "medium": 12 if touch else 14, "large": 13 if touch else 16}
    width_padding = 20 if touch else 28
    title_font = _fit_text(draw, title, width - width_padding, title_size_map.get(size_name, 15), 11)
    value_font = _fit_text(draw, value, width - width_padding, value_size_map.get(size_name, 34), 18)
    subtitle_font = _fit_text(draw, subtitle, width - width_padding, subtitle_size_map.get(size_name, 12), 10)
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    value_bbox = draw.textbbox((0, 0), value, font=value_font)
    subtitle_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    title_w = title_bbox[2] - title_bbox[0]
    title_h = title_bbox[3] - title_bbox[1]
    value_w = value_bbox[2] - value_bbox[0]
    value_h = value_bbox[3] - value_bbox[1]
    subtitle_w = subtitle_bbox[2] - subtitle_bbox[0]
    subtitle_h = subtitle_bbox[3] - subtitle_bbox[1]
    percent_value = _touch_readout_percent(value)
    bar_h = 8 if percent_value is not None else 0
    bar_gap = 8 if percent_value is not None else 0
    stack_h = title_h + 6 + value_h + bar_gap + bar_h + 6 + subtitle_h
    if stack_position == "top":
        start_y = 10
    elif stack_position == "bottom":
        start_y = max(8, height - stack_h - 10)
    else:
        start_y = max(8, (height - stack_h) // 2)
    title_x = (width - title_w) // 2 - title_bbox[0]
    value_x = (width - value_w) // 2 - value_bbox[0]
    subtitle_x = (width - subtitle_w) // 2 - subtitle_bbox[0]
    title_y = start_y - title_bbox[1]
    value_y = start_y + title_h + 6 - value_bbox[1]
    subtitle_y = start_y + title_h + 6 + value_h + bar_gap + bar_h + 6 - subtitle_bbox[1]
    draw.text((title_x, title_y), title, font=title_font, fill=text_color + (190,))
    draw.text((value_x, value_y), value, font=value_font, fill=text_color + (255,))
    if percent_value is not None:
        bar_x = 14 if touch else 18
        bar_y = start_y + title_h + 6 + value_h + 4
        bar_w = width - (28 if touch else 36)
        fill_ratio = max(0.0, min(1.0, percent_value / 100.0))
        fill_w = max(10 if percent_value > 0 else 0, int(round(bar_w * fill_ratio)))
        track_color = tuple(max(0, channel - 90) for channel in accent_color)
        draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=4, fill=track_color + (255,))
        if fill_w > 0:
            draw.rounded_rectangle((bar_x, bar_y, bar_x + fill_w, bar_y + bar_h), radius=4, fill=accent_color + (255,))
    draw.text((subtitle_x, subtitle_y), subtitle, font=subtitle_font, fill=accent_color + (210,))
    if touch:
        return encode_vsd_touch_icon_jpeg(image.convert("RGB"))
    return encode_vsd_icon_jpeg(image.convert("RGB"))


def _render_touch_readout_icon(module_payload: dict) -> bytes:
    return _render_readout_icon(module_payload, N4_TOUCH_ICON_WIDTH, N4_TOUCH_ICON_HEIGHT, touch=True)


def _render_button_readout_icon(module_payload: dict) -> bytes:
    return _render_readout_icon(module_payload, N4_ICON_SIZE, N4_ICON_SIZE, touch=False)


def render_module_button_icon(module_payload: dict, asset_loader=None) -> bytes:
    if module_payload.get("type") == "builtin.touch_readout":
        return _render_button_readout_icon(module_payload)
    if (
        module_payload.get("type") == "builtin.advanced_touch"
        and str(module_payload.get("settings", {}).get("display_mode", "graphic")).strip().lower() == "readout"
    ):
        return _render_button_readout_icon(module_payload)
    visual = _resolved_toggle_visual(module_payload)
    background = _safe_color(visual.get("background", "#1f2937"), "#1f2937")
    image = Image.new("RGBA", (N4_ICON_SIZE, N4_ICON_SIZE), background + (255,))
    _draw_asset_background(image, _load_visual_asset(visual.get("image_path"), asset_loader, size=(N4_ICON_SIZE, N4_ICON_SIZE)))
    _draw_asset_icon(
        image,
        _load_visual_asset(
            visual.get("icon_path"),
            asset_loader,
            tint=_visual_color(visual, "icon_foreground"),
            size=(N4_ICON_SIZE, N4_ICON_SIZE),
        ),
    )
    _draw_module_text(image, module_payload)
    return encode_vsd_icon_jpeg(image.convert("RGB"))


def render_blank_button_icon(background: str = "#050816") -> bytes:
    image = Image.new("RGB", (N4_ICON_SIZE, N4_ICON_SIZE), _safe_color(background, "#050816"))
    return encode_vsd_icon_jpeg(image)


def render_module_touch_icon(module_payload: dict, asset_loader=None) -> bytes:
    if module_payload.get("type") == "builtin.touch_readout":
        return _render_touch_readout_icon(module_payload)
    if (
        module_payload.get("type") == "builtin.advanced_touch"
        and str(module_payload.get("settings", {}).get("display_mode", "graphic")).strip().lower() == "readout"
    ):
        return _render_touch_readout_icon(module_payload)
    visual = _resolved_toggle_visual(module_payload)
    background = _safe_color(visual.get("background", "#0f172a"), "#0f172a")
    foreground = _safe_color(visual.get("foreground", "#ffffff"), "#ffffff")
    image = Image.new("RGBA", (N4_TOUCH_ICON_WIDTH, N4_TOUCH_ICON_HEIGHT), background + (255,))
    _draw_asset_background(image, _load_visual_asset(visual.get("image_path"), asset_loader, size=(N4_TOUCH_ICON_WIDTH, N4_TOUCH_ICON_HEIGHT)))
    _draw_asset_icon(
        image,
        _load_visual_asset(
            visual.get("icon_path"),
            asset_loader,
            tint=_visual_color(visual, "icon_foreground"),
            size=(N4_TOUCH_ICON_WIDTH, N4_TOUCH_ICON_HEIGHT),
        ),
    )
    if module_payload.get("type") != "builtin.advanced_touch":
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((6, 6, N4_TOUCH_ICON_WIDTH - 7, N4_TOUCH_ICON_HEIGHT - 7), radius=12, outline=foreground, width=3)
    _draw_module_text(image, module_payload)
    return encode_vsd_touch_icon_jpeg(image.convert("RGB"))


def render_blank_touch_icon(background: str = "#050816") -> bytes:
    image = Image.new("RGB", (N4_TOUCH_ICON_WIDTH, N4_TOUCH_ICON_HEIGHT), _safe_color(background, "#050816"))
    return encode_vsd_touch_icon_jpeg(image)


def button_address_from_control_id(control_id: str) -> int | None:
    if not control_id.startswith("button_"):
        return None
    try:
        index = int(control_id.split("_", 1)[1])
    except ValueError:
        return None
    try:
        return n4_main_button_display_address(index)
    except ValueError:
        return None


def touch_address_from_control_id(control_id: str) -> int | None:
    if not control_id.startswith("touch_"):
        return None
    try:
        index = int(control_id.split("_", 1)[1])
    except ValueError:
        return None
    if 1 <= index <= 4:
        return index
    return None
