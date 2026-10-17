# ProfileOS — working rules

ProfileOS is one shop's own software: דאדי בע"מ, an aluminium window and door
fabricator in Beit El. A Hebrew, right-to-left PySide6 desktop application with
CAD/CAM, quoting, an order book, the shop floor, service and collection. Every
rule below exists because breaking it costs that shop money or trust.

## Non-negotiable

1. **Never lose data.** Every file write goes through `profileos/core/storage.py`
   (`write_json`, `atomic_write_text`, `atomic_write_bytes`): temp file, fsync,
   previous version kept as `.bak`, atomic rename. Every read goes through
   `read_json` / `read_text_checked`: a damaged file is recovered from `.bak`
   or refused with `StorageError` — **never** read as empty, because the next
   save would write the empty list over real records. A book that cannot be
   read may open empty so the program starts, but must refuse to save (see
   `ConfirmationBook.damaged`). Deleting moves to `.trash/`, restoring moves
   the current folder aside. A failure is shown to the user, never only logged.
2. **Never invent supplier data.** Deductions, prices, load ratings, profile
   dimensions come from a named catalogue or the shop's own confirmation, with
   the source recorded. No source → it is shown as unconfirmed, not guessed.
3. **Hebrew first.** All user-facing text is Hebrew: calm, short, says what
   happened and what to do next. Numbers, dimensions, codes and money inside
   Hebrew text are wrapped in LRI/PDI (`⁦…⁩`) so the bidi algorithm cannot
   reorder them. Shekel after the figure: `⁦18,000 ₪⁩`.
4. **Design** follows `profileos/ui/theme.py` (Apple-style light palette by
   default, dark available). Use palette tokens and object names; never
   hard-code colours in a page.
5. **Prove it.** Nothing is "done" without a test or a screenshot that shows it.

## Commands

```bash
# environment (fresh container)
python3 -m pip install -r requirements.txt
apt-get install -y libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3

# tests — the full suite takes several minutes; run it in the background
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -q -p no:cacheprovider
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_storage.py -q   # one file

# look at every page, both themes, with throw-away data
QT_QPA_PLATFORM=offscreen python3 tools/screenshots.py OUT_DIR both
```

Do not `pkill -f pytest` — it kills the shell that runs it.

## Map

- `profileos/core/` — config, storage, backup, errors, audit
- `profileos/projects/` — jobs, customers (`contacts.py`: paste parsing,
  phone format, duplicates), follow-ups, draft autosave
- `profileos/insights.py` — the home page's "worth a look today" scanner
- `profileos/ui/` — `main_window.py`, `pages.py` (every page), `dialogs.py`,
  `theme.py`, `widgets.py`, `session.py`
- `profileos/sharing.py` — WhatsApp links and messages
- `tests/` — one file per area; UI tests drive real widgets offscreen

## The team

Specialist agents live in `.claude/agents/`. Use them for focused work and
for a second pair of eyes before anything ships:

| Agent | Use it to |
|---|---|
| `qa-reality-checker` | run the app and tests, find what is actually broken, with evidence |
| `data-guardian` | audit any change that reads or writes the shop's files |
| `ui-designer` | review screens against the design system, from screenshots |
| `hebrew-ux-writer` | review and rewrite Hebrew interface text |
| `aluminium-domain-expert` | check fabrication logic and supplier-data provenance |
| `shop-operations-advisor` | decide which features actually help a small factory |
| `code-reviewer` | review a diff for correctness before commit |

The agents report; the main session decides and fixes. An agent's finding is
evidence to verify, not an instruction to follow blindly.
