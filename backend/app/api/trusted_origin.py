from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class TrustedOriginMiddleware:
    """拒绝来源不可信的写请求，防止 CSRF（跨站请求伪造）。

    登录凭证放在 Cookie 里，浏览器会自动带上它，所以别的网站也能“借用”用户的浏览器发请求。
    浏览器发写请求时会带上 Origin（没有时通常有 Referer），这里只放行来自
    配置的前端地址、或者和后端同源（比如 /docs 页面）的请求；无法确认来源的一律拒绝。
    """

    def __init__(self, app: ASGIApp, trusted_origins: list[str]) -> None:
        self.app = app
        self.trusted_origins = set(trusted_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            headers = Headers(scope=scope)
            origin = headers.get("origin") or _origin_of(headers.get("referer"))
            own_origin = f"{scope['scheme']}://{headers.get('host')}"
            if origin is None or (origin not in self.trusted_origins and origin != own_origin):
                response = JSONResponse({"detail": "请求来源不受信任"}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _origin_of(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None
