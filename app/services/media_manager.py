"""Media processing: download -> (convert) -> Telegram upload -> cleanup.

Files are cached in MEDIA_DIR keyed by SHA-256 of the source URL, so:
  - a post downloaded once is never downloaded again;
  - a background prefetcher can warm the cache before the user asks;
  - in production MEDIA_DIR can be a mounted external disk (LXC).
"""
import asyncio
import hashlib
import os
import structlog
from typing import Optional, Tuple
from pathlib import Path
from app.core.config import settings

logger = structlog.get_logger()

# Retention for cached media files (matches the posts cache TTL by default,
# cleanup worker prunes files not touched within this window).
MEDIA_TTL_HOURS = 48


class MediaManager:
    def __init__(self, media_dir: str | None = None):
        self.media_dir = Path(media_dir or settings.media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir = self.media_dir / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.max_size = settings.max_media_size_mb * 1024 * 1024

    # ------------------------------------------------------------- paths

    def _raw_path(self, source_url: str) -> Path:
        h = hashlib.sha256(source_url.encode()).hexdigest()[:24]
        ext = self._get_extension_from_url(source_url)
        return self.media_dir / f"raw_{h}.{ext}"

    def _processed_path(self, source_url: str, mime: str) -> Path:
        h = hashlib.sha256((source_url + "|processed").encode()).hexdigest()[:24]
        ext = "mp4" if mime == "video/mp4" else "jpg"
        return self.media_dir / f"proc_{h}.{ext}"

    @staticmethod
    def _get_extension_from_url(url: str) -> str:
        tail = url.rsplit("/", 1)[-1]
        if "." in tail:
            return tail.rsplit(".", 1)[1].lower()[:5]
        return "bin"

    def cached_path(self, source_url: str) -> Optional[Path]:
        """Ready-to-send file if present in the media cache.
        - processed video (mp4) / converted image (jpg) wins;
        - raw image (jpeg/png) is also ready-to-send (no conversion needed);
        - raw gif/webm is NOT ready (needs ffmpeg) -> None."""
        import glob as _glob
        key = hashlib.sha256(source_url.encode()).hexdigest()[:24]
        proc_key = hashlib.sha256((source_url + "|processed").encode()).hexdigest()[:24]
        for f in (self.media_dir / f"proc_{proc_key}.mp4", self.media_dir / f"proc_{proc_key}.jpg"):
            if f.exists() and f.stat().st_size > 0:
                return f
        for f in _glob.glob(str(self.media_dir / f"raw_{key}.*")):
            fp = Path(f)
            if fp.suffix.lower() in (".jpg", ".jpeg", ".png"):
                return fp
        return None

    # ------------------------------------------------------------ public

    async def process_media(self, source_url: str, media_type: str) -> Tuple[Path, str]:
        """Download (if needed), process (if needed), return a ready file path.

        Cached by URL: repeated deliveries of the same media do not re-download.
        The caller decides when to delete the file (post-delivery cleanup only
        removes files that were downloaded in this run).
        """
        cached = self.cached_path(source_url)
        if cached:
            mime = "video/mp4" if cached.suffix == ".mp4" else "image/jpeg"
            return cached, mime

        file_ext = self._get_extension(media_type)
        temp_file = self.tmp_dir / f"src_{os.urandom(8).hex()}.{file_ext}"

        # 1. Streaming download with size limit (TS #35)
        await self._download_file_stream(source_url, temp_file)

        # 2. Process based on type (TS #12: image | video | gif)
        if media_type == "webp":
            processed_file = await self._convert_webp(temp_file)
            if processed_file != temp_file:
                await asyncio.to_thread(os.remove, temp_file)
            final = self._store_processed(processed_file, source_url, "image/jpeg")
            return final, "image/jpeg"

        if media_type in ["mp4", "webm", "gif", "video"]:
            # GIF/webm content is converted to MP4 for Telegram (TS #33)
            processed_file, final_mime = await self._compress_video(temp_file)
            if processed_file != temp_file:
                await asyncio.to_thread(os.remove, temp_file)
            final = self._store_processed(processed_file, source_url, final_mime)
            return final, final_mime

        # image (jpeg/png) — no conversion needed; move raw into cache
        final = self._raw_path(source_url)
        if final.exists():
            await asyncio.to_thread(os.remove, temp_file)
        else:
            await asyncio.to_thread(os.replace, str(temp_file), str(final))
        mime = f"image/{file_ext}" if file_ext in ("jpg", "png") else "image/jpeg"
        return final, mime

    def _store_processed(self, processed: Path, source_url: str, final_mime: str = "video/mp4") -> Path:
        """Move a processed file into its stable cache location."""
        final = self._processed_path(source_url, final_mime)
        if final.exists():
            os.remove(processed)
        else:
            os.replace(str(processed), str(final))
        return final

    # --------------------------------------------------------- download

    async def _download_file_stream(self, url: str, dest: Path):
        # httpx connect from the long-lived polling loop intermittently fails
        # TLS handshake with the image CDN (ConnectTimeout), while a blocking
        # stdlib download in a worker thread is reliable — so use it.
        await asyncio.to_thread(self._download_file_sync, url, dest)

    # host -> last known working IP (some CDN IPs silently drop TLS handshakes)
    _good_ip: dict = {}

    def _download_file_sync(self, url: str, dest: Path):
        from urllib.parse import urlparse

        # Test deployments may redirect the CDN through a local proxy
        cdn_base = getattr(settings, "img_cdn_base", None)
        if cdn_base:
            from urllib.parse import urlsplit
            sp = urlsplit(url)
            new = f"{cdn_base}{sp.path}"
            if sp.query:
                new += "?" + sp.query
            url = new

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
        from urllib.parse import quote as _quote
        path = _quote(parsed.path or "/", safe="/")
        if parsed.query:
            path += "?" + parsed.query

        # Connect to the chosen IP but do TLS handshake with the real hostname
        parsed_port = parsed.port or 443
        raw = socket.create_connection((ip, parsed_port), timeout=30)
        try:
            from app.net.https import _sni_for
            tls = ssl.create_default_context().wrap_socket(raw, server_hostname=_sni_for(host, parsed_port))
        except Exception:
            raw.close()
            raise
        try:
            real_host = _sni_for(host, parsed_port)
            tls.sendall(
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {real_host}\r\n"
                f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
                f"Referer: https://joyreactor.cc/\r\n"
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

    # ------------------------------------------------------- conversions

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
        # Always unique output path
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
                return path, f"video/{path.suffix[1:]}"  # Fallback to original MIME

            return output_path, "video/mp4"
        except Exception as e:
            logger.error("video_compression_failed", error=str(e))
            return path, f"video/{path.suffix[1:]}"

    def _get_extension(self, media_type: str) -> str:
        mapping = {
            "png": "png", "jpeg": "jpg", "jpg": "jpg", "image": "jpg",
            "webp": "webp", "gif": "gif", "mp4": "mp4", "webm": "webm",
            "video": "webm"
        }
        return mapping.get(media_type.lower(), "bin")

    # ----------------------------------------------------------- cleanup

    async def cleanup_old_media(self, ttl_hours: int = MEDIA_TTL_HOURS):
        """Remove cached media files older than the TTL (background worker)."""
        import time as _time
        cutoff = _time.time() - ttl_hours * 3600
        removed = 0
        for f in list(self.media_dir.glob("raw_*")) + list(self.media_dir.glob("proc_*")) + list(self.tmp_dir.glob("*")):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    await asyncio.to_thread(os.remove, f)
                    removed += 1
            except Exception as e:
                logger.warning("media_cleanup_file_failed", path=str(f), error=str(e))
        if removed:
            logger.info("media_files_cleaned", removed=removed)

    async def cleanup_file(self, path: Path):
        """Remove a temporary (non-cached) file: only files in tmp_dir."""
        try:
            if Path(path).exists() and self.tmp_dir in Path(path).parents:
                await asyncio.to_thread(os.remove, path)
        except Exception as e:
            logger.error("file_cleanup_failed", path=str(path), error=str(e))
