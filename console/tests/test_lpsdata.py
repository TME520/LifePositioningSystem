"""Unit tests for lpsconsole.lpsdata against REAL LPS Companion output.

tests/fixtures/sdcard/LPS was produced by the Companion's own PicoCalc adapter
(src/main.cpp) compiled on the host, see tests/fixtures/gen_fixtures.cpp.
"""
import datetime as dt
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lpsconsole import lpsdata as L  # noqa: E402

SD = HERE / "fixtures" / "sdcard"
LPS = SD / "LPS"
D = dt.date


def fixture_days():
    """The days gen_fixtures.cpp exported, as (Day, total_xp, day_xp, language)."""
    return [
        (L.Day(D(2026, 10, 1), flags=0b111, expected=110500, actual=111200, note="Good day, rain; path C:\\tmp"), 140, 40, "en"),
        (L.Day(D(2026, 10, 2), flags=(1 << 1) | (1 << 9) | (1 << 14) | (1 << 15), actual=109750, note="Journee calme"), 190, 50, "fr"),
        (L.Day(D(2026, 12, 31)), 190, 0, "en"),
        (L.Day(D(2026, 10, 5), flags=(1 << 0) | (1 << 2) | (1 << 4), expected=100500, actual=101250, note="Saved on PicoCalc"), 40, 40, "en"),
    ]


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lpsc-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class RulesTest(unittest.TestCase):
    def test_points_and_bonus(self):
        self.assertEqual(L.points(0), 0)
        self.assertEqual(L.points(0b11), 20)
        self.assertEqual(L.points(0b111), 40)  # README example: three activities -> 40 XP
        self.assertEqual(L.points(L.ACTIVITY_MASK), 170)

    def test_gifts(self):
        self.assertEqual(L.unlocked_gifts(0, 40), [0])
        self.assertEqual(L.unlocked_gifts(60, 130), [1, 2])
        self.assertEqual(L.unlocked_gifts(330, 400), [])

    def test_weight_parsing_like_companion(self):
        self.assertEqual(L.parse_weight("110.5"), 110500)
        self.assertEqual(L.parse_weight("110,500"), 110500)
        self.assertIsNone(L.parse_weight(""))
        for bad in ("1000", "1.2345", "0", "0.000", "abc", "1.", ".5", "-1"):
            with self.assertRaises(ValueError, msg=bad):
                L.parse_weight(bad)
        self.assertEqual(L.format_weight(109750), "109.750")

    def test_dates(self):
        self.assertEqual(L.parse_date("2028-02-29"), D(2028, 2, 29))
        for bad in ("2027-02-29", "1999-12-31", "2100-01-01", "2026-1-01", "2026/01/01"):
            self.assertIsNone(L.parse_date(bad), bad)
        self.assertEqual(L.next_date(D(2026, 12, 31)), D(2027, 1, 1))
        self.assertIsNone(L.next_date(L.LAST_DATE))


