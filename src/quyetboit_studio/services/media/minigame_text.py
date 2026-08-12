from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ...core.minigame import MinigameProject, MinigameThemeManifest
from ...core.models import PipelineError, sha256_file, stable_hash


class MinigameTextLayerService:
    """Renders hook typography with Pillow so its layout does not depend on libass fonts."""

    def __init__(self) -> None:
        self.font_dir = Path(__file__).resolve().parents[2] / "assets" / "fonts"

    def build_hook(self, project: MinigameProject, manifest: MinigameThemeManifest, cache_dir: Path) -> Path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        total = len(project.vocabulary)
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
                "title": total,
                "topic": project.topic,
            }
        )[:20]
        output = cache_dir / f"hook-text-{key}.png"
        if output.is_file():
            return output
        image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        title = self.hook_title(total)
        title_slot = manifest.slots["start_title"]
        title_style = manifest.styles["title"]
        self._draw_gradient_centered(
            image,
            title,
            title_slot.bounds,
            self.font_dir / title_style.font_file,
            title_style.max_size,
            title_style.min_size,
        )
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
            max_lines=topic_slot.max_lines,
        )
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
        return f"CAN YOU GET {total}/{total}?"

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
        maximum: int, minimum: int,
    ) -> None:
        mask = Image.new("L", image.size, 0)
        draw = ImageDraw.Draw(mask)
        for size in range(maximum, minimum - 1, -2):
            font = self._font(font_path, size)
            box = draw.multiline_textbbox((0, 0), text, font=font, align="center", spacing=-size // 10)
            if box[2] - box[0] <= bounds[2] - bounds[0] and box[3] - box[1] <= bounds[3] - bounds[1]:
                x = (bounds[0] + bounds[2] - (box[2] - box[0])) // 2
                y = (bounds[1] + bounds[3] - (box[3] - box[1])) // 2 - box[1]
                draw.multiline_text((x, y), text, font=font, fill=255, align="center", spacing=-size // 10)
                break
        gradient = Image.new("RGBA", image.size)
        pixels = gradient.load()
        for y in range(bounds[1], bounds[3] + 1):
            ratio = (y - bounds[1]) / max(1, bounds[3] - bounds[1])
            color = (246 - round(90 * ratio), 248, 255, 255)
            for x in range(bounds[0], bounds[2] + 1):
                pixels[x, y] = color
        image.alpha_composite(Image.composite(gradient, Image.new("RGBA", image.size), mask))

    def _draw_centered(
        self, image: Image.Image, text: str, bounds: tuple[int, int, int, int], font_path: Path,
        maximum: int, minimum: int, *, fill: tuple[int, int, int, int], max_lines: int,
    ) -> None:
        draw = ImageDraw.Draw(image)
        words = text.split()
        for size in range(maximum, minimum - 1, -1):
            font = self._font(font_path, size)
            lines: list[str] = [""]
            for word in words:
                proposal = f"{lines[-1]} {word}".strip()
                if draw.textbbox((0, 0), proposal, font=font)[2] <= bounds[2] - bounds[0]:
                    lines[-1] = proposal
                else:
                    lines.append(word)
            value = "\n".join(lines)
            box = draw.multiline_textbbox((0, 0), value, font=font, align="center", spacing=4)
            if len(lines) <= max_lines and box[3] - box[1] <= bounds[3] - bounds[1]:
                x = (bounds[0] + bounds[2] - (box[2] - box[0])) // 2
                y = (bounds[1] + bounds[3] - (box[3] - box[1])) // 2 - box[1]
                draw.multiline_text((x, y), value, font=font, fill=fill, align="center", spacing=4)
                return
