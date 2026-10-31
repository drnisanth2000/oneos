"""Authentication precedes route matching, including framework error handlers."""
import base64
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from .auth import AuthStore, AuthUnavailable

COOKIE = "__Host-oneos"
LOGIN_COOKIE = "__Host-oneos-login"
# When a kept session's cookie was last issued (Unix seconds). It only schedules
# renewal; whether a session is kept comes from the database, never from a cookie.
ISSUED_COOKIE = "__Host-oneos-issued"
UNKEPT_COOKIE_AGE = 43200
KEPT_COOKIE_AGE = 34560000
RENEW_AFTER_SECONDS = 86400
_COOKIE_FLAGS = dict(secure=True, httponly=True, samesite="strict", path="/")
LIMIT = 16384
# Lifecycle proposal bytes may reach 4 MiB. Their reviewed fields add JSON
# escaping and form percent-encoding; leave room for both and the form envelope.
AUTHENTICATED_LIMIT = 32 * 1024 * 1024
# The only paths answered without a session: the sign-in steps themselves.
PRE_SIGN_IN = frozenset({"/login", "/login/passkey/options", "/login/passkey"})
# Signed-in passkey setup and removal. Their bodies are small, so they keep the
# pre-sign-in size cap rather than the lifecycle-review one.
PASSKEY_SETTINGS = frozenset({"/settings/passkey/begin", "/settings/passkey/finish", "/settings/passkey/remove"})
FORM = "application/x-www-form-urlencoded"
JSON_TYPE = "application/json"
CHECK_MESSAGE = "Password or code not accepted. If you just used this code to sign in, wait for the next one."
BLOCKED_MESSAGE = "Too many failed sign-ins. Wait a few minutes and try again."
DEFAULT_CSP = "frame-ancestors 'none'"


def _set_session_cookie(response, token, *, kept):
    """The one rule for the session cookie, shared by every sign-in path and renewal."""
    if kept:
        response.set_cookie(COOKIE, token, max_age=KEPT_COOKIE_AGE, **_COOKIE_FLAGS)
        response.set_cookie(ISSUED_COOKIE, str(int(time.time())), max_age=KEPT_COOKIE_AGE, **_COOKIE_FLAGS)
    else:
        response.set_cookie(COOKIE, token, max_age=UNKEPT_COOKIE_AGE, **_COOKIE_FLAGS)
        response.delete_cookie(ISSUED_COOKIE, secure=True, httponly=True, samesite="strict")


def _clear_session_cookies(response):
    for name in (COOKIE, ISSUED_COOKIE):
        response.delete_cookie(name, secure=True, httponly=True, samesite="strict")


def _media_type(request):
    """The content type without parameters, compared exactly (as the form rule always has)."""
    return request.headers.get("content-type", "").split(";")[0]


def _refuse_constant(name):
    raise ValueError(f"{name} is not JSON")


