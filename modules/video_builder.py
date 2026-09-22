import json
import logging
import math
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def probe_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def compute_num_segments(audio_duration: float, max_segment_seconds: float) -> int:
    return max(1, math.ceil(audio_duration / max_segment_seconds))


def build_video(clip_paths: list[str], audio_path: str, subtitles_path: str, out_path: str,
                 resolution: tuple[int, int] = (1080, 1920), max_segment_seconds: float = 8) -> None:
    width, height = resolution
    audio_duration = probe_duration(audio_path)
    num_segments = compute_num_segments(audio_duration, max_segment_seconds)
    segment_duration = audio_duration / num_segments
    clip_durations = [probe_duration(c) for c in clip_paths]

    filter_parts = []
    concat_inputs = []
    use_counts = [0] * len(clip_paths)
    for seg in range(num_segments):
        clip_idx = seg % len(clip_paths)
        native_duration = clip_durations[clip_idx]

        if native_duration > segment_duration:
            usable_range = native_duration - segment_duration
            start = (use_counts[clip_idx] * segment_duration) % usable_range
            trim_duration = segment_duration
        else:
            start = 0.0
            trim_duration = native_duration
        use_counts[clip_idx] += 1

        pad_needed = segment_duration - trim_duration
        pad_filter = f",tpad=stop_mode=clone:stop_duration={pad_needed:.3f}" if pad_needed > 0.01 else ""

        filter_parts.append(
            f"[{clip_idx}:v]trim=start={start:.3f}:duration={trim_duration:.3f},setpts=PTS-STARTPTS{pad_filter},fps=30,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}[v{seg}]"
        )
        concat_inputs.append(f"[v{seg}]")

    concat_filter = f"{''.join(concat_inputs)}concat=n={num_segments}:v=1:a=0[concat]"
    subtitles_filter = f"[concat]ass={subtitles_path}[outv]"
    filter_complex = ";".join(filter_parts + [concat_filter, subtitles_filter])

    cmd = ["ffmpeg", "-y"]
    for clip in clip_paths:
        cmd += ["-i", clip]
    cmd += ["-i", audio_path]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", f"{len(clip_paths)}:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", "30",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        out_path,
    ]

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    logger.info("ffmpeg: %d segments (~%.1fs chacun) sur %d clips sources", num_segments, segment_duration, len(clip_paths))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg a échoué (code {result.returncode}):\n{result.stderr[-4000:]}")
