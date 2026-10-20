"""Reusable LPS data layer (pure Python 3 standard library, no curses).

Mirrors LPS Companion v1.11 for the Clockwork PicoCalc
(src/lps_companion.cpp + src/main.cpp of TME520/LPS-Companion-PicoCalc):

* constants and rules: activities, XP, keepsakes, limits, 2000-2099 calendar;
* ICS day export ``LPS/EXPORT/YYYY-MM-DD_LPS-Companion.ics`` (write + parse);
* yearly weights journal ``LPS/WEIGHTS/YYYY.csv`` (append without duplicate + parse);
* read-only decoder for the A/B snapshots ``LPS/SAVE_A.BIN`` / ``SAVE_B.BIN``
  (wire formats 1-6);
* the LPS Console local JSON state, atomic writes, close-day and import/merge.

Intended to be imported by other LPS programs (for instance c18/lps-rpi4.py)::

    sys.path.insert(0, "/path/to/LifePositioningSystem/console")
    from lpsconsole import lpsdata
    days = lpsdata.parse_ics(open(path, encoding="utf-8").read())

Weights are integer grams (``None`` = not recorded), exactly like the device.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# --------------------------------------------------------------------------
# Constants copied from lps_companion.cpp (v1.11)
# --------------------------------------------------------------------------
COMPANION_VERSION = "1.11"
ACTIVITIES_FR = ["Marche", "Régime", "Bible", "Messe", "Travail", "Projet", "Sieste", "Gurumed", "Maladie",
                 "Congé", "Weekend", "Sortie", "Jeu", "Docteur", "Lecture", "Shopping"]
ACTIVITIES_EN = ["Walk", "Diet", "Bible", "Mass", "Work", "Project", "Nap", "Gurumed", "Illness",
                 "Day off", "Weekend", "Outing", "Gaming", "Doctor", "Reading", "Shopping"]
ACTIVITY_COUNT = len(ACTIVITIES_FR)
ACTIVITY_MASK = (1 << ACTIVITY_COUNT) - 1
PAGE_SIZE = 10
GIFTS = [  # (French name, English name, XP threshold)
    ("Carnet de poche", "Pocket notebook", 30),
    ("Tasse de thé", "Cup of tea", 70),
    ("Boussole", "Compass", 120),
    ("Radio de poche", "Pocket radio", 180),
    ("Mini-ordinateur", "Mini computer", 250),
    ("Lanterne", "Lantern", 330),
]
NOTE_LENGTH = 96
HISTORY_LENGTH = 31
TASK_CAPACITY = 24
TASK_NAME_LENGTH = 32
REMINDER_CAPACITY = 3
REMINDER_TEXT_LENGTH = 32
FIRST_DATE = _dt.date(2000, 1, 1)
LAST_DATE = _dt.date(2099, 12, 31)
DEFAULT_DATE = _dt.date(2026, 9, 17)
TASK_STATUSES = ("todo", "doing", "done")
PALETTES = ("red", "green", "blue", "orange", "pink")  # PAL1..PAL5, PAL2 green default
LANGUAGES = ("en", "fr")

ICS_SUFFIX = "_LPS-Companion.ics"
CSV_HEADER = "date,expected_kg,measured_kg"


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
def count(flags: int) -> int:
    return bin(flags & 0xFFFF).count("1")


def points(flags: int) -> int:
    """10 XP per checked activity, +10 bonus for at least three."""
    n = count(flags)
    return n * 10 + (10 if n >= 3 else 0)


def unlocked_gifts(before_xp: int, after_xp: int) -> List[int]:
    return [i for i, g in enumerate(GIFTS) if before_xp < g[2] <= after_xp]


def activity_name(index: int, language: str = "en") -> str:
    return (ACTIVITIES_FR if language == "fr" else ACTIVITIES_EN)[index]


def valid_date(d: Optional[_dt.date]) -> bool:
    return isinstance(d, _dt.date) and FIRST_DATE <= d <= LAST_DATE


def parse_date(text: str) -> Optional[_dt.date]:
    """Strict ``YYYY-MM-DD`` in 2000-01-01..2099-12-31, like Companion parseDate()."""
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    if not all(c.isdigit() and c.isascii() for i, c in enumerate(text) if i not in (4, 7)):
        return None
    try:
        d = _dt.date(int(text[:4]), int(text[5:7]), int(text[8:]))
    except ValueError:
        return None
    return d if valid_date(d) else None


def next_date(d: _dt.date) -> Optional[_dt.date]:
    if not valid_date(d) or d == LAST_DATE:
        return None
    return d + _dt.timedelta(days=1)


def date_text(d: Optional[_dt.date]) -> str:
    return d.isoformat() if d else ""


_WEIGHT_RE = re.compile(r"^([0-9]{1,3})(?:[.,]([0-9]{1,3}))?$")


def parse_weight(text: str) -> Optional[int]:
    """'' -> None (not recorded); '110,5' / '110.500' -> 110500 grams.

    Up to 3 integer digits and 3 decimals, must be > 0. Raises ValueError otherwise.
    """
    text = text.strip()
    if not text:
        return None
    m = _WEIGHT_RE.match(text)
    if not m:
        raise ValueError("invalid weight %r" % text)
    grams = int(m.group(1)) * 1000 + int((m.group(2) or "").ljust(3, "0") or 0)
    if grams <= 0:
        raise ValueError("invalid weight %r" % text)
    return grams


def format_weight(grams: Optional[int]) -> str:
    """Companion weightText(): '110.500'; None -> '' (use '--' for display)."""
    if grams is None or grams < 0:
        return ""
    return "%d.%03d" % (grams // 1000, grams % 1000)


def day_index(d: _dt.date) -> int:
    return d.toordinal()


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------
@dataclass
class Day:
    date: Optional[_dt.date] = None
    number: int = 1
    flags: int = 0
    expected: Optional[int] = None  # grams
    actual: Optional[int] = None    # grams
    note: str = ""
    source: str = "console"         # console | ics | save | csv

    def activities(self) -> List[int]:
        return [i for i in range(ACTIVITY_COUNT) if self.flags & (1 << i)]

    def to_dict(self) -> dict:
        return {"date": date_text(self.date) or None, "number": self.number, "flags": self.flags,
                "expected": self.expected, "actual": self.actual, "note": self.note, "source": self.source}

    @classmethod
    def from_dict(cls, d: dict) -> "Day":
        return cls(date=parse_date(d["date"]) if d.get("date") else None, number=int(d.get("number", 1)),
                   flags=int(d.get("flags", 0)) & ACTIVITY_MASK, expected=d.get("expected"), actual=d.get("actual"),
                   note=str(d.get("note", ""))[:NOTE_LENGTH], source=d.get("source", "console"))


@dataclass
class Task:
    status: str = "todo"
    name: str = ""


@dataclass
class Reminder:
    start: _dt.date = DEFAULT_DATE
    duration: int = 1
    text: str = ""

    def expired(self, today: Optional[_dt.date]) -> bool:
        return valid_date(today) and day_index(today) >= day_index(self.start) + self.duration

    def active(self, today: Optional[_dt.date]) -> bool:
        return valid_date(today) and day_index(today) >= day_index(self.start) and not self.expired(today)


# --------------------------------------------------------------------------
# ICS (one all-day VEVENT per closed day), byte-identical to PicoCalc::exportIcs
# --------------------------------------------------------------------------
def ics_escape(text: str) -> str:
    out = []
    for c in text:
        if c in "\\;,":
            out.append("\\" + c)
        elif c == "\n":
            out.append("\\n")
        else:
            out.append(c)
    return "".join(out)


def ics_unescape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            n = text[i + 1]
            out.append("\n" if n in "nN" else n)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def ics_filename(d: _dt.date) -> str:
    return d.isoformat() + ICS_SUFFIX


def ics_text(day: Day, total_xp: int, day_xp: int, language: str = "en") -> str:
    """Return the exact text PicoCalc::exportIcs writes (CRLF line ends, UTF-8)."""
    if not valid_date(day.date):
        raise ValueError("ICS export needs a valid date")
    tomorrow = next_date(day.date)
    if tomorrow is None:
        raise ValueError("no day after 2099-12-31")
    en = language != "fr"
    ymd, nxt = day.date.strftime("%Y%m%d"), tomorrow.strftime("%Y%m%d")
    desc = ics_escape("Activities: " if en else "Activités: ")
    names = [ics_escape(activity_name(i, "en" if en else "fr")) for i in day.activities()]
    desc += ", ".join(names) if names else "none"   # separator written unescaped, like the device
    desc += "\\NXP: +%d (total %d)" % (day_xp, total_xp)
    if day.expected is not None and day.expected >= 0:
        desc += "\\N" + ics_escape("%s: %s kg" % ("Expected weight" if en else "Poids attendu", format_weight(day.expected)))
    if day.actual is not None and day.actual >= 0:
        desc += "\\N" + ics_escape("%s: %s kg" % ("Actual weight" if en else "Poids effectif", format_weight(day.actual)))
    if day.note:
        desc += "\\NNote: " + ics_escape(day.note)
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//LPS Companion//EN\r\nCALSCALE:GREGORIAN\r\nBEGIN:VEVENT\r\n"
            "UID:lps-companion-%s@picocalc\r\nDTSTART;VALUE=DATE:%s\r\nDTEND;VALUE=DATE:%s\r\n"
            "SUMMARY:LPS Companion\r\nDESCRIPTION:%s\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n") % (ymd, ymd, nxt, desc)


def write_ics(export_dir, day: Day, total_xp: int, day_xp: int, language: str = "en") -> Path:
    """Write/replace ``<export_dir>/YYYY-MM-DD_LPS-Companion.ics`` atomically."""
    path = Path(export_dir) / ics_filename(day.date)
    atomic_write_bytes(path, ics_text(day, total_xp, day_xp, language).encode("utf-8"))
    return path


@dataclass
class IcsDay:
    date: _dt.date
    flags: int = 0
    day_xp: Optional[int] = None
    total_xp: Optional[int] = None
    expected: Optional[int] = None
    actual: Optional[int] = None
    note: str = ""
    language: Optional[str] = None
    unknown_activities: List[str] = field(default_factory=list)

    def to_day(self, number: int = 1, source: str = "ics") -> Day:
        return Day(date=self.date, number=number, flags=self.flags, expected=self.expected, actual=self.actual,
                   note=self.note[:NOTE_LENGTH], source=source)


_ACTIVITY_LOOKUP = {}
for _i, (_fr, _en) in enumerate(zip(ACTIVITIES_FR, ACTIVITIES_EN)):
    _ACTIVITY_LOOKUP[_fr.casefold()] = _i
    _ACTIVITY_LOOKUP[_en.casefold()] = _i
_KG_RE = re.compile(r"^([0-9]{1,3}(?:[.,][0-9]{1,3})?) ?kg$")


def _unfold(text: str) -> List[str]:
    lines: List[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _split_description(value: str) -> List[str]:
    """Split on escaped line breaks (\\N or \\n) and unescape each line."""
    parts, cur, i = [], [], 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            n = value[i + 1]
            if n in "nN":
                parts.append("".join(cur))
                cur = []
            else:
                cur.append(n)
            i += 2
        else:
            cur.append(c)
            i += 1
    parts.append("".join(cur))
    return parts


def parse_ics(text: str) -> List[IcsDay]:
    """Parse LPS Companion ICS text (one or more VEVENTs). Non-LPS lines are ignored."""
    events: List[IcsDay] = []
    cur: Optional[dict] = None
    for line in _unfold(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            cur = {}
        elif upper == "END:VEVENT":
            if cur is not None and cur.get("date"):
                events.append(_event_from_props(cur))
            cur = None
        elif cur is not None and ":" in line:
            name, value = line.split(":", 1)
            key = name.split(";", 1)[0].upper()
            if key == "DTSTART":
                v = value.strip()[:8]
                try:
                    d = _dt.date(int(v[:4]), int(v[4:6]), int(v[6:8]))
                    cur["date"] = d if valid_date(d) else None
                except ValueError:
                    cur["date"] = None
            elif key == "DESCRIPTION":
                cur["description"] = value
    return events


def _event_from_props(props: dict) -> IcsDay:
    ev = IcsDay(date=props["date"])
    for line in _split_description(props.get("description", "")):
        head, _, rest = line.partition(": ")
        h = head.strip().casefold()
        if h in ("activities", "activités"):
            ev.language = "fr" if h == "activités" else "en"
            if rest.strip().casefold() != "none":
                for name in (n.strip() for n in rest.split(",")):
                    if not name:
                        continue
                    idx = _ACTIVITY_LOOKUP.get(name.casefold())
                    if idx is None:
                        ev.unknown_activities.append(name)
                    else:
                        ev.flags |= 1 << idx
        elif line.startswith("XP: "):
            m = re.match(r"XP: \+?(\d+) \(total (\d+)\)", line)
            if m:
                ev.day_xp, ev.total_xp = int(m.group(1)), int(m.group(2))
        elif h in ("expected weight", "poids attendu", "actual weight", "poids effectif"):
            m = _KG_RE.match(rest.strip())
            if m:
                grams = parse_weight(m.group(1))
                if h in ("expected weight", "poids attendu"):
                    ev.expected = grams
                else:
                    ev.actual = grams
        elif head == "Note":
            ev.note = rest
    return ev


def read_ics_dir(export_dir) -> Tuple[List[IcsDay], List[str]]:
    """Parse every ``*_LPS-Companion.ics`` (any *.ics) in a folder. Returns (days, errors)."""
    days, errors = [], []
    for p in sorted(Path(export_dir).glob("*.ics")):
        try:
            days.extend(parse_ics(p.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, ValueError) as ex:
            errors.append("%s: %s" % (p.name, ex))
    return days, errors


# --------------------------------------------------------------------------
# Weights CSV: LPS/WEIGHTS/YYYY.csv, "date,expected_kg,measured_kg", CRLF
# --------------------------------------------------------------------------
def weight_csv_path(weights_dir, year: int) -> Path:
    return Path(weights_dir) / ("%04d.csv" % year)


def weight_csv_line(d: _dt.date, expected: Optional[int], actual: Optional[int]) -> str:
    return "%s,%s,%s\r\n" % (d.isoformat(), format_weight(expected), format_weight(actual))


def append_weight_csv(weights_dir, d: _dt.date, expected: Optional[int], actual: Optional[int]) -> bool:
    """Same semantics as PicoCalc::exportWeightCsv: header on a new file, one row
    per date, a date already present is treated as exported (returns False).
    The file is rewritten atomically (existing bytes kept verbatim)."""
    if not valid_date(d):
        raise ValueError("weights CSV needs a valid date")
    path = weight_csv_path(weights_dir, d.year)
    existing = path.read_bytes() if path.exists() else b""
    key = d.isoformat().encode() + b","
    for i, line in enumerate(existing.split(b"\n")):
        if line.startswith(key):
            return False
    data = existing if existing else (CSV_HEADER + "\r\n").encode()
    atomic_write_bytes(path, data + weight_csv_line(d, expected, actual).encode())
    return True


def parse_weight_csv(text: str) -> Tuple[List[Tuple[_dt.date, Optional[int], Optional[int]]], List[str]]:
    rows, errors = [], []
    for n, raw in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        line = raw.strip()
        if not line or line.lower().startswith("date,"):
            continue
        cols = line.split(",")
        if len(cols) != 3:
            errors.append("line %d: expected 3 columns" % n)
            continue
        d = parse_date(cols[0].strip())
        try:
            exp, act = parse_weight(cols[1]), parse_weight(cols[2])
        except ValueError as ex:
            errors.append("line %d: %s" % (n, ex))
            continue
        if d is None:
            errors.append("line %d: invalid date %r" % (n, cols[0]))
            continue
        rows.append((d, exp, act))
    return rows, errors


# --------------------------------------------------------------------------
# SAVE_A.BIN / SAVE_B.BIN (read-only). Record = magic 'LPR1', generation,
# payload size, payload (wire format 1-6, little endian, own CRC32), CRC32.
# --------------------------------------------------------------------------
RECORD_MAGIC = 0x3152504C
PAYLOAD_MAGIC = 0x3153504C
WIRE_V1 = 3568
WIRE_V2 = WIRE_V1 + 1
WIRE_V3 = WIRE_V2 + 32 * 4
WIRE_V4 = WIRE_V3 + 1
WIRE_V5 = WIRE_V4 + 1 + TASK_CAPACITY * (1 + TASK_NAME_LENGTH + 1)
WIRE_V6 = WIRE_V5 + 1 + REMINDER_CAPACITY * (2 + 1 + 1 + 2 + REMINDER_TEXT_LENGTH + 1)
WIRE_SIZES = {1: WIRE_V1, 2: WIRE_V2, 3: WIRE_V3, 4: WIRE_V4, 5: WIRE_V5, 6: WIRE_V6}


def crc32(data: bytes) -> int:
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF


class SaveFormatError(ValueError):
    pass


@dataclass
class CompanionSave:
    version: int
    language: str
    palette: int
    xp: int
    today: Day
    history: List[Day]          # oldest first (closure order)
    tasks: List[Task]
    reminders: List[Reminder]
    generation: int = 0
    slot: str = ""


def _cstr(raw: bytes) -> str:
    if raw[-1:] != b"\0":
        raise SaveFormatError("unterminated string")
    s = raw.split(b"\0", 1)[0]
    if any(c < 32 or c > 126 for c in s):
        raise SaveFormatError("non-printable text")
    return s.decode("ascii")


def decode_payload(b: bytes) -> CompanionSave:
    """Port of lps::decode(): validates size, CRC, magic, version and every field."""
    size = len(b)
    if size not in WIRE_SIZES.values():
        raise SaveFormatError("unexpected payload size %d" % size)
    if struct.unpack_from("<I", b, size - 4)[0] != crc32(b[:-4]):
        raise SaveFormatError("payload CRC mismatch")
    pos = 0

    def get(n: int) -> int:
        nonlocal pos
        v = int.from_bytes(b[pos:pos + n], "little")
        pos += n
        return v

    def raw(n: int) -> bytes:
        nonlocal pos
        v = b[pos:pos + n]
        pos += n
        return v

    if get(4) != PAYLOAD_MAGIC:
        raise SaveFormatError("bad payload magic")
    version = get(2)
    if WIRE_SIZES.get(version) != size:
        raise SaveFormatError("version %d does not match size %d" % (version, size))
    xp, nxt, cnt = get(4), get(1), get(1)
    language = "en"
    palette = 1
    if version >= 2:
        lang = get(1)
        if lang > 1:
            raise SaveFormatError("bad language")
        language = LANGUAGES[lang]
    if version >= 4:
        palette = get(1)
        if palette > 4:
            raise SaveFormatError("bad palette")
    if not (nxt < HISTORY_LENGTH and cnt <= HISTORY_LENGTH):
        raise SaveFormatError("bad history ring")

    def day() -> Day:
        d = None
        if version >= 3:
            y, m, dd = get(2), get(1), get(1)
            if y:
                try:
                    d = _dt.date(y, m, dd)
                except ValueError:
                    raise SaveFormatError("bad date")
                if not valid_date(d):
                    raise SaveFormatError("bad date")
        number, flags = get(4), get(2)

        def w():
            v = get(4)
            if v == 0xFFFFFFFF:
                return None
            if v == 0 or v > 999999:
                raise SaveFormatError("bad weight")
            return v
        exp, act = w(), w()
        note = _cstr(raw(NOTE_LENGTH + 1))
        if number == 0 or flags & ~ACTIVITY_MASK:
            raise SaveFormatError("bad day")
        return Day(date=d, number=number, flags=flags, expected=exp, actual=act, note=note, source="save")

    today = day()
    ring = [day() for _ in range(HISTORY_LENGTH)]
    tasks: List[Task] = []
    if version >= 5:
        tc = get(1)
        if tc > TASK_CAPACITY:
            raise SaveFormatError("bad task count")
        for i in range(TASK_CAPACITY):
            st = get(1)
            if st > 2:
                raise SaveFormatError("bad task status")
            name = _cstr(raw(TASK_NAME_LENGTH + 1))
            if i < tc:
                if not name:
                    raise SaveFormatError("empty task")
                tasks.append(Task(TASK_STATUSES[st], name))
    reminders: List[Reminder] = []
    if version >= 6:
        rc = get(1)
        if rc > REMINDER_CAPACITY:
            raise SaveFormatError("bad reminder count")
        for i in range(REMINDER_CAPACITY):
            y, m, dd, dur = get(2), get(1), get(1), get(2)
            text = _cstr(raw(REMINDER_TEXT_LENGTH + 1))
            if i < rc:
                try:
                    start = _dt.date(y, m, dd)
                except ValueError:
                    raise SaveFormatError("bad reminder date")
                if not valid_date(start) or not 1 <= dur <= 365 or not text:
                    raise SaveFormatError("bad reminder")
                reminders.append(Reminder(start, dur, text))
    # Ring order: oldest .. newest closed day.
    history = [ring[(nxt - cnt + i) % HISTORY_LENGTH] for i in range(cnt)]
    return CompanionSave(version, language, palette, xp, today, history, tasks, reminders)


def read_save_record(path) -> CompanionSave:
    data = Path(path).read_bytes()
    if len(data) - 16 not in WIRE_SIZES.values():
        raise SaveFormatError("unexpected record size %d" % len(data))
    magic, generation, size = struct.unpack_from("<III", data, 0)
    if magic != RECORD_MAGIC or size != len(data) - 16:
        raise SaveFormatError("bad record header")
    if struct.unpack_from("<I", data, len(data) - 4)[0] != crc32(data[:-4]):
        raise SaveFormatError("record CRC mismatch")
    save = decode_payload(data[12:-4])
    save.generation = generation
    return save


def load_companion_save(lps_dir) -> Tuple[Optional[CompanionSave], List[str]]:
    """Pick the highest-generation valid SAVE_A/B.BIN, like the firmware."""
    best, errors = None, []
    for slot in ("A", "B"):
        p = Path(lps_dir) / ("SAVE_%s.BIN" % slot)
        if not p.exists():
            continue
        try:
            s = read_save_record(p)
            s.slot = slot
            if best is None or s.generation > best.generation:
                best = s
        except (OSError, SaveFormatError) as ex:
            errors.append("%s: %s" % (p.name, ex))
    return best, errors


# --------------------------------------------------------------------------
# Atomic file helpers
# --------------------------------------------------------------------------
def atomic_write_bytes(path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


# --------------------------------------------------------------------------
# LPS Console local state
# --------------------------------------------------------------------------
STATE_FORMAT = "lps-console/1"


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "lps-console"


@dataclass
class State:
    language: str = "en"
    palette: int = 1
    xp: int = 0
    today: Day = field(default_factory=lambda: Day(date=DEFAULT_DATE))
    days: List[Day] = field(default_factory=list)       # closed days, sorted by date
    tasks: List[Task] = field(default_factory=list)
    reminders: List[Reminder] = field(default_factory=list)
    weights: Dict[str, List[Optional[int]]] = field(default_factory=dict)  # 'YYYY-MM-DD' -> [exp, act]
    settings: Dict[str, object] = field(default_factory=dict)  # e.g. import_dir, import_save

    # ---- serialisation
    def to_dict(self) -> dict:
        return {"format": STATE_FORMAT, "language": self.language, "palette": self.palette, "xp": self.xp,
                "today": self.today.to_dict(), "days": [d.to_dict() for d in self.days],
                "tasks": [{"status": t.status, "name": t.name} for t in self.tasks],
                "reminders": [{"from": r.start.isoformat(), "duration": r.duration, "text": r.text}
                              for r in self.reminders],
                "weights": self.weights, "settings": self.settings}

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        if d.get("format") != STATE_FORMAT:
            raise ValueError("unknown state format %r" % d.get("format"))
        s = cls(language=d.get("language", "en") if d.get("language") in LANGUAGES else "en",
                palette=int(d.get("palette", 1)) % len(PALETTES), xp=int(d.get("xp", 0)),
                today=Day.from_dict(d["today"]), days=[Day.from_dict(x) for x in d.get("days", [])],
                tasks=[Task(t["status"], t["name"][:TASK_NAME_LENGTH]) for t in d.get("tasks", [])
                       if t.get("status") in TASK_STATUSES][:TASK_CAPACITY],
                reminders=[Reminder(parse_date(r["from"]) or DEFAULT_DATE, int(r["duration"]), r["text"])
                           for r in d.get("reminders", [])][:REMINDER_CAPACITY],
                weights={k: list(v) for k, v in d.get("weights", {}).items()},
                settings=dict(d.get("settings", {})))
        s.sort_days()
        return s

    def copy(self) -> "State":
        return State.from_dict(json.loads(json.dumps(self.to_dict())))

    # ---- helpers
    def sort_days(self) -> None:
        self.days.sort(key=lambda x: (x.date or _dt.date.min, x.number))

    def day_for(self, d: _dt.date) -> Optional[Day]:
        for x in self.days:
            if x.date == d:
                return x
        return None

    def history(self) -> List[Day]:
        """Read-only browsable history: the last 31 closed days, oldest first."""
        return self.days[-HISTORY_LENGTH:]

    def prune_reminders(self) -> bool:
        kept = [r for r in self.reminders if not r.expired(self.today.date)]
        changed = len(kept) != len(self.reminders)
        self.reminders = kept
        return changed

    def active_reminders(self) -> List[Reminder]:
        return [r for r in self.reminders if r.active(self.today.date)]


class Store:
    """JSON state file with atomic replacement and one backup generation."""

    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.path = self.data_dir / "state.json"
        self.blocked = False

    @property
    def lps_dir(self) -> Path:
        return self.data_dir / "LPS"

    def load(self, new_today: Optional[_dt.date] = None) -> State:
        if not self.path.exists():
            return State(today=Day(date=new_today or DEFAULT_DATE))
        try:
            return State.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError) as ex:
            bak = self.path.with_suffix(".json.bak")
            try:
                st = State.from_dict(json.loads(bak.read_text(encoding="utf-8")))
            except Exception:
                self.blocked = True  # never silently overwrite a damaged save
                raise ValueError("unreadable state %s: %s" % (self.path, ex))
            return st

    def save(self, state: State) -> None:
        if self.blocked:
            raise IOError("writes blocked: unreadable save")
        data = json.dumps(state.to_dict(), ensure_ascii=False, indent=1).encode("utf-8")
        if self.path.exists():
            atomic_write_bytes(self.path.with_suffix(".json.bak"), self.path.read_bytes())
        atomic_write_bytes(self.path, data)


@dataclass
class CloseResult:
    gain: int
    unlocked: List[int]
    ics_path: Path
    csv_written: bool
    state: State


class CloseError(Exception):
    pass


def close_day(state: State, export_lps_dir, language: Optional[str] = None) -> CloseResult:
    """Companion Finish/Enter: export weights CSV, then ICS, then bank XP and advance.

    Returns a NEW state (the caller commits it); exports are idempotent per date."""
    today = state.today
    gain = points(today.flags)
    if not valid_date(today.date):
        raise CloseError("date")
    tomorrow = next_date(today.date)
    if tomorrow is None:
        raise CloseError("calendar_end")
    lps_dir = Path(export_lps_dir)
    csv_written = append_weight_csv(lps_dir / "WEIGHTS", today.date, today.expected, today.actual)
    ics = write_ics(lps_dir / "EXPORT", today, state.xp + gain, gain, language or state.language)
    nxt = state.copy()
    closed = Day.from_dict(today.to_dict())
    closed.source = "console"
    nxt.days = [d for d in nxt.days if d.date != closed.date] + [closed]
    nxt.sort_days()
    nxt.weights[today.date.isoformat()] = [today.expected, today.actual]
    nxt.xp = state.xp + gain
    nxt.today = Day(date=tomorrow, number=today.number + 1)
    return CloseResult(gain, unlocked_gifts(state.xp, nxt.xp), ics, csv_written, nxt)


# --------------------------------------------------------------------------
# Import / merge from a Companion SD card
# --------------------------------------------------------------------------
@dataclass
class ImportReport:
    source: str = ""
    days_added: List[str] = field(default_factory=list)
    days_skipped: List[str] = field(default_factory=list)
    weights_added: List[str] = field(default_factory=list)
    weights_skipped: List[str] = field(default_factory=list)
    xp_added: int = 0
    tasks_imported: int = 0
    reminders_imported: int = 0
    save_used: str = ""
    errors: List[str] = field(default_factory=list)

    def summary(self, language: str = "en") -> List[str]:
        fr = language == "fr"
        lines = [("Source : %s" if fr else "Source: %s") % self.source,
                 ("Jours importés : %d (déjà présents : %d)" if fr else "Days imported: %d (already present: %d)")
                 % (len(self.days_added), len(self.days_skipped)),
                 ("Poids CSV ajoutés : %d (déjà connus : %d)" if fr else "CSV weights added: %d (already known: %d)")
                 % (len(self.weights_added), len(self.weights_skipped)),
                 ("XP ajoutés : +%d" if fr else "XP added: +%d") % self.xp_added]
        if self.save_used:
            lines.append(("Sauvegarde lue : %s (Kanban %d, rappels %d)" if fr else
                          "Save read: %s (Kanban %d, reminders %d)") % (self.save_used, self.tasks_imported,
                                                                       self.reminders_imported))
        lines += [("Erreur : %s" if fr else "Error: %s") % e for e in self.errors]
        return lines


def find_lps_dir(path) -> Path:
    """Accept an SD card root (containing LPS/) or the LPS folder itself."""
    p = Path(path).expanduser()
    if (p / "LPS").is_dir():
        return p / "LPS"
    return p


def import_companion(state: State, path, include_save: bool = False) -> Tuple[State, ImportReport]:
    """Merge Companion exports into a copy of ``state`` (no duplicates by date).

    * ICS days whose date is not yet closed locally are added as closed days and
      their XP (Companion rules, recomputed from the activities) is banked.
    * Weight CSV rows fill the weights journal and missing weights of closed days.
    * With ``include_save``, dated history days from SAVE_A/B.BIN are merged the
      same way; Kanban tasks and reminders are imported only if empty locally.
    Local data always wins over imported data."""
    lps_dir = find_lps_dir(path)
    rep = ImportReport(source=str(lps_dir))
    st = state.copy()
    if not lps_dir.is_dir():
        rep.errors.append("not a folder: %s" % lps_dir)
        return st, rep
    known = {d.date for d in st.days if d.date}
    next_number = max([d.number for d in st.days] + [0]) + 1

    def add_day(day: Day):
        nonlocal next_number
        key = day.date.isoformat()
        if day.date in known:
            rep.days_skipped.append(key)
            return
        day.number = next_number
        next_number += 1
        st.days.append(day)
        known.add(day.date)
        gain = points(day.flags)
        st.xp += gain
        rep.xp_added += gain
        rep.days_added.append(key)
        if (day.expected is not None or day.actual is not None) and key not in st.weights:
            st.weights[key] = [day.expected, day.actual]

    ics_days, errs = read_ics_dir(lps_dir / "EXPORT") if (lps_dir / "EXPORT").is_dir() else ([], [])
    rep.errors += errs
    for ev in sorted(ics_days, key=lambda e: e.date):
        if ev.unknown_activities:
            rep.errors.append("%s: unknown activities %s" % (ev.date, ", ".join(ev.unknown_activities)))
        add_day(ev.to_day())
    if include_save:
        save, errs = load_companion_save(lps_dir)
        rep.errors += errs
        if save:
            rep.save_used = "SAVE_%s.BIN v%d gen %d" % (save.slot, save.version, save.generation)
            for d in save.history:
                if d.date:
                    add_day(Day.from_dict(d.to_dict()))
            if not st.tasks and save.tasks:
                st.tasks = list(save.tasks)
                rep.tasks_imported = len(save.tasks)
            if not st.reminders and save.reminders:
                st.reminders = list(save.reminders)
                rep.reminders_imported = len(save.reminders)
    wdir = lps_dir / "WEIGHTS"
    if wdir.is_dir():
        for p in sorted(wdir.glob("*.csv")):
            try:
                rows, errs = parse_weight_csv(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as ex:
                rep.errors.append("%s: %s" % (p.name, ex))
                continue
            rep.errors += ["%s %s" % (p.name, e) for e in errs]
            for d, exp, act in rows:
                key = d.isoformat()
                if key in st.weights:
                    rep.weights_skipped.append(key)
                else:
                    st.weights[key] = [exp, act]
                    rep.weights_added.append(key)
                day = st.day_for(d)
                if day is not None:
                    if day.expected is None:
                        day.expected = exp
                    if day.actual is None:
                        day.actual = act
    st.sort_days()
    return st, rep
