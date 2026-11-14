"""A showroom: the program as a busy shop sees it, in a folder of its own.

Shown to somebody new, an empty program says nothing — zeros on the home
page, an empty job list. This fills a separate data folder with a month of a
believable aluminium shop: enquiries, quotations out, jobs in production, a
building on two stages with its blind frames, cheques in the drawer and one
that came back, visits today and tomorrow, a service call.

It never touches the shop's own data: the folder is its own, every name in it
is invented, and the window says "הדגמה" in its title so nobody mistakes it
for the real books. Prices here are demonstration figures, not quotations.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

#: The variable a showroom window is started with.
ENV = "PROFILEOS_SHOWROOM"

_CUSTOMERS = (
    ("C-0101", "משפחת אורן", "052-4410023", "עפרה", "רחוב הגפן 12"),
    ("C-0102", "י.ש. בנייה וקבלנות", "054-6612290", "ירושלים", "פרויקט גבעת שאול"),
    ("C-0103", "משפחת ברק", "050-7781334", "בית אל", "רחוב הארזים 4"),
    ("C-0104", "דנה ואבי לוין", "053-2219987", "כוכב יעקב", "רחוב התאנה 8"),
    ("C-0105", "מרכז רפואי הגבעה", "02-6543210", "ירושלים", "שדרות הגבעה 30"),
    ("C-0106", "משפחת שגיא", "058-3324411", "שילה", "רחוב השקד 17"),
    ("C-0107", "גלעד מזרחי", "052-9901122", "פסגות", "רחוב הנוף 3"),
    ("C-0108", "מוסדות חינוך ההר", "02-9970011", "בית אל", "קריית החינוך"),
)

#: (job, customer, status, what is in it, days since it was opened, total before VAT)
_JOBS = (
    ("J-2026-0141", "C-0101", "installed", [("W1", "חלון הזזה", 2400, 1500, 2),
                                            ("W2", "חלון ציר", 1000, 1200, 3),
                                            ("D1", "דלת הזזה למרפסת", 3200, 2300, 1)], 75, 48_600),
    ("J-2026-0148", "C-0103", "in_production", [("W1", "חלון הזזה", 1800, 1300, 4),
                                                ("W2", "חלון נטוי-נפתח", 800, 1100, 2),
                                                ("D1", "דלת הזזה למרפסת", 3600, 2400, 1)], 34, 62_400),
    ("J-2026-0152", "C-0102", "won", [(f"W{n}", "חלון הזזה", 1500, 1300, 8) for n in range(1, 4)]
                                     + [("D1", "דלת הזזה למרפסת", 2800, 2200, 8)], 21, 236_000),
    ("J-2026-0155", "C-0104", "won", [("W1", "חלון ציר", 1100, 1300, 3),
                                      ("D1", "דלת כניסה", 1000, 2200, 1)], 12, 31_800),
    ("J-2026-0157", "C-0105", "quoted", [("W1", "חזית מסחרית", 4200, 2800, 2),
                                         ("W2", "חלון נטוי-נפתח", 1200, 1400, 12)], 9, 184_500),
    ("J-2026-0159", "C-0106", "quoted", [("W1", "חלון הזזה", 2000, 1400, 3),
                                         ("W2", "חלון ציר", 900, 1100, 2)], 6, 27_900),
    ("J-2026-0160", "C-0107", "quoted", [("D1", "הרמה והזזה", 4500, 2400, 1)], 4, 58_000),
    ("J-2026-0162", "C-0108", "enquiry", [("W1", "חלון הזזה", 1600, 1200, 24)], 2, 0),
    ("J-2026-0128", "C-0106", "installed", [("W1", "חלון הזזה", 2200, 1400, 3),
                                            ("W2", "חלון ציר", 900, 1200, 2)], 120, 29_400),
    ("J-2026-0131", "C-0107", "lost", [("W1", "חלון הזזה", 1800, 1400, 4)], 100, 24_800),
    ("J-2026-0136", "C-0104", "lost", [("D1", "דלת כניסה", 1000, 2200, 1)], 90, 14_500),
    ("J-2026-0163", "C-0103", "enquiry", [("W1", "חלון ציר", 600, 600, 1)], 1, 0),
)


def showroom_dir() -> Path:
    """Beside the shop's documents, never inside its data folder."""
    from .ui.widgets import documents_dir

    return Path(documents_dir()) / "ProfileOS הדגמה"


