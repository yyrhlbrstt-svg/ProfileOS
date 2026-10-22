"""Letting a phone in, without letting the phone become a way in.

The installation is locked to a USB key and the machines it was enrolled on.
A phone cannot hold that key, and putting a copy on one would undo the whole
arrangement — a phone is lost, lent and repaired far more often than an office
computer, and the point of the key was that possession of it is deliberate.

So a phone is never an installation. It is a **client of an unlocked machine**:

1. Somebody sits at the office computer, which is already open because the USB
   key is in it, and asks for a pairing code.
2. The code is six digits, lives for five minutes, and works once.
3. The phone enters it and receives a device token. The token is a random
   secret; only its hash is kept, the same way a password is.
4. The office computer holds the list of paired devices and can revoke any of
   them without touching the others.

The consequences follow from that shape rather than from a policy document: a
stolen phone is revoked from the office in one command; a phone alone can never
open anything, because tokens are only ever issued by a machine that was open
at the time; and when the USB key is out, no new phone can be paired.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("mobile.pairing")

#: How long a pairing code is good for. Long enough to walk across the shop,
#: short enough that a code read over somebody's shoulder is worthless by the
#: time they act on it.
CODE_LIFETIME = timedelta(minutes=5)
#: How long a paired device stays paired without being seen.
DEVICE_LIFETIME = timedelta(days=90)
#: Rounds for the token hash. Tokens are 32 random bytes, so this is not
#: guarding against a dictionary attack; it is here so a leaked store is not
#: instantly a set of working tokens.
_ITERATIONS = 200_000


class PairingError(ProfileOSError):
    """The phone cannot be paired, and the message says why."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, _ITERATIONS).hex()


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


@dataclass
class Device:
    """One paired phone or tablet."""

    device_id: str
    name: str
    salt: str
    token_hash: str
    paired_at: datetime
    last_seen: datetime
    #: What this device may do. A phone in a fitter's pocket does not need to
    #: post journal entries, and not giving it the ability is cheaper than
    #: auditing whether it did.
    scopes: tuple[str, ...] = ("jobs", "measure", "drawings")
    revoked: bool = False
    note: str = ""

    @property
    def expired(self) -> bool:
        return _now() - self.last_seen > DEVICE_LIFETIME

    @property
    def active(self) -> bool:
        return not self.revoked and not self.expired

    def may(self, scope: str) -> bool:
        return self.active and scope in self.scopes

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "salt": self.salt,
            "token_hash": self.token_hash,
            "paired_at": self.paired_at.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "scopes": list(self.scopes),
            "revoked": self.revoked,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Device":
        return cls(
            device_id=str(data["device_id"]),
            name=str(data.get("name", "")),
            salt=str(data["salt"]),
            token_hash=str(data["token_hash"]),
            paired_at=datetime.fromisoformat(data["paired_at"]),
            last_seen=datetime.fromisoformat(data["last_seen"]),
            scopes=tuple(data.get("scopes", ())),
            revoked=bool(data.get("revoked", False)),
            note=str(data.get("note", "")),
        )


@dataclass
class PairingCode:
    """A short-lived, single-use code shown on the unlocked machine."""

    code: str
    name: str
    scopes: tuple[str, ...]
    expires_at: datetime
    used: bool = False

    @property
    def valid(self) -> bool:
        return not self.used and _now() < self.expires_at

    @property
    def seconds_left(self) -> int:
        return max(0, int((self.expires_at - _now()).total_seconds()))

    def to_dict(self) -> dict[str, Any]:
        # The code itself is not written down, only its hash: the file is
        # read by whichever process serves the phone, not shown to anybody.
        return {
            "hash": _hash_code(self.code), "name": self.name, "scopes": list(self.scopes),
            "expires_at": self.expires_at.isoformat(), "used": self.used,
        }


