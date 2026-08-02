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
    "Overlay ánh sáng": "overlay",
    "Text đầu video": "intro_text",
    "Text cố định phía trên": "intro_text",
    "Chữ chạy quanh video": "scroll_text",
}


def new_studio_layer(kind: str, *, name: str = "", **values: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "id": f"{kind}-{uuid4().hex[:10]}",
        "kind": kind,
        "name": name or kind.replace("_", " ").title(),
        "enabled": True,
        "start": 0.0,
        "duration": 5.0,
    }
    if kind in {"logo", "overlay", "intro_text", "scroll_text", "subtitle"}:
        defaults.update({
            "full_duration": True,
            "margin": 24,
            "width_percent": 90,
            "height": 64,
            "background_color": "#000000",
            "background_opacity": 60,
            "font_color": "white",
        })
    if kind in {"logo", "overlay"}:
        defaults.update({
            "path": "",
            "position": "top-right" if kind == "logo" else "center",
            "scale": 20 if kind == "logo" else 100,
            "opacity": 100 if kind == "logo" else 50,
            "width_percent": 20 if kind == "logo" else 100,
            "height": 80 if kind == "logo" else 0,
            "background_color": "transparent",
            "background_opacity": 0,
        })
    elif kind in {"intro_text", "scroll_text"}:
        defaults.update({
            "text": "",
            "position": "top" if kind == "intro_text" else "bottom",
            "font_size": 48,
            "height": 72 if kind == "intro_text" else 56,
        })
    elif kind == "subtitle":
        defaults.update({"position": "bottom", "font_size": 42})
    defaults.update(values)
    return defaults


def normalize_studio_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    source = deepcopy(settings or {})
    normalized = {
        "speed": max(0.25, min(4.0, float(source.get("speed", 1.0)))),
        "pitch": max(-12, min(12, int(source.get("pitch", 0)))),
        "zoom": max(100, min(200, int(source.get("zoom", 100)))),
        "color": max(0, min(200, int(source.get("color", 100)))),
        "flip_horizontal": bool(source.get("flip_horizontal", False)),
        "flip_vertical": bool(source.get("flip_vertical", False)),
    }
    layers: list[dict[str, Any]] = []
    for item in source.get("layers") or BASE_LAYERS:
        if isinstance(item, str):
            kind = LAYER_NAMES.get(item, "")
            if not kind:
                continue
            base = next((layer for layer in BASE_LAYERS if layer["kind"] == kind), None)
            layers.append(new_studio_layer(
                kind,
                id=str(base["id"]) if base else f"{kind}-{uuid4().hex[:10]}",
                name=str(base["name"]) if base else item,
                start=float(source.get("layer_start", 0)),
                duration=float(source.get("layer_duration", 5)),
            ))
            continue
        if not isinstance(item, dict) or not item.get("kind"):
            continue
        layer = new_studio_layer(str(item["kind"]), name=str(item.get("name") or ""))
        layer.update(item)
        layer["enabled"] = bool(layer.get("enabled", True))
        layer["start"] = max(0.0, float(layer.get("start", 0)))
        layer["duration"] = max(0.1, float(layer.get("duration", 5)))
        layer["full_duration"] = bool(layer.get("full_duration", True))
        layer["scale"] = max(5, min(200, int(layer.get("scale", 100))))
        layer["opacity"] = max(0, min(100, int(layer.get("opacity", 100))))
        layer["font_size"] = max(12, min(200, int(layer.get("font_size", 48))))
        if "width_percent" not in item and "scale" in item:
            layer["width_percent"] = item["scale"]
        layer["width_percent"] = max(5, min(100, int(layer.get("width_percent", 90))))
        layer["height"] = max(0, min(2160, int(layer.get("height", 64))))
        layer["margin"] = max(0, min(1000, int(layer.get("margin", 24))))
        layer["background_opacity"] = max(
            0, min(100, int(layer.get("background_opacity", 60)))
        )
        layer["background_color"] = str(layer.get("background_color", "#000000"))
        layer["font_color"] = str(layer.get("font_color", "white"))
        layers.append(layer)
    for base in reversed(BASE_LAYERS):
        if not any(layer.get("kind") == base["kind"] for layer in layers):
            layers.insert(0, new_studio_layer(
                str(base["kind"]), id=str(base["id"]), name=str(base["name"])
            ))
    normalized["layers"] = layers
    return normalized


def enabled_layer(settings: dict[str, Any], kind: str) -> bool:
    return any(
        layer.get("kind") == kind and layer.get("enabled", True)
        for layer in normalize_studio_settings(settings)["layers"]
    )
