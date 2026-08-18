import argparse
import logging
import os
import shutil
import sys
import uuid
from datetime import date
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

from modules import script_generator, tts, subtitles, visuals, video_builder, youtube_uploader, state


def setup_logging(logs_dir: str) -> None:
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(logs_dir) / f"{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
    )


def alert(webhook_url: str | None, message: str) -> None:
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json={"content": message}, timeout=10)
    except Exception:
        logging.getLogger(__name__).exception("Échec de l'envoi de l'alerte webhook")


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def run(config: dict, dry_run: bool) -> None:
    logger = logging.getLogger(__name__)
    run_id = uuid.uuid4().hex[:8]
    work_dir = Path(config["paths"]["output_dir"]) / run_id
    work_dir.mkdir(parents=True, exist_ok=True)

    db_path = config["paths"]["state_db"]
    webhook_url = os.environ.get(config["alerts"]["webhook_url_env"])

    try:
        logger.info("[%s] Génération du script...", run_id)
        script = script_generator.generate_script(config, db_path)
        topic_id = state.add_topic(db_path, script["title"], script["fact"])

        logger.info("[%s] Synthèse audio...", run_id)
        audio_path = str(work_dir / "audio.wav")
        tts.synthesize(
            script["script"],
            config["tts"]["voice_model"],
            config["tts"]["voice_config"],
            audio_path,
        )

        logger.info("[%s] Génération des sous-titres...", run_id)
        subtitles_path = str(work_dir / "subtitles.ass")
        subtitles.build_karaoke_subtitles(
            audio_path, subtitles_path, whisper_model=config["subtitles"]["whisper_model"]
        )

        max_segment_seconds = config["video"].get("max_clip_segment_seconds", 8)
        audio_duration = video_builder.probe_duration(audio_path)
        num_segments = video_builder.compute_num_segments(audio_duration, max_segment_seconds)

        logger.info("[%s] Récupération des visuels (%d clips visés)...", run_id, num_segments)
        api_key = os.environ["PEXELS_API_KEY"]
        clip_paths = visuals.fetch_clips(
            script["visual_keywords"],
            api_key,
            str(work_dir / "clips"),
            orientation=config["visuals"]["orientation"],
            min_clips=num_segments,
        )

        logger.info("[%s] Assemblage de la vidéo...", run_id)
        video_path = str(work_dir / "final.mp4")
        video_builder.build_video(
            clip_paths, audio_path, subtitles_path, video_path,
            resolution=tuple(config["video"]["resolution"]),
            max_segment_seconds=max_segment_seconds,
        )

        if dry_run:
            logger.info("[%s] DRY RUN - vidéo générée sans upload: %s", run_id, video_path)
            state.add_video(db_path, topic_id, None, "dry_run")
            return

        logger.info("[%s] Upload sur YouTube...", run_id)
        video_id = youtube_uploader.upload(
            video_path,
            {
                "title": script["title"],
                "description": script["description"],
                "tags": script["tags"],
            },
            token_file=config["youtube"]["token_file"],
            category_id=config["youtube"]["category_id"],
            privacy_status=config["youtube"]["privacy_status"],
            max_attempts=config["retry"]["max_attempts"],
            backoff_seconds=tuple(config["retry"]["backoff_seconds"]),
        )
        state.add_video(db_path, topic_id, video_id, "published")
        logger.info("[%s] Publiée: https://youtube.com/watch?v=%s", run_id, video_id)
        alert(
            webhook_url,
            f"[shorts:{config['theme']}] Nouvelle vidéo publiée : {script['title']}\n"
            f"Fait : {script['fact']}\n"
            f"https://youtube.com/watch?v={video_id}",
        )

        shutil.rmtree(work_dir, ignore_errors=True)

    except Exception as exc:
        logger.exception("[%s] Échec du pipeline", run_id)
        alert(webhook_url, f"[shorts:{config['theme']}] Run {run_id} a échoué: {exc}")
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    config = load_config(args.config)
    setup_logging(config["paths"]["logs_dir"])
    run(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
