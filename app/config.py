import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# google api key requires port 8080, change when prod
REQUIRED_LISTEN_PORT = 8080


@dataclass(frozen=True)
class Settings:
    port: int
    default_stop: str
    timezone: str
    google_api_key: str | None
    google_referer: str
    data_dir: Path


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file)

    google_key = os.environ.get("GOOGLE_MAPS_API_KEY") or None
    port = int(os.environ.get("PORT") or REQUIRED_LISTEN_PORT)
    referer = os.environ.get("GOOGLE_REFERER") or f"http://localhost:{port}"

    if port != REQUIRED_LISTEN_PORT:
        raise ValueError(
            f"PORT={port} is not allowed; Google API key is allowlisted "
            f"to http://localhost:{REQUIRED_LISTEN_PORT}"
        )

    return Settings(
        port=port,
        default_stop=os.environ.get("DEFAULT_STOP", "3023"),
        timezone=os.environ.get("TZ_NAME", "America/Toronto"),
        google_api_key=google_key,
        google_referer=referer,
        data_dir=Path(__file__).resolve().parent.parent / "data",
    )
