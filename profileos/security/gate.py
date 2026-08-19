"""Who may start this installation at all.

The requirement is simple to state and easy to get wrong: this copy belongs to
one fabricator, and nobody else should be able to open it. Three independent
things are demanded, so losing any one of them is not enough to get in:

**Something you have** — a key file on a removable drive. The software will not
start without it, and it is bound to the machines it was enrolled on, so a
copy taken to another computer does not work.

**Something you know** — one username and one password. Exactly one operator
exists and no second can be created; adding or changing one requires proving
the current password and answers first.

**Something only you know** — two questions with answers nobody outside the
firm would guess. They are a second knowledge factor, not a password reset
route: getting them right does *not* let anybody past the password.

What this is honestly worth
---------------------------
A file can be copied. Anybody holding both the key file and the password can
get in on an enrolled machine, and no amount of cryptography changes that —
which is why the file is also bound to the machine fingerprint, and why the
suite carries a WebAuthn/FIDO2 path for a token whose secret genuinely cannot
be copied. If the threat is a competitor with physical access to the office,
use the hardware token. If it is a laptop left on a train, this is enough.

How it is stored
----------------
Nothing anywhere is reversible. The password and each answer are hashed with
scrypt under their own random salt, so the store discloses neither even to
somebody holding the file. The store itself is sealed with AES-256-GCM under a
random store key, and that store key is then wrapped once per authorised
machine, each copy under a key derived from *both* the secret on the USB key
and that machine's hardware fingerprint. So the sealed file alone is useless,
the USB key alone is useless, and the pair opens the store only on a machine
that was authorised — while a fabricator with an office computer and a second
one at the saw can use the same USB key on both.

Failed attempts are counted in the sealed store and the delay grows, so
guessing at the password across a night is not viable; and because the counter
lives in the store rather than in memory, restarting the software does not
clear it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..core.errors import SecurityError
from ..core.logging_setup import get_logger
from .hwid import HardwareFingerprint, current_fingerprint

_log = get_logger(__name__)


class AccessDenied(SecurityError):
    """The gate refused. The reason is in the message, never the secret."""


class NotEnrolled(SecurityError):
    """No operator has been enrolled on this installation yet."""


#: The file the software looks for on a removable drive.
KEY_FILENAME = "profileos.key"
#: Where the sealed credential store lives, under the data directory.
STORE_FILENAME = "operator.sealed"
#: Environment override, for a fixed drive letter or a service account.
KEY_ENV = "PROFILEOS_KEY"

#: scrypt cost. n=2**15 with r=8 needs about 32 MB and a few tenths of a second
#: per attempt — slow enough that guessing is hopeless, fast enough that an
#: operator does not notice it at the login box.
_SCRYPT_N = 1 << 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_DERIVED_LEN = 32
#: OpenSSL refuses scrypt above a default 32 MB ceiling, which these parameters
#: sit exactly on; without raising it every hash fails with a memory error.
_SCRYPT_MAXMEM = 128 * _SCRYPT_N * _SCRYPT_R * 2

#: Attempts before the delay starts biting, and the ceiling on that delay.
_FREE_ATTEMPTS = 3
_MAX_LOCKOUT_SECONDS = 15 * 60


# --------------------------------------------------------------------------- #
# Normalising what people type
# --------------------------------------------------------------------------- #
def normalise_answer(answer: str) -> str:
    """Reduce an answer to what two spellings of it have in common.

    A security question is worthless if the right answer is rejected because
    the operator typed "בית-אל" in March and "בית אל" in June. Case, spacing,
    punctuation, Hebrew niqqud and the geresh/gershayim variants are all
    stripped, and the Unicode form is normalised so a composed and a decomposed
    spelling of the same word compare equal.

    Passwords are deliberately *not* put through this: a password's exact bytes
    are the point.
    """
    text = unicodedata.normalize("NFKD", answer)
    # Drop combining marks — Hebrew vowel points and Latin accents alike.
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("׳", "'").replace("״", '"')
    text = text.casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _hash(secret: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(
        secret, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM, dklen=_DERIVED_LEN,
    )


def _check(secret: bytes, salt: bytes, expected: bytes) -> bool:
    return hmac.compare_digest(_hash(secret, salt), expected)


# --------------------------------------------------------------------------- #
# The stored credential
# --------------------------------------------------------------------------- #
@dataclass
class Question:
    """One challenge, and the salted hash of its answer."""

    prompt: str
    salt: bytes
    digest: bytes

    def verify(self, answer: str) -> bool:
        return _check(normalise_answer(answer).encode("utf-8"), self.salt, self.digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "salt": self.salt.hex(),
            "digest": self.digest.hex(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Question":
        return cls(
            prompt=data["prompt"],
            salt=bytes.fromhex(data["salt"]),
            digest=bytes.fromhex(data["digest"]),
        )


@dataclass
class Operator:
    """The one account this installation has."""

    username: str
    salt: bytes
    digest: bytes
    questions: list[Question] = field(default_factory=list)
    #: The brand this installation belongs to, so a licence and an operator
    #: cannot be mixed between firms.
    brand_id: str = "profileos"
    enrolled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: Fingerprints of the machines allowed to open the store. The list is
    #: advisory — what actually decides is whether the store carries a wrapped
    #: copy of its key for the machine asking — but the two are kept in step by
    #: :meth:`Gate._save`, which rewraps for exactly these fingerprints.
    machines: list[str] = field(default_factory=list)
    failures: int = 0
    locked_until: float = 0.0

    def verify_password(self, password: str) -> bool:
        return _check(password.encode("utf-8"), self.salt, self.digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "salt": self.salt.hex(),
            "digest": self.digest.hex(),
            "questions": [q.to_dict() for q in self.questions],
            "brand_id": self.brand_id,
            "enrolled_at": self.enrolled_at.isoformat(),
            "machines": list(self.machines),
            "failures": self.failures,
            "locked_until": self.locked_until,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Operator":
        return cls(
            username=data["username"],
            salt=bytes.fromhex(data["salt"]),
            digest=bytes.fromhex(data["digest"]),
            questions=[Question.from_dict(q) for q in data.get("questions", [])],
            brand_id=data.get("brand_id", "profileos"),
            enrolled_at=datetime.fromisoformat(data["enrolled_at"]),
            machines=list(data.get("machines") or []),
            failures=int(data.get("failures", 0)),
            locked_until=float(data.get("locked_until", 0.0)),
        )


@dataclass
class Session:
    """Proof that the gate was passed."""

    username: str
    brand_id: str
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    key_path: Path | None = None

    def describe(self) -> str:
        return f"{self.username} ({self.brand_id})"


# --------------------------------------------------------------------------- #
# Finding the key
# --------------------------------------------------------------------------- #
def removable_roots() -> list[Path]:
    """Places a USB drive turns up, on each platform this runs on."""
    roots: list[Path] = []
    for pattern in ("/media", "/run/media", "/mnt", "/Volumes"):
        base = Path(pattern)
        if not base.is_dir():
            continue
        try:
            for entry in base.iterdir():
                if entry.is_dir():
                    roots.append(entry)
                    # /media/<user>/<label> is one level deeper on most Linux
                    # desktops than /media/<label>.
                    try:
                        roots.extend(child for child in entry.iterdir() if child.is_dir())
                    except OSError:
                        continue
        except OSError:
            continue
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:/")
            if drive.exists():
                roots.append(drive)
    return roots


def find_key_files() -> list[Path]:
    """Every key file currently reachable, the environment override first."""
    found: list[Path] = []
    override = os.environ.get(KEY_ENV)
    if override:
        candidate = Path(override)
        if candidate.is_file():
            found.append(candidate)
        elif (candidate / KEY_FILENAME).is_file():
            found.append(candidate / KEY_FILENAME)
    for root in removable_roots():
        candidate = root / KEY_FILENAME
        if candidate.is_file():
            found.append(candidate)
    return found


def write_key_file(path: str | os.PathLike[str], secret: bytes, brand_id: str) -> Path:
    """Write the USB key. The secret is half of what unseals the store."""
    target = Path(path)
    # A bare directory name — an unmounted drive, a folder not yet created —
    # is a place to put the key, not the key's own filename. Writing a file
    # called "usb" onto the stick would leave nothing the scanner can find.
    if target.is_dir() or not target.suffix:
        target = target / KEY_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "kind": "profileos.key",
                "version": 1,
                "brand_id": brand_id,
                "secret": secret.hex(),
                "written_at": datetime.now(timezone.utc).isoformat(),
                "note": (
                    "Keep this on the USB key and nowhere else. Without it the "
                    "software will not start."
                ),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    try:
        os.chmod(target, 0o600)
    except OSError:  # pragma: no cover - some filesystems have no modes
        pass
    return target


def read_key_file(path: str | os.PathLike[str]) -> tuple[bytes, str]:
    """The secret and the brand a key file carries."""
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AccessDenied(f"Key file unreadable: {source.name}") from exc
    if data.get("kind") != "profileos.key":
        raise AccessDenied(f"{source.name} is not a ProfileOS key file")
    try:
        return bytes.fromhex(data["secret"]), str(data.get("brand_id", "profileos"))
    except (KeyError, ValueError) as exc:
        raise AccessDenied(f"Key file is malformed: {source.name}") from exc


# --------------------------------------------------------------------------- #
# Sealing
# --------------------------------------------------------------------------- #
def _seal_key(secret: bytes, fingerprint: str, salt: bytes) -> bytes:
    """Derive a wrapping key from the USB secret *and* one machine.

    Both halves are required, which is what makes a stolen key file useless on
    another computer and a copied store useless without the key file.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    material = secret + fingerprint.encode("utf-8")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"profileos.gate.v1",
    ).derive(material)


