import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _probe_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def build_video(clip_paths: list[str], audio_path: str, subtitles_path: str, out_path: str,
                 resolution: tuple[int, int] = (1080, 1920)) -> None:
    width, height = resolution
    audio_duration = _probe_duration(audio_path)
    per_clip_duration = audio_duration / len(clip_paths)

    filter_parts = []
    concat_inputs = []
    for i in range(len(clip_paths)):
        filter_parts.append(
            f"[{i}:v]trim=duration={per_clip_duration:.3f},setpts=PTS-STARTPTS,fps=30,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}[v{i}]"
        )
        concat_inputs.append(f"[v{i}]")

    concat_filter = f"{''.join(concat_inputs)}concat=n={len(clip_paths)}:v=1:a=0[concat]"
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
    logger.info("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg a échoué (code {result.returncode}):\n{result.stderr[-4000:]}")
