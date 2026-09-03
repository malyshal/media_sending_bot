"""TLS-with-IP-fallback HTTP helper.

Docker Desktop VM (and some LXC networks) intermittently fail TLS handshakes
to certain JoyReactor CDN IPs. Strategy:
  resolve all IPs -> try each with a short timeout -> remember the working one.

Also supports TLS SNI overrides via settings.tls_sni_overrides
("connect-host:sni-host" pairs) for proxy-based test deployments.
"""
import socket
import ssl
import threading
from urllib.parse import urlparse

import structlog
from app.core.config import settings

logger = structlog.get_logger()

# host -> last known working IP
_good_ip: dict = {}
_lock = threading.Lock()

DEFAULT_TIMEOUT = 15.0


def _sni_for(host: str, port: int | None = None) -> str:
    """Real hostname for TLS handshake when connecting through a proxy.
    Override format: "connect-host:port:sni" (port optional)."""
    for pair in getattr(settings, "tls_sni_overrides", []) or []:
        parts = pair.split(":")
        if len(parts) == 2:
            connect_host, sni = parts
            port_match = True
        elif len(parts) == 3:
            connect_host, port_s, sni = parts
            port_match = (str(port or "") == port_s)
        else:
            continue
        if host == connect_host and port_match:
            return sni
    return host


def resolve_ips(host: str) -> list:
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


def https_request_sync(url: str, method: str = "GET", body: bytes | None = None,
                       headers: dict | None = None,
                       timeout: float = DEFAULT_TIMEOUT,
                       follow_redirects: bool = True) -> tuple[int, dict, bytes]:
    """Blocking HTTPS request with per-IP fallback. Returns (status, headers, body)."""
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    with _lock:
        cached = _good_ip.get((host, port))

    candidates = ([cached] if cached else []) + [ip for ip in resolve_ips(host) if ip != cached]
    last_err: Exception | None = None

    for ip in candidates:
        try:
            status, resp_headers, data = _request_via_ip(
                ip, port, _sni_for(host, port), path, method, body, headers, timeout
            )
            if follow_redirects and status in (301, 302, 303, 307, 308):
                loc = resp_headers.get("location") or resp_headers.get("Location")
                if loc:
                    if loc.startswith("/"):
                        loc = f"https://{host}:{port}{loc}"
                    return https_request_sync(loc, method, body, headers, timeout, follow_redirects)
            with _lock:
                _good_ip[(host, port)] = ip
            return status, resp_headers, data
        except Exception as e:
            logger.warning("https_request_ip_failed", host=host, ip=ip,
                           error=str(e) or repr(e))
            last_err = e

    if last_err:
        raise last_err
    raise ConnectionError(f"DNS resolution failed for {host}")


def _request_via_ip(ip: str, port: int, sni_host: str, path: str, method: str,
                    body: bytes | None, headers: dict | None,
                    timeout: float) -> tuple[int, dict, bytes]:
    """Connect to `ip:port` but do TLS handshake with sni_host (SNI + cert check)."""
    raw = socket.create_connection((ip, port), timeout=timeout)
    try:
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(raw, server_hostname=sni_host)
    except Exception:
        raw.close()
        raise
    try:
        hdrs = {"Host": sni_host, "Connection": "close",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        if headers:
            hdrs.update(headers)
        if body is not None:
            hdrs["Content-Length"] = str(len(body))
        req = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n"
        tls.sendall(req.encode("latin-1") + (body or b""))

        fobj = tls.makefile("rb")
        status_line = fobj.readline().decode("latin-1").strip()
        parts = status_line.split(" ", 2)
        status = int(parts[1]) if len(parts) >= 2 else 0
        resp_headers: dict = {}
        while True:
            line = fobj.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
            k, _, v = line.decode("latin-1").partition(":")
            resp_headers[k.strip().lower()] = v.strip()

        length = resp_headers.get("content-length")
        te = (resp_headers.get("transfer-encoding") or "").lower()
        if "chunked" in te:
            data = _read_chunked(fobj)
        elif length is not None:
            data = fobj.read(int(length))
        else:
            data = b""
            while True:
                chunk = fobj.read(64 * 1024)
                if not chunk:
                    break
                data += chunk
        return status, resp_headers, data
    finally:
        try:
            tls.close()
        except Exception:
            pass


def _read_chunked(fobj) -> bytes:
    data = b""
    while True:
        size_line = fobj.readline().strip()
        if not size_line:
            break
        try:
            size = int(size_line.split(b";")[0], 16)
        except ValueError:
            break
        if size == 0:
            fobj.readline()  # trailing CRLF
            break
        data += fobj.read(size)
        fobj.readline()  # CRLF after chunk
    return data
