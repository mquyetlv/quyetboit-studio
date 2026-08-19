from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ...core.minigame import MinigameThemeManifest, TextStyle, ThemeColorPalette
from ...core.models import stable_hash
from .minigame_theme import MinigameThemeService


@dataclass(slots=True, frozen=True)
class MinigameVideoThemeProfile:
    theme_id: str
    display_name: str
    base_theme_id: str
    art_direction: str
    colors: ThemeColorPalette
    scrim: str
    panel: str
    panel_alt: str
    border: str


class MinigameVideoThemeService:
    """Build themed RGBA surfaces while keeping the selected video visible."""

    VERSION: ClassVar[int] = 6
    PROFILES: ClassVar[dict[str, MinigameVideoThemeProfile]] = {
        "video-ocean": MinigameVideoThemeProfile(
            "video-ocean", "Theme 2 · Medical Blue", "theme-2", "medical",
            ThemeColorPalette(
                "#123A5A", "#315B78", "#0781B5", "#FFFFFF", "#D8EAF4",
                "#123A5A", "#315B78", "#0781B5", "#FFFFFF",
                "#123A5A", "#0781B5", "#123A5A",
            ),
            "#063B66", "#F5FCFF", "#087EB8", "#58D9F4",
        ),
        "video-violet": MinigameVideoThemeProfile(
            "video-violet", "Theme 3 · Scrapbook", "theme-3", "scrapbook",
            ThemeColorPalette(
                "#263247", "#5A4B3B", "#A76D00", "#FFF8E7", "#45321D",
                "#263247", "#5A4B3B", "#A76D00", "#FFF8E7",
                "#263247", "#A76D00", "#263247",
            ),
            "#263247", "#FFF8E7", "#244B70", "#E7B43D",
        ),
        "video-emerald": MinigameVideoThemeProfile(
            "video-emerald", "Theme 5 · Garden", "theme-5", "garden",
            ThemeColorPalette(
                "#173E29", "#315C3B", "#397D2B", "#FFFDF0", "#16351F",
                "#173E29", "#315C3B", "#397D2B", "#FFFDF0",
                "#173E29", "#397D2B", "#173E29",
            ),
            "#173E29", "#FFFDF0", "#397D2B", "#A9DC4B",
        ),
    }

    def __init__(self, themes: MinigameThemeService | None = None) -> None:
        self.themes = themes or MinigameThemeService()
        self.font_dir = Path(__file__).resolve().parents[2] / "assets" / "fonts"

    def discover(self) -> dict[str, MinigameVideoThemeProfile]:
        return dict(self.PROFILES)

    def get(self, theme_id: str) -> MinigameVideoThemeProfile:
        try:
            return self.PROFILES[theme_id]
        except KeyError as exc:
            raise ValueError("Bộ màu nền video không hợp lệ.") from exc

    def manifest(self, theme_id: str) -> MinigameThemeManifest:
        profile = self.get(theme_id)
        base = self.themes.get(profile.base_theme_id)
        styles = {
            name: self._recolor_style(name, style, profile.colors)
            for name, style in base.styles.items()
        }
        slots = dict(base.slots)
        if profile.art_direction == "scrapbook":
            # Video manifests keep their own id, so the Theme 3 one-line special case in
            # MinigameTextLayerService does not apply. Give the standard two-line copy
            # enough room without changing the established static Theme 3 layout.
            slots["review_sentence"] = replace(
                slots["review_sentence"], bounds=(145, 680, 935, 805), max_lines=2
            )
        checksum = stable_hash(
            {"version": self.VERSION, "profile": profile, "base": base.checksum,
             "styles": styles, "slots": slots}
        )
        return replace(
            base, theme_id=profile.theme_id, display_name=profile.display_name,
            palette=(profile.panel, profile.panel_alt, profile.border),
            colors=profile.colors, styles=styles, slots=slots,
            layout_version=base.layout_version + 100, checksum=checksum,
        )

    @staticmethod
    def _recolor_style(
        name: str, style: TextStyle, colors: ThemeColorPalette
    ) -> TextStyle:
        hook = name in {"title", "topic", "dynamic_title"}
        review = name.startswith(("review_", "cta_")) or name == "score"
        secondary = name in {
            "body", "voice_helper", "ipa", "review_ipa", "review_example",
            "review_example_vi", "cta_secondary",
        }
        accent = name in {
            "header", "label", "word_type", "review_word_type", "cta_eyebrow",
        }
        if hook:
            color = colors.hook_secondary if name == "topic" else colors.hook_primary
            outline = colors.hook_outline
        elif name == "cta_headline":
            color = "#FFFFFF"
            outline = colors.hook_outline
        elif name == "cta_eyebrow":
            color = "#FFD85A"
            outline = colors.hook_outline
        elif review:
            color = colors.review_accent if accent else (
                colors.review_secondary if secondary else colors.review_primary
            )
            outline = colors.review_outline
        else:
            color = colors.accent if accent else (
                colors.secondary if secondary else colors.primary
            )
            outline = colors.outline
        return replace(
            style, color=color, outline_color=outline, shadow_color=colors.shadow
        )

    def build_surfaces(
        self, theme_id: str, manifest: MinigameThemeManifest, cache_dir: Path
    ) -> dict[str, Path]:
        profile = self.get(theme_id)
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = stable_hash(
            {"version": self.VERSION, "profile": profile, "manifest": manifest.checksum}
        )[:20]
        return {
            kind: self._build_surface(kind, profile, cache_dir / f"{kind}-{key}.png")
            for kind in ("hook", "body", "score", "review_start", "review", "cta")
        }

    def _build_surface(
        self, kind: str, profile: MinigameVideoThemeProfile, output: Path
    ) -> Path:
        if output.is_file():
            with Image.open(output) as cached:
                if cached.mode == "RGBA" and cached.size == (1080, 1920):
                    return output
        image = Image.new("RGBA", (1080, 1920), self._rgba(profile.scrim, 32))
        if profile.art_direction == "medical":
            self._medical(image, kind)
        elif profile.art_direction == "scrapbook":
            self._scrapbook(image, kind)
        else:
            self._garden(image, kind)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        try:
            image.save(partial, "PNG")
            os.replace(partial, output)
            return output
        finally:
            partial.unlink(missing_ok=True)

    def _medical(self, image: Image.Image, kind: str) -> None:
        white, blue, cyan, navy = "#F7FDFF", "#087EB8", "#58D9F4", "#073A82"
        yellow, green = "#FFD45C", "#55C98B"
        draw = ImageDraw.Draw(image)
        self._plus(draw, 930, 125, 34, cyan)
        self._plus(draw, 120, 1780, 27, white)
        self._sparkle(draw, 985, 1730, 26, yellow)
        draw.ellipse((25, 100, 135, 210), outline=self._rgba(cyan, 100), width=5)
        if kind == "hook":
            self._glass(image, (70, 175, 1010, 1540), white, blue)
            self._banner(image, (135, 260, 945, 465), blue, cyan)
            self._label(image, "CAN YOU GET", (150, 290, 930, 430), 69, white)
            for x, color, text in ((305, blue, "LISTEN"), (540, yellow, "GUESS"),
                                   (775, green, "REPEAT")):
                self._round_badge(image, x, 1405, 72, color, text, navy)
            self._panel(image, (155, 970, 925, 1090), white, cyan)
            self._label(image, "TOPIC", (180, 985, 900, 1065), 28, navy, cyan)
        elif kind == "body":
            self._banner(image, (325, 65, 755, 215), blue, cyan)
            self._glass(image, (65, 245, 1015, 815), white, blue)
            self._panel(image, (105, 930, 975, 1405), white, cyan)
            self._panel(image, (105, 1450, 975, 1830), white, blue)
            self._plus(draw, 935, 300, 22, cyan)
            self._heartbeat(draw, 145, 1720, 935, cyan)
        elif kind == "score":
            self._banner(image, (90, 115, 990, 340), blue, cyan)
            self._label(image, "HOW DID YOU DO?", (120, 155, 960, 300), 70, white)
            cards = (((90, 390, 990, 730), blue, "BEGINNER", "+"),
                     ((90, 800, 990, 1140), yellow, "GOOD JOB", "★"),
                     ((90, 1210, 990, 1550), green, "PERFECT", "✓"))
            self._score_cards(image, cards, "medical", navy, cyan)
        elif kind == "review_start":
            self._glass(image, (80, 250, 1000, 1370), white, blue)
            self._banner(image, (125, 330, 955, 580), blue, cyan)
            self._label(image, "REVIEW TIME", (150, 385, 930, 535), 82, white)
            self._panel(image, (140, 650, 940, 920), white, cyan)
        elif kind == "review":
            self._banner(image, (250, 65, 830, 220), blue, cyan)
            self._label(image, "REVIEW", (280, 95, 800, 195), 43, white)
            self._panel(image, (95, 260, 985, 470), white, cyan)
            self._glass(image, (80, 500, 1000, 1335), white, blue)
            self._panel(image, (95, 1380, 985, 1760), white, cyan)
            self._label(image, "EXAMPLE", (140, 1400, 940, 1470), 26, navy, cyan)
        else:
            self._cta_layout(image, "medical", white, blue, cyan, navy, yellow, green)

    def _scrapbook(self, image: Image.Image, kind: str) -> None:
        navy, blue, cream = "#263247", "#315C7D", "#FFF7E2"
        kraft, orange, mustard, green = "#D8B77A", "#CF6840", "#E7B43D", "#71975B"
        draw = ImageDraw.Draw(image)
        self._doodle_star(draw, 92, 155, 34, mustard)
        self._doodle_heart(draw, 970, 250, 22, orange)
        if kind == "hook":
            self._paper(image, (75, 185, 1005, 1545), cream, navy)
            self._tape(image, 135, 160, mustard, -7)
            self._stitched_banner(image, (125, 270, 955, 480), navy, cream)
            self._label(image, "CAN YOU GET", (150, 315, 930, 450), 67, cream)
            for x, color, text in ((295, blue, "LISTEN"), (540, orange, "GUESS"),
                                   (785, mustard, "REPEAT")):
                self._round_badge(image, x, 1405, 72, color, text, cream)
            self._paper(image, (155, 965, 925, 1095), kraft, navy)
            self._label(image, "TOPIC", (175, 990, 905, 1070), 28, navy, mustard)
        elif kind == "body":
            self._stitched_banner(image, (320, 60, 760, 215), navy, cream)
            self._paper(image, (60, 250, 1020, 820), cream, navy)
            self._tape(image, 790, 225, mustard, 7)
            self._paper(image, (75, 930, 1005, 1410), "#F9E6C2", blue)
            self._paper(image, (75, 1460, 1005, 1830), cream, orange)
        elif kind == "score":
            self._stitched_banner(image, (80, 110, 1000, 335), navy, cream)
            self._label(image, "HOW DID YOU DO?", (115, 160, 965, 300), 67, cream)
            cards = (((75, 390, 1005, 730), blue, "BEGINNER", "A"),
                     ((75, 800, 1005, 1140), orange, "GOOD JOB", "★"),
                     ((75, 1210, 1005, 1550), green, "PERFECT", "✓"))
            self._score_cards(image, cards, "scrapbook", navy, mustard)
        elif kind == "review_start":
            self._paper(image, (75, 240, 1005, 1395), cream, navy)
            self._tape(image, 175, 215, mustard, -8)
            self._stitched_banner(image, (120, 325, 960, 575), navy, cream)
            self._label(image, "REVIEW TIME", (145, 385, 935, 535), 80, cream)
            self._paper(image, (140, 650, 940, 920), kraft, navy)
        elif kind == "review":
            self._stitched_banner(image, (245, 55, 835, 220), navy, cream)
            self._label(image, "REVIEW", (275, 92, 805, 195), 43, cream)
            self._paper(image, (90, 255, 990, 475), cream, navy)
            self._paper(image, (70, 500, 1010, 1340), cream, blue)
            self._tape(image, 790, 475, mustard, 8)
            self._paper(image, (80, 1380, 1000, 1770), "#F9E6C2", orange)
            self._label(image, "EXAMPLE", (140, 1400, 940, 1470), 26, navy, mustard)
        else:
            self._cta_layout(image, "scrapbook", cream, navy, mustard, navy,
                             orange, green)

    def _garden(self, image: Image.Image, kind: str) -> None:
        dark, green, lime = "#285B26", "#4D902E", "#A9DC4B"
        cream, yellow, orange, wood = "#FFFDF0", "#FFD85A", "#EF8442", "#8B5A2B"
        self._garden_decor(image)
        if kind == "hook":
            self._garden_card(image, (70, 180, 1010, 1550), cream, green)
            self._wood(image, (125, 270, 955, 480), wood, yellow)
            self._label(image, "CAN YOU GET", (150, 320, 930, 450), 66, cream)
            for x, color, text in ((295, green, "LISTEN"), (540, yellow, "GUESS"),
                                   (785, orange, "REPEAT")):
                self._round_badge(image, x, 1405, 72, color, text, dark)
            self._panel(image, (150, 970, 930, 1100), cream, green)
            self._label(image, "TOPIC", (175, 995, 905, 1075), 28, dark, lime)
        elif kind == "body":
            self._wood(image, (320, 65, 760, 215), wood, yellow)
            self._garden_card(image, (60, 245, 1020, 825), cream, green)
            self._panel(image, (90, 930, 990, 1415), cream, lime)
            self._panel(image, (90, 1460, 990, 1835), cream, green)
        elif kind == "score":
            self._wood(image, (90, 110, 990, 340), wood, yellow)
            self._label(image, "HOW DID YOU DO?", (120, 165, 960, 300), 66, cream)
            cards = (((80, 390, 1000, 730), green, "BEGINNER", "A"),
                     ((80, 800, 1000, 1140), yellow, "GOOD JOB", "★"),
                     ((80, 1210, 1000, 1550), lime, "PERFECT", "✓"))
            self._score_cards(image, cards, "garden", dark, lime)
        elif kind == "review_start":
            self._garden_card(image, (75, 240, 1005, 1400), cream, green)
            self._wood(image, (120, 325, 960, 580), wood, yellow)
            self._label(image, "REVIEW TIME", (145, 390, 935, 540), 79, cream)
            self._panel(image, (140, 650, 940, 920), cream, lime)
        elif kind == "review":
            self._wood(image, (245, 55, 835, 220), wood, yellow)
            self._label(image, "REVIEW", (275, 92, 805, 195), 43, cream)
            self._panel(image, (90, 255, 990, 475), cream, lime)
            self._garden_card(image, (70, 500, 1010, 1340), cream, green)
            self._panel(image, (80, 1380, 1000, 1770), cream, lime)
            self._label(image, "EXAMPLE", (140, 1400, 940, 1470), 26, dark, lime)
        else:
            self._cta_layout(image, "garden", cream, wood, lime, dark, yellow, green)

    def _score_cards(self, image: Image.Image, cards: tuple, style: str,
                     text_color: str, accent: str) -> None:
        papers = ("#DCEAF1", "#F8D5BA", "#DDE8C8")
        for index, (bounds, color, label, icon) in enumerate(cards):
            if style == "medical":
                self._glass(image, bounds, "#F7FDFF", color)
            elif style == "scrapbook":
                self._paper(image, bounds, papers[index], color)
                self._tape(image, bounds[0] + 30 + index * 220, bounds[1] - 18,
                           "#E7B43D", -7 + index * 7)
            else:
                self._garden_card(image, bounds, "#FFFDF0", color)
            y = (bounds[1] + bounds[3]) // 2
            self._round_badge(image, 240, y, 76, color, icon, text_color)
            self._label(image, label, (350, bounds[1] + 45, 930, bounds[1] + 155),
                        39, text_color, accent)

    def _cta_layout(self, image: Image.Image, style: str, panel: str, banner: str,
                    accent: str, text: str, second: str, third: str) -> None:
        if style == "medical":
            self._glass(image, (65, 210, 1015, 1725), panel, banner)
            self._banner(image, (115, 285, 965, 790), banner, accent)
        elif style == "scrapbook":
            self._paper(image, (60, 205, 1020, 1740), panel, banner)
            self._tape(image, 130, 185, accent, -8)
            self._stitched_banner(image, (105, 285, 975, 790), banner, panel)
        else:
            self._garden_card(image, (60, 205, 1020, 1740), panel, accent)
            self._wood(image, (105, 285, 975, 790), banner, second)
        for x, color, label in ((285, accent, "LIKE"), (540, second, "SHARE"),
                                (795, third, "FOLLOW")):
            self._round_badge(image, x, 900, 72, color, label, text)
        if style == "medical":
            rows = ((1125, accent, "A", 180), (1560, third, "✓", 185))
        elif style == "scrapbook":
            rows = ((1170, accent, "A", 265), (1520, third, "✓", 230))
        else:
            rows = ((1240, accent, "A", 190), (1515, third, "✓", 220))
        for y, color, icon, height in rows:
            bounds = (135, y - height // 2, 945, y + height // 2)
            if style == "scrapbook":
                self._paper(image, bounds, panel, color)
            else:
                self._panel(image, bounds, panel, color)
            self._round_badge(image, 225, y, 48, color, icon, text)

    def _glass(self, image: Image.Image, bounds: tuple[int, int, int, int],
               fill: str, border: str) -> None:
        self._rounded(image, bounds, 52, fill, 218, border, 5)
        draw = ImageDraw.Draw(image)
        inset = 13
        draw.rounded_rectangle(
            (bounds[0] + inset, bounds[1] + inset, bounds[2] - inset,
             bounds[3] - inset), radius=39,
            outline=self._rgba("#FFFFFF", 165), width=3,
        )
        draw.line((bounds[0] + 60, bounds[1] + 22, bounds[2] - 60,
                   bounds[1] + 22), fill=self._rgba("#FFFFFF", 190), width=5)

    def _garden_card(self, image: Image.Image, bounds: tuple[int, int, int, int],
                     fill: str, border: str) -> None:
        self._rounded(image, bounds, 52, fill, 220, border, 6)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (bounds[0] + 14, bounds[1] + 14, bounds[2] - 14, bounds[3] - 14),
            radius=38, outline=self._rgba("#B9E46A", 170), width=3,
        )

    def _panel(self, image: Image.Image, bounds: tuple[int, int, int, int],
               fill: str, border: str) -> None:
        self._rounded(image, bounds, 34, fill, 225, border, 4, blur=13)

    def _banner(self, image: Image.Image, bounds: tuple[int, int, int, int],
                fill: str, border: str) -> None:
        self._rounded(image, bounds, 46, fill, 246, border, 6)
        ImageDraw.Draw(image).rounded_rectangle(
            (bounds[0] + 12, bounds[1] + 12, bounds[2] - 12, bounds[1] + 54),
            radius=24, fill=self._rgba("#FFFFFF", 58),
        )

    def _paper(self, image: Image.Image, bounds: tuple[int, int, int, int],
               fill: str, border: str) -> None:
        x1, y1, x2, y2 = bounds
        points = [(x1 + 9, y1), (x2 - 20, y1 + 6), (x2, y1 + 22),
                  (x2 - 8, y2 - 15), (x2 - 27, y2), (x1 + 18, y2 - 4),
                  (x1, y2 - 22), (x1 + 5, y1 + 18)]
        shadow = Image.new("RGBA", image.size)
        ImageDraw.Draw(shadow).polygon(
            [(x + 11, y + 15) for x, y in points], fill=self._rgba("#24180D", 125)
        )
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(13)))
        draw = ImageDraw.Draw(image)
        draw.polygon(points, fill=self._rgba(fill, 235), outline=self._rgba(border, 235))
        self._stitches(draw, (x1 + 20, y1 + 20, x2 - 20, y2 - 20), border)
        for y in range(y1 + 55, y2 - 20, 54):
            draw.line((x1 + 38, y, x2 - 38, y), fill=self._rgba(border, 22), width=2)

    def _stitched_banner(self, image: Image.Image, bounds: tuple[int, int, int, int],
                         fill: str, stitch: str) -> None:
        self._rounded(image, bounds, 22, fill, 246, "#182236", 3)
        self._stitches(ImageDraw.Draw(image),
                       (bounds[0] + 15, bounds[1] + 15,
                        bounds[2] - 15, bounds[3] - 15), stitch)

    def _wood(self, image: Image.Image, bounds: tuple[int, int, int, int],
              fill: str, accent: str) -> None:
        self._rounded(image, bounds, 36, fill, 248, accent, 5)
        draw = ImageDraw.Draw(image)
        for y in range(bounds[1] + 35, bounds[3] - 20, 42):
            draw.arc((bounds[0] + 40, y - 18, bounds[2] - 40, y + 18),
                     195, 345, fill=self._rgba("#D6A15C", 85), width=3)

    def _rounded(self, image: Image.Image, bounds: tuple[int, int, int, int],
                 radius: int, fill: str, alpha: int, border: str, width: int,
                 *, blur: int = 18) -> None:
        shadow = Image.new("RGBA", image.size)
        shifted = (bounds[0] + 8, bounds[1] + 13, bounds[2] + 8, bounds[3] + 13)
        ImageDraw.Draw(shadow).rounded_rectangle(
            shifted, radius=radius, fill=self._rgba("#04140D", 125)
        )
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(blur)))
        ImageDraw.Draw(image).rounded_rectangle(
            bounds, radius=radius, fill=self._rgba(fill, alpha),
            outline=self._rgba(border, 245), width=width,
        )

    def _round_badge(self, image: Image.Image, x: int, y: int, radius: int,
                     color: str, label: str, text: str) -> None:
        draw = ImageDraw.Draw(image)
        draw.ellipse((x - radius + 8, y - radius + 13, x + radius + 8,
                      y + radius + 13), fill=self._rgba("#07101F", 105))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                     fill=self._rgba(color, 245),
                     outline=self._rgba("#FFFFFF", 225), width=6)
        draw.arc((x - radius + 13, y - radius + 13, x + radius - 13,
                  y + radius - 13), 205, 335,
                 fill=self._rgba("#FFFFFF", 160), width=5)
        size = 21 if len(label) > 3 else max(27, radius // 2)
        self._label(image, label, (x - radius, y - size, x + radius, y + size),
                    size, text)

    def _tape(self, image: Image.Image, x: int, y: int, color: str,
              angle: float) -> None:
        tape = Image.new("RGBA", (245, 85))
        draw = ImageDraw.Draw(tape)
        draw.polygon(((12, 15), (232, 8), (224, 72), (8, 65)),
                     fill=self._rgba(color, 185))
        for line_x in range(25, 225, 28):
            draw.line((line_x, 15, line_x - 8, 68),
                      fill=self._rgba("#FFFFFF", 42), width=3)
        tape = tape.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        image.alpha_composite(tape, (x, y))

    def _label(self, image: Image.Image, text: str,
               bounds: tuple[int, int, int, int], size: int, color: str,
               stroke: str = "#17253A") -> None:
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(self.font_dir / "Montserrat-Variable.ttf"), size)
        try:
            font.set_variation_by_name("ExtraBold")
        except OSError:
            pass
        box = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        x = (bounds[0] + bounds[2] - (box[2] - box[0])) // 2
        y = (bounds[1] + bounds[3] - (box[3] - box[1])) // 2 - box[1]
        draw.text((x + 3, y + 5), text, font=font, fill=self._rgba("#07101F", 90),
                  stroke_width=2, stroke_fill=self._rgba("#07101F", 80))
        draw.text((x, y), text, font=font, fill=self._rgba(color, 255),
                  stroke_width=2, stroke_fill=self._rgba(stroke, 225))

    def _stitches(self, draw: ImageDraw.ImageDraw,
                  bounds: tuple[int, int, int, int], color: str) -> None:
        x1, y1, x2, y2 = bounds
        for x in range(x1, x2, 24):
            draw.line((x, y1, min(x + 13, x2), y1),
                      fill=self._rgba(color, 180), width=3)
            draw.line((x, y2, min(x + 13, x2), y2),
                      fill=self._rgba(color, 180), width=3)
        for y in range(y1, y2, 24):
            draw.line((x1, y, x1, min(y + 13, y2)),
                      fill=self._rgba(color, 180), width=3)
            draw.line((x2, y, x2, min(y + 13, y2)),
                      fill=self._rgba(color, 180), width=3)

    def _garden_decor(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        for x, y, angle in ((80, 140, -25), (1000, 1580, 25),
                            (135, 1770, 35), (950, 260, -35)):
            self._leaf(image, x, y, 38, "#4D902E", angle)
        self._flower(draw, 90, 1750, 32, "#FFD85A")
        self._sparkle(draw, 965, 210, 26, "#FFD85A")

    def _leaf(self, image: Image.Image, x: int, y: int, size: int,
              color: str, angle: float) -> None:
        leaf = Image.new("RGBA", (size * 3, size * 3))
        draw = ImageDraw.Draw(leaf)
        draw.ellipse((size // 2, size, size * 5 // 2, size * 2),
                     fill=self._rgba(color, 220),
                     outline=self._rgba("#285B26", 180), width=2)
        draw.line((size // 2, size * 3 // 2, size * 5 // 2, size * 3 // 2),
                  fill=self._rgba("#FFFDF0", 100), width=2)
        leaf = leaf.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
        image.alpha_composite(leaf, (x - leaf.width // 2, y - leaf.height // 2))

    def _flower(self, draw: ImageDraw.ImageDraw, x: int, y: int,
                size: int, color: str) -> None:
        for dx, dy in ((0, -size), (size, 0), (0, size), (-size, 0)):
            draw.ellipse((x + dx - size // 2, y + dy - size // 2,
                          x + dx + size // 2, y + dy + size // 2),
                         fill=self._rgba("#FFFDF0", 230),
                         outline=self._rgba(color, 180), width=2)
        draw.ellipse((x - size // 2, y - size // 2,
                      x + size // 2, y + size // 2), fill=self._rgba(color, 245))

    def _plus(self, draw: ImageDraw.ImageDraw, x: int, y: int,
              size: int, color: str) -> None:
        width = max(8, size // 3)
        draw.rounded_rectangle((x - width // 2, y - size,
                                x + width // 2, y + size),
                               radius=width // 2, fill=self._rgba(color, 210))
        draw.rounded_rectangle((x - size, y - width // 2,
                                x + size, y + width // 2),
                               radius=width // 2, fill=self._rgba(color, 210))

    def _heartbeat(self, draw: ImageDraw.ImageDraw, x1: int, y: int,
                   x2: int, color: str) -> None:
        center = (x1 + x2) // 2
        points = [(x1, y), (center - 110, y), (center - 70, y - 55),
                  (center - 25, y + 75), (center + 20, y - 95),
                  (center + 70, y), (x2, y)]
        draw.line(points, fill=self._rgba(color, 190), width=7, joint="curve")

    def _sparkle(self, draw: ImageDraw.ImageDraw, x: int, y: int,
                 size: int, color: str) -> None:
        points = [(x, y - size), (x + size // 4, y - size // 4),
                  (x + size, y), (x + size // 4, y + size // 4),
                  (x, y + size), (x - size // 4, y + size // 4),
                  (x - size, y), (x - size // 4, y - size // 4)]
        draw.polygon(points, fill=self._rgba(color, 235))

    def _doodle_star(self, draw: ImageDraw.ImageDraw, x: int, y: int,
                     size: int, color: str) -> None:
        points = [(x, y - size), (x + size // 4, y - size // 4),
                  (x + size, y - size // 5), (x + size // 3, y + size // 4),
                  (x + size // 2, y + size), (x, y + size // 2),
                  (x - size // 2, y + size), (x - size // 3, y + size // 4),
                  (x - size, y - size // 5), (x - size // 4, y - size // 4)]
        draw.line(points + [points[0]], fill=self._rgba(color, 230),
                  width=5, joint="curve")

    def _doodle_heart(self, draw: ImageDraw.ImageDraw, x: int, y: int,
                      size: int, color: str) -> None:
        points = [(x, y + size), (x - size, y - size // 4),
                  (x - size // 2, y - size), (x, y - size // 2),
                  (x + size // 2, y - size), (x + size, y - size // 4),
                  (x, y + size)]
        draw.line(points, fill=self._rgba(color, 230), width=5, joint="curve")

    @staticmethod
    def _rgba(value: str, alpha: int) -> tuple[int, int, int, int]:
        value = value.lstrip("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha
