from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageFont

from ...core.football import FootballRenderConfig, FootballSegment
from ...core.models import (
    CancellationToken,
    PipelineError,
    ProgressCallback,
    ProgressEvent,
    VideoArtifact,
    sha256_file,
    stable_hash,
)
from .ffmpeg import FFmpegRunner, FilterGraphBuilder
from .football_cache import atomic_text, cache_record, cache_valid


class FootballMediaService:
    FADE_MS = 450
    OUTPUT_FPS = 30

    def __init__(
        self,
        workspace: Path,
        runner: FFmpegRunner,
        config: FootballRenderConfig,
        cancel: CancellationToken,
    ) -> None:
        self.workspace, self.runner, self.render_config, self.cancel = (
            workspace,
            runner,
            config,
            cancel,
        )

    def _run_video(
        self, args: list[str], *, duration_ms: int, progress: ProgressCallback | None = None
    ) -> None:
        self.cancel.raise_if_cancelled()
        encoder = getattr(self, "_encoder", None)
        if encoder is None:
            encoder = self.runner.preflight().get("encoder") or "libx264"
            self._encoder = encoder
        gpu_args = list(args)
        if encoder != "libx264":
            gpu_args[gpu_args.index("-c:v") + 1] = encoder
            for flag in ("-preset", "-crf"):
                position = gpu_args.index(flag)
                del gpu_args[position : position + 2]
            options = {
                "h264_nvenc": ["-preset", "p4", "-cq", "20", "-b:v", "0"],
                "h264_qsv": ["-preset", "medium", "-global_quality", "20"],
                "h264_amf": ["-quality", "balanced", "-qp_i", "20", "-qp_p", "20"],
            }
            gpu_args[-1:-1] = options.get(encoder, [])
        try:
            self.runner.run(
                gpu_args, duration_ms=duration_ms, progress=progress, cancel=self.cancel
            )
        except PipelineError:
            self.cancel.raise_if_cancelled()
            if encoder == "libx264":
                raise
            self._encoder = "libx264"
            self.runner.run(args, duration_ms=duration_ms, progress=progress, cancel=self.cancel)

    def _validate_render(self, path: Path, expected_ms: int) -> None:
        info = self.runner.probe.inspect(path)
        for kind in ("video", "audio"):
            stream = next(
                (item for item in info.get("streams", []) if item.get("codec_type") == kind), None
            )
            if stream is None or float(stream.get("duration", 0)) * 1000 < expected_ms - 40:
                raise PipelineError(
                    "football_truncated_media",
                    "Video thiếu hình hoặc bị hụt lời đọc. File cũ được giữ nguyên; hãy thử render lại.",
                )
        self.runner.probe.video_artifact(path)

    def write_cover(self, output: Path, directory: Path) -> None:
        cover = directory / "cover.jpg"
        partial_cover = cover.with_name(".cover.partial.jpg")
        try:
            self.runner.run(
                ["-i", str(output), "-frames:v", "1", str(partial_cover)], cancel=self.cancel
            )
            with Image.open(partial_cover) as image:
                image.verify()
            os.replace(partial_cover, cover)
        finally:
            partial_cover.unlink(missing_ok=True)

    def _join_voice(self, clips: list[Path], output: Path) -> None:
        partial = output.with_name(f".{output.stem}.partial.wav")
        args = []
        for path in clips:
            args += ["-i", str(path)]
        graph = "".join(f"[{i}:a]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=0:a=1[a]"
        try:
            self.runner.run(
                [*args, "-filter_complex", graph, "-map", "[a]", "-c:a", "pcm_s16le", str(partial)],
                cancel=self.cancel,
            )
            self.runner.probe.audio_artifact(partial)
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)

    def _extract_frames(
        self, source: VideoArtifact, segment: FootballSegment, index: int
    ) -> list[Path]:
        timestamps = segment.timestamps()
        if not timestamps:
            raise PipelineError(
                "football_no_frames",
                f"Phân đoạn {index + 1} không có timestamp ảnh. Hãy bổ sung frames.",
            )
        for timestamp in timestamps:
            seconds = 0
            for part in timestamp.split(":"):
                seconds = seconds * 60 + int(part)
            if seconds * 1000 >= source.duration_ms:
                raise PipelineError(
                    "football_timestamp_range",
                    f"Phân đoạn {index + 1}: timestamp {timestamp} vượt thời lượng video. Hãy sửa JSON.",
                )
        source_key = source.checksum or sha256_file(source.path, self.cancel)
        directory = self.workspace / "frames" / source_key[:20]
        directory.mkdir(parents=True, exist_ok=True)
        frames: list[Path] = []
        for timestamp in timestamps:
            self.cancel.raise_if_cancelled()
            key = stable_hash({"source": source_key, "timestamp": timestamp, "version": 2})
            target = directory / f"{key[:20]}.jpg"
            if not cache_valid(target, key, self.cancel):
                partial = target.with_name(f".{target.stem}.partial.jpg")
                try:
                    self.runner.run(
                        [
                            "-ss",
                            timestamp,
                            "-i",
                            str(source.path),
                            "-frames:v",
                            "1",
                            "-q:v",
                            "2",
                            str(partial),
                        ],
                        cancel=self.cancel,
                    )
                    with Image.open(partial) as image:
                        image.verify()
                    os.replace(partial, target)
                    cache_record(target, key, self.cancel)
                except (OSError, PipelineError) as exc:
                    self.cancel.raise_if_cancelled()
                    raise PipelineError(
                        "football_frame_failed",
                        f"Không trích được ảnh {timestamp} ở phân đoạn {index + 1}. Hãy chọn timestamp khác hoặc kiểm tra video nguồn.",
                    ) from exc
                finally:
                    partial.unlink(missing_ok=True)
            frames.append(target)
        return frames

    def _normalize_audio(self, source: Path, target: Path, *, volume: float = 1.0) -> None:
        partial = target.with_name(f".{target.stem}.partial.wav")
        try:
            self.runner.run(
                [
                    "-i",
                    str(source),
                    "-af",
                    f"loudnorm=I=-16:TP=-1.5:LRA=11,volume={volume:.6f}",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-c:a",
                    "pcm_s16le",
                    str(partial),
                ],
                cancel=self.cancel,
            )
            self.runner.probe.audio_artifact(partial)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)

    def _render_segment(
        self,
        frames: list[Path],
        audio: Path,
        ass: Path | None,
        target: Path,
        progress: ProgressCallback | None,
        base: float,
        span: float,
        *,
        weights: list[float] | None = None,
        source: VideoArtifact | None = None,
        timestamps: list[str] | None = None,
    ) -> None:
        duration = self.runner.probe.audio_artifact(audio).duration_ms
        weights = weights if weights and len(weights) == len(frames) else [1.0] * len(frames)
        seconds = duration / 1000
        fade = min(self.FADE_MS / 1000, seconds * min(weights) / sum(weights) / 3)
        # Overlap is extra video time; speech is never shortened to fit images.
        lengths = [
            (seconds + (len(frames) - 1) * fade) * weight / sum(weights) for weight in weights
        ]
        partial = target.with_name(f".{target.stem}.partial.mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        args: list[str] = []
        # Loop at the delivery cadence so each zoompan output frame maps to one
        # display frame; this keeps the configured motion smooth and predictable.
        motion = self.render_config.use_motion and source is not None and timestamps is not None
        for i, (frame, length) in enumerate(zip(frames, lengths)):
            if motion:
                args += [
                    "-ss",
                    timestamps[i],
                    "-t",
                    f"{min(3.0, length):.6f}",
                    "-i",
                    str(source.path),
                ]
            else:
                args += [
                    "-loop",
                    "1",
                    "-framerate",
                    str(self.OUTPUT_FPS),
                    "-t",
                    f"{length:.6f}",
                    "-i",
                    str(frame),
                ]
        args += ["-i", str(audio)]
        chains: list[str] = []
        for i in range(len(frames)):
            if motion:
                chains.append(
                    f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=increase,"
                    f"crop=1920:1080:(iw-ow)*{self.render_config.focus_x}:(ih-oh)*{self.render_config.focus_y},"
                    f"setsar=1,fps={self.OUTPUT_FPS},settb=AVTB,setpts=PTS-STARTPTS,"
                    f"tpad=stop_mode=clone:stop_duration={lengths[i]:.6f},trim=duration={lengths[i]:.6f}[v{i}]"
                )
            else:
                chains.append(self._ken_burns_filter(i, lengths[i]))
        label = "[v0]"
        for i in range(1, len(frames)):
            offset = sum(lengths[:i]) - i * fade
            chains.append(
                f"{label}[v{i}]xfade=transition=fade:duration={fade:.3f}:offset={offset:.3f}[x{i}]"
            )
            label = f"[x{i}]"
        if ass:
            escaped = self._filter_path(ass)
            chains.append(f"{label}subtitles=filename={escaped}[outv]")
            label = "[outv]"
        if self.render_config.top_text.strip():
            chains.append(self._top_text_filter(label))
            label = "[title]"
        chains.append(
            f"{label}tpad=stop_mode=clone:stop_duration=1,trim=duration={seconds:.6f}[fullvoice]"
        )
        label = "[fullvoice]"
        # libass/JPEG inputs can otherwise leave the final MP4 tagged yuvj444p,
        # which Windows Media Player does not decode on many installations.
        chains.append(f"{label}scale=in_range=auto:out_range=tv,format=yuv420p[compat]")
        label = "[compat]"
        args += [
            "-filter_complex",
            ";".join(chains),
            "-map",
            label,
            "-map",
            f"{len(frames)}:a",
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-level:v",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{seconds:.6f}",
            "-movflags",
            "+faststart",
            str(partial),
        ]
        try:
            self._run_video(
                args,
                duration_ms=duration,
                progress=lambda event: self._emit(
                    progress,
                    base + span * min(0.99, event.fraction),
                    f"Render segment — {event.message}",
                ),
            )
            self._validate_render(partial, duration)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)

    def _ken_burns_filter(self, index: int, duration_seconds: float) -> str:
        """Build a configured centered 30 fps camera push-in without lateral shake."""
        frames = max(1, round(duration_seconds * self.OUTPUT_FPS))
        start = self.render_config.ken_burns_start_zoom
        end = self.render_config.ken_burns_end_zoom
        # `on` is zoompan's output-frame number. d=1 and a 30 fps looping input
        # avoid the old d=<segment frame count> burst/reset behaviour. The cosine
        # curve has zero velocity at both ends, so the move eases in and out.
        # This deliberately operates at delivery resolution. It applies the full
        # start/end range selected in the UI; a 4K intermediate adds cost without
        # making the zoom smoother.
        # Keep the crop at the image centre: changing focus for each frame made
        # consecutive xfade transitions look like a lateral shake.
        denominator = max(1, frames - 1)
        return (
            f"[{index}:v]scale=1920:1080:force_original_aspect_ratio=increase,"
            f"crop=1920:1080:(iw-ow)*{self.render_config.focus_x}:(ih-oh)*{self.render_config.focus_y},setsar=1,"
            f"zoompan=z='{start:.8f}+{end - start:.8f}*(0.5-0.5*cos(PI*on/{denominator}))':"
            f"x='(iw-iw/zoom)*{self.render_config.focus_x}':"
            f"y='(ih-ih/zoom)*{self.render_config.focus_y}':"
            f"d=1:s=1920x1080:fps={self.OUTPUT_FPS},trim=duration={duration_seconds:.3f},"
            f"setpts=PTS-STARTPTS[v{index}]"
        )

    def _concat(
        self, segments: list[Path], output: Path, progress: ProgressCallback | None
    ) -> None:
        partial = output.with_name(f".{output.stem}.partial.mp4")
        durations = [self.runner.probe.video_artifact(path).duration_ms / 1000 for path in segments]
        args: list[str] = []
        for segment in segments:
            args += ["-i", str(segment)]
        chains: list[str] = []
        for index in range(len(segments)):
            chains.append(
                f"[{index}:v]fps={self.OUTPUT_FPS},format=yuv420p,setpts=PTS-STARTPTS[v{index}]"
            )
            chains.append(
                f"[{index}:a]aresample=48000,apad,atrim=duration={durations[index]:.6f},asetpts=PTS-STARTPTS[a{index}]"
            )
        # Cut between events: neither speech nor burned captions overlap. Image
        # transitions inside an event retain their compensated xfade.
        chains.append(
            "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
            + f"concat=n={len(segments)}:v=1:a=1[joined_video][joined_audio]"
        )
        video, audio, cursor = "[joined_video]", "[joined_audio]", sum(durations)
        args += [
            "-filter_complex",
            ";".join(chains),
            "-map",
            video,
            "-map",
            audio,
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-level:v",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(partial),
        ]
        try:
            self._run_video(
                args,
                duration_ms=round(cursor * 1000),
                progress=lambda event: self._emit(
                    progress, 0.90 + 0.08 * event.fraction, event.message
                ),
            )
            self._validate_render(partial, round(cursor * 1000))
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)

    def _mix_music(self, video: Path, music: Path, output: Path) -> None:
        self.runner.probe.audio_artifact(music)
        duration = self.runner.probe.video_artifact(video).duration_ms / 1000
        partial = output.with_name(f".{output.stem}.partial.mp4")
        graph = (
            "[0:a]asplit=2[voice][side];"
            "[1:a]aresample=48000,loudnorm=I=-24:TP=-3:LRA=7,volume=0.25[bed];"
            "[bed][side]sidechaincompress=threshold=0.02:ratio=12:attack=10:release=300[duck];"
            "[voice][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=0[mix]"
        )
        try:
            self.runner.run(
                [
                    "-i",
                    str(video),
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(music),
                    "-filter_complex",
                    graph,
                    "-map",
                    "0:v",
                    "-map",
                    "[mix]",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-t",
                    f"{duration:.6f}",
                    "-movflags",
                    "+faststart",
                    str(partial),
                ],
                cancel=self.cancel,
            )
            self.runner.probe.video_artifact(partial)
            self.runner.probe.audio_artifact(partial)
            os.replace(partial, output)
        finally:
            partial.unlink(missing_ok=True)

    def _write_ass(
        self,
        text: str,
        index: int,
        duration_ms: int,
        *,
        timing_text: str | None = None,
        cues: list[tuple[int, int, str]] | None = None,
        event: str = "",
    ) -> Path:
        path = self.workspace / "subtitles" / f"segment-{index + 1:03d}.ass"
        path.parent.mkdir(parents=True, exist_ok=True)
        cues = cues if cues is not None else [(0, duration_ms, text)]
        dialogues = "".join(
            "Dialogue: 0,"
            f"{self._ass_time(start_ms)},{self._ass_time(end_ms)},Default,,0,0,0,,"
            f"{self._layout_cue(cue)}\n"
            for start_ms, end_ms, cue in cues
        )
        if event:
            event_y = (
                max(
                    90,
                    self.render_config.top_margin
                    + round(self.render_config.top_font_size * 1.5)
                    + 24,
                )
                if self.render_config.top_text
                else 60
            )
            if self.render_config.subtitle_position == "top" and cues:
                event_y = max(
                    event_y,
                    self.render_config.subtitle_margin
                    + round(self.render_config.subtitle_font_size * 1.5)
                    + 24,
                )
            event_y = min(event_y, 900)
            dialogues += f"Dialogue: 1,0:00:00.00,{self._ass_time(duration_ms)},Event,,0,0,0,,{{\\an7\\pos(60,{event_y})}}{self._escape_ass(event)}\n"
        font_name = FilterGraphBuilder._font_family(self._font_path())
        atomic_text(
            path,
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
            "WrapStyle: 0\nScaledBorderAndShadow: yes\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
            "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
            "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Default,{font_name},{self.render_config.subtitle_font_size},&H{self._ass_color(self.render_config.subtitle_font_color)},&H000000FF,&H{255 - round(self.render_config.subtitle_background_opacity * 2.55):02X}{self._ass_color(self.render_config.subtitle_background_color)[2:]},&HFF000000,"
            # Spacing -0.5 tightens the otherwise visibly loose libass tracking.
            "-1,0,0,0,100,100,-0.5,0,3,8,0,5,90,90,0,1\n"
            f"Style: Event,{font_name},34,&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,-1,0,0,0,100,100,0,0,3,8,0,7,60,60,60,1\n[Events]\n"
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n" + dialogues,
            encoding="utf-8-sig",
        )
        return path

    def _layout_cue(self, text: str) -> str:
        size = self.render_config.subtitle_font_size
        font_path = self._font_path()
        font = (
            ImageFont.truetype(str(font_path), size)
            if font_path
            else ImageFont.load_default(size=size)
        )
        lines, current = [], ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if current and font.getlength(candidate) > 1740:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        if len(lines) <= 1:
            return f"{{\\an5\\pos(960,{self._subtitle_box_center_y()})}}{self._escape_ass(text)}"
        # Fit long clauses without fabricating individual word timing.
        size = min(size, max(16, round(220 / len(lines))))
        if self.render_config.subtitle_position == "bottom":
            anchor, y = 2, 1080 - self.render_config.subtitle_margin
        else:
            anchor, y = 8, self.render_config.subtitle_margin
        return f"{{\\an{anchor}\\pos(960,{y})\\fs{size}}}" + r"\N".join(
            self._escape_ass(line) for line in lines
        )

    @staticmethod
    def _ass_time(value_ms: int) -> str:
        hours, remainder = divmod(max(0, value_ms), 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, milliseconds = divmod(remainder, 1_000)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds // 10:02d}"

    @staticmethod
    def _escape_ass(value: str) -> str:
        return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")

    @staticmethod
    def _ass_color(value: str) -> str:
        clean = value.strip().lstrip("#")
        if len(clean) != 6 or not all(char in "0123456789abcdefABCDEF" for char in clean):
            clean = "FFFFFF"
        return f"00{clean[4:6]}{clean[2:4]}{clean[:2]}"

    def _subtitle_box_center_y(self) -> int:
        config = self.render_config
        height = round(config.subtitle_font_size * 1.5)
        y = (
            config.subtitle_margin
            if config.subtitle_position == "top"
            else 1080 - config.subtitle_margin - height
        )
        return y + height // 2

    @staticmethod
    def _font_path() -> Path | None:
        """One font source for the title drawtext and subtitle libass style."""
        return FilterGraphBuilder._font_path(False)

    def _top_text_filter(self, label: str) -> str:
        config = self.render_config
        height = round(config.top_font_size * 1.5)
        y = config.top_margin
        color = config.top_background_color.strip() or "#000000"
        textfile = self.workspace / "title.txt"
        textfile.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(textfile, config.top_text.strip().replace("\n", " "))
        escaped = self._filter_path(textfile)
        font = self._font_path()
        font_option = f"fontfile={self._filter_path(font)}:" if font else ""
        return (
            f"{label}drawbox=x=0:y={y}:w=iw:h={height}:color={color}@{config.top_background_opacity / 100:.4g}:t=fill[titlebg];"
            f"[titlebg]drawtext={font_option}textfile={escaped}:expansion=none:fontsize={config.top_font_size}:fontcolor={config.top_font_color}:"
            f"x=(w-text_w)/2:y={y}+({height}-text_h)/2:fix_bounds=1[title]"
        )

    @staticmethod
    def _filter_path(path: Path) -> str:
        # Escape the option parser, then the surrounding filtergraph parser.
        value = str(path.resolve()).replace("\\", "/")
        value = "".join("\\" + char if char in "\\':" else char for char in value)
        return "".join("\\" + char if char in "\\'[],;" else char for char in value)

    @staticmethod
    def _emit(callback: ProgressCallback | None, fraction: float, message: str) -> None:
        if callback:
            callback(ProgressEvent(0, max(0, min(1, fraction)), message))
