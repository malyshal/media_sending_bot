-- Migration: normalize media_type values to TS #12/#33 vocabulary
-- ('photo' -> 'image'; 'video' -> 'gif'/'video' handled by delivery logic;
--  keep 'video' as-is). Only rewrites legacy 'photo'.

UPDATE posts
SET media_type = 'image'
WHERE media_type = 'photo';