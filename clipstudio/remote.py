"""Phone remote: control live clipping from a phone on the same Wi-Fi.

Off by default and off again every time MasterClip starts. When the buyer turns it on:
  * a SECOND small web server starts on this PC's home-network address only (never 0.0.0.0,
    never the internet). The main app keeps listening on 127.0.0.1 only;
  * that server has just a handful of routes: the phone page, sign in with a 6-digit PIN, live
    status, start / Clip now / Stop, and downloading finished clips. Nothing else of the app is
    reachable from the phone;
  * 5 wrong PINs lock the remote for 15 minutes (a new PIN on the PC unlocks it at once);
  * turning it off or making a new PIN signs every phone out.
"""

from __future__ import annotations

import hmac
import ipaddress
import secrets
import socket
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

MAX_TRIES = 5
LOCK_SECONDS = 15 * 60
SESSION_SECONDS = 12 * 3600
COOKIE = "mc_remote"
DEFAULT_PORT = 8790


class PinGate:
    """6-digit PIN, lockout after MAX_TRIES wrong ones, session tokens. Clock is injectable."""

    def __init__(self, now=time.time, pin: str | None = None):
        self.now = now
        self.lock = threading.Lock()
        self.new_pin(pin)

    def new_pin(self, pin: str | None = None) -> str:
        with getattr(self, "lock", threading.Lock()):
            self.pin = pin or f"{secrets.randbelow(1_000_000):06d}"
            self.wrong = 0
            self.locked_until = 0.0
            self.sessions: dict[str, float] = {}
            return self.pin

    def locked(self) -> bool:
        return self.now() < self.locked_until

    def try_pin(self, pin: str) -> tuple[str, str | None]:
        """('ok', token) | ('wrong', None) | ('locked', None)."""
        with self.lock:
            now = self.now()
            if now < self.locked_until:
                return "locked", None
            if self.locked_until and now >= self.locked_until:
                self.locked_until, self.wrong = 0.0, 0  # lock over: a fresh set of tries
            if hmac.compare_digest(str(pin or "").strip().encode()[:12], self.pin.encode()):
                self.wrong = 0
                token = secrets.token_urlsafe(32)
                self.sessions = {t: exp for t, exp in self.sessions.items() if exp > now}
                self.sessions[token] = now + SESSION_SECONDS
                return "ok", token
            self.wrong += 1
            if self.wrong >= MAX_TRIES:
                self.locked_until = now + LOCK_SECONDS
                return "locked", None
            return "wrong", None

    def tries_left(self) -> int:
        return max(0, MAX_TRIES - self.wrong)

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self.lock:
            exp = self.sessions.get(token)
            return bool(exp and exp > self.now())

    def sign_out_all(self):
        with self.lock:
            self.sessions = {}


def lan_address() -> str | None:
    """This PC's address on the home network (192.168.x.x / 10.x.x.x), or None.
    A UDP 'connect' only picks the route; no packet is sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    return ip if addr.is_private and not addr.is_loopback and not addr.is_link_local else None


class Pin(BaseModel):
    pin: str = Field(default="", max_length=12)


class Start(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    length: int = Field(default=30, ge=10, le=90)


def build_app(gate: PinGate, actions, allowed_hosts: list[str], page: Path):
    """The phone's tiny FastAPI app. `actions` has state(), start(url, length), clip_now(),
    stop(), clip_file(job, index) -> Path."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def signed_in(request: Request):
        if not gate.valid(request.cookies.get(COOKIE)):
            raise HTTPException(401, "Type the PIN shown on your PC.")

    def same_page(request: Request):
        # a page on another site can't add this header, so it can't press buttons for you
        if request.headers.get("x-masterclip") != "1":
            raise HTTPException(403, "Open the remote page again.")


    @app.get("/", include_in_schema=False)
    @app.get("/m", include_in_schema=False)
    def phone_page():
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @app.post("/m/api/login", dependencies=[Depends(same_page)])
    def login(body: Pin, response: Response):
        result, token = gate.try_pin(body.pin)
        if result == "locked":
            raise HTTPException(429, "Too many wrong PINs. Locked for 15 minutes, or make a new PIN on your PC.")
        if result == "wrong":
            raise HTTPException(401, f"Wrong PIN. {gate.tries_left()} tries left.")
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict")
        return {"ok": True}

    def run(call, *args):
        try:
            return call(*args)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/m/api/state", dependencies=[Depends(signed_in)])
    def state():
        return run(actions.state)

    @app.post("/m/api/start", dependencies=[Depends(same_page), Depends(signed_in)])
    def start(body: Start):
        return run(actions.start, body.url.strip(), body.length)

    @app.post("/m/api/clip", dependencies=[Depends(same_page), Depends(signed_in)])
    def clip_now():
        return run(actions.clip_now)

    @app.post("/m/api/stop", dependencies=[Depends(same_page), Depends(signed_in)])
    def stop():
        return run(actions.stop)

    @app.get("/m/api/clip/{job}/{index}", dependencies=[Depends(signed_in)])
    def download(job: str, index: int):
        path = run(actions.clip_file, job, index)
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    return app


class RemoteServer:
    """Starts / stops the phone server in a background thread."""

    def __init__(self, actions, page: Path):
        self.actions = actions
        self.page = page
        self.gate = PinGate()
        self.server = None
        self.thread = None
        self.address = None
        self.port = None

    @property
    def on(self) -> bool:
        return bool(self.server and self.thread and self.thread.is_alive())

    def start(self, port: int = DEFAULT_PORT) -> dict:
        if self.on:
            return self.info()
        ip = lan_address()
        if not ip:
            raise ValueError("This PC isn't on a home Wi-Fi or network, so a phone can't reach it.")
        import uvicorn

        port = _free_port(ip, port)
        self.gate.new_pin()
        app = build_app(self.gate, self.actions, [ip], self.page)
        config = uvicorn.Config(app, host=ip, port=port, log_level="warning", access_log=False, lifespan="off")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="phone-remote", daemon=True)
        self.thread.start()
        self.address, self.port = ip, port
        for _ in range(50):
            if getattr(self.server, "started", False):
                break
            time.sleep(0.1)
        return self.info()

    def stop(self):
        if self.server:
            self.server.should_exit = True
        self.gate.sign_out_all()
        self.gate.new_pin()
        self.server = self.thread = None

    def info(self) -> dict:
        on = self.on
        return {"on": on, "url": f"http://{self.address}:{self.port}/m" if on else None,
                "pin": self.gate.pin if on else None, "locked": self.gate.locked(),
                "locked_until": self.gate.locked_until if self.gate.locked() else None}


def _free_port(ip: str, preferred: int) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 20)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((ip, port))
                return port
            except OSError:
                continue
    raise ValueError("No free network port for the phone remote. Restart MasterClip and try again.")
