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

from modules import ai_images, script_generator, tts, subtitles, visuals, video_builder, youtube_uploader, state


def setup_logging(logs_dir: str) -> None:
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(logs_dir) / f"{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
    )


def alert(webhook_url: str | None, message: str, ping_user_id: str | None = None) -> None:
    if not webhook_url:
        return
    payload = {"content": message}
    if ping_user_id:
        payload["content"] = f"<@{ping_user_id}> {message}"
        payload["allowed_mentions"] = {"users": [ping_user_id]}
    try:
        requests.post(webhook_url, json=payload, timeout=10)
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
        tts.synthesize(script["script"], config["tts"], audio_path)

        logger.info("[%s] Génération des sous-titres...", run_id)
        subtitles_path = str(work_dir / "subtitles.ass")
        subtitles.build_karaoke_subtitles(
            audio_path, subtitles_path, whisper_model=config["subtitles"]["whisper_model"],
            script_text=script["script"],
        )

        max_segment_seconds = config["video"].get("max_clip_segment_seconds", 8)
        audio_duration = video_builder.probe_duration(audio_path)
        num_segments = video_builder.compute_num_segments(audio_duration, max_segment_seconds)

        video_path = str(work_dir / "final.mp4")
        resolution = tuple(config["video"]["resolution"])
        visuals_cfg = config["visuals"]
        image_paths = []

        if visuals_cfg.get("image_providers"):
            image_seconds = visuals_cfg.get("image_segment_seconds", 6)
            num_images = video_builder.compute_num_segments(audio_duration, image_seconds)
            logger.info("[%s] Génération de %d images IA...", run_id, num_images)
            try:
                prompts = ai_images.generate_scene_prompts(
                    script["script"], num_images, config["llm"]["model"], os.environ["GROQ_API_KEY"]
                )
                image_paths = ai_images.fetch_images(prompts, str(work_dir / "images"), visuals_cfg["image_providers"])
            except Exception:
                logger.exception("[%s] Images IA indisponibles, repli sur Pexels", run_id)
            if not image_paths:
                logger.warning("[%s] Aucune image IA, repli sur Pexels", run_id)

        if image_paths:
            logger.info("[%s] Assemblage de la vidéo (images IA)...", run_id)
            video_builder.build_video_from_images(image_paths, audio_path, subtitles_path, video_path, resolution=resolution)
        else:
            logger.info("[%s] Récupération des visuels Pexels (%d clips visés)...", run_id, num_segments)
            clip_paths = visuals.fetch_clips(
                script["visual_keywords"],
                os.environ["PEXELS_API_KEY"],
                str(work_dir / "clips"),
                orientation=visuals_cfg["orientation"],
                min_clips=num_segments,
            )
            logger.info("[%s] Assemblage de la vidéo (Pexels)...", run_id)
            video_builder.build_video(
                clip_paths, audio_path, subtitles_path, video_path,
                resolution=resolution, max_segment_seconds=max_segment_seconds,
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
        if "invalid_grant" in str(exc):
            host = os.environ.get("REAUTH_PUBLIC_HOST")
            secret = os.environ.get("REAUTH_SECRET")
            if host and secret:
                alert(
                    webhook_url,
                    f"[shorts:{config['theme']}] Token YouTube expiré, reclique ici pour le renouveler :\n"
                    f"https://{host}/start?key={secret}",
                    ping_user_id=os.environ.get("DISCORD_PING_USER_ID"),
                )
            else:
                alert(webhook_url, f"[shorts:{config['theme']}] Token YouTube expiré (serveur de réauth non configuré)")
        else:
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
