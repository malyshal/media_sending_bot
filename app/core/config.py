from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    # Telegram Bot Settings
    bot_token: str
    initial_admin_ids: List[int] = []
    
    # Database Settings
    database_url: str
    
    # Redis Settings
    redis_url: str
    
    # JoyReactor API Settings
    joyreactor_api_key: str | None = None
    joyreactor_base_url: str = "https://joyreactor.cc"
    joyreactor_api_url: str = "https://api.joyreactor.com/graphql"
    api_request_interval: float = 2.0
    
    # Application Settings
    log_level: str = "INFO"
    log_retention_days: int = 7
    cache_retention_hours: int = 6
    max_fresh_posts_for_batch: int = 20
    max_media_size_mb: int = 50
    default_timezone: str = "Europe/Moscow"
    
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        case_sensitive=False
    )

settings = Settings()