class IcsTest(TempDir):
    def test_byte_identical_to_companion_exports(self):
        for day, total, gain, lang in fixture_days():
            expected = (LPS / "EXPORT" / L.ics_filename(day.date)).read_bytes()
            self.assertEqual(L.ics_text(day, total, gain, lang).encode("utf-8"), expected, day.date)

    def test_matches_companion_storage_test_strings(self):
        # Expectations copied from LPS-Companion-PicoCalc/tests/storage_test.cpp
        t = L.ics_text(L.Day(D(2028, 2, 29), flags=0b11), 20, 20, "en")
        self.assertIn("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n", t)
        self.assertIn("Activities: Walk, Diet\\NXP: +20 (total 20)", t)
        self.assertIn("DTEND;VALUE=DATE:20280301", t)
        fr = L.Day(D(2028, 3, 2), flags=(1 << 1) | (1 << 9), expected=110000, actual=109500, note="comma, semi; slash\\")
        self.assertIn("Activités: Régime, Congé\\NXP: +40 (total 60)\\NPoids attendu: 110.000 kg\\N"
                      "Poids effectif: 109.500 kg\\NNote: comma\\, semi\\; slash\\\\", L.ics_text(fr, 60, 40, "fr"))

    def test_parse_companion_exports(self):
        days, errors = L.read_ics_dir(LPS / "EXPORT")
        self.assertEqual(errors, [])
        by_date = {d.date: d for d in days}
        for day, total, gain, lang in fixture_days():
            ev = by_date[day.date]
            self.assertEqual((ev.flags, ev.expected, ev.actual, ev.note, ev.day_xp, ev.total_xp, ev.language),
                             (day.flags, day.expected, day.actual, day.note, gain, total, lang), day.date)
            self.assertEqual(ev.unknown_activities, [])

    def test_round_trip(self):
        notes = ["", "plain", "a,b;c\\d", "trailing backslash\\", "x" * 96, "Note: inside, \\N literal"]
        for i, note in enumerate(notes):
            for lang in ("en", "fr"):
                day = L.Day(D(2027, 1, 1) + dt.timedelta(days=i), flags=(0x5A5A >> i) & L.ACTIVITY_MASK,
                            expected=None if i % 2 else 99999 + i, actual=123456 if i % 3 else None, note=note)
                path = L.write_ics(self.tmp, day, 500, L.points(day.flags), lang)
                ev, = L.parse_ics(path.read_text(encoding="utf-8"))
                self.assertEqual((ev.date, ev.flags, ev.expected, ev.actual, ev.note, ev.day_xp),
                                 (day.date, day.flags, day.expected, day.actual, day.note, L.points(day.flags)))

    def test_parse_tolerates_folding_and_lf(self):
        text = L.ics_text(L.Day(D(2026, 5, 5), flags=0b1, note="hello world"), 10, 10, "en").replace("\r\n", "\n")
        folded = text.replace("Walk\\NXP", "Walk\\N\n XP")
        ev, = L.parse_ics(folded)
        self.assertEqual((ev.flags, ev.day_xp, ev.note), (1, 10, "hello world"))


class CsvTest(TempDir):
    def test_append_reproduces_companion_file(self):
        wdir = self.tmp / "WEIGHTS"
        order = [fixture_days()[i][0] for i in (0, 1, 2)]
        for d in order:
            self.assertTrue(L.append_weight_csv(wdir, d.date, d.expected, d.actual))
        self.assertFalse(L.append_weight_csv(wdir, order[1].date, 1, 1))  # duplicate date ignored
        last = fixture_days()[3][0]
        L.append_weight_csv(wdir, last.date, last.expected, last.actual)
        self.assertEqual((wdir / "2026.csv").read_bytes(), (LPS / "WEIGHTS" / "2026.csv").read_bytes())

    def test_parse(self):
        rows, errors = L.parse_weight_csv((LPS / "WEIGHTS" / "2026.csv").read_text())
        self.assertEqual(errors, [])
        self.assertEqual(rows, [(D(2026, 10, 1), 110500, 111200), (D(2026, 10, 2), None, 109750),
                                (D(2026, 12, 31), None, None), (D(2026, 10, 5), 100500, 101250)])
        rows, errors = L.parse_weight_csv("date,expected_kg,measured_kg\nbad,1,2\n2026-01-01,x,\n2026-01-02,80,\n")
        self.assertEqual(rows, [(D(2026, 1, 2), 80000, None)])
        self.assertEqual(len(errors), 2)


class SaveTest(TempDir):
    def test_decode_real_save(self):
        save, errors = L.load_companion_save(LPS)
        self.assertEqual(errors, [])
        self.assertEqual((save.slot, save.generation, save.version, save.xp, save.language), ("B", 12, 6, 40, "en"))
        self.assertEqual(save.today.date, D(2026, 10, 6))
        self.assertEqual(save.today.flags, 1 << 6)  # Nap checked after closing
        h, = save.history
        self.assertEqual((h.date, h.flags, h.expected, h.actual, h.note),
                         (D(2026, 10, 5), 0b10101, 100500, 101250, "Saved on PicoCalc"))
        self.assertEqual([(t.status, t.name) for t in save.tasks], [("todo", "Write console app"), ("doing", "Test fixtures")])
        self.assertEqual([(r.start, r.duration, r.text) for r in save.reminders], [(D(2026, 10, 5), 7, "Buy bread")])

    def test_damaged_newest_falls_back(self):
        shutil.copy(LPS / "SAVE_A.BIN", self.tmp / "SAVE_A.BIN")
        data = bytearray((LPS / "SAVE_B.BIN").read_bytes())
        data[100] ^= 0xFF
        (self.tmp / "SAVE_B.BIN").write_bytes(bytes(data))
        save, errors = L.load_companion_save(self.tmp)
        self.assertEqual(save.slot, "A")
        self.assertEqual(save.generation, 11)
        self.assertEqual(len(errors), 1)
        with self.assertRaises(L.SaveFormatError):
            L.read_save_record(self.tmp / "SAVE_B.BIN")


