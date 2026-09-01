-- Migration: rename max_posts_per_batch -> schedule_max_posts, add next_max_posts
-- Safe for repeated runs (idempotent).

ALTER TABLE chat_configs
    RENAME COLUMN max_posts_per_batch TO schedule_max_posts;

ALTER TABLE chat_configs
    ADD COLUMN IF NOT EXISTS next_max_posts INTEGER NOT NULL DEFAULT 1;