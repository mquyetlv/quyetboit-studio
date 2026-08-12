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
    ThemeColorPalette,
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
        "theme-1": ("#F7FBFF", "#43E6F4", "#CBEFFF"),
        "theme-2": ("#123A5A", "#075E8C", "#315B78"),
        "theme-3": ("#263247", "#745300", "#4C4B59"),
        "theme-4": ("#F8F7FF", "#72DDF7", "#DCCFFF"),
        "theme-5": ("#173E29", "#17652F", "#315C3B"),
    }
    # Body themes 1/4 are dark; 2/3/5 are light. Review follows the same split except
    # Theme 1, whose review card is light. These semantic palettes are deliberately
    # explicit so no scene inherits an unreadable global white foreground.
    _COLORS: ClassVar[dict[str, ThemeColorPalette]] = {
        "theme-1": ThemeColorPalette(
            "#F7FBFF", "#CBEFFF", "#43E6F4", "#061425", "#061425",
            "#073A82", "#315B87", "#0B6596", "#F7FBFF",
            "#F7FBFF", "#9DEBFF", "#061425",
        ),
        "theme-2": ThemeColorPalette(
            "#123A5A", "#315B78", "#075E8C", "#FFFFFF", "#D8EAF4",
            "#073A82", "#315B87", "#0B6596", "#FFFFFF",
            "#123A5A", "#075E8C", "#123A5A",
        ),
        "theme-3": ThemeColorPalette(
            "#263247", "#4C4B59", "#745300", "#FFFFFF", "#E9E1CD",
            "#263247", "#4C4B59", "#745300", "#FFFFFF",
            "#263247", "#745300", "#263247",
        ),
        "theme-4": ThemeColorPalette(
            "#F8F7FF", "#DCCFFF", "#72DDF7", "#160F31", "#160F31",
            "#F8F7FF", "#DCCFFF", "#72DDF7", "#160F31",
            "#F8F7FF", "#B9F1FF", "#160F31",
        ),
        "theme-5": ThemeColorPalette(
            "#173E29", "#315C3B", "#17652F", "#FFFFFF", "#DDEBDF",
            "#173E29", "#315C3B", "#17652F", "#FFFFFF",
            "#173E29", "#17652F", "#173E29",
        ),
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
            "review_meaning": TextSlot("review_meaning", (202, 304, 878, 423), 2),
            "review_word": TextSlot("review_word", (147, 529, 932, 952), 2),
            "review_ipa": TextSlot("review_ipa", (193, 1049, 887, 1104), 1),
            "review_word_type": TextSlot("review_word_type", (316, 1189, 764, 1274), 1),
            "review_example": TextSlot("review_example", (148, 1395, 933, 1676), 4),
            "score_beginner": TextSlot("score_beginner", (390, 510, 730, 620), 1),
            "score_good": TextSlot("score_good", (390, 925, 730, 1035), 1),
            "score_perfect": TextSlot("score_perfect", (390, 1320, 730, 1430), 1),
        }

    @classmethod
    def _styles(cls, theme_id: str) -> dict[str, TextStyle]:
        colors = cls._COLORS[theme_id]

        def style(*args, surface: str = "body", role: str = "primary", **kwargs) -> TextStyle:
            prefix = "review_" if surface == "review" else ""
            color = getattr(colors, f"{prefix}{role}")
            outline = colors.review_outline if surface == "review" else colors.outline
            return TextStyle(
                *args,
                color=color,
                outline_color=outline,
                shadow_color=colors.shadow,
                **kwargs,
            )

        return {
            "title": TextStyle(
                "Title", "Bebas Neue", "BebasNeue-Regular.ttf", 68, 118,
                colors.hook_primary, colors.hook_outline, colors.shadow, 3, 1,
            ),
            "topic": TextStyle(
                "Topic", "Montserrat", "Montserrat-Variable.ttf", 25, 44,
                colors.hook_primary, colors.hook_outline, colors.shadow, 1, 1, bold=True,
            ),
            "header": style(
                "Header", "Montserrat", "Montserrat-Variable.ttf", 18, 30,
                role="accent", bold=True, spacing=3, margin=80,
            ),
            "label": style(
                "Label", "Montserrat", "Montserrat-Variable.ttf", 16, 24,
                role="accent", bold=True, spacing=4, margin=80,
            ),
            "hero": style(
                "Hero", "Montserrat", "Montserrat-Variable.ttf", 36, 88, bold=True,
            ),
            "word": style(
                "Word", "Montserrat", "Montserrat-Variable.ttf", 40, 126, bold=True,
            ),
            "body": style(
                "Body", "Montserrat", "Montserrat-Variable.ttf", 18, 34,
                role="secondary",
            ),
            "ipa": style(
                "IPA", "Montserrat", "Montserrat-Italic-Variable.ttf", 20, 46,
                role="secondary", italic=True,
            ),
            "review_meaning": style(
                "ReviewMeaning", "Montserrat", "Montserrat-Variable.ttf", 30, 68,
                surface="review", bold=True,
            ),
            "review_word": style(
                "ReviewWord", "Montserrat", "Montserrat-Variable.ttf", 44, 126,
                surface="review", bold=True,
            ),
            "review_ipa": style(
                "ReviewIPA", "Montserrat", "Montserrat-Italic-Variable.ttf", 20, 42,
                surface="review", role="secondary", italic=True,
            ),
            "review_word_type": style(
                "ReviewWordType", "Montserrat", "Montserrat-Variable.ttf", 18, 34,
                surface="review", role="accent", bold=True,
            ),
            "review_example": style(
                "ReviewExample", "Montserrat", "Montserrat-Variable.ttf", 18, 38,
                surface="review", role="secondary",
            ),
            "start": style(
                "Start", "Bebas Neue", "BebasNeue-Regular.ttf", 52, 104,
                bold=True, spacing=1, margin=90,
            ),
            "score": TextStyle(
                "Score", "Montserrat", "Montserrat-Variable.ttf", 24, 44,
                "#FFFFFF", "#173A70", "#173A70", 3, 1, bold=True,
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
            "answer_word_type": TextPlacement("header", "phase", 1585, 2),
            "review_meaning": TextPlacement("review_meaning", "review_meaning"),
            "review_word": TextPlacement("review_word", "review_word"),
            "review_ipa": TextPlacement("review_ipa", "review_ipa"),
            "review_word_type": TextPlacement("review_word_type", "review_word_type"),
            "review_example": TextPlacement("review_example", "review_example"),
            "score_beginner": TextPlacement("score", "score_beginner"),
            "score_good": TextPlacement("score", "score_good"),
            "score_perfect": TextPlacement("score", "score_perfect"),
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
        score = root / "score.png"
        review_start = root / "review-start-screen.png"
        if not review_start.is_file():
            review_start = root / "start-review-screen.png"
        review = root / "review.png"
        if not review.is_file():
            review = root / "rewview.png"
        self._validate_image(start, (941, 1672), "start screen")
        self._validate_image(background, (941, 1672), "background video")
        self._validate_image(text, (971, 1619), "background text")
        self._validate_image(score, (941, 1672), "score screen")
        self._validate_image(review_start, (941, 1672), "review start screen")
        self._validate_image(review, (941, 1672), "review screen")
        styles = self._styles(theme_id)
        self._validate_fonts(styles)
        checksum = self._checksum((start, background, text, score, review_start, review))
        return MinigameThemeManifest(
            theme_id,
            theme_id.replace("-", " ").title(),
            self._PALETTES[theme_id],
            self._COLORS[theme_id],
            ("9:16",),
            self.CANVAS,
            start,
            background,
            text,
            score,
            review_start,
            review,
            self._PROFILES[theme_id],
            self._slots(),
            styles,
            self._placements(),
            6,
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
