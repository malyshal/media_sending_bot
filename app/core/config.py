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
    joyreactor_base_url: str = "https://joyreactor.cc"
    joyreactor_api_url: str = "https://api.joyreactor.com/graphql"
    api_request_interval: float = 2.5
    
    # Application Settings
    log_level: str = "INFO"
    log_retention_days: int = 7
    cache_retention_hours: int = 6
    max_fresh_posts_for_batch: int = 20
    max_media_size_mb: int = 50
    default_timezone: str = "Europe/Minsk"
    
    # Queue backend (TS #66): 'memory' is the supported single-process mode.
    # 'redis' is reserved for multi-instance scaling.
    queue_type: str = "memory"

    # TLS SNI overrides for proxy-based test deployments:
    # "connect-host:sni-host", e.g. "host.docker.internal:api.joyreactor.com".
    # Empty by default in production.
    tls_sni_overrides: List[str] = []
    
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        case_sensitive=False
    )

settings = Settings()
