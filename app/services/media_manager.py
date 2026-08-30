import asyncio
import os
import subprocess
import structlog
from typing import Optional, Tuple
from pathlib import Path
from app.core.config import settings
import httpx

logger = structlog.get_logger()

class MediaManager:
    def __init__(self, tmp_dir: str = "tmp/media"):
        self.tmp_dir = Path(tmp_dir)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.max_size = getattr(settings, "max_media_size_mb", 50) * 1024 * 1024

    async def process_media(self, source_url: str, media_type: str) -> Tuple[Path, str]:
        file_ext = self._get_extension(media_type)
        temp_file = self.tmp_dir / f"{os.urandom(8).hex()}.{file_ext}"
        
        # 1. Streaming Download with size limit
        await self._download_file_stream(source_url, temp_file)
        
        # 2. Process based on type
        if media_type == "webp":
            processed_file = await self._convert_webp(temp_file)
            if processed_file != temp_file:
                os.remove(temp_file)
            return processed_file, "image/jpeg"
            
        if media_type in ["mp4", "webm", "gif"]:
            processed_file = await self._compress_video(temp_file)
            if processed_file != temp_file:
                os.remove(temp_file)
            return processed_file, "video/mp4"

        return temp_file, f"image/{file_ext}"

    async def _download_file_stream(self, url: str, dest: Path):
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()
                
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > self.max_size:
                    raise ValueError(f"File too large: {content_length} bytes")
                
                bytes_downloaded = 0
                with open(dest, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        bytes_downloaded += len(chunk)
                        if bytes_downloaded > self.max_size:
                            raise ValueError("File exceeded MAX_MEDIA_SIZE_MB during download")
                        f.write(chunk)

    async def _convert_webp(self, path: Path) -> Path:
        from PIL import Image
        output_path = path.with_suffix(".jpg")
        try:
            def convert():
                with Image.open(path) as img:
                    rgb_img = img.convert("RGB")
                    rgb_img.save(output_path, "JPEG", quality=85)
            
            await asyncio.to_thread(convert)
            return output_path
        except Exception as e:
            logger.error("webp_conversion_failed", error=str(e))
            return path

    async def _compress_video(self, path: Path) -> Path:
        # FIX: Always use a unique output file to avoid input/output collision
        output_path = self.tmp_dir / f"proc_{os.urandom(8).hex()}.mp4"
        
        cmd = [
            "ffmpeg", "-y", "-i", str(path),
            "-vcodec", "libx264", "-crf", "28", 
            "-preset", "faster", "-movflags", "+faststart",
            "-acodec", "aac", "-strict", "experimental",
            str(output_path)
        ]
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
            
            if process.returncode != 0:
                logger.error("ffmpeg_error", stderr=stderr.decode())
                return path # Fallback to original
                
            return output_path
        except Exception as e:
            logger.error("video_compression_failed", error=str(e))
            return path

    def _get_extension(self, media_type: str) -> str:
        mapping = {
            "png": "png", "jpeg": "jpg", "jpg": "jpg", 
            "webp": "webp", "gif": "gif", "mp4": "mp4", "webm": "webm"
        }
        return mapping.get(media_type.lower(), "bin")

    async def cleanup_file(self, path: Path):
        try:
            if path.exists():
                os.remove(path)
        except Exception as e:
            logger.error("file_cleanup_failed", path=str(path), error=str(e))
