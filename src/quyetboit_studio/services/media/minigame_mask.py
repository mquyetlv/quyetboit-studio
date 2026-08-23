from __future__ import annotations

import os
from collections import deque
from pathlib import Path
from typing import ClassVar

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ...core.minigame import MinigameThemeManifest
from ...core.models import PipelineError, stable_hash


class MinigameAlphaMaskService:
    """Builds a cached RGBA text-panel overlay without ever mutating source assets."""

    REVIEW_MASK_VERSION: ClassVar[int] = 3
    # Measured from the canonical 1080x1920 review artwork. These silhouettes
    # deliberately sit inside the outer glow of the card: the source artwork
    # blends that glow into its baked photo background, so keeping it exposes a
    # thin strip of the original theme when a user video is underneath.
    _REVIEW_CARD_SILHOUETTES: ClassVar[
        dict[str, tuple[tuple[int, int, int, int], int]]
    ] = {
        "theme-1": ((98, 104, 982, 1858), 78),
        "theme-2": ((114, 104, 966, 1858), 76),
        "theme-3": ((90, 94, 990, 1854), 76),
        "theme-4": ((96, 94, 984, 1858), 84),
        "theme-5": ((90, 99, 990, 1858), 76),
    }

    def build(self, manifest: MinigameThemeManifest, cache_dir: Path) -> Path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = stable_hash({"theme": manifest.checksum, "mask": manifest.mask_profile})[:20]
        output = cache_dir / f"{manifest.theme_id}-{key}.png"
        if output.is_file():
            with Image.open(output) as image:
                if image.mode == "RGBA" and image.size == (1080, 1920):
                    return output
        with Image.open(manifest.background_text) as source:
            rgb = source.convert("RGB").resize((1080, 1920), Image.Resampling.LANCZOS)
        values = np.asarray(rgb, dtype=np.uint8)
        matte = self._border_matte(values, manifest.mask_profile.matte_threshold)
        alpha = np.where(matte, 0, round(255 * manifest.mask_profile.panel_opacity)).astype(np.uint8)
        # Preserve decorative high-contrast details above the translucent panel.
        local_range = values.max(axis=2).astype(np.int16) - values.min(axis=2).astype(np.int16)
        detail = (local_range >= manifest.mask_profile.detail_threshold) & ~matte
        alpha[detail] = 255
        alpha_image = Image.fromarray(alpha).filter(
            ImageFilter.GaussianBlur(manifest.mask_profile.feather_radius)
        )
        alpha = np.asarray(alpha_image, dtype=np.uint8).copy()
        alpha[matte] = 0
        rgba = Image.fromarray(np.dstack((values, alpha)))
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        rgba.save(partial, "PNG")
        with Image.open(partial) as result:
            if result.mode != "RGBA" or result.size != (1080, 1920):
                raise PipelineError("theme_mask_invalid", "Không thể tạo alpha mask của theme.")
        os.replace(partial, output)
        return output

    def build_review(self, manifest: MinigameThemeManifest, cache_dir: Path) -> Path:
        """Lift the unchanged central Review card off its bundled photo background."""
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            bounds, radius = self._REVIEW_CARD_SILHOUETTES[manifest.theme_id]
        except KeyError as exc:
            raise PipelineError(
                "theme_review_mask_invalid", "Theme không có silhouette Review hợp lệ."
            ) from exc
        key = stable_hash(
            {
                "theme": manifest.checksum,
                "review_mask": self.REVIEW_MASK_VERSION,
                "bounds": bounds,
                "radius": radius,
            }
        )[:20]
        output = cache_dir / f"{manifest.theme_id}-review-{key}.png"
        if output.is_file():
            with Image.open(output) as image:
                if image.mode == "RGBA" and image.size == (1080, 1920):
                    return output

        with Image.open(manifest.review_screen) as source:
            rgb = source.convert("RGB").resize((1080, 1920), Image.Resampling.LANCZOS)
        scale = 4
        alpha_large = Image.new("L", (1080 * scale, 1920 * scale), 0)
        ImageDraw.Draw(alpha_large).rounded_rectangle(
            tuple(value * scale for value in bounds),
            radius=radius * scale,
            fill=255,
        )
        alpha = alpha_large.resize((1080, 1920), Image.Resampling.LANCZOS)
        # LANCZOS leaves a one-value halo a few pixels outside the silhouette.
        # Remove it so the selected video is fully visible outside the original
        # Review card, while the genuine card edge remains anti-aliased.
        alpha_values = np.asarray(alpha, dtype=np.uint8).copy()
        alpha_values[alpha_values < 8] = 0
        alpha = Image.fromarray(alpha_values)
        rgba = rgb.convert("RGBA")
        rgba.putalpha(alpha)
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        try:
            rgba.save(partial, "PNG")
            with Image.open(partial) as result:
                if result.mode != "RGBA" or result.size != (1080, 1920):
                    raise PipelineError(
                        "theme_review_mask_invalid",
                        "Không thể tạo alpha mask Review của theme.",
                    )
            os.replace(partial, output)
            return output
        finally:
            partial.unlink(missing_ok=True)

    @staticmethod
    def _border_matte(rgb: np.ndarray, threshold: int) -> np.ndarray:
        """Flood-fill only near-black pixels connected to an image edge."""
        height, width, _ = rgb.shape
        candidates = rgb.max(axis=2) <= threshold
        matte = np.zeros((height, width), dtype=bool)
        queue: deque[tuple[int, int]] = deque()
        for x in range(width):
            queue.extend(((0, x), (height - 1, x)))
        for y in range(height):
            queue.extend(((y, 0), (y, width - 1)))
        while queue:
            y, x = queue.popleft()
            if not (0 <= y < height and 0 <= x < width) or matte[y, x] or not candidates[y, x]:
                continue
            matte[y, x] = True
            queue.extend(((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)))
        return matte