@dataclass
class DeviceRegistry:
    """The paired devices, kept beside the credential store.

    Held in a plain file rather than inside the sealed store: the office
    machine has to be able to answer "is this token valid" on every request,
    and unsealing the operator record for each one would mean either keeping
    the USB secret in memory for the life of the server or asking for the key
    on every tap.
    """

    path: Path
    devices: dict[str, Device] = field(default_factory=dict)
    codes: dict[str, PairingCode] = field(default_factory=dict)
    #: Codes issued by another process (the desktop window, the command
    #: line) and read from the file: ``hash -> PairingCode`` without the code.
    issued_elsewhere: dict[str, PairingCode] = field(default_factory=dict)
    _seen_mtime: float = 0.0

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "DeviceRegistry":
        target = Path(path)
        registry = cls(path=target)
        from ..core.storage import stored as _stored

        if not _stored(target):
            return registry
        from ..core.storage import read_json

        registry._read()
        return registry

    def _from_disk(self) -> tuple[dict[str, Device], dict[str, PairingCode]]:
        """The devices and the issued codes (by hash) as the file has them."""
        from ..core.storage import read_json

        try:
            data = read_json(self.path, default={}) or {}
        except Exception as exc:  # noqa: BLE001
            raise PairingError(f"רשימת המכשירים המחוברים פגומה: {exc}") from exc
        devices: dict[str, Device] = {}
        for entry in data.get("devices", []):
            device = Device.from_dict(entry)
            devices[device.device_id] = device
        codes: dict[str, PairingCode] = {}
        for entry in data.get("codes", []):
            try:
                codes[str(entry.get("hash", ""))] = PairingCode(
                    code="", name=str(entry["name"]), scopes=tuple(entry.get("scopes", ())),
                    expires_at=datetime.fromisoformat(entry["expires_at"]),
                    used=bool(entry.get("used", False)),
                )
            except (KeyError, ValueError):
                continue
        return devices, codes

    def _read(self) -> None:
        """Take the devices and the issued codes from the file."""
        mtime = self.path.stat().st_mtime if self.path.exists() else 0.0
        self.devices, self.issued_elsewhere = self._from_disk()
        self._seen_mtime = mtime

    def _refresh(self) -> None:
        """Re-read the file when another process changed it — a code issued
        at the desktop, a phone revoked there — keeping this copy's own codes."""
        try:
            mtime = self.path.stat().st_mtime if self.path.exists() else 0.0
        except OSError:
            return
        if mtime != self._seen_mtime:
            self._read()

    def save(self) -> None:
        """Write this copy's changes onto the file as it is now.

        The desk revokes a phone while the server, a moment earlier, read the
        file to answer another phone: writing the server's whole list would
        put the revoked phone back. So the file is re-read here, under the
        lock, and merged device by device — revoked stays revoked, the latest
        "last seen" wins, a device forgotten here stays forgotten.
        """
        from ..core.storage import path_lock, write_json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        forgotten = getattr(self, "_forgotten", set())
        with path_lock(self.path):
            disk_devices, disk_codes = self._from_disk()
            merged: dict[str, Device] = {}
            for device_id in list(disk_devices) + [d for d in self.devices if d not in disk_devices]:
                if device_id in forgotten:
                    continue
                mine, theirs = self.devices.get(device_id), disk_devices.get(device_id)
                device = mine or theirs
                if mine is not None and theirs is not None:
                    mine.revoked = mine.revoked or theirs.revoked
                    mine.last_seen = max(mine.last_seen, theirs.last_seen)
                merged[device_id] = device
            codes: dict[str, PairingCode] = dict(disk_codes)
            for digest, entry in self.issued_elsewhere.items():
                if digest in codes:
                    codes[digest].used = codes[digest].used or entry.used
                else:
                    codes[digest] = entry
            own = {_hash_code(c.code): c for c in self.codes.values()}
            for digest, entry in own.items():
                if digest in codes:
                    entry.used = entry.used or codes[digest].used
                codes[digest] = entry
            payload = {
                "version": 1,
                "devices": [d.to_dict() for d in merged.values()],
                "codes": [
                    {**entry.to_dict(), "hash": digest}
                    for digest, entry in codes.items() if entry.valid
                ],
            }
            write_json(self.path, payload)
            try:
                self.path.chmod(0o600)
            except OSError:  # pragma: no cover - Windows and odd filesystems
                pass
            self.devices = merged
            self.issued_elsewhere = {
                digest: entry for digest, entry in codes.items()
                if digest not in own and entry.valid
            }
            self._seen_mtime = self.path.stat().st_mtime

    # -- pairing ------------------------------------------------------------ #
    def issue_code(
        self, name: str, *, scopes: Iterable[str] = ("jobs", "measure", "drawings")
    ) -> PairingCode:
        """Make a code on the machine that is already open."""
        if not name.strip():
            raise PairingError("תנו למכשיר שם (למשל ״הטלפון של יוסי״) — כך אפשר לנתק אותו אחר כך")
        self._refresh()
        self._sweep()
        code = f"{secrets.randbelow(1_000_000):06d}"
        entry = PairingCode(
            code=code,
            name=name.strip(),
            scopes=tuple(scopes),
            expires_at=_now() + CODE_LIFETIME,
        )
        self.codes[code] = entry
        # Written down, so the process that serves the phone — the window's
        # own server or `profileos serve` — knows the code when it arrives.
        self.save()
        _log.info("Pairing code issued for %s, valid %ds", entry.name, entry.seconds_left)
        return entry

    def redeem(self, code: str, *, description: str = "") -> tuple[Device, str]:
        """Exchange a code for a device token. Returns the device and its token.

        The token is returned once and never stored in the clear, so a copy of
        the registry is not a set of working credentials.
        """
        self._refresh()
        self._sweep()
        entry = self.codes.get(code.strip()) or self.issued_elsewhere.get(_hash_code(code))
        if entry is None or not entry.valid:
            # Deliberately one message for wrong, expired and already-used: a
            # phone that is told which of the three it hit learns something.
            raise PairingError("הקוד לא תקף. בקשו קוד חדש במחשב במשרד.")
        entry.used = True

        token = secrets.token_urlsafe(32)
        salt = secrets.token_bytes(16)
        device = Device(
            device_id=secrets.token_hex(8),
            name=entry.name,
            salt=salt.hex(),
            token_hash=_hash_token(token, salt),
            paired_at=_now(),
            last_seen=_now(),
            scopes=entry.scopes,
            note=description.strip(),
        )
        self.devices[device.device_id] = device
        self.save()
        _log.info("Paired %s (%s)", device.name, device.device_id)
        return device, token

    def authenticate(self, device_id: str, token: str) -> Device | None:
        """The device behind a request, or ``None``.

        Compared with :func:`hmac.compare_digest` so the time taken does not
        depend on how much of the token was right.
        """
        self._refresh()
        device = self.devices.get(device_id)
        if device is None or not device.active:
            return None
        expected = _hash_token(token, bytes.fromhex(device.salt))
        if not hmac.compare_digest(expected, device.token_hash):
            return None
        # Written once a day: a phone used every day must not lapse ninety
        # days after it was paired because "last seen" lived only in memory.
        stale = _now() - device.last_seen > timedelta(days=1)
        device.last_seen = _now()
        if stale:
            try:
                self.save()
            except Exception:  # noqa: BLE001 - the request still goes through
                _log.exception("Could not record when %s was last seen", device.name)
        return device

    # -- management --------------------------------------------------------- #
    def revoke(self, device_id: str) -> Device:
        self._refresh()
        device = self.devices.get(device_id)
        if device is None:
            raise PairingError(f"אין מכשיר מחובר ⁦{device_id}⁩")
        device.revoked = True
        self.save()
        _log.info("Revoked %s (%s)", device.name, device_id)
        return device

    def revoke_all(self) -> int:
        self._refresh()
        count = 0
        for device in self.devices.values():
            if not device.revoked:
                device.revoked = True
                count += 1
        self.save()
        return count

    def forget(self, device_id: str) -> None:
        self._refresh()
        self.devices.pop(device_id, None)
        self.__dict__.setdefault("_forgotten", set()).add(device_id)
        self.save()

    def active_devices(self) -> list[Device]:
        return [d for d in self.devices.values() if d.active]

    def _sweep(self) -> None:
        """Drop codes that have expired, so the table cannot grow unbounded."""
        for code in [c for c, entry in self.codes.items() if not entry.valid]:
            self.codes.pop(code, None)
        for digest in [d for d, entry in self.issued_elsewhere.items() if not entry.valid]:
            self.issued_elsewhere.pop(digest, None)


def default_registry_path() -> Path:
    """Beside the credential store, so the two travel together."""
    from ..core.config import get_settings

    settings = get_settings()
    base = getattr(settings, "data_dir", None) or Path.home() / ".config" / "ProfileOS"
    return Path(base) / "paired-devices.json"


__all__ = [
    "CODE_LIFETIME",
    "DEVICE_LIFETIME",
    "Device",
    "DeviceRegistry",
    "PairingCode",
    "PairingError",
    "default_registry_path",
]
