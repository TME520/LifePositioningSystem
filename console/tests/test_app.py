"""Behaviour tests for the LPS Console screen state machine (no curses)."""
import datetime as dt
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lpsconsole import lpsdata as L  # noqa: E402
from lpsconsole.app import App  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lpsc-app-"))
        self.store = L.Store(self.tmp)
        self.clock = FakeClock()
        self.app = App(self.store, self.store.load(new_today=dt.date(2026, 10, 1)), clock=self.clock)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def keys(self, *ks):
        for k in ks:
            self.app.key(k)

    def text(self):
        return "\n".join(t for _, t, _ in self.app.render())

    def test_activities_close_day_and_reward(self):
        self.keys("1", "1", "2", "3", "ESC")
        self.assertIn("3/16 activities - 40 XP pending", self.text())
        self.keys("UP", "ENTER")
        self.assertIn("Variety bonus: 10 XP", self.text())
        self.keys("ENTER")
        self.assertIn("NEW KEEPSAKE", self.text())
        self.assertIn("Pocket notebook", self.text())
        self.assertEqual(self.app.state.xp, 40)
        self.assertEqual(self.app.state.today.date, dt.date(2026, 10, 2))
        self.assertTrue((self.tmp / "LPS" / "EXPORT" / "2026-10-01_LPS-Companion.ics").exists())
        self.assertTrue((self.tmp / "LPS" / "WEIGHTS" / "2026.csv").exists())
        # persisted
        self.assertEqual(L.Store(self.tmp).load().xp, 40)

    def test_history_read_only(self):
        self.keys("1", "5", "ESC", "UP", "ENTER", "ENTER", "ENTER")
        self.keys("LEFT")
        self.assertIn("ARCHIVED DAY - READ ONLY", self.text())
        self.keys("1", "3")
        self.assertIn("read-only", self.text())
        self.assertEqual(self.app.state.days[0].flags, 1 << 4)
        self.keys("ESC", "LEFT")
        self.assertIn("Oldest saved day", self.text())
        self.keys("RIGHT")
        self.assertIn("TODAY", self.text())

    def test_weight_and_note(self):
        self.keys("3", *"110,5", "DOWN", *"111.2")
        self.assertIn("Difference: +0.700 kg", self.text())
        self.keys("ENTER", "2", *"Hello q world", "ENTER")
        self.assertEqual((self.app.state.today.expected, self.app.state.today.actual, self.app.state.today.note),
                         (110500, 111200, "Hello q world"))
        self.keys("2", *("x" * 120))
        self.assertEqual(len(self.app.draft), 96)

    def test_kanban(self):
        self.keys("4", "N", *"Task A", "ENTER", "N", *"Task B", "ENTER")
        self.assertEqual([t.name for t in self.app.state.tasks], ["Task A", "Task B"])
        self.keys("F2")
        self.assertEqual(self.app.kanban_column, "doing")
        self.keys("LEFT")
        self.assertIn("Task A", self.text())
        self.keys("x")
        self.assertEqual([t.name for t in self.app.state.tasks], ["Task B"])
        for i in range(30):
            self.keys("N", *("t%d" % i), "ENTER")
        self.assertEqual(len(self.app.state.tasks), 24)

    def test_reminders_popup_and_expiry(self):
        self.keys("8", "N", "DOWN", "BS", "3", "DOWN", *"Call mum", "ENTER")
        self.assertEqual(len(self.app.state.reminders), 1)
        app2 = App(self.store, self.store.load())
        self.assertEqual(app2.screen, "ReminderPopup")
        self.assertIn("1. Call mum", "\n".join(t for _, t, _ in app2.render()))
        st = self.store.load()
        st.today.date = dt.date(2026, 10, 4)
        self.store.save(st)
        app3 = App(self.store, self.store.load())
        self.assertEqual(app3.screen, "Home")
        self.assertEqual(self.store.load().reminders, [])

    def test_rosary(self):
        self.keys("5", "1", "1")
        self.assertEqual(self.app.rosary_set, 1)  # 2026-10-01 is a Thursday -> Luminous
        self.keys("ESC", "ESC", "1")
        self.keys("1", "1")  # decade mode, today's set, first mystery
        self.assertEqual(self.app.screen, "RosaryPrayer")
        self.assertEqual(self.app.rosary_steps(), 17)
        for _ in range(100):
            if self.app.screen != "RosaryPrayer":
                break
            self.keys("ENTER")
        self.assertEqual(self.app.screen, "RosaryComplete")
        self.app.rosary_mode = 1
        self.assertEqual(self.app.rosary_steps(), 80)
        self.app.rosary_mode = 3
        self.assertEqual(self.app.rosary_steps(), 7 + 5 * 17 + 3)

    def test_timer(self):
        self.keys("9", "ENTER")
        self.assertIn("05:00", self.text())
        self.clock.t += 241
        self.app.tick()
        self.assertIn("00:59", self.text())
        self.assertIn("bigred", [s for _, _, s in self.app.render()])
        self.keys("ENTER")
        self.clock.t += 100
        self.app.tick()
        self.assertIn("PAUSED", self.text())
        self.keys("ENTER")
        self.clock.t += 60
        self.app.tick()
        self.assertEqual(self.app.screen, "TimerComplete")

    def test_language_and_import(self):
        self.keys("7", "1", "2", "3")
        self.assertEqual(self.app.state.language, "fr")
        self.assertIn("AUJOURD'HUI", self.text())
        self.keys("7", "4", *str(HERE / "fixtures" / "sdcard"), "TAB", "ENTER")
        self.assertIn("Jours importés : 4", self.text())
        self.assertEqual(self.app.state.settings["import_dir"], str(HERE / "fixtures" / "sdcard"))
        self.keys("ENTER", "7", "4", "ENTER")
        self.assertIn("Jours importés : 0", self.text())

    def test_render_fits_80_columns(self):
        for screen_keys in (("1",), ("4",), ("5",), ("6",), ("7",), ("8",), ("9",), ("UP", "ENTER")):
            self.app.width = 80
            self.keys(*screen_keys)
            for _, t, s in self.app.render():
                if s not in ("big", "bigred"):
                    self.assertLessEqual(len(t), 80, t)
            self.keys("ESC")


if __name__ == "__main__":
    unittest.main()