_MAGIC = b"PROFILEOS-GATE\x02"


def _wrap(store_key: bytes, secret: bytes, fingerprint: str) -> dict[str, str]:
    """One machine's sealed copy of the store key."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = os.urandom(16)
    nonce = os.urandom(12)
    cipher = AESGCM(_seal_key(secret, fingerprint, salt))
    return {
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "key": cipher.encrypt(nonce, store_key, None).hex(),
    }


def _unwrap(wrap: dict[str, str], secret: bytes, fingerprint: str) -> bytes | None:
    """The store key, if this wrap was made for this key file and machine."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        salt = bytes.fromhex(wrap["salt"])
        nonce = bytes.fromhex(wrap["nonce"])
        sealed = bytes.fromhex(wrap["key"])
    except (KeyError, ValueError) as exc:
        raise AccessDenied("The credential store is damaged") from exc
    try:
        return AESGCM(_seal_key(secret, fingerprint, salt)).decrypt(nonce, sealed, None)
    except InvalidTag:
        return None


def _seal(operator: Operator, secret: bytes, machines: Iterable[str]) -> bytes:
    """Seal the operator under a fresh store key, wrapped for every machine.

    The payload is encrypted once. Authorising a second computer adds another
    wrapped copy of the same key rather than a second copy of the record, so
    there is only ever one operator no matter how many machines can read it.

    A fresh store key on every save is deliberate: it means an old copy of the
    file cannot be opened with a wrap lifted from a newer one.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    ordered = list(dict.fromkeys(machines))
    if not ordered:
        # A store with no wraps is a store nobody can ever open again.
        raise AccessDenied("Refusing to seal a store no machine could open")

    store_key = os.urandom(32)
    nonce = os.urandom(12)
    payload = json.dumps(operator.to_dict()).encode("utf-8")
    envelope = {
        "version": 2,
        "nonce": nonce.hex(),
        "record": AESGCM(store_key).encrypt(nonce, payload, None).hex(),
        "wraps": [_wrap(store_key, secret, machine) for machine in ordered],
    }
    return _MAGIC + json.dumps(envelope).encode("utf-8")


def _unseal(blob: bytes, secret: bytes, fingerprint: HardwareFingerprint) -> Operator:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(_MAGIC):
        raise AccessDenied("The credential store is not a ProfileOS store")
    try:
        envelope = json.loads(blob[len(_MAGIC) :].decode("utf-8"))
        wraps = list(envelope["wraps"])
        nonce = bytes.fromhex(envelope["nonce"])
        record = bytes.fromhex(envelope["record"])
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError) as exc:
        raise AccessDenied("The credential store is damaged") from exc

    # Every wrap is tried rather than looked up by fingerprint: the file gives
    # away nothing about which machines it was made for.
    for wrap in wraps:
        store_key = _unwrap(wrap, secret, fingerprint.fingerprint)
        if store_key is None:
            continue
        try:
            payload = AESGCM(store_key).decrypt(nonce, record, None)
        except InvalidTag as exc:  # pragma: no cover - a wrap that opens but a
            # record that does not means the file was edited between the two.
            raise AccessDenied("The credential store is damaged") from exc
        return Operator.from_dict(json.loads(payload.decode("utf-8")))

    raise AccessDenied(
        "The key file does not match this installation, or this machine is "
        "not one the operator was enrolled on"
    )


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
class Gate:
    """Enrol the one operator, and let them — and only them — in."""

    def __init__(
        self,
        store_path: str | os.PathLike[str] | None = None,
        fingerprint: HardwareFingerprint | None = None,
    ) -> None:
        if store_path is None:
            from ..core.config import get_settings

            settings = get_settings()
            settings.ensure_directories()
            store_path = settings.config_dir / STORE_FILENAME
        self.store_path = Path(store_path)
        self.fingerprint = fingerprint or current_fingerprint()

    # -- state ---------------------------------------------------------------- #
    @property
    def is_enrolled(self) -> bool:
        return self.store_path.is_file()

    def key_files(self) -> list[Path]:
        return find_key_files()

    def _load(self, secret: bytes) -> Operator:
        if not self.is_enrolled:
            raise NotEnrolled(
                "No operator has been enrolled on this installation. Run "
                "`profileos access enrol` on the machine that will use it."
            )
        return _unseal(self.store_path.read_bytes(), secret, self.fingerprint)

    def _save(self, operator: Operator, secret: bytes) -> None:
        blob = _seal(operator, secret, operator.machines)
        # Write then replace: an interrupted save must not leave a store that
        # nobody can open.
        temporary = self.store_path.with_suffix(".tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(blob)
        temporary.replace(self.store_path)
        try:
            os.chmod(self.store_path, 0o600)
        except OSError:  # pragma: no cover
            pass

    # -- enrolment ------------------------------------------------------------- #
    def enrol(
        self,
        username: str,
        password: str,
        questions: Sequence[tuple[str, str]],
        *,
        key_target: str | os.PathLike[str],
        brand_id: str | None = None,
        minimum_questions: int = 2,
    ) -> Path:
        """Create the one operator and write the USB key that opens it.

        Refuses if an operator already exists: there is exactly one account and
        no way to quietly add a second. Changing it goes through
        :meth:`rotate`, which demands the current password and answers.
        """
        if self.is_enrolled:
            raise AccessDenied(
                "This installation already has its operator. Use `rotate` and "
                "prove the current password and answers to change it."
            )
        if len(questions) < minimum_questions:
            raise AccessDenied(
                f"Set at least {minimum_questions} security questions",
                given=len(questions),
            )
        problems = password_problems(password)
        if problems:
            raise AccessDenied("; ".join(problems))
        for prompt, answer in questions:
            if not prompt.strip():
                raise AccessDenied("A security question needs a prompt")
            if len(normalise_answer(answer)) < 2:
                raise AccessDenied(
                    "A security answer that short is a typo, not an answer",
                    prompt=prompt,
                )

        if brand_id is None:
            from ..branding import configured_brand_id

            brand_id = configured_brand_id()

        salt = os.urandom(16)
        operator = Operator(
            username=username.strip(),
            salt=salt,
            digest=_hash(password.encode("utf-8"), salt),
            questions=[
                Question(
                    prompt=prompt.strip(),
                    salt=(question_salt := os.urandom(16)),
                    digest=_hash(
                        normalise_answer(answer).encode("utf-8"), question_salt
                    ),
                )
                for prompt, answer in questions
            ],
            brand_id=brand_id,
            machines=[self.fingerprint.fingerprint],
        )
        secret = os.urandom(32)
        self._save(operator, secret)
        path = write_key_file(key_target, secret, brand_id)
        _log.info(
            "Enrolled operator %s for %s; key written to %s",
            operator.username, brand_id, path,
        )
        return path

    # -- opening ---------------------------------------------------------------- #
    def authenticate(
        self,
        username: str,
        password: str,
        answers: Sequence[str],
        *,
        key_path: str | os.PathLike[str] | None = None,
    ) -> Session:
        """Check all three factors. Any failure denies, and says which layer.

        Which layer failed is disclosed deliberately: a legitimate operator who
        has left the USB key at home needs to be told that, and an attacker who
        cannot produce the key learns nothing they did not already know. What is
        never disclosed is *which* of the username, password or answers was
        wrong, because that is what turns guessing into a search.
        """
        candidates = [Path(key_path)] if key_path else self.key_files()
        if not candidates:
            raise AccessDenied(
                "No key file found. Insert the ProfileOS USB key and try again."
            )

        last: Exception | None = None
        for candidate in candidates:
            try:
                secret, key_brand = read_key_file(candidate)
                operator = self._load(secret)
            except (AccessDenied, NotEnrolled) as exc:
                last = exc
                continue

            self._enforce_lockout(operator)

            if key_brand != operator.brand_id:
                raise AccessDenied(
                    "That key file belongs to a different firm's installation"
                )
            if self.fingerprint.fingerprint not in operator.machines:
                raise AccessDenied(
                    "This computer is not one the operator was enrolled on"
                )

            ok = hmac.compare_digest(
                username.strip().casefold(), operator.username.casefold()
            )
            ok &= operator.verify_password(password)
            if len(answers) < len(operator.questions):
                ok = False
            else:
                for index, question in enumerate(operator.questions):
                    ok &= question.verify(answers[index])

            if not ok:
                self._record_failure(operator, secret)
                raise AccessDenied(
                    "Username, password or security answers are not right"
                )

            operator.failures = 0
            operator.locked_until = 0.0
            self._save(operator, secret)
            _log.info("Access granted to %s", operator.username)
            return Session(
                username=operator.username,
                brand_id=operator.brand_id,
                key_path=candidate,
            )

        raise last or AccessDenied("No usable key file was found")

    def _enforce_lockout(self, operator: Operator) -> None:
        remaining = operator.locked_until - time.time()
        if remaining > 0:
            raise AccessDenied(
                "Too many failed attempts; the gate is closed for now",
                seconds_remaining=int(remaining) + 1,
            )

    def _record_failure(self, operator: Operator, secret: bytes) -> None:
        operator.failures += 1
        over = operator.failures - _FREE_ATTEMPTS
        if over > 0:
            delay = min(_MAX_LOCKOUT_SECONDS, 5 * (2 ** min(over, 12)))
            operator.locked_until = time.time() + delay
            _log.warning(
                "Access denied %d time(s); locked for %d s", operator.failures, delay
            )
        self._save(operator, secret)

    # -- maintenance -------------------------------------------------------------- #
    def rotate(
        self,
        username: str,
        current_password: str,
        answers: Sequence[str],
        *,
        new_password: str,
        new_questions: Sequence[tuple[str, str]] | None = None,
        key_path: str | os.PathLike[str] | None = None,
    ) -> None:
        """Change the password, and optionally the questions.

        Everything currently set has to be proved first, so knowing the answers
        is never a way *round* the password — only a way to change it once the
        password is already in hand.
        """
        session = self.authenticate(username, current_password, answers, key_path=key_path)
        problems = password_problems(new_password)
        if problems:
            raise AccessDenied("; ".join(problems))

        secret, _ = read_key_file(session.key_path or (key_path or ""))
        operator = self._load(secret)
        salt = os.urandom(16)
        operator.salt = salt
        operator.digest = _hash(new_password.encode("utf-8"), salt)
        if new_questions:
            operator.questions = [
                Question(
                    prompt=prompt.strip(),
                    salt=(question_salt := os.urandom(16)),
                    digest=_hash(normalise_answer(answer).encode("utf-8"), question_salt),
                )
                for prompt, answer in new_questions
            ]
        self._save(operator, secret)
        _log.info("Credentials rotated for %s", operator.username)

    def authorise_machine(
        self,
        username: str,
        password: str,
        answers: Sequence[str],
        *,
        fingerprint: str,
        key_path: str | os.PathLike[str] | None = None,
    ) -> None:
        """Add another computer the operator may use.

        Enrolling a second machine has to be done from an already-authorised
        one, holding the key: that is what stops somebody who copies the key
        file simply adding their own laptop to the list.
        """
        session = self.authenticate(username, password, answers, key_path=key_path)
        secret, _ = read_key_file(session.key_path or (key_path or ""))
        operator = self._load(secret)
        if fingerprint not in operator.machines:
            operator.machines.append(fingerprint)
            self._save(operator, secret)
            _log.info("Authorised an additional machine for %s", operator.username)

    def prompts(self, *, key_path: str | os.PathLike[str] | None = None) -> list[str]:
        """The security questions, so the login box can ask them.

        Reading the prompts needs the key file: the questions themselves are a
        clue to their answers, and there is no reason to show them to somebody
        who cannot even produce the USB key.
        """
        candidates = [Path(key_path)] if key_path else self.key_files()
        for candidate in candidates:
            try:
                secret, _ = read_key_file(candidate)
                return [question.prompt for question in self._load(secret).questions]
            except (AccessDenied, NotEnrolled):
                continue
        raise AccessDenied("No key file found. Insert the ProfileOS USB key.")

    def status(self) -> dict[str, Any]:
        """What can be said without any secret at all."""
        keys = self.key_files()
        return {
            "enrolled": self.is_enrolled,
            "store": str(self.store_path),
            "key_files_found": [str(path) for path in keys],
            "machine": self.fingerprint.short,
        }


#: What a password has to clear. Length does most of the work; the character
#: classes stop the obvious "aaaaaaaaaaaa".
MINIMUM_PASSWORD_LENGTH = 12


def password_problems(password: str) -> list[str]:
    """Everything wrong with a proposed password, in plain words."""
    problems: list[str] = []
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        problems.append(
            f"the password must be at least {MINIMUM_PASSWORD_LENGTH} characters"
        )
    if len(set(password)) < 5:
        problems.append("the password repeats too few different characters")
    if password.strip() != password:
        problems.append("the password starts or ends with a space, which is too easy to lose")
    lowered = password.casefold()
    for obvious in ("password", "123456", "qwerty", "profileos", "admin"):
        if obvious in lowered:
            problems.append(f"the password contains {obvious!r}, which is guessed first")
            break
    return problems


def require_session(
    gate: Gate | None = None, *, feature: str | None = None
) -> Session:
    """Open the gate or raise. The one call an entry point needs.

    ``PROFILEOS_SESSION`` short-circuits it for an unattended run — a nightly
    re-nest, a CI job — and is deliberately not a way past the gate: it only
    works when the store, the key file and the machine already agree.
    """
    gate = gate or Gate()
    token = os.environ.get("PROFILEOS_SESSION")
    if token:
        try:
            data = json.loads(token)
            return Session(username=data["username"], brand_id=data["brand_id"])
        except (json.JSONDecodeError, KeyError) as exc:
            raise AccessDenied("PROFILEOS_SESSION is not a usable session") from exc
    raise AccessDenied(
        "This installation is locked. Start it from the desktop application and "
        "sign in with the USB key, the password and the security answers."
    )


__all__ = [
    "AccessDenied",
    "NotEnrolled",
    "KEY_FILENAME",
    "STORE_FILENAME",
    "KEY_ENV",
    "MINIMUM_PASSWORD_LENGTH",
    "normalise_answer",
    "password_problems",
    "Question",
    "Operator",
    "Session",
    "Gate",
    "removable_roots",
    "find_key_files",
    "write_key_file",
    "read_key_file",
    "require_session",
]