class StateTest(TempDir):
    def test_close_day_exports_and_banks(self):
        st = L.State(xp=20, today=L.Day(D(2026, 10, 1), flags=0b111, expected=110500, actual=111200,
                                       note="Good day, rain; path C:\\tmp"))
        st.xp = 100
        res = L.close_day(st, self.tmp)
        self.assertEqual(res.gain, 40)
        self.assertEqual(res.state.xp, 140)
        self.assertEqual(res.unlocked, [2])  # Compass at 120 XP
        self.assertEqual(res.state.today.date, D(2026, 10, 2))
        self.assertEqual(res.state.today.number, 2)
        self.assertEqual(res.ics_path.read_bytes(), (LPS / "EXPORT" / "2026-10-01_LPS-Companion.ics").read_bytes())
        self.assertTrue((self.tmp / "WEIGHTS" / "2026.csv").exists())
        self.assertEqual(st.xp, 100)  # original untouched until committed
        with self.assertRaises(L.CloseError):
            L.close_day(L.State(today=L.Day(None)), self.tmp)
        with self.assertRaises(L.CloseError):
            L.close_day(L.State(today=L.Day(L.LAST_DATE)), self.tmp)

    def test_store_atomic_and_backup(self):
        store = L.Store(self.tmp)
        st = store.load(new_today=D(2026, 10, 20))
        self.assertEqual(st.today.date, D(2026, 10, 20))
        st.tasks.append(L.Task("doing", "x"))
        store.save(st)
        store.save(st)
        self.assertTrue((self.tmp / "state.json.bak").exists())
        self.assertEqual(L.Store(self.tmp).load().tasks[0].name, "x")
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.endswith(".tmp")], [])
        (self.tmp / "state.json").write_text("{broken")
        self.assertEqual(L.Store(self.tmp).load().tasks[0].name, "x")  # backup used
        (self.tmp / "state.json.bak").write_text("{broken")
        s2 = L.Store(self.tmp)
        with self.assertRaises(ValueError):
            s2.load()
        with self.assertRaises(IOError):
            s2.save(st)

    def test_import_merges_without_duplicates(self):
        st = L.State(today=L.Day(D(2026, 10, 20)))
        st.days.append(L.Day(D(2026, 10, 1), flags=1, note="local wins"))
        st2, rep = L.import_companion(st, SD, include_save=True)
        # 2026-10-05 is both in EXPORT/ and in SAVE_B.BIN history: merged once.
        self.assertEqual(rep.days_skipped, ["2026-10-01", "2026-10-05"])
        self.assertEqual(sorted(rep.days_added), ["2026-10-02", "2026-10-05", "2026-12-31"])
        self.assertEqual(rep.xp_added, 50 + 40 + 0)
        self.assertEqual(st2.day_for(D(2026, 10, 1)).note, "local wins")
        self.assertEqual(st2.day_for(D(2026, 10, 1)).expected, 110500)  # filled from the CSV
        self.assertEqual(rep.tasks_imported, 2)
        self.assertEqual(rep.reminders_imported, 1)
        self.assertEqual(rep.errors, [])
        st3, rep3 = L.import_companion(st2, LPS, include_save=True)
        self.assertEqual(rep3.days_added, [])
        self.assertEqual(rep3.weights_added, [])
        self.assertEqual(rep3.xp_added, 0)
        self.assertEqual(len(st3.days), len(st2.days))
        self.assertEqual(len(st3.tasks), 2)
        self.assertEqual([d.date for d in st3.days], sorted(d.date for d in st3.days))

    def test_exports_of_console_import_back(self):
        st = L.State(today=L.Day(D(2027, 3, 1), flags=0b1011, actual=80500, note="from console"))
        res = L.close_day(st, self.tmp / "LPS")
        st2, rep = L.import_companion(L.State(), self.tmp)
        self.assertEqual(rep.days_added, ["2027-03-01"])
        d = st2.day_for(D(2027, 3, 1))
        self.assertEqual((d.flags, d.actual, d.note, st2.xp), (0b1011, 80500, "from console", res.gain))


if __name__ == "__main__":
    unittest.main()
