from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from ...core.minigame import MinigameProject, MinigameThemeManifest
from ...core.models import PipelineError, sha256_file, stable_hash


class MinigameTextLayerService:
    """Renders cache-safe dynamic Hook, Review Start and CTA RGBA layers."""

    def __init__(self) -> None:
        self.font_dir = Path(__file__).resolve().parents[2] / "assets" / "fonts"

    def build_hook(self, project: MinigameProject, manifest: MinigameThemeManifest, cache_dir: Path) -> Path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = stable_hash(
            {
                "theme": manifest.checksum,
                "layout": {
                    "version": manifest.layout_version,
                    "slots": manifest.slots,
                    "styles": manifest.styles,
                    "placements": manifest.placements,
                },
                "fonts": self.font_fingerprint(),
                "topic": project.topic,
                "total": len(project.vocabulary),
            }
        )[:20]
        output = cache_dir / f"hook-text-{key}.png"
        if output.is_file():
            return output
        image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        total = str(len(project.vocabulary))
        for slot_name in ("hook_first", "hook_second"):
            self._draw_atlas_number(image, total, manifest, manifest.slots[slot_name].bounds)
        topic_slot = manifest.slots["start_topic"]
        topic_style = manifest.styles["topic"]
        self._draw_centered(
            image,
            project.topic,
            topic_slot.bounds,
            self.font_dir / topic_style.font_file,
            topic_style.max_size,
            topic_style.min_size,
            fill=self._rgba(topic_style.color),
            stroke_fill=self._rgba(topic_style.outline_color),
            stroke_width=topic_style.outline_width,
            bold=topic_style.bold,
            max_lines=topic_slot.max_lines,
            line_spacing=topic_style.line_spacing,
        )
        return self._save_atomic(image, output)

    @staticmethod
    def _draw_atlas_number(
        image: Image.Image,
        value: str,
        manifest: MinigameThemeManifest,
        bounds: tuple[int, int, int, int],
    ) -> None:
        if not value.isdecimal():
            raise PipelineError("theme_digit_invalid", "Hook chỉ chấp nhận chữ số thập phân.")
        spec = manifest.digit_atlas_spec
        glyphs: list[Image.Image] = []
        with Image.open(manifest.digit_atlas) as source:
            atlas = source.convert("RGBA")
            for character in value:
                digit = int(character)
                column, row = digit % spec.columns, digit // spec.columns
                cell_bounds = (
                    round(column * atlas.width / spec.columns),
                    round(row * atlas.height / spec.rows),
                    round((column + 1) * atlas.width / spec.columns),
                    round((row + 1) * atlas.height / spec.rows),
                )
                cell = atlas.crop(cell_bounds)
                alpha = cell.getchannel("A").point(
                    lambda item: 255 if item >= spec.alpha_threshold else 0
                )
                optical_bounds = alpha.getbbox()
                if optical_bounds is None:
                    raise PipelineError(
                        "theme_digit_invalid", f"Digit atlas thiếu glyph số {digit}."
                    )
                glyphs.append(cell.crop(optical_bounds))

        source_height = max(glyph.height for glyph in glyphs)
        gap = round(source_height * spec.spacing_ratio)
        source_width = sum(glyph.width for glyph in glyphs) + gap * (len(glyphs) - 1)
        strip = Image.new("RGBA", (source_width, source_height), (0, 0, 0, 0))
        cursor = 0
        for glyph in glyphs:
            strip.alpha_composite(glyph, (cursor, source_height - glyph.height))
            cursor += glyph.width + gap

        target_width = bounds[2] - bounds[0] - spec.padding_px * 2
        target_height = bounds[3] - bounds[1] - spec.padding_px * 2
        scale = min(target_width / strip.width, target_height / strip.height)
        if scale <= 0:
            raise PipelineError("theme_digit_invalid", "Safe box của digit atlas không hợp lệ.")
        size = (max(1, round(strip.width * scale)), max(1, round(strip.height * scale)))
        rendered = strip.resize(size, Image.Resampling.LANCZOS)
        x = round((bounds[0] + bounds[2] - rendered.width) / 2)
        y = round((bounds[1] + bounds[3] - rendered.height) / 2)
        image.alpha_composite(rendered, (x, y))

    def build_review_start(
        self, project: MinigameProject, manifest: MinigameThemeManifest, cache_dir: Path
    ) -> Path:
        total = len(project.vocabulary)
        return self._build_dynamic_layer(
            "review-start", manifest, cache_dir, {"total": total},
            (
                ("review_sentence", f"Let's review the {total} words you just learned", "review_dynamic"),
                ("review_badge", f"{total}/{total} completed", "review_dynamic"),
            ),
        )

    def build_cta(
        self, project: MinigameProject, manifest: MinigameThemeManifest, cache_dir: Path
    ) -> Path:
        total = len(project.vocabulary)
        return self._build_dynamic_layer(
            "cta", manifest, cache_dir, {"total": total},
            (
                ("cta_eyebrow", "GREAT WORK!", "cta_eyebrow"),
                ("cta_headline", f"YOU LEARNED {total} NEW WORDS", "cta_headline"),
                ("cta_primary", "FOLLOW FOR MORE ENGLISH MINI GAMES", "cta_primary"),
                ("cta_secondary", "SAVE • REVIEW • SHARE", "cta_secondary"),
            ),
        )

    def _build_dynamic_layer(
        self,
        prefix: str,
        manifest: MinigameThemeManifest,
        cache_dir: Path,
        content_key: dict[str, object],
        lines: tuple[tuple[str, str, str], ...],
    ) -> Path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = stable_hash({
            "theme": manifest.checksum,
            "layout": {
                "version": manifest.layout_version,
                "slots": manifest.slots,
                "styles": manifest.styles,
            },
            "fonts": self.font_fingerprint(),
            "content": content_key,
            "lines": lines,
        })[:20]
        output = cache_dir / f"{prefix}-text-{key}.png"
        if output.is_file():
            return output
        image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        for slot_name, value, style_name in lines:
            slot = manifest.slots[slot_name]
            style = manifest.styles[style_name]
            self._draw_centered(
                image, value, slot.bounds, self.font_dir / style.font_file,
                style.max_size, style.min_size,
                fill=self._rgba(style.color), stroke_fill=self._rgba(style.outline_color),
                stroke_width=style.outline_width, bold=style.bold,
                max_lines=slot.max_lines, line_spacing=style.line_spacing,
            )
        return self._save_atomic(image, output)

    @staticmethod
    def _save_atomic(image: Image.Image, output: Path) -> Path:
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        try:
            image.save(partial, "PNG")
            with Image.open(partial) as result:
                if result.mode != "RGBA" or result.size != (1080, 1920):
                    raise PipelineError("theme_text_invalid", "Không thể tạo lớp chữ cho theme.")
            os.replace(partial, output)
            return output
        finally:
            partial.unlink(missing_ok=True)

    @staticmethod
    def hook_title(total: int) -> str:
        """Return the approved single-line challenge title."""
        return f"CAN YOU GET {total} OUT OF {total}?"

    @staticmethod
    def _rgba(color: str) -> tuple[int, int, int, int]:
        value = color.removeprefix("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), 255

    @staticmethod
    def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(path, size=size)

    def font_fingerprint(self) -> str:
        """Fingerprint every bundled font used by the pre-rendered visual layer."""
        return stable_hash(
            {
                path.name: sha256_file(path)
                for path in (
                    self.font_dir / "BebasNeue-Regular.ttf",
                    self.font_dir / "Montserrat-Variable.ttf",
                )
            }
        )

    def fit_size(
        self, text: str, bounds: tuple[int, int, int, int], maximum: int, minimum: int,
        *, title: bool = False, max_lines: int = 2, font_file: str | None = None,
        tracking: int = 0, line_spacing: int = 4,
    ) -> int:
        """Return the largest Pillow-measured font size that stays inside a safe box."""
        font_path = self.font_dir / (
            font_file or ("BebasNeue-Regular.ttf" if title else "Montserrat-Variable.ttf")
        )
        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        for size in range(maximum, minimum - 1, -1):
            font = self._font(font_path, size)
            lines = self._wrap_lines(draw, text, font, width, tracking)
            value = "\n".join(lines)
            box = draw.multiline_textbbox((0, 0), value, font=font, spacing=line_spacing)
            widest = max(self._line_width(draw, line, font, tracking) for line in lines)
            if len(lines) <= max_lines and widest <= width and box[3] - box[1] <= height:
                return size
        return minimum

    def wrap_text(
        self,
        text: str,
        bounds: tuple[int, int, int, int],
        size: int,
        font_file: str,
        tracking: int = 0,
    ) -> str:
        """Wrap without changing text, using the same Pillow font metrics as auto-fit."""
        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        font = self._font(self.font_dir / font_file, size)
        return "\n".join(
            self._wrap_lines(draw, text, font, bounds[2] - bounds[0], tracking)
        )

    @staticmethod
    def _wrap_lines(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        width: int,
        tracking: int = 0,
    ) -> list[str]:
        lines: list[str] = []
        for paragraph in text.replace("\\N", "\n").split("\n"):
            current = ""
            for word in paragraph.split():
                candidate = f"{current} {word}".strip()
                if not current or MinigameTextLayerService._line_width(
                    draw, candidate, font, tracking
                ) <= width:
                    current = candidate
                else:
                    lines.append(current)
                    current = word
            lines.append(current)
        return lines or [""]

    @staticmethod
    def _line_width(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        tracking: int,
    ) -> int:
        base = draw.textbbox((0, 0), text, font=font)[2]
        return base + max(0, len(text) - 1) * tracking

    def _draw_gradient_centered(
        self, image: Image.Image, text: str, bounds: tuple[int, int, int, int], font_path: Path,
        maximum: int, minimum: int, start_color: tuple[int, int, int, int],
        end_color: tuple[int, int, int, int], outline_color: tuple[int, int, int, int],
        outline_width: int,
    ) -> None:
        mask = Image.new("L", image.size, 0)
        draw = ImageDraw.Draw(mask)
        for size in range(maximum, minimum - 1, -2):
            font = self._font(font_path, size)
            box = draw.multiline_textbbox((0, 0), text, font=font, align="center", spacing=-size // 10)
            if box[2] - box[0] <= bounds[2] - bounds[0] and box[3] - box[1] <= bounds[3] - bounds[1]:
                x = (bounds[0] + bounds[2] - (box[2] - box[0])) // 2
                y = (bounds[1] + bounds[3] - (box[3] - box[1])) // 2 - box[1]
                draw.multiline_text(
                    (x, y), text, font=font, fill=255, align="center",
                    spacing=-size // 10, stroke_width=outline_width,
                )
                break
        gradient = Image.new("RGBA", image.size)
        pixels = gradient.load()
        for y in range(bounds[1], bounds[3] + 1):
            ratio = (y - bounds[1]) / max(1, bounds[3] - bounds[1])
            color = tuple(
                round(start + (end - start) * ratio)
                for start, end in zip(start_color, end_color, strict=True)
            )
            for x in range(bounds[0], bounds[2] + 1):
                pixels[x, y] = color
        text_layer = Image.composite(gradient, Image.new("RGBA", image.size), mask)
        if outline_width:
            outline_mask = mask.filter(ImageFilter.MaxFilter(outline_width * 2 + 1))
            outline = Image.new("RGBA", image.size, outline_color)
            outline.putalpha(ImageChops.subtract(outline_mask, mask))
            image.alpha_composite(outline)
        image.alpha_composite(text_layer)

    def _draw_centered(
        self, image: Image.Image, text: str, bounds: tuple[int, int, int, int], font_path: Path,
        maximum: int, minimum: int, *, fill: tuple[int, int, int, int],
        stroke_fill: tuple[int, int, int, int], stroke_width: int, bold: bool,
        max_lines: int, line_spacing: int = 4,
    ) -> None:
        draw = ImageDraw.Draw(image)
        for size in range(maximum, minimum - 1, -1):
            font = self._font(font_path, size)
            if bold and hasattr(font, "set_variation_by_name"):
                try:
                    font.set_variation_by_name("SemiBold")
                except OSError:
                    # Static fonts expose the Pillow method but have no variation axis.
                    pass
            lines = self._wrap_lines(draw, text, font, bounds[2] - bounds[0])
            value = "\n".join(lines)
            box = draw.multiline_textbbox(
                (0, 0), value, font=font, align="center", spacing=line_spacing,
                stroke_width=stroke_width,
            )
            widest = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines)
            if (
                len(lines) <= max_lines
                and widest + stroke_width * 2 <= bounds[2] - bounds[0]
                and box[3] - box[1] <= bounds[3] - bounds[1]
            ):
                center_x = (bounds[0] + bounds[2]) / 2
                center_y = (bounds[1] + bounds[3]) / 2
                x = round(center_x - (box[0] + box[2]) / 2)
                y = round(center_y - (box[1] + box[3]) / 2)
                draw.multiline_text(
                    (x, y), value, font=font, fill=fill, align="center", spacing=line_spacing,
                    stroke_fill=stroke_fill, stroke_width=stroke_width,
                )
                return
        raise PipelineError(
            "theme_topic_overflow",
            "Tên Topic quá dài để hiển thị rõ trên ảnh bìa. Hãy rút ngắn Topic.",
        )