def is_showroom() -> bool:
    return os.environ.get(ENV) == "1"


def _opening(mark: str, kind: str, width: float, height: float, quantity: int,
             system_id: str) -> Any:
    from .library import search_openings
    from .quoting.estimate import opening_from_preset

    found = search_openings(f"{kind} {width:.0f}x{height:.0f}", limit=1)
    if not found:
        found = search_openings(kind, limit=1)
    opening = opening_from_preset(found[0], name=mark)
    opening.width, opening.height = float(width), float(height)
    opening.reference = mark
    opening.quantity = quantity
    opening.system_id = system_id
    return opening


def build(folder: Path, *, today: date | None = None) -> Path:
    """Fill ``folder`` with the showroom shop. Returns its data folder.

    Writes only under ``folder``: the caller points the settings there first.
    """
    from .core.config import get_settings
    from .elements.model import ElementSchedule
    from .erp.collection import Cheque, default_cheques
    from .projects import (
        JobFile, JobStatus, MaterialOrder, Payment, PaymentMethod, default_customers,
        default_store,
    )
    from .projects.agenda import Appointment, VisitKind, default_agenda
    from .projects.blindframes import record_check, record_installed, set_two_stage
    from .projects.followups import Kind, Task, default_tasks
    from .projects.model import Customer, Snag
    from .service.model import ServiceCall, Symptom
    from .service.register import default_register

    today = today or date.today()
    data = Path(get_settings().data_dir)
    if Path(folder).resolve() not in (data.resolve(), *data.resolve().parents):
        raise RuntimeError("the showroom is built only in its own folder")
    data.mkdir(parents=True, exist_ok=True)
    system = "klil-7300"

    customers = {cid: Customer(customer_id=cid, name=name, phone=phone, city=city,
                               address=address)
                 for cid, name, phone, city, address in _CUSTOMERS}
    default_customers().save_all(customers.values())

    store = default_store()
    order = [JobStatus.ENQUIRY, JobStatus.QUOTED, JobStatus.WON, JobStatus.IN_PRODUCTION,
             JobStatus.INSTALLED]
    jobs: dict[str, Any] = {}
    for job_id, cid, status, openings, age, net in _JOBS:
        customer = customers[cid]
        opened = today - timedelta(days=age)
        job = JobFile(job_id=job_id, name=f"{customer.name} — {customer.city}",
                      customer_id=cid, customer_name=customer.name,
                      site_address=f"{customer.address}, {customer.city}", system_id=system,
                      created=opened.isoformat(), source="המלצה" if age % 2 else "גוגל")
        job.schedule = ElementSchedule(name=job.name, system_id=system, openings=[
            _opening(mark, kind, w, h, q, system) for mark, kind, w, h, q in openings])
        target = JobStatus(status)
        if target is JobStatus.LOST:
            job.record_quote(float(net))
            job.advance(JobStatus.QUOTED, "הדגמה")
            job.lost_reason = "מחיר"
            job.advance(JobStatus.LOST, "הדגמה")
            jobs[job_id] = job
            continue
        for step in order[1:order.index(target) + 1]:
            if step is JobStatus.QUOTED and net:
                job.record_quote(float(net))
                job.quoted_on = (opened + timedelta(days=2)).isoformat()
            job.advance(step, "הדגמה")
        if net and not job.quote_total:
            job.record_quote(float(net))
        if target in (JobStatus.WON, JobStatus.IN_PRODUCTION, JobStatus.INSTALLED):
            job.due_date = (today + timedelta(days=max(5, 40 - age))).isoformat()
        jobs[job_id] = job

    # Money: a deposit by transfer, cheques for the rest.
    drawer = default_cheques()
    banks = ("לאומי", "הפועלים", "מזרחי טפחות", "דיסקונט")
    number = 4410
    for index, (job_id, job) in enumerate(jobs.items()):
        if job.status not in (JobStatus.WON, JobStatus.IN_PRODUCTION, JobStatus.INSTALLED):
            continue
        gross = round(job.quote_total * 1.18)
        deposit = round(gross * 0.3, -2)
        job.payments.append(Payment(amount=deposit, method=PaymentMethod.TRANSFER,
                                    on=(today - timedelta(days=10 + index)).isoformat(),
                                    reference="מקדמה"))
        rest = gross - deposit
        for month in range(3):
            number += 1
            due = today + timedelta(days=30 * month - 20 + index * 3)
            part = round(rest / 3) if month < 2 else rest - 2 * round(rest / 3)
            cheque = Cheque(customer=job.customer_name, amount=part, due=due,
                            received=today - timedelta(days=8), bank=banks[index % 4],
                            number=f"{number:07d}", job_id=job_id)
            drawer.add(cheque)
            if job.status is JobStatus.INSTALLED and month == 0:
                cheque.deposit(due)
                cheque.clear(due + timedelta(days=2))
    returned = Cheque(customer="משפחת אורן", amount=6_200, due=today - timedelta(days=6),
                      received=today - timedelta(days=40), bank="הפועלים",
                      number="0003187", job_id="J-2026-0141")
    drawer.add(returned)
    returned.deposit(today - timedelta(days=6))
    returned.bounce(today - timedelta(days=3), "אין כיסוי מספיק")
    supplier = Cheque(customer="קליל — אספקת פרופילים", amount=38_400,
                      due=today + timedelta(days=9), number="0000612", outgoing=True)
    drawer.add(supplier)
    drawer.save()

    # The building on two stages: blind frames in, waiting on the plaster.
    building = jobs["J-2026-0152"]
    set_two_stage(building, True)
    marks = [opening.reference for opening in building.schedule.openings]
    record_installed(building, marks, by="צוות א׳", level_mark="קו המטר של הקבלן",
                     on=today - timedelta(days=38))
    record_check(building, marks[:2], ok=True, on=today - timedelta(days=2))
    record_check(building, [marks[2]], ok=False, note="טיח בתוך המשקוף בקומה ב׳",
                 on=today - timedelta(days=2))
    building.material_orders.append(MaterialOrder(
        supplier="קליל", ordered_on=(today - timedelta(days=4)).isoformat()))
    production = jobs["J-2026-0148"]
    production.material_orders.append(MaterialOrder(
        supplier="קליל", ordered_on=(today - timedelta(days=20)).isoformat()))
    jobs["J-2026-0141"].snags.append(Snag(description="ידית בחדר הילדים נתקעת"))

    for job in jobs.values():
        store.save(job)

    # The shop floor: the job in production, part cut and assembled.
    from .mes.store import save_work_order
    from .mes.tracking import Stage, work_order_from_builds
    from .projects.materials import build_job

    builds, _problems = build_job(production)
    floor = work_order_from_builds(builds, project_id=production.job_id, name=production.name)
    for count, item in enumerate(floor.items):
        if count % 3 == 2:
            continue
        floor.scan(item.item_id, Stage.CUT, operator="משה", station="מסור")
        if count % 2 == 0:
            floor.scan(item.item_id, Stage.MACHINED, operator="משה", station="CNC")
    save_work_order(floor, production.job_id, store)

    agenda = default_agenda()
    for visit in (
        Appointment(on=today, time="07:30", kind=VisitKind.INSTALLATION,
                    customer_name="דנה ואבי לוין", job_id="J-2026-0155",
                    address="רחוב התאנה 8, כוכב יעקב", crew="צוות א׳"),
        Appointment(on=today, time="13:00", kind=VisitKind.SURVEY,
                    customer_name="מוסדות חינוך ההר", job_id="J-2026-0162",
                    address="קריית החינוך, בית אל", crew="דאדי"),
        Appointment(on=today + timedelta(days=1), time="08:00", kind=VisitKind.INSTALLATION,
                    customer_name="משפחת ברק", job_id="J-2026-0148",
                    address="רחוב הארזים 4, בית אל", crew="צוות א׳"),
        Appointment(on=today + timedelta(days=2), time="09:00", kind=VisitKind.SERVICE,
                    customer_name="משפחת אורן", job_id="J-2026-0141",
                    address="רחוב הגפן 12, עפרה", crew="יוסי"),
    ):
        agenda.add(visit)
    agenda.save()

    tasks = default_tasks()
    for task in (
        Task(kind=Kind.CHASE_QUOTE, what="לשאול אם יש החלטה על החזית", about="job:J-2026-0157",
             subject_name="מרכז רפואי הגבעה", due=today - timedelta(days=1), assigned_to="דאדי"),
        Task(kind=Kind.CHASE_QUOTE, what="להתקשר לגבי ההצעה", about="job:J-2026-0159",
             subject_name="משפחת שגיא", due=today, assigned_to="דאדי"),
        Task(kind=Kind.CHASE_PAYMENT, what="צ׳ק שחזר — לבקש צ׳ק חדש", about="job:J-2026-0141",
             subject_name="משפחת אורן", due=today, assigned_to="דאדי"),
    ):
        tasks.add(task, save=False)
    tasks.save()

    register = default_register()
    register.add(ServiceCall(job_id="J-2026-0141", customer_name="משפחת אורן",
                             element_name="W2", symptom=Symptom.HANDLE,
                             description="הידית בחדר הילדים נתקעת בסגירה",
                             delivered=today - timedelta(days=20), phone="052-4410023",
                             site="רחוב הגפן 12, עפרה"))
    register.save()

    # The showroom's own settings and a backup of it, so the home page shows
    # the shop's day and not the showroom's setup.
    from .core.backup import write_backup
    from .core.config import save_settings

    settings = get_settings()
    settings.withholding_certificate_until = (today + timedelta(days=210)).isoformat()
    settings.books_certificate_until = (today + timedelta(days=210)).isoformat()
    settings.backup_folder = Path(folder) / "backups"
    save_settings(settings)
    write_backup(settings.backup_folder)
    return data


def open_showroom(folder: Path | None = None) -> subprocess.Popen:
    """Build the showroom if it is not there, and open it in a window of its
    own — the shop's window stays as it is, on the shop's own data."""
    folder = Path(folder or showroom_dir())
    env = dict(os.environ)
    env["PROFILEOS_DATA_DIR"] = str(folder / "data")
    env["PROFILEOS_CONFIG_DIR"] = str(folder / "config")
    env[ENV] = "1"
    # The shop's own window already runs under the update launcher; a second
    # launcher would wait on its lock. The showroom installs nothing.
    env["PROFILEOS_NO_LAUNCHER"] = "1"
    fresh = not (folder / "data" / "jobs").is_dir()
    args = [sys.executable, "-m", "profileos.showroom"] + (["--build"] if fresh else [])
    return subprocess.Popen(args, env=env, cwd=str(Path(__file__).resolve().parents[1]))


def main(argv: list[str]) -> int:
    """``python -m profileos.showroom [--build]``: the window, on the showroom
    folder the environment names."""
    if not is_showroom():
        print("run through open_showroom(): the showroom needs its own folder")
        return 2
    if "--build" in argv:
        from .core.config import get_settings

        build(Path(get_settings().data_dir).parent)
    from .ui.app import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
