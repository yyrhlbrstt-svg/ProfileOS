"""The phone's way in, served by the office window itself.

Until now a phone needed somebody to run ``profileos serve`` in a terminal —
which nobody in a shop does. The desktop window can serve the phone pages
on its own, in the background, for as long as it is open: a switch on the
System page, remembered for the next start.

Only the phone routes are served here, never the engine API: everything
under ``/m`` needs a device token, and a token is only issued for a code
shown on this unlocked screen.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("mobile.host")

#: Away from 8000, which ``profileos serve`` and every other development
#: server in the world defaults to.
DEFAULT_PORT = 8765


def lan_addresses() -> list[str]:
    """The addresses a phone on the shop's own network can actually reach.

    ``localhost`` is useless to a phone, and the hostname often is too, so the
    address is worked out rather than guessed — by asking the routing table
    which interface would be used to reach the outside, without sending
    anything.
    """
    found: list[str] = []
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # a documentation address; nothing is sent
        address = probe.getsockname()[0]
        # A container that routes the probe address to itself hands back an
        # address from the documentation block, which no phone can reach.
        if not address.startswith(("127.", "192.0.2.")):
            found.append(address)
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith(("127.", "192.0.2.")) and address not in found:
                found.append(address)
    except OSError:
        pass
    return found


def phone_app() -> Any:
    """A web app with the phone routes and nothing else."""
    from fastapi import FastAPI
    from fastapi.responses import RedirectResponse

    from .app import build_router

    app = FastAPI(title="ProfileOS — טלפון", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_router())

    @app.get("/", include_in_schema=False)
    def root() -> Any:
        return RedirectResponse("/m")

    return app


class PhoneHost:
    """The phone server, running on a background thread of this program."""

    def __init__(self) -> None:
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self.port = 0
        self.problem = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._server is not None

    def urls(self) -> list[str]:
        if not self.running:
            return []
        addresses = lan_addresses() or ["127.0.0.1"]
        return [f"http://{address}:{self.port}/m" for address in addresses]

    def start(self, port: int = DEFAULT_PORT, *, host: str = "0.0.0.0") -> list[str]:
        """Start serving; the addresses to open on the phone. Raises in Hebrew
        when the port is taken or the web parts are not installed."""
        if self.running:
            return self.urls()
        try:
            import uvicorn
        except ImportError as exc:
            raise ProfileOSError("רכיב השרת לטלפון לא מותקן (uvicorn)") from exc
        # Asked first, so "the port is taken" is said here, in words, rather
        # than lost in a background thread's log.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                check.bind((host, port))
            except OSError as exc:
                raise ProfileOSError(
                    f"הפתחה ⁦{port}⁩ במחשב הזה תפוסה — אולי ProfileOS כבר פתוח "
                    "במחשב הזה, או תוכנה אחרת משתמשת בה"
                ) from exc
        config = uvicorn.Config(phone_app(), host=host, port=port, log_level="warning",
                                access_log=False)
        server = uvicorn.Server(config)
        self.problem = ""

        def serve() -> None:
            try:
                server.run()
            except BaseException as exc:  # noqa: BLE001 - kept for the screen
                self.problem = str(exc)
                _log.exception("Phone server stopped")

        self._server = server
        self.port = port
        self._thread = threading.Thread(target=serve, name="phone-server", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not server.started and self._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            self.stop()
            raise ProfileOSError(
                "השרת לטלפון לא עלה" + (f": {self.problem}" if self.problem else "")
            )
        _log.info("Phone server on port %d", port)
        return self.urls()

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)


#: The one host of this program.
HOST = PhoneHost()


__all__ = ["DEFAULT_PORT", "HOST", "PhoneHost", "lan_addresses", "phone_app"]