def _json_object(request, body):
    """The (already size-capped) body as a JSON object, or None.

    None for a wrong content type, bytes that are not UTF-8 JSON, NaN or
    Infinity, nesting too deep to parse or to write back out, or any value
    that is not an object. A parse failure is the caller's 400, never an
    exception (and never `AuthUnavailable`).
    """
    if _media_type(request) != JSON_TYPE:
        return None
    try:
        value = json.loads(body.decode("utf-8"), parse_constant=_refuse_constant)
        json.dumps(value)  # callers hand the store a re-serialised copy
    except (UnicodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _single_fields(request, fields, required, allowed):
    """Form fields as single strings when the body is a form with exactly the expected names; else None."""
    names = set(fields)
    if _media_type(request) != FORM or not required <= names or not names <= required | allowed or any(len(v) != 1 for v in fields.values()):
        return None
    return {name: values[0] for name, values in fields.items()}


def _back(request, origin):
    """Where to return after a form post: the referring page on this origin only, else home."""
    try:
        parts = urlsplit(request.headers.get("referer", ""))
    except ValueError:
        return "/"
    if (f"{parts.scheme}://{parts.netloc}" != origin
            or not re.fullmatch(r"/(?!/)[^\\\x00-\x20\x7f]*", parts.path)
            or not re.fullmatch(r"[^\x00-\x20\x7f]*", parts.query)):
        return "/"
    return parts.path + ("?" + parts.query if parts.query else "")


def _invalid():
    return JSONResponse({"error": "invalid"}, 400)


# The sign-in page's only script. Without JavaScript (or without passkey
# support) the Touch ID controls stay hidden and the password form is the page.
# It writes text only (`textContent`), converts base64url itself (not every
# browser has `PublicKeyCredential.parseRequestOptionsFromJSON`), and fetches
# fresh options on every press.
_LOGIN_SCRIPT = r"""
(function () {
  'use strict';
  var touch = document.getElementById('touch-id');
  if (!touch || !window.PublicKeyCredential || !navigator.credentials || !window.fetch) return;
  var form = document.getElementById('sign-in');
  var fields = document.getElementById('password-fields');
  var submit = document.getElementById('password-submit');
  var other = document.getElementById('use-password');
  var button = document.getElementById('touch-id-button');
  var status = document.getElementById('touch-id-status');
  var token = form.elements.csrf_token.value;
  var retry = ' Try again, or use password and code instead.';
  function show(passkey) {
    touch.hidden = !passkey;
    other.hidden = !passkey;
    fields.hidden = passkey;
    submit.hidden = passkey;
  }
  function say(text) { status.textContent = text; }
  function toBuffer(text) {
    if (typeof text !== 'string' || !/^[A-Za-z0-9_-]*$/.test(text)) throw new TypeError('not base64url');
    var binary = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((text.length + 3) % 4));
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }
  function toText(buffer) {
    var bytes = new Uint8Array(buffer), binary = '';
    for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function post(path, body) {
    return fetch(path, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
      body: JSON.stringify(body)
    });
  }
  async function signIn() {
    button.disabled = true;
    say('');
    try {
      var response = await post('/login/passkey/options', {});
      if (response.status === 409) { show(false); return; }
      if (response.status === 429) { say('Too many failed sign-ins. Wait a few minutes, or use password and code instead.'); return; }
      if (response.status === 403) { location.reload(); return; }
      if (!response.ok) { say('Touch ID sign-in is not available right now.' + retry); return; }
      var options = await response.json();
      var publicKey = {
        challenge: toBuffer(options.challenge),
        rpId: options.rpId,
        timeout: options.timeout,
        userVerification: options.userVerification,
        allowCredentials: (options.allowCredentials || []).map(function (item) {
          return {type: item.type, id: toBuffer(item.id), transports: item.transports};
        })
      };
      var credential;
      try {
        credential = await navigator.credentials.get({publicKey: publicKey});
      } catch (error) {
        say('Touch ID was cancelled or timed out.' + retry);
        return;
      }
      if (!credential) { say('Touch ID sign-in failed.' + retry); return; }
      var answer = credential.response;
      var body = {
        id: credential.id, rawId: toText(credential.rawId), type: credential.type,
        response: {
          clientDataJSON: toText(answer.clientDataJSON),
          authenticatorData: toText(answer.authenticatorData),
          signature: toText(answer.signature)
        },
        clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {}
      };
      if (answer.userHandle) body.response.userHandle = toText(answer.userHandle);
      if (credential.authenticatorAttachment) body.authenticatorAttachment = credential.authenticatorAttachment;
      var result = await post('/login/passkey', {credential: body, keep: form.elements.keep.checked});
      if (result.ok) { location.assign('/'); return; }
      if (result.status === 403) { location.reload(); return; }
      say('Touch ID sign-in failed.' + retry);
    } catch (error) {
      say('Touch ID sign-in failed.' + retry);
    } finally {
      button.disabled = false;
    }
  }
  button.addEventListener('click', signIn);
  other.querySelector('a').addEventListener('click', function (event) {
    event.preventDefault();
    show(false);
    form.elements.password.focus();
  });
  show(true);
})();
"""
_LOGIN_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_LOGIN_SCRIPT.encode()).digest()).decode()
_LOGIN_CSP = "default-src 'none'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"


