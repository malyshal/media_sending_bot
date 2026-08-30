import asyncio
import os
import subprocess
import structlog
from typing import Optional, Tuple
from pathlib import Path
from app.core.config import settings

logger = structlog.get_logger()

class MediaManager:
    def __init__(self, tmp_dir: str = "tmp/media"):
        self.tmp_dir = Path(tmp_dir)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    async def process_media(self, source_url: str, media_type: str) -> Tuple[Path, str]:
        """
        Downloads media, processes it (converts/compresses), and returns the local path.
        """
        file_ext = self._get_extension(media_type)
        temp_file = self.tmp_dir / f"{os.urandom(8).hex()}.{file_ext}"
        
        # 1. Download
        await self._download_file(source_url, temp_file)
        
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

    async def _download_file(self, url: str, dest: Path):
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            with open(dest, "wb") as f:
                f.write(resp.content)

    async def _convert_webp(self, path: Path) -> Path:
        from PIL import Image
        output_path = path.with_suffix(".jpg")
        try:
            # Run in thread because Pillow is blocking
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
        output_path = path.with_suffix(".mp4")
        # FFmpeg command: convert to h264, faststart for web, moderate bitrate
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
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
            
            if process.returncode != 0:
                logger.error("ffmpeg_error", stderr=stderr.decode())
                return path
                
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
