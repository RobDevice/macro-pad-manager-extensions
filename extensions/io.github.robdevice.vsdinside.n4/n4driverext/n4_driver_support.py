from __future__ import annotations

from dataclasses import asdict

from macro_pad_manager.core.layout import Bounds, ControlDefinition, DeviceLayout
from macro_pad_manager.daemon.drivers.base import Driver, DriverManifest

from .n4_icons import (
    button_address_from_control_id,
    render_blank_button_icon,
    render_blank_touch_icon,
    render_module_button_icon,
    render_module_touch_icon,
    touch_address_from_control_id,
)
from .n4_session import N4DeviceSession
from .vendor.vsdn4 import hidraw
from .vendor.vsdn4.icon_upload import build_boot_logo_from_path


class N4DriverSupport(Driver):
    manifest = DriverManifest(
        schema_version=1,
        type="driver",
        id="io.github.robdevice.vsdinside.n4",
        name="VSDinside N4 Driver",
        version="0.1.1",
        author="RobDevice",
        licence="GPL-3.0-only",
        description="Driver extension for the VSDinside/HOTSPOTEK N4.",
        min_app_version="0.1.0",
    )

    def __init__(self) -> None:
        self._session = N4DeviceSession()

    def detect(self) -> dict | None:
        device = hidraw.find_vendor_device()
        if device is None:
            return None
        return {
            "node": str(device.node),
            "name": device.name,
            "serial": device.uniq,
            "hid_id": device.hid_id,
            "driver_id": self.manifest.id,
            "connected": True,
        }

    def default_settings(self) -> dict:
        return {"brightness": 80, "boot_splash_path": ""}

    def settings_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "brightness": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 80,
                    "title": "Brightness",
                },
                "boot_splash_path": {
                    "type": "string",
                    "default": "",
                    "title": "Boot splash",
                    "format": "image-asset",
                },
            },
        }

    def profile_settings_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "horizontal_swipe_behavior": {
                    "type": "string",
                    "enum": ["none", "change_page", "change_profile"],
                    "default": "change_page",
                    "title": "Horizontal Swipe",
                },
                "vertical_swipe_behavior": {
                    "type": "string",
                    "enum": ["none", "change_page", "change_profile"],
                    "default": "none",
                    "title": "Vertical Swipe",
                },
            },
        }

    def layout(self) -> DeviceLayout:
        controls: list[ControlDefinition] = []

        top_y = 34
        bottom_y = 178
        button_w = 132
        button_h = 132
        button_gap = 12
        button_start_x = 46

        for index in range(5):
            button_id = index + 1
            controls.append(
                ControlDefinition(
                    id=f"button_{button_id}",
                    label=f"Top {button_id}",
                    kind="button",
                    bounds=Bounds(button_start_x + index * (button_w + button_gap), top_y, button_w, button_h),
                    triggers=["press", "release"],
                    capabilities=["press", "release", "display_image"],
                    runtime_ids={"press": button_id, "release": button_id},
                )
            )
        for index in range(5):
            button_id = index + 6
            controls.append(
                ControlDefinition(
                    id=f"button_{button_id}",
                    label=f"Bottom {index + 1}",
                    kind="button",
                    bounds=Bounds(button_start_x + index * (button_w + button_gap), bottom_y, button_w, button_h),
                    triggers=["press", "release"],
                    capabilities=["press", "release", "display_image"],
                    runtime_ids={"press": button_id, "release": button_id},
                )
            )

        touch_y = 334
        touch_w = 170
        touch_h = 64
        touch_start_x = 45
        touch_gap = 10
        for index, runtime_id in enumerate((0x40, 0x41, 0x42, 0x43), 1):
            controls.append(
                ControlDefinition(
                    id=f"touch_{index}",
                    label=f"Touch {index}",
                    kind="touch",
                    bounds=Bounds(touch_start_x + (index - 1) * (touch_w + touch_gap), touch_y, touch_w, touch_h),
                    triggers=["touch", "swipe_up", "swipe_right", "swipe_down", "swipe_left"],
                    capabilities=["touch", "swipe", "touch_zone_image"],
                    runtime_ids={
                        "touch": runtime_id,
                        "swipe_up": 0xB1,
                        "swipe_right": 0x38,
                        "swipe_down": 0xB2,
                        "swipe_left": 0x39,
                    },
                )
            )

        knob_specs = [
            ("knob_1", "Knob 1", 86, 434, 0xA0, 0xA1, 0x37),
            ("knob_2", "Knob 2", 266, 434, 0x50, 0x51, 0x35),
            ("knob_3", "Knob 3", 446, 434, 0x90, 0x91, 0x33),
            ("knob_4", "Knob 4", 626, 434, 0x70, 0x71, 0x36),
        ]
        for control_id, label, x, y, left_id, right_id, press_id in knob_specs:
            controls.append(
                ControlDefinition(
                    id=control_id,
                    label=label,
                    kind="dial",
                    bounds=Bounds(x, y, 86, 86),
                    triggers=["left", "right", "press"],
                    capabilities=["rotary_left", "rotary_right", "press"],
                    runtime_ids={"left": left_id, "right": right_id, "press": press_id},
                )
            )

        return DeviceLayout(
            driver_id=self.manifest.id,
            device_name="VSDinside / HOTSPOTEK N4",
            preview_width=800,
            preview_height=520,
            controls=controls,
        )

    def resolve_runtime_event(self, event: dict) -> dict | None:
        location = event.get("location")
        state = event.get("state")
        if location is None:
            return None

        if 1 <= int(location) <= 10:
            button_trigger = "press" if int(state or 0) == 1 else "release"
            return {
                "control_id": f"button_{location}",
                "trigger_id": button_trigger,
                "location": location,
                "state": state,
                "timestamp": event.get("timestamp"),
                "description": event.get("description"),
            }

        touch_zones = {
            0x40: "touch_1",
            0x41: "touch_2",
            0x42: "touch_3",
            0x43: "touch_4",
        }
        if location in touch_zones:
            return {
                "control_id": touch_zones[location],
                "trigger_id": "touch",
                "location": location,
                "state": state,
                "timestamp": event.get("timestamp"),
                "description": event.get("description"),
            }

        gesture_map = {
            0xB1: ("touch_1", "swipe_up"),
            0x38: ("touch_1", "swipe_right"),
            0xB2: ("touch_1", "swipe_down"),
            0x39: ("touch_1", "swipe_left"),
            0xA0: ("knob_1", "left"),
            0xA1: ("knob_1", "right"),
            0x37: ("knob_1", "press"),
            0x50: ("knob_2", "left"),
            0x51: ("knob_2", "right"),
            0x35: ("knob_2", "press"),
            0x90: ("knob_3", "left"),
            0x91: ("knob_3", "right"),
            0x33: ("knob_3", "press"),
            0x70: ("knob_4", "left"),
            0x71: ("knob_4", "right"),
            0x36: ("knob_4", "press"),
        }
        if location not in gesture_map:
            return None
        control_id, trigger_id = gesture_map[location]
        return {
            "control_id": control_id,
            "trigger_id": trigger_id,
            "location": location,
            "state": state,
            "timestamp": event.get("timestamp"),
            "description": event.get("description"),
        }

    def activate_runtime(self, detected: dict, driver_settings: dict, event_callback) -> None:
        self._session.set_event_callback(event_callback)
        self._session.start(
            str(detected.get("node") or ""),
            brightness=int(driver_settings.get("brightness", self.default_settings().get("brightness", 80))),
        )

    def stop_runtime(self) -> None:
        self._session.stop()

    def runtime_snapshot(self) -> dict:
        return asdict(self._session.snapshot())

    def force_heartbeat(self) -> bool:
        return self._session.force_heartbeat()

    def apply_runtime_settings(
        self,
        driver_settings: dict,
        changed_keys: set[str] | None = None,
        *,
        asset_loader=None,
    ) -> None:
        changed = changed_keys or set(driver_settings)
        if "brightness" in changed:
            self._session.set_brightness(int(driver_settings.get("brightness", self.default_settings().get("brightness", 80))))
        if "boot_splash_path" in changed:
            snapshot = self._session.snapshot()
            if not snapshot.connected or asset_loader is None:
                return
            relative_path = str(driver_settings.get("boot_splash_path", "") or "").strip()
            if not relative_path:
                return
            resolved = asset_loader(relative_path)
            if resolved is None:
                return
            try:
                target = build_boot_logo_from_path(resolved)
            except (OSError, ValueError):
                return
            self._session.upload_boot_logo(target.image)

    def sync_control_visual(self, control_id: str, module_payload: dict, asset_loader=None) -> None:
        snapshot = self._session.snapshot()
        if not snapshot.connected:
            return
        button_address = button_address_from_control_id(control_id)
        if button_address is not None:
            self._session.upload_button_image(
                button_address,
                render_module_button_icon(module_payload, asset_loader=asset_loader),
            )
            return
        touch_address = touch_address_from_control_id(control_id)
        if touch_address is not None:
            self._session.upload_button_image(
                touch_address,
                render_module_touch_icon(module_payload, asset_loader=asset_loader),
            )

    def clear_control_visual(self, control_id: str) -> None:
        snapshot = self._session.snapshot()
        if not snapshot.connected:
            return
        button_address = button_address_from_control_id(control_id)
        if button_address is not None:
            self._session.upload_button_image(button_address, render_blank_button_icon())
            return
        touch_address = touch_address_from_control_id(control_id)
        if touch_address is not None:
            self._session.upload_button_image(touch_address, render_blank_touch_icon())

    def clear_all_visuals(self) -> None:
        snapshot = self._session.snapshot()
        if not snapshot.connected:
            return
        for button_address in range(1, 11):
            self._session.upload_button_image(button_address, render_blank_button_icon(background="#000000"))
        for touch_address in range(0x40, 0x44):
            self._session.upload_button_image(touch_address, render_blank_touch_icon(background="#000000"))