def _login_page(nonce, touch_id):
    """The sign-in page and its Content-Security-Policy. No external resources; usable without JavaScript."""
    parts = [
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Sign in · OneOS</title>'
        '<main><h1>Sign in to OneOS</h1><form id="sign-in" method="post" action="/login">'
        f'<input type="hidden" name="csrf_token" value="{nonce}">'
    ]
    if touch_id:
        parts.append('<div id="touch-id" hidden><button type="button" id="touch-id-button">Sign in with Touch ID</button>'
                     '<p id="touch-id-status" role="status" aria-live="polite"></p></div>')
    parts.append(
        '<div id="password-fields">'
        '<label>Password <input type="password" name="password" autocomplete="current-password" required maxlength="1024"></label>'
        '<label>Authenticator code <input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" required maxlength="6"></label>'
        '</div>'
        '<label><input type="checkbox" name="keep" value="1" checked> Keep me signed in on this Mac</label>'
        '<button type="submit" id="password-submit">Sign in</button>'
    )
    csp = _LOGIN_CSP
    if touch_id:
        parts.append('<p id="use-password" hidden><a href="#sign-in">Use password and code instead</a></p>')
    parts.append('</form>')
    if touch_id:
        parts.append(f'<script>{_LOGIN_SCRIPT}</script>')
        csp += f"; script-src 'sha256-{_LOGIN_SCRIPT_HASH}'"
    parts.append('</main></html>')
    return "".join(parts), csp


