from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

BASE_LAYERS = (
    {"id": "video", "kind": "video", "name": "Video", "enabled": True},
    {"id": "voice", "kind": "voice", "name": "Voice", "enabled": True},
    {"id": "subtitle", "kind": "subtitle", "name": "Subtitle", "enabled": True},
)

LAYER_NAMES = {
    "Video": "video",
    "Voice": "voice",
    "Subtitle": "subtitle",
    "Logo thương hiệu": "logo",
    "Text đầu video": "intro_text",
    "Text cố định phía trên": "intro_text",
    "Chữ chạy quanh video": "scroll_text",
}


def new_studio_layer(kind: str, *, name: str = "", **values: Any) -> dict[str, Any]:
    """Create the small, public configuration surface supported by each layer kind."""
    defaults: dict[str, Any] = {
        "id": f"{kind}-{uuid4().hex[:10]}",
        "kind": kind,
        "name": name or kind.replace("_", " ").title(),
        "enabled": True,
    }
    if kind == "subtitle":
        defaults.update({
            "position": "bottom",
            "margin": 24,
            "background_color": "#000000",
            "font_color": "#FFFFFF",
            "font_size": 42,
            "background_opacity": 60,
        })
    elif kind == "logo":
        defaults.update({
            "path": "",
            "position": "top-right",
            "opacity": 100,
            "margin_x": 24,
            "margin_y": 24,
            "height": 80,
        })
    elif kind == "intro_text":
        defaults.update({
            "text": "",
            "position": "top",
            "margin": 24,
            "background_color": "#000000",
            "font_color": "#FFFFFF",
            "font_size": 48,
            "background_opacity": 60,
        })
    elif kind == "scroll_text":
        defaults.update({
            "text": "",
            "font_size": 48,
            "font_color": "#FFFFFF",
            "opacity": 60,
            "speed": 1.0,
            "bold": True,
        })
    defaults.update(values)
    return defaults


def _clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return fallback


def _clamp_float(value: Any, minimum: float, maximum: float, fallback: float) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return fallback


def _normalize_layer(item: dict[str, Any]) -> dict[str, Any]:
    kind = str(item["kind"])
    layer = new_studio_layer(
        kind,
        id=str(item.get("id") or f"{kind}-{uuid4().hex[:10]}"),
        name=str(item.get("name") or ""),
    )
    if kind in {"video", "voice"}:
        # Video and voice are structural layers, not configurable effects.
        layer["enabled"] = True
        return layer

    layer["enabled"] = bool(item.get("enabled", True))
    if kind in {"subtitle", "intro_text"}:
        position = str(item.get("position", layer["position"]))
        layer["position"] = position if position in {"top", "bottom"} else layer["position"]
        layer["margin"] = _clamp_int(item.get("margin", 24), 0, 1000, 24)
        layer["font_size"] = _clamp_int(item.get("font_size", layer["font_size"]), 12, 200, layer["font_size"])
        layer["font_color"] = str(item.get("font_color", layer["font_color"]))
        layer["background_color"] = str(item.get("background_color", "#000000"))
        layer["background_opacity"] = _clamp_int(item.get("background_opacity", 60), 0, 100, 60)
        if kind == "intro_text":
            layer["text"] = str(item.get("text", ""))
    elif kind == "logo":
        position = str(item.get("position", "top-right"))
        layer["position"] = position if position in {
            "top-left", "top-right", "bottom-left", "bottom-right",
        } else "top-right"
        legacy_margin = item.get("margin", 24)
        layer["path"] = str(item.get("path", ""))
        layer["opacity"] = _clamp_int(item.get("opacity", 100), 0, 100, 100)
        layer["margin_x"] = _clamp_int(item.get("margin_x", legacy_margin), 0, 1000, 24)
        layer["margin_y"] = _clamp_int(item.get("margin_y", legacy_margin), 0, 1000, 24)
        layer["height"] = _clamp_int(item.get("height", 80), 1, 2160, 80)
    elif kind == "scroll_text":
        layer["text"] = str(item.get("text", ""))
        layer["font_size"] = _clamp_int(item.get("font_size", 48), 12, 200, 48)
        layer["font_color"] = str(item.get("font_color", item.get("color", "#FFFFFF")))
        layer["opacity"] = _clamp_int(item.get("opacity", 60), 0, 100, 60)
        layer["speed"] = _clamp_float(item.get("speed", 1.0), 0.1, 10.0, 1.0)
        layer["bold"] = bool(item.get("bold", True))
    return layer


def normalize_studio_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    source = deepcopy(settings or {})
    normalized = {
        "speed": _clamp_float(source.get("speed", 1.0), 0.25, 4.0, 1.0),
        "pitch": _clamp_int(source.get("pitch", 0), -12, 12, 0),
        "zoom": _clamp_int(source.get("zoom", 100), 100, 200, 100),
        "color": _clamp_int(source.get("color", 100), 0, 200, 100),
        "flip_horizontal": bool(source.get("flip_horizontal", False)),
        "flip_vertical": bool(source.get("flip_vertical", False)),
    }
    layers: list[dict[str, Any]] = []
    for item in source.get("layers") or BASE_LAYERS:
        if isinstance(item, str):
            kind = LAYER_NAMES.get(item, "")
            if not kind:
                continue
            base = next((entry for entry in BASE_LAYERS if entry["kind"] == kind), None)
            item = {
                "kind": kind,
                "id": str(base["id"]) if base else f"{kind}-{uuid4().hex[:10]}",
                "name": str(base["name"]) if base else item,
            }
        if (
            isinstance(item, dict)
            and item.get("kind") in {"video", "voice", "subtitle", "logo", "intro_text", "scroll_text"}
        ):
            layers.append(_normalize_layer(item))
    for base in reversed(BASE_LAYERS):
        if not any(layer.get("kind") == base["kind"] for layer in layers):
            layers.insert(0, _normalize_layer(dict(base)))
    normalized["layers"] = layers
    return normalized


def enabled_layer(settings: dict[str, Any], kind: str) -> bool:
    return any(
        layer.get("kind") == kind and layer.get("enabled", True)
        for layer in normalize_studio_settings(settings)["layers"]
    )
