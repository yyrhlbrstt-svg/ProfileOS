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

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "DeviceRegistry":
        target = Path(path)
        registry = cls(path=target)
        if not target.is_file():
            return registry
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PairingError(f"The paired-device list is damaged: {exc}") from exc
        for entry in data.get("devices", []):
            device = Device.from_dict(entry)
            registry.devices[device.device_id] = device
        return registry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "devices": [d.to_dict() for d in self.devices.values()]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:  # pragma: no cover - Windows and odd filesystems
            pass

    # -- pairing ------------------------------------------------------------ #
    def issue_code(
        self, name: str, *, scopes: Iterable[str] = ("jobs", "measure", "drawings")
    ) -> PairingCode:
        """Make a code on the machine that is already open."""
        if not name.strip():
            raise PairingError("A device needs a name, so it can be revoked by name later")
        self._sweep()
        code = f"{secrets.randbelow(1_000_000):06d}"
        entry = PairingCode(
            code=code,
            name=name.strip(),
            scopes=tuple(scopes),
            expires_at=_now() + CODE_LIFETIME,
        )
        self.codes[code] = entry
        _log.info("Pairing code issued for %s, valid %ds", entry.name, entry.seconds_left)
        return entry

    def redeem(self, code: str, *, description: str = "") -> tuple[Device, str]:
        """Exchange a code for a device token. Returns the device and its token.

        The token is returned once and never stored in the clear, so a copy of
        the registry is not a set of working credentials.
        """
        self._sweep()
        entry = self.codes.get(code.strip())
        if entry is None or not entry.valid:
            # Deliberately one message for wrong, expired and already-used: a
            # phone that is told which of the three it hit learns something.
            raise PairingError("That pairing code is not valid. Ask for a new one.")
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
        device = self.devices.get(device_id)
        if device is None or not device.active:
            return None
        expected = _hash_token(token, bytes.fromhex(device.salt))
        if not hmac.compare_digest(expected, device.token_hash):
            return None
        device.last_seen = _now()
        return device

    # -- management --------------------------------------------------------- #
    def revoke(self, device_id: str) -> Device:
        device = self.devices.get(device_id)
        if device is None:
            raise PairingError(f"No paired device {device_id!r}")
        device.revoked = True
        self.save()
        _log.info("Revoked %s (%s)", device.name, device_id)
        return device

    def revoke_all(self) -> int:
        count = 0
        for device in self.devices.values():
            if not device.revoked:
                device.revoked = True
                count += 1
        self.save()
        return count

    def forget(self, device_id: str) -> None:
        self.devices.pop(device_id, None)
        self.save()

    def active_devices(self) -> list[Device]:
        return [d for d in self.devices.values() if d.active]

    def _sweep(self) -> None:
        """Drop codes that have expired, so the table cannot grow unbounded."""
        for code in [c for c, entry in self.codes.items() if not entry.valid]:
            self.codes.pop(code, None)


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
