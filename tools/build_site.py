#!/usr/bin/env python3
"""Build the public site from the code it describes.

The comparison page makes claims about this suite. Writing those claims by
hand would let them drift the first time an engine is renamed, so they are
generated: :mod:`profileos.compare` resolves every claim to a real symbol,
this script refuses to build if any of them fails to resolve, and the result
is inlined into a single self-contained page.

Inlined rather than fetched on purpose — one file has no second request to
fail, no CORS to configure and no loading state to design.

    python tools/build_site.py            # writes site/index.html
    python tools/build_site.py --check    # verifies the checked-in page is current
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
TEMPLATE = SITE / "template.html"
DATA = SITE / "data.json"
OUTPUT = SITE / "index.html"
PLACEHOLDER = "__DATA__"

#: Hebrew names for the capability areas. They live here rather than in
#: profileos.compare because they are presentation, not engineering.
AREA_NAMES_HE: list[tuple[str, str]] = [
    ("geometry", "גאומטריה"),
    ("structural", "חישוב סטטי"),
    ("elements", "אלמנטים"),
    ("glass", "זיגוג"),
    ("optimisation", "אופטימיזציה"),
    ("cnc", "CNC"),
    ("shopfloor", "רצפת ייצור"),
    ("commercial", "מסחר"),
    ("catalogue", "קטלוגים"),
    ("platform", "פלטפורמה"),
    ("adjacent", "מעבר לאלומיניום"),
]


def build_data() -> dict:
    sys.path.insert(0, str(ROOT))
    from profileos import compare as cmp

    failures = cmp.verify_claims()
    if failures:
        for capability_id, reason in sorted(failures.items()):
            print(f"  {capability_id}: {reason}", file=sys.stderr)
        raise SystemExit(
            f"{len(failures)} capability claim(s) have no code behind them; "
            "the site would advertise something that is not there."
        )

    known = {area.value for area in cmp.Area}
    named = {key for key, _ in AREA_NAMES_HE}
    if known != named:
        raise SystemExit(
            f"area names are out of step with the code: "
            f"missing {sorted(known - named)}, stale {sorted(named - known)}"
        )

    return {
        "summary": cmp.summary(),
        "areas": [{"id": key, "he": name} for key, name in AREA_NAMES_HE],
        "capabilities": cmp.matrix(),
        "packages": [
            {
                "id": package.id,
                "name": package.name,
                "short": package.heading,
                "vendor": package.vendor,
                "origin": package.origin,
                "note": package.note,
                "source": package.source,
                "coverage": cmp.coverage(package),
            }
            for package in cmp.PACKAGES
        ],
        "profileos_coverage": cmp.coverage(),
        "gaps": [
            {"id": c.id, "en": c.name_en, "he": c.name_he, "detail": c.detail}
            for c in cmp.missing_from_profileos()
        ],
        "distinctive": [
            {"id": c.id, "en": c.name_en, "he": c.name_he, "detail": c.detail}
            for c in cmp.not_documented_elsewhere()
        ],
        "limitations": list(cmp.STANDING_LIMITATIONS),
    }


def page_payload(data: dict) -> dict:
    """Only what the page actually renders.

    The full record in ``data.json`` carries the English descriptions, the
    vendor source URLs and the per-package coverage counts. The page renders
    none of them, and inlining them would put twenty-five kilobytes of unread
    JSON into every visitor's first paint.
    """
    package_ids = [package["id"] for package in data["packages"]]
    return {
        "summary": data["summary"],
        "areas": data["areas"],
        "capabilities": [
            {
                "area": capability["area"],
                "name_he": capability["name_he"],
                "name_en": capability["name_en"],
                "differentiator": capability["differentiator"],
                "profileos": capability["profileos"],
                **{key: capability[key] for key in package_ids},
            }
            for capability in data["capabilities"]
        ],
        "packages": [
            {
                "id": package["id"],
                "name": package["name"],
                "short": package["short"],
                "vendor": package["vendor"],
                "origin": package["origin"],
            }
            for package in data["packages"]
        ],
    }


def render(data: dict) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise SystemExit(f"{TEMPLATE} has no {PLACEHOLDER} placeholder")
    payload = json.dumps(page_payload(data), ensure_ascii=False, separators=(",", ":"))
    # A literal </script> inside the JSON would close the tag early and drop
    # the rest of the page into the document as text.
    return template.replace(PLACEHOLDER, payload.replace("</script>", "<\\/script>"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Exit non-zero if the checked-in page is out of date.",
    )
    args = parser.parse_args()

    data = build_data()
    page = render(data)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != page:
            print(
                "site/index.html is out of date; run tools/build_site.py",
                file=sys.stderr,
            )
            return 1
        print("site/index.html is current.")
        return 0

    OUTPUT.write_text(page, encoding="utf-8")
    summary = data["summary"]
    print(
        f"Wrote {OUTPUT} — {summary['profileos_implemented']} of "
        f"{summary['capabilities']} capabilities, "
        f"{summary['packages_compared']} packages compared, all claims resolved."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
