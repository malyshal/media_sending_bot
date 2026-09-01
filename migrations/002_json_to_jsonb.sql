-- Migration: convert json columns to jsonb (required for @> containment operators).

-- posts.tags, posts.raw_data
ALTER TABLE posts
    ALTER COLUMN tags TYPE jsonb USING tags::jsonb;
ALTER TABLE posts
    ALTER COLUMN raw_data TYPE jsonb USING raw_data::jsonb;

-- chat_configs.include_tags, exclude_tags
ALTER TABLE chat_configs
    ALTER COLUMN include_tags TYPE jsonb USING include_tags::jsonb;
ALTER TABLE chat_configs
    ALTER COLUMN exclude_tags TYPE jsonb USING exclude_tags::jsonb;