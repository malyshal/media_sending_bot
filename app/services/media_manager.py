import asyncio
import os
import structlog
from typing import Optional, Tuple
from pathlib import Path
from app.core.config import settings

logger = structlog.get_logger()

class MediaManager:
    def __init__(self, tmp_dir: str = "tmp/media"):
        self.tmp_dir = Path(tmp_dir)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.max_size = getattr(settings, "max_media_size_mb", 50) * 1024 * 1024

    async def process_media(self, source_url: str, media_type: str) -> Tuple[Path, str]:
        file_ext = self._get_extension(media_type)
        temp_file = self.tmp_dir / f"src_{os.urandom(8).hex()}.{file_ext}"
        
        # 1. Streaming Download with size limit
        await self._download_file_stream(source_url, temp_file)
        
        # 2. Process based on type
        if media_type == "webp":
            processed_file = await self._convert_webp(temp_file)
            if processed_file != temp_file:
                os.remove(temp_file)
            return processed_file, "image/jpeg"
            
        if media_type in ["mp4", "webm", "gif", "video"]:
            processed_file, final_mime = await self._compress_video(temp_file)
            if processed_file != temp_file:
                os.remove(temp_file)
            return processed_file, final_mime

        return temp_file, f"image/{file_ext}"

    async def _download_file_stream(self, url: str, dest: Path):
        # httpx connect from the long-lived polling loop intermittently fails
        # TLS handshake with the image CDN (ConnectTimeout), while a blocking
        # stdlib download in a worker thread is reliable — so use it.
        await asyncio.to_thread(self._download_file_sync, url, dest)

    # host -> last known working IP (some CDN IPs silently drop TLS handshakes)
    _good_ip: dict = {}

    def _download_file_sync(self, url: str, dest: Path):
        import urllib.request
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname
        ip_override = self._good_ip.get(host)

        last_err: Exception | None = None
        candidates = [ip_override] if ip_override else self._resolve_ips(host)
        for ip in candidates:
            try:
                self._fetch_via_ip(url, ip, host, dest)
                self._good_ip[host] = ip
                return
            except Exception as e:
                logger.warning("media_download_ip_failed", host=host, ip=ip, error=str(e))
                last_err = e
        if last_err:
            raise last_err
        raise ConnectionError(f"DNS resolution failed for {host}")

    @staticmethod
    def _resolve_ips(host: str) -> list:
        import socket
        try:
            infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            seen, ips = set(), []
            for ai in infos:
                ip = ai[4][0]
                if ip not in seen:
                    seen.add(ip)
                    ips.append(ip)
            return ips
        except socket.gaierror as e:
            raise ConnectionError(f"DNS failure for {host}: {e}") from e

    def _fetch_via_ip(self, url: str, ip: str, host: str, dest: Path):
        import socket
        import ssl
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        # Connect to the chosen IP but do TLS handshake with the real hostname
        raw = socket.create_connection((ip, 443), timeout=30)
        try:
            tls = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        except Exception:
            raw.close()
            raise
        try:
            tls.sendall(
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
                f"Connection: close\r\n\r\n".encode()
            )
            fobj = tls.makefile("rb")
            status_line = fobj.readline().decode("latin-1").strip()
            parts = status_line.split(" ", 2)
            status = int(parts[1]) if len(parts) >= 2 else 0
            headers = {}
            while True:
                line = fobj.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break
                k, _, v = line.decode("latin-1").partition(":")
                headers[k.strip().lower()] = v.strip()
            if status in (301, 302, 303, 307, 308):
                raise ConnectionError(f"Redirect not supported: {headers.get('location')}")
            if status != 200:
                raise ConnectionError(f"HTTP {status}")
            length = headers.get("content-length")
            if length and int(length) > self.max_size:
                raise ValueError(f"File too large: {length} bytes")
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = fobj.read(64 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > self.max_size:
                        f.close()
                        if dest.exists():
                            os.remove(dest)
                        raise ValueError("File exceeded MAX_MEDIA_SIZE_MB during download")
                    f.write(chunk)
        finally:
            try:
                tls.close()
            except Exception:
                pass

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

    async def _compress_video(self, path: Path) -> Tuple[Path, str]:
        # FIX: Always unique output path
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
                return path, f"video/{path.suffix[1:]}" # Fallback to original MIME
                
            return output_path, "video/mp4"
        except Exception as e:
            logger.error("video_compression_failed", error=str(e))
            return path, f"video/{path.suffix[1:]}"

    def _get_extension(self, media_type: str) -> str:
        mapping = {
            "png": "png", "jpeg": "jpg", "jpg": "jpg", "photo": "jpg",
            "webp": "webp", "gif": "gif", "mp4": "mp4", "webm": "webm",
            "video": "webm"
        }
        return mapping.get(media_type.lower(), "bin")

    async def cleanup_file(self, path: Path):
        try:
            if path.exists():
                os.remove(path)
        except Exception as e:
            logger.error("file_cleanup_failed", path=str(path), error=str(e))
