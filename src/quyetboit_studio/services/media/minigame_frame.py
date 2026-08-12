from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from ...core.models import PipelineError, sha256_file, stable_hash


class MinigameVisualFrameService:
    """Atomically cache immutable 1080×1920 composites used by visual scenes."""

    ALGORITHM_VERSION = 1

    def build(
        self,
        background: Path,
        overlays: tuple[Path, ...],
        cache_dir: Path,
        *,
        layout_key: object,
    ) -> Path:
        sources = (background, *overlays)
        key = stable_hash(
            {
                "algorithm": self.ALGORITHM_VERSION,
                "sources": [
                    (index, path.name, sha256_file(path)) for index, path in enumerate(sources)
                ],
                "layout": layout_key,
                "canvas": (1080, 1920),
            }
        )[:20]
        cache_dir.mkdir(parents=True, exist_ok=True)
        output = cache_dir / f"visual-frame-{key}.png"
        if output.is_file():
            with Image.open(output) as cached:
                if cached.mode == "RGBA" and cached.size == (1080, 1920):
                    return output
        with Image.open(background) as source:
            frame = source.convert("RGBA").resize((1080, 1920), Image.Resampling.LANCZOS)
        for overlay_path in overlays:
            with Image.open(overlay_path) as source:
                overlay = source.convert("RGBA")
                if overlay.size != frame.size:
                    overlay = overlay.resize(frame.size, Image.Resampling.LANCZOS)
                frame.alpha_composite(overlay)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        try:
            frame.save(partial, "PNG")
            with Image.open(partial) as result:
                if result.mode != "RGBA" or result.size != (1080, 1920):
                    raise PipelineError(
                        "minigame_frame_invalid", "Không thể tạo visual frame 1080×1920."
                    )
            os.replace(partial, output)
            return output
        finally:
            partial.unlink(missing_ok=True)
