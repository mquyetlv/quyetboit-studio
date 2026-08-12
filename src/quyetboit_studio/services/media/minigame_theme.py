from __future__ import annotations

import hashlib
from pathlib import Path
from typing import ClassVar

from PIL import Image, ImageFont

from ...core.minigame import (
    AlphaMaskProfile,
    CanvasSpec,
    MinigameThemeManifest,
    TextPlacement,
    TextSlot,
    TextStyle,
)
from ...core.models import PipelineError


class MinigameThemeService:
    """Discovers immutable bundled theme assets and returns typed layout metadata."""

    CANVAS: ClassVar[CanvasSpec] = CanvasSpec()
    _PROFILES: ClassVar[dict[str, AlphaMaskProfile]] = {
        "theme-1": AlphaMaskProfile(32, 3, 0.82, 35),
        "theme-2": AlphaMaskProfile(28, 3, 0.88, 30),
        "theme-3": AlphaMaskProfile(28, 3, 0.90, 30),
        "theme-4": AlphaMaskProfile(32, 3, 0.82, 35),
        "theme-5": AlphaMaskProfile(28, 3, 0.88, 30),
    }
    _PALETTES: ClassVar[dict[str, tuple[str, ...]]] = {
        "theme-1": ("#43E6F4", "#07152B"),
        "theme-2": ("#BDEFFF", "#426B91"),
        "theme-3": ("#F5D47A", "#24334D"),
        "theme-4": ("#72DDF7", "#563D8C"),
        "theme-5": ("#82D98A", "#315C3B"),
    }

    def __init__(self, assets_root: Path | None = None, fonts_root: Path | None = None) -> None:
        self.assets_root = assets_root or Path(__file__).resolve().parents[2] / "assets" / "theme"
        self.fonts_root = fonts_root or Path(__file__).resolve().parents[2] / "assets" / "fonts"

    @staticmethod
    def _slots() -> dict[str, TextSlot]:
        # Coordinates are on the canonical 1080×1920 canvas.
        return {
            "header": TextSlot("header", (420, 103, 660, 184), 1),
            "header_wide": TextSlot("header_wide", (250, 103, 830, 184), 1),
            "meaning": TextSlot("meaning", (130, 340, 950, 770), 2),
            "word": TextSlot("word", (130, 990, 950, 1185), 2),
            "ipa": TextSlot("ipa", (130, 1180, 950, 1255), 1),
            "phase": TextSlot("phase", (130, 1480, 950, 1740), 2),
            "footer": TextSlot("footer", (278, 1797, 802, 1868), 2),
            "start_title": TextSlot("start_title", (166, 459, 913, 873), 1),
            "start_topic": TextSlot("start_topic", (235, 1116, 845, 1229), 2),
        }

    @staticmethod
    def _styles() -> dict[str, TextStyle]:
        return {
            "title": TextStyle("Title", "Bebas Neue", "BebasNeue-Regular.ttf", 68, 118),
            "topic": TextStyle("Topic", "Montserrat", "Montserrat-Variable.ttf", 25, 44),
            "header": TextStyle(
                "Header", "Montserrat", "Montserrat-Variable.ttf", 18, 30,
                bold=True, spacing=3, margin=80,
            ),
            "label": TextStyle(
                "Label", "Montserrat", "Montserrat-Variable.ttf", 16, 24, "#70D8FF",
                bold=True, spacing=4, margin=80,
            ),
            "hero": TextStyle(
                "Hero", "Montserrat", "Montserrat-Variable.ttf", 36, 88, bold=True,
            ),
            "word": TextStyle(
                "Word", "Montserrat", "Montserrat-Variable.ttf", 40, 126, bold=True,
            ),
            "body": TextStyle(
                "Body", "Montserrat", "Montserrat-Variable.ttf", 18, 34, "#D5EEFF",
            ),
            "ipa": TextStyle(
                "IPA", "Montserrat", "Montserrat-Italic-Variable.ttf", 20, 46, italic=True,
            ),
            "start": TextStyle(
                "Start", "Bebas Neue", "BebasNeue-Regular.ttf", 52, 104,
                bold=True, spacing=1, margin=90,
            ),
        }

    @staticmethod
    def _placements() -> dict[str, TextPlacement]:
        return {
            "word_header": TextPlacement("header", "header"),
            "ending_header": TextPlacement("header", "header_wide"),
            "meaning_label": TextPlacement("label", "meaning", 360),
            "meaning_value": TextPlacement("hero", "meaning"),
            "reveal_label": TextPlacement("label", "word", 895),
            "countdown": TextPlacement("body", "word", 942, 2),
            "english_label": TextPlacement("label", "word", 1045),
            "english_word": TextPlacement("word", "word", 1170),
            "ipa": TextPlacement("ipa", "ipa", 1305),
            "voice_header": TextPlacement("header", "phase", 1605, 2),
            "voice_helper": TextPlacement("body", "phase", 1655, 2),
            "review_label": TextPlacement("label", "phase", 1515),
            "review_meaning": TextPlacement("body", "phase", 1580),
            "review_example": TextPlacement("body", "phase", 1640),
            "score_title": TextPlacement("start", "meaning", 520),
            "score_beginner": TextPlacement("body", "word", 1040),
            "score_good": TextPlacement("body", "word", 1130),
            "score_perfect": TextPlacement("header", "word", 1220),
            "cta_title": TextPlacement("start", "meaning", 620),
            "cta_helper": TextPlacement("body", "phase", 1600),
            "cta_topic": TextPlacement("header", "footer"),
        }

    def discover(self) -> dict[str, MinigameThemeManifest]:
        return {theme_id: self.get(theme_id) for theme_id in self._PROFILES}

    def get(self, theme_id: str) -> MinigameThemeManifest:
        if theme_id not in self._PROFILES:
            raise PipelineError("theme_unknown", "Theme minigame không được hỗ trợ.")
        root = self.assets_root / theme_id
        start = root / "start-screen.png"
        background = root / "background-video.png"
        # One release migration alias: old projects/assets can still be opened once.
        if not background.is_file():
            background = root / "backgound-videos.png"
        text = root / "background-text.png"
        self._validate_image(start, (941, 1672), "start screen")
        self._validate_image(background, (941, 1672), "background video")
        self._validate_image(text, (971, 1619), "background text")
        self._validate_fonts(self._styles())
        checksum = self._checksum((start, background, text))
        return MinigameThemeManifest(
            theme_id,
            theme_id.replace("-", " ").title(),
            self._PALETTES[theme_id],
            ("9:16",),
            self.CANVAS,
            start,
            background,
            text,
            self._PROFILES[theme_id],
            self._slots(),
            self._styles(),
            self._placements(),
            3,
            checksum,
        )

    @staticmethod
    def _validate_image(path: Path, size: tuple[int, int], label: str) -> None:
        if not path.is_file():
            raise PipelineError("theme_asset_missing", f"Thiếu asset {label} của theme.")
        with Image.open(path) as image:
            if image.size != size or image.mode not in {"RGB", "RGBA"}:
                raise PipelineError(
                    "theme_asset_invalid", f"Asset {label} có kích thước hoặc màu không hợp lệ."
                )

    def _validate_fonts(self, styles: dict[str, TextStyle]) -> None:
        expected = {style.font_file: style.font for style in styles.values()}
        for filename, family in expected.items():
            path = self.fonts_root / filename
            if not path.is_file():
                raise PipelineError(
                    "theme_font_missing",
                    f"Thiếu font {filename}. Hãy cài đặt lại ứng dụng hoặc khôi phục assets/fonts.",
                )
            try:
                actual_family = ImageFont.truetype(path, 20).getname()[0]
            except OSError as exc:
                raise PipelineError(
                    "theme_font_invalid",
                    f"Font {filename} bị hỏng. Hãy cài đặt lại ứng dụng.",
                    debug_detail=type(exc).__name__,
                ) from exc
            if actual_family != family:
                raise PipelineError(
                    "theme_font_invalid",
                    f"Font {filename} không đúng family {family}. Hãy cài đặt lại ứng dụng.",
                )

    @staticmethod
    def _checksum(paths: tuple[Path, ...]) -> str:
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.read_bytes())
        return digest.hexdigest()
