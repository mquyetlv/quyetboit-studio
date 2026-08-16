from __future__ import annotations

import hashlib
from pathlib import Path
from typing import ClassVar

from PIL import Image, ImageFont

from ...core.minigame import (
    AlphaMaskProfile,
    CanvasSpec,
    DigitAtlasSpec,
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
    _START_TOPIC_BOUNDS: ClassVar[dict[str, tuple[int, int, int, int]]] = {
        "theme-1": (145, 1005, 935, 1270),
        "theme-2": (120, 1010, 960, 1305),
        "theme-3": (160, 1000, 920, 1335),
        "theme-4": (160, 1035, 920, 1365),
        "theme-5": (145, 1050, 935, 1320),
    }
    _START_TOPIC_COLORS: ClassVar[dict[str, tuple[str, str]]] = {
        "theme-1": ("#073A82", "#FFFFFF"),
        "theme-2": ("#073A82", "#FFFFFF"),
        "theme-3": ("#263247", "#FFF7E2"),
        "theme-4": ("#F8F7FF", "#160F31"),
        "theme-5": ("#173E29", "#FFFFFF"),
    }
    # These were reviewed per theme on annotated 1080×1920 composites. The current
    # artwork shares geometry, but keeping an explicit record per theme prevents a
    # later asset refresh from silently moving every theme at once. The review pill
    # reserves only its baked-in check mark so the remaining score stays legible on
    # a phone.
    _DYNAMIC_BOUNDS: ClassVar[
        dict[str, dict[str, tuple[int, int, int, int]]]
    ] = {
        theme_id: {
            "hook_first": (430, 470, 650, 690),
            "hook_second": (475, 685, 695, 905),
            # The headline is deliberately wider than the pill.  The badge text
            # begins after the baked-in check mark, rather than being centred in
            # the whole pill (which made it look visibly right-heavy).
            "review_sentence": (145, 678, 935, 785),
            "review_badge": (460, 798, 722, 870),
            "cta_eyebrow": (170, 350, 910, 440),
            "cta_headline": (130, 430, 950, 760),
        }
        for theme_id in ("theme-1", "theme-2", "theme-3", "theme-4", "theme-5")
    }
    _CTA_BOUNDS: ClassVar[dict[str, dict[str, tuple[int, int, int, int]]]] = {
        "theme-1": {
            "cta_primary": (145, 1330, 935, 1470),
            "cta_secondary": (145, 1510, 935, 1640),
        },
        "theme-2": {
            "cta_primary": (180, 1060, 900, 1195),
            "cta_secondary": (145, 1510, 935, 1650),
        },
        "theme-3": {
            "cta_primary": (220, 1060, 860, 1285),
            "cta_secondary": (165, 1435, 915, 1605),
        },
        "theme-4": {
            "cta_primary": (230, 1240, 850, 1410),
            "cta_secondary": (150, 1570, 930, 1745),
        },
        "theme-5": {
            "cta_primary": (210, 1185, 870, 1310),
            "cta_secondary": (160, 1440, 920, 1605),
        },
    }
    # Score pills are drawn at different heights in every theme asset. These are
    # measured on the canonical 1080x1920 canvas and intentionally stay per-theme.
    _SCORE_BOUNDS: ClassVar[dict[str, dict[str, tuple[int, int, int, int]]]] = {
        "theme-1": {"score_beginner": (390, 522, 728, 612), "score_good": (390, 934, 735, 1023), "score_perfect": (390, 1318, 735, 1406)},
        "theme-2": {"score_beginner": (400, 527, 735, 624), "score_good": (405, 972, 740, 1068), "score_perfect": (415, 1372, 740, 1462)},
        "theme-3": {"score_beginner": (430, 570, 800, 680), "score_good": (430, 980, 800, 1085), "score_perfect": (430, 1350, 800, 1460)},
        "theme-4": {"score_beginner": (390, 532, 735, 642), "score_good": (390, 950, 740, 1060), "score_perfect": (410, 1350, 735, 1460)},
        "theme-5": {"score_beginner": (385, 552, 745, 656), "score_good": (390, 975, 740, 1075), "score_perfect": (390, 1365, 745, 1465)},
    }
    _SCORE_COLORS: ClassVar[dict[str, tuple[str, str]]] = {
        "theme-1": ("#073A82", "#FFFFFF"),
        "theme-2": ("#073A82", "#FFFFFF"),
        "theme-3": ("#4A2C10", "#FFF5D9"),
        "theme-4": ("#FFFFFF", "#10265E"),
        "theme-5": ("#234F18", "#FFFFFF"),
    }
    _REVIEW_PILL_BOUNDS: ClassVar[dict[str, dict[str, tuple[int, int, int, int]]]] = {
        "theme-1": {
            "review_ipa": (193, 1048, 887, 1105),
            "review_word_type": (413, 1216, 667, 1287),
        },
        "theme-2": {
            "review_ipa": (193, 1047, 887, 1104),
            "review_word_type": (413, 1214, 667, 1285),
        },
        "theme-3": {
            "review_ipa": (193, 1075, 887, 1130),
            "review_word_type": (413, 1245, 667, 1317),
        },
        "theme-4": {
            "review_ipa": (193, 1052, 887, 1107),
            "review_word_type": (413, 1218, 667, 1292),
        },
        "theme-5": {
            "review_ipa": (193, 1054, 887, 1111),
            "review_word_type": (413, 1227, 667, 1297),
        },
    }
    # Measured against the thin divider in each canonical background-text asset.
    # Keeping these per theme prevents an artwork refresh from moving the bar.
    _COUNTDOWN_BAR_BOUNDS: ClassVar[dict[str, tuple[int, int, int, int]]] = {
        "theme-1": (175, 878, 905, 920),
        "theme-2": (170, 880, 910, 922),
        "theme-3": (180, 884, 900, 926),
        "theme-4": (170, 882, 910, 924),
        "theme-5": (175, 884, 905, 926),
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

    @classmethod
    def _slots(cls, theme_id: str) -> dict[str, TextSlot]:
        # Coordinates are on the canonical 1080×1920 canvas.
        slots = {
            "header": TextSlot("header", (420, 103, 660, 184), 1),
            "header_wide": TextSlot("header_wide", (250, 103, 830, 184), 1),
            "meaning": TextSlot("meaning", (130, 340, 950, 770), 2),
            "word": TextSlot("word", (130, 990, 950, 1225), 2),
            "english_label": TextSlot("english_label", (160, 995, 920, 1070), 1),
            "english_word": TextSlot("english_word", (135, 1070, 945, 1225), 2),
            "ipa": TextSlot("ipa", (150, 1265, 930, 1350), 1),
            "phase": TextSlot("phase", (130, 1480, 950, 1780), 4),
            "answer_word_type": TextSlot("answer_word_type", (170, 1500, 910, 1585), 1),
            "voice_header": TextSlot("voice_header", (170, 1590, 910, 1680), 1),
            "voice_helper": TextSlot("voice_helper", (170, 1690, 910, 1780), 2),
            "footer": TextSlot("footer", (278, 1797, 802, 1868), 2),
            "start_topic": TextSlot(
                "start_topic", cls._START_TOPIC_BOUNDS[theme_id], 3
            ),
            "review_meaning": TextSlot("review_meaning", (202, 304, 878, 423), 2),
            "review_word": TextSlot("review_word", (147, 540, 932, 970), 2),
            "review_example": TextSlot("review_example", (175, 1430, 905, 1670), 4),
            "review_example_en": TextSlot("review_example_en", (175, 1430, 905, 1545), 3),
            "review_example_vi": TextSlot("review_example_vi", (175, 1555, 905, 1670), 3),
        }
        slots.update(
            {name: TextSlot(name, bounds, 2 if name in {"cta_headline", "review_sentence"} else 1)
            for name, bounds in cls._DYNAMIC_BOUNDS[theme_id].items()}
        )
        slots.update(
            {name: TextSlot(name, bounds, 2 if name == "cta_primary" else 1)
             for name, bounds in cls._CTA_BOUNDS[theme_id].items()}
        )
        slots.update(
            {name: TextSlot(name, bounds, 1) for name, bounds in cls._SCORE_BOUNDS[theme_id].items()}
        )
        slots.update(
            {
                name: TextSlot(name, bounds, 1)
                for name, bounds in cls._REVIEW_PILL_BOUNDS[theme_id].items()
            }
        )
        slots["countdown_bar"] = TextSlot(
            "countdown_bar", cls._COUNTDOWN_BAR_BOUNDS[theme_id], 1
        )
        return slots

    @classmethod
    def _styles(cls, theme_id: str) -> dict[str, TextStyle]:
        colors = cls._COLORS[theme_id]
        topic_color, topic_outline = cls._START_TOPIC_COLORS[theme_id]
        score_color, score_outline = cls._SCORE_COLORS[theme_id]

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
                "Topic", "Montserrat", "Montserrat-Variable.ttf", 34, 88,
                topic_color, topic_outline, colors.shadow, 2, 1, bold=True,
                line_spacing=10,
            ),
            "header": style(
                "Header", "Montserrat", "Montserrat-Variable.ttf", 18, 30,
                role="accent", bold=True, spacing=3, margin=80,
            ),
            "label": style(
                "Label", "Montserrat", "Montserrat-Variable.ttf", 22, 34,
                role="accent", bold=True, spacing=4, margin=80,
            ),
            "hero": style(
                "Hero", "Montserrat", "Montserrat-Variable.ttf", 36, 88, bold=True,
            ),
            "question_meaning": style(
                "QuestionMeaning", "Montserrat", "Montserrat-Variable.ttf", 44, 104,
                bold=True,
            ),
            "question_placeholder": style(
                "QuestionPlaceholder", "Montserrat", "Montserrat-Variable.ttf", 40, 112,
                bold=True, spacing=8,
            ),
            "word": style(
                "Word", "Montserrat", "Montserrat-Variable.ttf", 48, 144, bold=True,
            ),
            "body": style(
                "Body", "Montserrat", "Montserrat-Variable.ttf", 18, 34,
                role="secondary",
            ),
            "voice_helper": style(
                "VoiceHelper", "Montserrat", "Montserrat-Variable.ttf", 26, 42,
                role="secondary", bold=True,
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
                "ReviewIPA", "Montserrat", "Montserrat-Italic-Variable.ttf", 24, 46,
                surface="review", role="secondary", italic=True,
            ),
            "review_word_type": style(
                "ReviewWordType", "Montserrat", "Montserrat-Variable.ttf", 28, 46,
                surface="review", role="accent", bold=True,
            ),
            "word_type": style(
                "WordType", "Montserrat", "Montserrat-Variable.ttf", 28, 46,
                role="accent", bold=True, spacing=2,
            ),
            "review_example": style(
                "ReviewExample", "Montserrat", "Montserrat-Variable.ttf", 24, 44,
                surface="review", role="secondary",
            ),
            "review_example_vi": style(
                "ReviewExampleVi", "Montserrat", "Montserrat-Italic-Variable.ttf", 22, 34,
                surface="review", role="secondary", italic=True,
            ),
            "start": style(
                "Start", "Bebas Neue", "BebasNeue-Regular.ttf", 52, 104,
                bold=True, spacing=1, margin=90,
            ),
            "score": TextStyle(
                "Score", "Montserrat", "Montserrat-Variable.ttf", 32, 54,
                score_color, score_outline, score_outline, 2, 1, bold=True,
            ),
            "dynamic_title": TextStyle(
                "DynamicTitle", "Bebas Neue", "BebasNeue-Regular.ttf", 48, 150,
                colors.hook_primary, colors.hook_outline, colors.shadow, 4, 2,
                bold=True,
            ),
            "review_dynamic": style(
                "ReviewDynamic", "Bebas Neue", "BebasNeue-Regular.ttf", 32, 54,
                surface="review", bold=True, spacing=1,
            ),
            "cta_eyebrow": style(
                "CtaEyebrow", "Montserrat", "Montserrat-Variable.ttf", 28, 48,
                surface="review", role="accent", bold=True, spacing=4,
            ),
            "cta_headline": style(
                "CtaHeadline", "Bebas Neue", "BebasNeue-Regular.ttf", 54, 112,
                surface="review", bold=True, spacing=1,
            ),
            "cta_primary": style(
                "CtaPrimary", "Montserrat", "Montserrat-Variable.ttf", 28, 52,
                surface="review", bold=True,
            ),
            "cta_secondary": style(
                "CtaSecondary", "Montserrat", "Montserrat-Variable.ttf", 24, 40,
                surface="review", role="secondary", bold=True, spacing=2,
            ),
        }

    @staticmethod
    def _placements() -> dict[str, TextPlacement]:
        return {
            "word_header": TextPlacement("header", "header"),
            "ending_header": TextPlacement("header", "header_wide"),
            "meaning_label": TextPlacement("label", "meaning", 360),
            "meaning_value": TextPlacement("question_meaning", "meaning"),
            "reveal_label": TextPlacement("label", "word", 895),
            "countdown": TextPlacement("body", "word", 942, 2),
            "english_label": TextPlacement("label", "english_label"),
            "english_word": TextPlacement("word", "english_word"),
            "question_english_label": TextPlacement("label", "english_label"),
            "question_english_placeholder": TextPlacement(
                "question_placeholder", "english_word"
            ),
            "ipa": TextPlacement("ipa", "ipa"),
            "voice_header": TextPlacement("header", "voice_header", layer=2),
            "voice_helper": TextPlacement("voice_helper", "voice_helper", layer=2),
            "answer_word_type": TextPlacement("word_type", "answer_word_type", layer=2),
            "review_meaning": TextPlacement("review_meaning", "review_meaning"),
            "review_word": TextPlacement("review_word", "review_word"),
            "review_ipa": TextPlacement("review_ipa", "review_ipa"),
            "review_word_type": TextPlacement("review_word_type", "review_word_type"),
            "review_example": TextPlacement("review_example", "review_example"),
            "review_example_en": TextPlacement("review_example", "review_example_en"),
            "review_example_vi": TextPlacement("review_example_vi", "review_example_vi"),
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
        start = root / "start.png"
        start_base = root / "start-base.png"
        background = root / "background-video.png"
        # One release migration alias: old projects/assets can still be opened once.
        if not background.is_file():
            background = root / "backgound-videos.png"
        text = root / "background-text.png"
        score = root / "score.png"
        review_start = root / "review-start-screen.png"
        if not review_start.is_file():
            review_start = root / "start-review-screen.png"
        review_start_base = root / "review-start-base.png"
        review = root / "review.png"
        if not review.is_file():
            review = root / "rewview.png"
        cta_screen = root / "cta-new.png"
        digit_atlas = root / "digit-atlas.png"
        self._validate_image(start, (941, 1672), "start screen")
        self._validate_image(start_base, (941, 1672), "start clean base")
        self._validate_image(background, (941, 1672), "background video")
        self._validate_image(text, (971, 1619), "background text")
        self._validate_image(score, (941, 1672), "score screen")
        self._validate_image(review_start, (941, 1672), "review start screen")
        self._validate_image(review_start_base, (941, 1672), "review start clean base")
        self._validate_image(review, (941, 1672), "review screen")
        self._validate_image(cta_screen, (941, 1672), "CTA screen")
        digit_atlas_spec = DigitAtlasSpec()
        self._validate_digit_atlas(digit_atlas, digit_atlas_spec)
        styles = self._styles(theme_id)
        self._validate_fonts(styles)
        checksum = self._checksum(
            (start, start_base, background, text, score, review_start, review_start_base, review,
            cta_screen, digit_atlas)
        )
        return MinigameThemeManifest(
            theme_id=theme_id,
            display_name=theme_id.replace("-", " ").title(),
            palette=self._PALETTES[theme_id],
            colors=self._COLORS[theme_id],
            supported_formats=("9:16",),
            canvas=self.CANVAS,
            start_screen=start,
            start_base=start_base,
            background_video=background,
            background_text=text,
            score_screen=score,
            review_start_screen=review_start,
            review_start_base=review_start_base,
            review_screen=review,
            cta_screen=cta_screen,
            digit_atlas=digit_atlas,
            digit_atlas_spec=digit_atlas_spec,
            mask_profile=self._PROFILES[theme_id],
            slots=self._slots(theme_id),
            styles=styles,
            placements=self._placements(),
            layout_version=15,
            checksum=checksum,
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

    @staticmethod
    def _validate_digit_atlas(path: Path, spec: DigitAtlasSpec) -> None:
        if not path.is_file():
            raise PipelineError("theme_asset_missing", "Thiếu digit atlas của theme.")
        with Image.open(path) as image:
            if image.mode != "RGBA" or image.width < spec.columns or image.height < spec.rows:
                raise PipelineError(
                    "theme_asset_invalid", "Digit atlas phải là PNG RGBA 5×2 hợp lệ."
                )
            alpha = image.getchannel("A")
            if alpha.getextrema()[0] != 0:
                raise PipelineError(
                    "theme_asset_invalid", "Digit atlas không có nền alpha trong suốt."
                )
            for digit in range(spec.glyph_count):
                column, row = digit % spec.columns, digit // spec.columns
                bounds = (
                    round(column * image.width / spec.columns),
                    round(row * image.height / spec.rows),
                    round((column + 1) * image.width / spec.columns),
                    round((row + 1) * image.height / spec.rows),
                )
                cell = alpha.crop(bounds).point(
                    lambda value: 255 if value >= spec.alpha_threshold else 0
                )
                if cell.getbbox() is None:
                    raise PipelineError(
                        "theme_asset_invalid", f"Digit atlas thiếu glyph số {digit}."
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