def _renewal_due(issued):
    """A missing, unreadable or future stamp counts as due: re-issuing is harmless."""
    try:
        age = time.time() - int(issued)
    except (TypeError, ValueError):
        return True
    return not 0 <= age <= RENEW_AFTER_SECONDS


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

        async def finish(response, csp=DEFAULT_CSP):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Security-Policy"] = csp
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
            info = await run_in_threadpool(store.session_info, token)
            csrf = info.csrf if info else None
            scope.setdefault("state", {})["csrf_token"] = csrf or ""
            scope["state"]["owner_authenticated"] = bool(csrf)
            path = request.url.path
            # For the Settings sheet: (id, created) of each stored passkey.
            # Static files never show it, so they skip the database read.
            scope["state"]["passkeys"] = (
                await run_in_threadpool(store.passkeys) if csrf and not path.startswith("/static/") else []
            )
            login = path in PRE_SIGN_IN
            if not csrf and not login:
                response = RedirectResponse("/login", 303) if request.method in ("GET", "HEAD") else PlainTextResponse("Sign in required", 401)
                response.headers["HX-Redirect"] = "/login"
                return await finish(response)
            if login and path != "/login" and request.method != "POST":
                return await finish(PlainTextResponse("Method not allowed", 405, headers={"Allow": "POST"}))
            if path == "/login" and request.method == "GET":
                nonce = secrets.token_urlsafe(32)
                touch_id = bool(await run_in_threadpool(store.passkeys))
                page, csp = _login_page(nonce, touch_id)
                response = HTMLResponse(page)
                response.set_cookie(LOGIN_COOKIE, nonce, max_age=600, secure=True, httponly=True, samesite="strict", path="/")
                return await finish(response, csp)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if request.headers.getlist("origin") != [origin]:
                    return await finish(PlainTextResponse("Invalid origin", 403))
                chunks = []
                size = 0
                limit = LIMIT if login or path in PASSKEY_SETTINGS else AUTHENTICATED_LIMIT
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        return await finish(PlainTextResponse("Request too large", 413))
                    chunks.append(chunk)
                body = b"".join(chunks)
                fields = {}
                if _media_type(request) == FORM:
                    try:
                        fields = parse_qs(body.decode("utf-8"), max_num_fields=40, keep_blank_values=True)
                    except (UnicodeError, ValueError):
                        return await finish(PlainTextResponse("Invalid form", 400))
                supplied = request.headers.get("x-csrf-token") or (fields.get("csrf_token") or [""])[0]
                expected = request.cookies.get(LOGIN_COOKIE, "") if login else csrf
                if not expected or len(expected) != 43 or not secrets.compare_digest(supplied.encode(), expected.encode()):
                    return await finish(PlainTextResponse("Invalid form token; reload and retry", 403))
                if path == "/login/passkey/options":
                    if _json_object(request, body) is None:
                        return await finish(_invalid())
                    # Ask first so a blocked throttle is never reported as "no passkey".
                    if not await run_in_threadpool(store.passkeys):
                        return await finish(JSONResponse({"error": "no-passkey"}, 409))
                    options = await run_in_threadpool(lambda: store.begin_login(origin=origin))
                    if options is None:
                        # Blocked, unless the last passkey was removed in between.
                        if not await run_in_threadpool(store.passkeys):
                            return await finish(JSONResponse({"error": "no-passkey"}, 409))
                        return await finish(JSONResponse({"error": "blocked"}, 429))
                    return await finish(Response(options, 200, media_type=JSON_TYPE))
                if path == "/login/passkey":
                    payload = _json_object(request, body)
                    if (payload is None or not set(payload) <= {"credential", "keep"}
                            or not isinstance(payload.get("credential"), dict)
                            or type(payload.get("keep", False)) is not bool):
                        return await finish(_invalid())
                    keep = payload.get("keep", False)
                    answer = json.dumps(payload["credential"])
                    session = await run_in_threadpool(lambda: store.finish_login(answer, origin=origin, keep=keep))
                    if session is None:
                        return await finish(JSONResponse({"error": "failed"}, 401))
                    response = JSONResponse({"redirect": "/"})
                    _set_session_cookie(response, session, kept=keep)
                    response.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="strict")
                    return await finish(response)
                if login:
                    if request.method != "POST" or any(len(v) != 1 for v in fields.values()):
                        return await finish(PlainTextResponse("Invalid form", 400))
                    keep = fields.get("keep") == ["1"]
                    session = await run_in_threadpool(lambda: store.login((fields.get("password") or [""])[0], (fields.get("code") or [""])[0], keep=keep))
                    if session is None:
                        return await finish(HTMLResponse('<p>Sign in failed. Check your credentials or wait before retrying.</p><a href="/login">Try again</a>', 401))
                    response = RedirectResponse("/", 303)
                    _set_session_cookie(response, session, kept=keep)
                    response.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="strict")
                    return await finish(response)
                if path == "/settings/passkey/begin" and request.method == "POST":
                    check = _single_fields(request, fields, {"password", "code"}, {"csrf_token"})
                    if check is None:
                        return await finish(_invalid())
                    options = await run_in_threadpool(lambda: store.begin_registration(check["password"], check["code"], origin=origin))
                    if options is None:
                        # Asked after the refusal (as sign-in options does): blocked
                        # now means the next try would be refused too, so say wait.
                        if await run_in_threadpool(store.blocked):
                            return await finish(JSONResponse({"error": "blocked", "message": BLOCKED_MESSAGE}, 429))
                        return await finish(JSONResponse({"error": "check", "message": CHECK_MESSAGE}, 401))
                    return await finish(Response(options, 200, media_type=JSON_TYPE))
                if path == "/settings/passkey/finish" and request.method == "POST":
                    credential = _json_object(request, body)
                    if credential is None:
                        return await finish(_invalid())
                    answer = json.dumps(credential)
                    if not await run_in_threadpool(lambda: store.finish_registration(answer, origin=origin)):
                        return await finish(JSONResponse({"error": "failed"}, 400))
                    return await finish(JSONResponse({"ok": True}))
                if path == "/settings/passkey/remove" and request.method == "POST":
                    chosen = _single_fields(request, fields, {"id"}, {"csrf_token"})
                    if chosen is None:
                        return await finish(_invalid())
                    await run_in_threadpool(store.remove_passkey, chosen["id"])
                    target = _back(request, origin)
                    if request.headers.get("hx-request") == "true":
                        response = Response(status_code=204, headers={"HX-Redirect": target})
                    else:
                        response = RedirectResponse(target, 303)
                    return await finish(response)
                if request.url.path == "/logout" and request.method == "POST":
                    await run_in_threadpool(store.logout, token)
                    response = RedirectResponse("/login", 303)
                    response.headers["HX-Redirect"] = "/login"
                    _clear_session_cookies(response)
                    return await finish(response)
                if request.url.path == "/logout/everywhere" and request.method == "POST":
                    await run_in_threadpool(store.logout_everywhere)
                    response = RedirectResponse("/login", 303)
                    response.headers["HX-Redirect"] = "/login"
                    _clear_session_cookies(response)
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
            renewal = []
            if info and info.kept and _renewal_due(request.cookies.get(ISSUED_COOKIE)):
                # Re-issue the cookie the client just presented (already validated as
                # a kept session in the database) so its 400 days restart.
                renewed = Response()
                _set_session_cookie(renewed, token, kept=True)
                renewal = [(k, v) for k, v in renewed.raw_headers if k == b"set-cookie"]
            async def secured_send(message):
                if message["type"] == "http.response.start":
                    message["headers"] = list(message["headers"]) + renewal + [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"), (b"content-security-policy", b"frame-ancestors 'none'")]
                await send(message)
            return await self.app(scope, downstream_receive, secured_send)
        except AuthUnavailable:
            return await finish(PlainTextResponse("Owner authentication unavailable. Run local setup or recovery.", 503))
