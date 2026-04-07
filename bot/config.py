import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def _find_env_file() -> str:
    """--env flag bilan boshqa .env fayl ko'rsatish mumkin."""
    for i, arg in enumerate(sys.argv):
        if arg == "--env" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return ".env"


load_dotenv(_find_env_file())


class Config:
    BOT_NAME: str = os.getenv("BOT_NAME", "ShaxsiyBot")
    TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    GEMINI_API_KEYS: list[str] = [
        k.strip()
        for k in os.getenv("GEMINI_API_KEYS", os.getenv("GEMINI_API_KEY", "")).split(",")
        if k.strip()
    ]
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))
    VIP_IDS: list[int] = [
        int(v.strip())
        for v in os.getenv("VIP_IDS", "").split(",")
        if v.strip()
    ]
    SYSTEM_PROMPT_FILE: str = os.getenv("SYSTEM_PROMPT_FILE", "")
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", "data"))
    USE_SEARCH: bool = os.getenv("USE_SEARCH", "false").lower() == "true"
    # Kanal monitoring — kanallardan muhim yangilikni guruhga ulashish
    WATCH_CHANNELS: list[str] = [
        c.strip()
        for c in os.getenv("WATCH_CHANNELS", "").split(",")
        if c.strip()
    ]
    NEWS_TARGET_CHAT: int = int(os.getenv("NEWS_TARGET_CHAT", "0"))
    DEBOUNCE_SEC: float = float(os.getenv("DEBOUNCE_SEC", "1.0"))
    MAX_MESSAGES_PER_MIN: int = int(os.getenv("MAX_MESSAGES_PER_MIN", "20"))

    ALLOWED_GROUPS: list[int] = [
        int(g.strip())
        for g in os.getenv("ALLOWED_GROUPS", "").split(",")
        if g.strip()
    ]

    @classmethod
    def db_path(cls) -> Path:
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        return cls.DATA_DIR / "bot.db"

    @classmethod
    def memories_dir(cls) -> Path:
        p = cls.DATA_DIR / "memories"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @classmethod
    def is_owner(cls, user_id: int) -> bool:
        return cls.OWNER_ID == user_id

    @classmethod
    def is_vip(cls, user_id: int) -> bool:
        return user_id == cls.OWNER_ID or user_id in cls.VIP_IDS

    @classmethod
    def is_allowed_group(cls, chat_id: int) -> bool:
        return not cls.ALLOWED_GROUPS or chat_id in cls.ALLOWED_GROUPS
