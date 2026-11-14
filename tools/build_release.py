"""Build one published version of ProfileOS: the zip, its fingerprint, its notes.

Run by the release workflow after the whole test suite passed — never by
hand at the shop. Writes into ``--out``:

* ``profileos-<version>.zip`` — every file under version control, plus
  ``profileos/_release.json`` saying which version and commit it is;
* ``release.json`` — the version, the zip's name, size and SHA-256, the
  commit, and what is new in Hebrew. The program reads this first and
  installs nothing whose fingerprint does not match;
* ``notes.md`` — the same notes, for the release page.

The version is the date and the run number: ``2026.10.22.14``. It only grows,
so "newer" is never a judgement call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Not part of what the shop runs.
LEFT_OUT = (".github/", ".claude/", "tests/", "site/", "vercel.json", "tasks.png")


def tracked_files(root: Path = ROOT) -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True,
                            check=True).stdout.decode("utf-8").split("\0")
    return [name for name in listed if name and not name.startswith(LEFT_OUT)
            and (root / name).is_file()]


def _key(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.strip().lstrip("v").split("."))
    except ValueError:
        return ()


def build(out: Path, *, run: int, commit: str, branch: str = "", root: Path = ROOT,
          now: datetime | None = None, after: str = "") -> dict:
    """Build the version. ``after`` is the newest one published: a version
    that would not sort after it is refused, never published as "newer"."""
    now = now or datetime.now(timezone.utc)
    version = f"{now:%Y.%m.%d}.{run}"
    if after and _key(after) and _key(version) <= _key(after):
        raise SystemExit(f"{version} is not newer than the published {after}: not built")
    out.mkdir(parents=True, exist_ok=True)
    stamp = {"version": version, "commit": commit, "date": now.date().isoformat(),
             "branch": branch}
    zip_name = f"profileos-{version}.zip"
    archive = out / zip_name
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as pack:
        for name in tracked_files(root):
            pack.write(root / name, name)
        pack.writestr("profileos/_release.json", json.dumps(stamp, ensure_ascii=False, indent=2))
    body = archive.read_bytes()

    notes_path = root / "profileos" / "data" / "whats_new.json"
    notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.is_file() else []
    manifest = {
        **stamp,
        "zip": zip_name,
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "notes": notes[:12],
    }
    (out / "release.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    lines = [f"# ProfileOS {version}", ""]
    for entry in notes[:6]:
        lines.append(f"## {entry.get('date', '')} — {entry.get('title', '')}")
        lines += [f"- {item}" for item in entry.get("items", [])]
        lines.append("")
    lines.append(f"SHA-256: `{manifest['sha256']}`")
    (out / "notes.md").write_text("\n".join(lines), encoding="utf-8")

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"version={version}\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--commit", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--after", default="", help="the newest published version")
    args = parser.parse_args()
    manifest = build(args.out, run=args.run, commit=args.commit, branch=args.branch,
                     after=args.after)
    print(f"{manifest['version']}  {manifest['size']} bytes  {manifest['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
