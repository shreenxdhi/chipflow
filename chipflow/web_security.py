"""Loopback browser boundary and bounded JSON requests."""

from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import JSONResponse

MAX_REQUEST_BYTES = 1024 * 1024
HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}


class LocalBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    *((key.encode(), value.encode()) for key, value in HEADERS.items()),
                ]
            await send(message)

        async def reject(detail, status):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, secured_send)

        request = Request(scope)
        try:
            if request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                return await reject("Loopback host required", 403)
            origin = request.headers.get("origin")
            if origin and (
                urlparse(origin).netloc != request.url.netloc
                or urlparse(origin).scheme != request.url.scheme
            ):
                return await reject("Same-origin requests required", 403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return await reject("Cross-site requests rejected", 403)
        except ValueError:
            return await reject("Invalid request host or origin", 400)
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                    return await reject("Request exceeds the 1 MiB configuration limit", 413)
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            return await self.app(scope, replay, secured_send)
        return await self.app(scope, receive, secured_send)
