"""Authentication precedes route matching, including framework error handlers."""
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from .auth import AuthStore, AuthUnavailable

COOKIE = "__Host-oneos"
LOGIN_COOKIE = "__Host-oneos-login"
LIMIT = 16384
# Lifecycle proposal bytes may reach 4 MiB. Their reviewed fields add JSON
# escaping and form percent-encoding; leave room for both and the form envelope.
AUTHENTICATED_LIMIT = 32 * 1024 * 1024


class AuthenticatedFormRoute(APIRoute):
    """Align the framework form parser with the already authenticated body cap."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def authenticated_form(request):
            if (getattr(request.state, "owner_authenticated", False)
                    and request.method not in ("GET", "HEAD", "OPTIONS")
                    and request.headers.get("content-type", "").split(";")[0]
                    == "application/x-www-form-urlencoded"):
                # FastAPI reuses this request's cached FormData for Form(...).
                # Login never reaches routing; development/anonymous requests
                # retain the framework defaults.
                await request.form(max_fields=40, max_part_size=AUTHENTICATED_LIMIT)
            return await handler(request)

        return authenticated_form


class OwnerAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        if request.url.path == "/healthz" and request.method == "GET":
            return await self.app(scope, receive, send)
        mode = os.getenv("ONEOS_AUTH_MODE", "required")
        if mode == "disabled" and os.getenv("ONEOS_DEPLOYMENT") != "local":
            return await self.app(scope, receive, send)

        async def finish(response):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            await response(scope, receive, send)

        try:
            raw = os.getenv("ONEOS_AUTH_STATE_DIR")
            origin = os.getenv("ONEOS_ORIGIN", "https://localhost:8443")
            try:
                parsed = urlsplit(origin)
            except ValueError as exc:
                raise AuthUnavailable() from exc
            if mode != "required" or not raw or parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise AuthUnavailable()
            store = AuthStore(Path(raw))
            await run_in_threadpool(store.available)
            hosts = request.headers.getlist("host")
            if hosts != [parsed.netloc]:
                return await finish(PlainTextResponse("Invalid host", 400))
            if request.headers.get("origin") is not None and request.headers.getlist("origin") != [origin]:
                return await finish(PlainTextResponse("Invalid origin", 403))
            token = request.cookies.get(COOKIE)
            csrf = await run_in_threadpool(store.session, token)
            scope.setdefault("state", {})["csrf_token"] = csrf or ""
            scope["state"]["owner_authenticated"] = bool(csrf)
            login = request.url.path == "/login"
            if not csrf and not login:
                response = RedirectResponse("/login", 303) if request.method in ("GET", "HEAD") else PlainTextResponse("Sign in required", 401)
                response.headers["HX-Redirect"] = "/login"
                return await finish(response)
            if login and request.method == "GET":
                nonce = secrets.token_urlsafe(32)
                # Login has no external resources and is usable without JavaScript.
                response = HTMLResponse(
                    '<!doctype html><html lang="en"><meta charset="utf-8"><title>Sign in · OneOS</title>'
                    '<main><h1>Sign in to OneOS</h1><form method="post" action="/login">'
                    f'<input type="hidden" name="csrf_token" value="{nonce}">'
                    '<label>Password <input type="password" name="password" autocomplete="current-password" required maxlength="1024"></label>'
                    '<label>Authenticator code <input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" required maxlength="6"></label>'
                    '<button type="submit">Sign in</button></form></main></html>'
                )
                response.set_cookie(LOGIN_COOKIE, nonce, max_age=600, secure=True, httponly=True, samesite="strict", path="/")
                return await finish(response)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if request.headers.getlist("origin") != [origin]:
                    return await finish(PlainTextResponse("Invalid origin", 403))
                chunks = []
                size = 0
                limit = LIMIT if login else AUTHENTICATED_LIMIT
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        return await finish(PlainTextResponse("Request too large", 413))
                    chunks.append(chunk)
                body = b"".join(chunks)
                fields = {}
                if request.headers.get("content-type", "").split(";")[0] == "application/x-www-form-urlencoded":
                    try:
                        fields = parse_qs(body.decode("utf-8"), max_num_fields=40, keep_blank_values=True)
                    except (UnicodeError, ValueError):
                        return await finish(PlainTextResponse("Invalid form", 400))
                supplied = request.headers.get("x-csrf-token") or (fields.get("csrf_token") or [""])[0]
                expected = request.cookies.get(LOGIN_COOKIE, "") if login else csrf
                if not expected or len(expected) != 43 or not secrets.compare_digest(supplied.encode(), expected.encode()):
                    return await finish(PlainTextResponse("Invalid form token; reload and retry", 403))
                if login:
                    if request.method != "POST" or any(len(v) != 1 for v in fields.values()):
                        return await finish(PlainTextResponse("Invalid form", 400))
                    session = await run_in_threadpool(store.login, (fields.get("password") or [""])[0], (fields.get("code") or [""])[0])
                    if session is None:
                        return await finish(HTMLResponse('<p>Sign in failed. Check your credentials or wait before retrying.</p><a href="/login">Try again</a>', 401))
                    response = RedirectResponse("/", 303)
                    response.set_cookie(COOKIE, session, max_age=43200, secure=True, httponly=True, samesite="strict", path="/")
                    response.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="strict")
                    return await finish(response)
                if request.url.path == "/logout" and request.method == "POST":
                    await run_in_threadpool(store.logout, token)
                    response = RedirectResponse("/login", 303)
                    response.headers["HX-Redirect"] = "/login"
                    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict")
                    return await finish(response)
                sent = False
                async def replay():
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await receive()
                downstream_receive = replay
            else:
                downstream_receive = receive
            async def secured_send(message):
                if message["type"] == "http.response.start":
                    message["headers"] = list(message["headers"]) + [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"), (b"content-security-policy", b"frame-ancestors 'none'")]
                await send(message)
            return await self.app(scope, downstream_receive, secured_send)
        except AuthUnavailable:
            return await finish(PlainTextResponse("Owner authentication unavailable. Run local setup or recovery.", 503))
