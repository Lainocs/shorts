import logging
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _load_credentials(token_file: str) -> Credentials:
    creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        Path(token_file).write_text(creds.to_json(), encoding="utf-8")
    return creds


def upload(video_path: str, metadata: dict, token_file: str, category_id: str = "27",
           privacy_status: str = "public", max_attempts: int = 3,
           backoff_seconds: tuple = (30, 120, 300)) -> str:
    creds = _load_credentials(token_file)
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata.get("tags", []),
            "categoryId": category_id,
        },
        "status": {"privacyStatus": privacy_status},
    }

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                _, response = request.next_chunk()
            logger.info("Vidéo uploadée id=%s (tentative %d)", response["id"], attempt)
            return response["id"]
        except Exception as exc:
            last_error = exc
            logger.warning("Échec upload tentative %d/%d: %s", attempt, max_attempts, exc)
            if attempt < max_attempts:
                delay = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
                time.sleep(delay)

    raise last_error
