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

    # Media storage directory (mount an external disk here in production).
    # Downloaded media is cached by URL hash and reused across deliveries.
    media_dir: str = "tmp/media"

    # Image CDN base (test deployments may point it at a local TCP proxy)
    img_cdn_base: str | None = None

    # Long posts with more than this many "runs" (text blocks + media groups)
    # are sent collapsed: only the first run, with a "Показать весь пост"
    # button. Pressing it deletes the placeholder and sends the rest.
    collapse_post_threshold: int = 3

    # TTL for the in-process stash that holds the deferred runs while the
    # user is reading the preview and deciding whether to expand.
    collapsed_post_stash_ttl_seconds: int = 3600

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
