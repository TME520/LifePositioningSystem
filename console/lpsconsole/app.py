"""LPS Console application logic (screen state machine), independent of curses.

The behaviour, texts, limits and key handling follow App::key()/App::render()
in LPS Companion v1.11 (src/lps_companion.cpp). Keys are strings: 'ENTER',
'ESC', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'DEL', 'BS', 'TAB', 'F1'..'F5', or a
single printable character. render() returns (row, text, style) tuples on a
20-row logical screen, style in {'', 'rev', 'alert', 'big', 'bold'}.
"""
from __future__ import annotations

import datetime as _dt
import textwrap
import time
from pathlib import Path
from typing import List, Optional, Tuple

from . import lpsdata as L
from . import rosary_texts as R

VERSION_LABEL = "LPS CONSOLE v0.1 (Companion 1.11)"

S_HOME, S_TIMER_SELECT, S_TIMER_RUN, S_TIMER_DONE = "Home", "TimerSelect", "TimerRunning", "TimerComplete"
S_POPUP, S_REMINDERS, S_REMINDER_EDIT, S_DATE = "ReminderPopup", "Reminders", "ReminderEdit", "Date"
S_ACTIVITIES, S_NOTE, S_WEIGHT, S_KANBAN, S_TASK_EDIT = "Activities", "Note", "Weight", "Kanban", "TaskEdit"
S_REVIEW, S_FINISH, S_REWARD, S_CONFIG, S_LANGUAGE, S_COLORS = "DailyReview", "Finish", "Reward", "Config", "Language", "Colors"
S_IMPORT, S_IMPORT_DONE = "Import", "ImportResult"
S_ROSARY_MODE, S_ROSARY_SET, S_ROSARY_DECADE, S_ROSARY_PRAYER, S_ROSARY_DONE = (
    "RosaryMode", "RosarySet", "RosaryDecade", "RosaryPrayer", "RosaryComplete")
TEXT_SCREENS = {S_NOTE, S_TASK_EDIT, S_REMINDER_EDIT, S_IMPORT}

MODE_DECADE, MODE_FULL, MODE_GUIDED, MODE_CONTEMPLATIVE = range(4)
JOYFUL, LUMINOUS, SORROWFUL, GLORIOUS = range(4)

Row = Tuple[int, str, str]


class App:
    def __init__(self, store: L.Store, state: Optional[L.State] = None, export_dir=None,
                 clock=time.monotonic, load_error: str = ""):
        self.store = store
        self.state = state if state is not None else L.State()
        self.export_dir = Path(export_dir) if export_dir else store.lps_dir
        self.clock = clock
        self.screen = S_HOME
        self.selection = self.page = self.field = self.days_back = 0
        self.message = ""
        self.blocked = bool(load_error)
        if load_error:
            self.message = self.tr("Read error: save protected", "Erreur lecture : sauvegarde protégée")
        self.pending_language = self.state.language
        self.pending_palette = self.state.palette
        self.date_draft = self.draft = self.task_draft = ""
        self.weights = ["", ""]
        self.earned, self.unlocked = 0, []
        self.kanban_column = "todo"
        self.review_offset = 0
        self.editing_reminder = L.REMINDER_CAPACITY
        self.reminder_field = 0
        self.reminder_date = self.reminder_duration = self.reminder_text = ""
        self.edited_task = L.TASK_CAPACITY
        self.rosary_mode, self.rosary_set, self.rosary_decade = MODE_DECADE, JOYFUL, 0
        self.rosary_step = self.rosary_page = 0
        self.timer_remaining = 0.0
        self.timer_last = 0.0
        self.timer_paused = False
        self.import_path = str(self.state.settings.get("import_dir", ""))
        self.import_save = bool(self.state.settings.get("import_save", False))
        self.import_lines: List[str] = []
        self.width = 40
        self.quit = False
        if not self.blocked:
            self._prune_reminders()
            if self.state.active_reminders():
                self.screen = S_POPUP

    # ------------------------------------------------------------------ utils
    def tr(self, en: str, fr: str) -> str:
        return fr if self.state.language == "fr" else en

    @property
    def lang(self) -> str:
        return self.state.language

    def say(self, text: str) -> None:
        self.message = text

    def read_only(self) -> None:
        self.say(self.tr("Archived day: read-only", "Jour archivé : lecture seule"))

    def history(self) -> List[L.Day]:
        return self.state.history()

    def viewed_day(self) -> L.Day:
        if self.days_back:
            return self.history()[-self.days_back]
        return self.state.today

    def commit(self, candidate: L.State) -> bool:
        if self.days_back:
            self.read_only()
            return False
        if self.blocked:
            self.say(self.tr("Unreadable save: writes blocked", "Sauvegarde illisible : écriture bloquée"))
            return False
        try:
            self.store.save(candidate)
        except OSError:
            self.say(self.tr("Save failed: check disk", "Échec sauvegarde : vérifier le disque"))
            return False
        self.state = candidate
        return True

    def _prune_reminders(self) -> None:
        nxt = self.state.copy()
        if nxt.prune_reminders():
            self.commit(nxt)

    def tasks_in(self, status: str) -> List[int]:
        return [i for i, t in enumerate(self.state.tasks) if t.status == status]

    def status_name(self, status: str) -> str:
        return {"todo": self.tr("TODO", "À FAIRE"), "doing": self.tr("DOING", "EN COURS"),
                "done": self.tr("DONE", "FINI")}[status]

    def go(self, screen: str) -> None:
        self.screen, self.selection, self.message = screen, 0, ""
        if screen == S_REVIEW:
            self.review_offset = 0
        elif screen == S_LANGUAGE:
            self.selection = L.LANGUAGES.index(self.pending_language)
        elif screen == S_COLORS:
            self.selection = self.pending_palette
        elif screen == S_NOTE:
            self.draft = self.viewed_day().note
        elif screen == S_WEIGHT:
            d = self.viewed_day()
            self.field = 0
            self.weights = [L.format_weight(d.expected), L.format_weight(d.actual)]
        elif screen == S_DATE:
            d = self.state.today.date if L.valid_date(self.state.today.date) else L.DEFAULT_DATE
            self.date_draft = d.isoformat()

    @staticmethod
    def edit(value: str, key: str, limit: int) -> str:
        if key == "BS":
            return value[:-1]
        if len(key) == 1 and 32 <= ord(key) <= 126 and len(value) < limit:
            return value + key
        return value

    # ------------------------------------------------------------------ timer
    def timer_seconds(self) -> int:
        return int(-(-self.timer_remaining // 1)) if self.timer_remaining > 0 else 0

    def tick(self) -> bool:
        """Advance the timer; returns True if a redraw is needed."""
        if self.screen != S_TIMER_RUN:
            return False
        now = self.clock()
        before = self.timer_seconds()
        if not self.timer_paused:
            self.timer_remaining = max(0.0, self.timer_remaining - (now - self.timer_last))
        self.timer_last = now
        if self.timer_remaining <= 0:
            self.go(S_TIMER_DONE)
            return True
        return before != self.timer_seconds()

    # ------------------------------------------------------------------ rosary
    def suggested_mysteries(self) -> int:
        d = self.state.today.date
        if not L.valid_date(d):
            return JOYFUL
        wd = (d.weekday() + 1) % 7  # 0 = Sunday, like the Companion
        if wd in (1, 6):
            return JOYFUL
        if wd in (2, 5):
            return SORROWFUL
        if wd == 4:
            return LUMINOUS
        return GLORIOUS

    def set_name(self, s: int) -> str:
        return R.mysterySetName[self.lang][s]

    def rosary_view(self, step: int) -> Tuple[str, int, int]:
        if self.rosary_mode == MODE_DECADE:
            d = self.rosary_decade
            if step == 0:
                return ("Sign", d, 0)
            if step == 1:
                return ("Mystery", d, 0)
            if step == 2:
                return ("OurFather", d, 0)
            if step < 13:
                return ("Hail", d, step - 2)
            return [("Glory", d, 0), ("Fatima", d, 0), ("Closing", d, 0)][step - 13] if step < 16 else ("EndSign", d, 0)
        if step == 0:
            return ("Sign", 0, 0)
        if step == 1:
            return ("Creed", 0, 0)
        if step == 2:
            return ("OpeningOurFather", 0, 0)
        if 3 <= step <= 5:
            return ("OpeningHail", 0, step - 2)
        if step == 6:
            return ("Glory", 0, 0)
        local = step - 7
        extras = {MODE_FULL: 0, MODE_GUIDED: 2}.get(self.rosary_mode, 3)
        block = 14 + extras
        if local < 5 * block:
            decade, pos = divmod(local, block)
            if pos == 0:
                return ("Mystery", decade, 0)
            p = pos - 1
            if extras:
                if p == 0:
                    return ("Scripture", decade, 0)
                if p == 1:
                    return ("Meditation", decade, 0)
                if extras == 3 and p == 2:
                    return ("Silence", decade, 0)
                p -= extras
            if p == 0:
                return ("OurFather", decade, 0)
            if p <= 10:
                return ("Hail", decade, p)
            if p == 11:
                return ("Glory", decade, 0)
            return ("Fatima", decade, 0)
        local -= 5 * block
        return [("HailHolyQueen", 4, 0), ("Closing", 4, 0)][local] if local < 2 else ("EndSign", 4, 0)

    def rosary_steps(self) -> int:
        if self.rosary_mode == MODE_DECADE:
            return 17
        extras = {MODE_FULL: 0, MODE_GUIDED: 2}.get(self.rosary_mode, 3)
        return 7 + 5 * (14 + extras) + 3

    def rosary_text(self, view) -> str:
        part, decade, _ = view
        if part == "Mystery":
            return R.mysteryName[self.lang][self.rosary_set][decade]
        if part == "Scripture":
            return R.scripture[self.lang][self.rosary_set][decade]
        if part == "Meditation":
            return R.meditation[self.lang][self.rosary_set][decade]
        return R.TEXT[part][self.lang]

    def rosary_pages(self, text: str) -> List[List[str]]:
        lines = textwrap.wrap(text, max(20, self.width - 2)) or [""]
        return [lines[i:i + 9] for i in range(0, len(lines), 9)]

    # ------------------------------------------------------------------ review
    def review_lines(self) -> List[str]:
        today = self.state.today
        acts = today.activities()
        out = [self.tr("ACTIVITIES (%d)", "ACTIVITÉS (%d)") % len(acts)]
        out += ["  " + L.activity_name(i, self.lang) for i in acts] or ["  " + self.tr("None selected", "Aucune sélectionnée")]
        for status in ("todo", "doing"):
            idx = self.tasks_in(status)
            out.append("%s (%d)" % (self.status_name(status), len(idx)))
            out += ["  " + self.state.tasks[i].name for i in idx] or ["  " + self.tr("None", "Aucune")]
        return out

    # ------------------------------------------------------------------ keys
    def key(self, k: str) -> None:
        if k == "q" and self.screen not in TEXT_SCREENS:
            if self.screen == S_HOME:
                self.quit = True
                return
            k = "ESC"
        if k == "ESC":
            target = S_HOME
            if self.screen in (S_LANGUAGE, S_COLORS, S_IMPORT, S_IMPORT_DONE):
                target = S_CONFIG
            elif self.screen == S_REMINDER_EDIT:
                target = S_REMINDERS
            elif self.screen == S_TASK_EDIT:
                target = S_KANBAN
            elif self.screen == S_ROSARY_SET:
                target = S_ROSARY_MODE
            elif self.screen == S_ROSARY_DECADE:
                target = S_ROSARY_SET
            self.go(target)
            return
        self.message = ""
        handler = getattr(self, "_key_" + self.screen, None)
        if handler:
            handler(k)

    def _key_ReminderPopup(self, k):
        self.go(S_HOME)

    def _key_Home(self, k):
        hist = self.history()
        if k == "LEFT":
            if self.days_back < len(hist):
                self.days_back += 1
                self.selection = self.page = 0
            else:
                self.say(self.tr("Oldest saved day", "Plus ancien jour conservé") if hist
                         else self.tr("No archived days", "Aucun jour archivé"))
        if k == "RIGHT":
            self.days_back = self.selection = self.page = 0
        count = 3 if self.days_back else 11
        if k == "UP":
            self.selection = (self.selection + count - 1) % count
        if k == "DOWN":
            self.selection = (self.selection + 1) % count
        if not self.days_back and k == "0":
            self.selection, k = 0, "ENTER"
        elif self.days_back and k in "123" and len(k) == 1:
            self.selection, k = int(k) - 1, "ENTER"
        elif not self.days_back and len(k) == 1 and "1" <= k <= "9":
            self.selection, k = int(k), "ENTER"
        if len(k) == 1 and (k == "0" or "4" <= k <= "9") and self.days_back:
            self.read_only()
        if k == "ENTER":
            current = [S_DATE, S_ACTIVITIES, S_NOTE, S_WEIGHT, S_KANBAN, S_ROSARY_MODE, S_REVIEW, S_CONFIG,
                       S_REMINDERS, S_TIMER_SELECT, S_FINISH]
            target = [S_ACTIVITIES, S_NOTE, S_WEIGHT][self.selection] if self.days_back else current[self.selection]
            if target == S_KANBAN:
                self.kanban_column = "todo"
            if target == S_REMINDERS:
                self._prune_reminders()
            if target == S_CONFIG:
                self.pending_language, self.pending_palette = self.state.language, self.state.palette
            self.go(target)

    def _key_TimerSelect(self, k):
        if k == "UP":
            self.selection = (self.selection + 2) % 3
        if k == "DOWN":
            self.selection = (self.selection + 1) % 3
        if k == "ENTER":
            self.timer_remaining = [5, 15, 30][self.selection] * 60.0
            self.timer_last, self.timer_paused = self.clock(), False
            self.go(S_TIMER_RUN)

    def _key_TimerRunning(self, k):
        if k == "ENTER":
            self.tick()
            self.timer_paused = not self.timer_paused
            self.timer_last = self.clock()

    def _key_TimerComplete(self, k):
        if k == "ENTER":
            self.go(S_TIMER_SELECT)

    def _key_Reminders(self, k):
        n = len(self.state.reminders)
        if k == "UP" and n:
            self.selection = (self.selection + n - 1) % n
        elif k == "DOWN" and n:
            self.selection = (self.selection + 1) % n
        elif k in ("n", "N"):
            if n >= L.REMINDER_CAPACITY:
                self.say(self.tr("Limit: 3 reminders", "Limite : 3 rappels"))
            else:
                self._begin_reminder_edit(L.REMINDER_CAPACITY)
        elif k == "ENTER" and n:
            self._begin_reminder_edit(self.selection)
        elif k == "DEL" and n:
            nxt = self.state.copy()
            del nxt.reminders[self.selection]
            if self.commit(nxt) and self.selection >= len(self.state.reminders) and self.selection:
                self.selection -= 1

    def _begin_reminder_edit(self, index):
        self.editing_reminder, self.reminder_field = index, 0
        r = self.state.reminders[index] if index < len(self.state.reminders) else L.Reminder(self.state.today.date, 1, "")
        self.reminder_date = r.start.isoformat() if L.valid_date(r.start) else ""
        self.reminder_duration, self.reminder_text = str(r.duration), r.text
        self.go(S_REMINDER_EDIT)

    def _key_ReminderEdit(self, k):
        if k == "UP" and self.reminder_field:
            self.reminder_field -= 1
        elif k in ("DOWN", "TAB") and self.reminder_field < 2:
            self.reminder_field += 1
        elif k == "ENTER":
            start = L.parse_date(self.reminder_date)
            dur = int(self.reminder_duration) if self.reminder_duration.isdigit() else 0
            if not start or not 1 <= dur <= 365 or not self.reminder_text:
                self.say(self.tr("Check date, 1-365 days and text", "Vérifiez date, 1-365 jours et texte"))
            elif L.valid_date(self.state.today.date) and start.toordinal() + dur <= self.state.today.date.toordinal():
                self.say(self.tr("Reminder already expired", "Rappel déjà expiré"))
            else:
                nxt = self.state.copy()
                r = L.Reminder(start, dur, self.reminder_text)
                if self.editing_reminder < len(nxt.reminders):
                    nxt.reminders[self.editing_reminder] = r
                else:
                    nxt.reminders.append(r)
                if self.commit(nxt):
                    self.go(S_REMINDERS)
        elif self.reminder_field == 0:
            if k == "BS" or k == "-" or k.isdigit():
                self.reminder_date = self.edit(self.reminder_date, k, 10)
        elif self.reminder_field == 1:
            if k == "BS" or k.isdigit():
                self.reminder_duration = self.edit(self.reminder_duration, k, 3)
        else:
            self.reminder_text = self.edit(self.reminder_text, k, L.REMINDER_TEXT_LENGTH)

    def _key_Date(self, k):
        if k == "ENTER":
            d = L.parse_date(self.date_draft)
            if not d:
                self.say(self.tr("Invalid date: YYYY-MM-DD, 2000-2099", "Date invalide : AAAA-MM-JJ, 2000-2099"))
            else:
                nxt = self.state.copy()
                nxt.today.date = d
                if self.commit(nxt):
                    self.go(S_HOME)
        elif k == "BS" or k == "-" or (len(k) == 1 and k.isdigit()):
            self.date_draft = self.edit(self.date_draft, k, 10)

    def _key_Activities(self, k):
        pages = (L.ACTIVITY_COUNT + L.PAGE_SIZE - 1) // L.PAGE_SIZE
        rows = min(L.PAGE_SIZE, L.ACTIVITY_COUNT - self.page * L.PAGE_SIZE)
        if k in ("LEFT", "RIGHT"):
            self.page = (self.page + (1 if k == "RIGHT" else pages - 1)) % pages
            self.selection = 0
        elif k == "UP":
            self.selection = (self.selection + rows - 1) % rows
        elif k == "DOWN":
            self.selection = (self.selection + 1) % rows
        elif k in (" ", "ENTER") or (len(k) == 1 and "1" <= k <= "9"):
            if self.days_back:
                self.read_only()
                return
            idx = self.page * L.PAGE_SIZE + self.selection if k in (" ", "ENTER") else int(k) - 1
            nxt = self.state.copy()
            nxt.today.flags ^= 1 << idx
            if self.commit(nxt):
                self.page, self.selection = divmod(idx, L.PAGE_SIZE)

    def _key_Note(self, k):
        if self.days_back:
            if k == "ENTER":
                self.go(S_HOME)
            else:
                self.read_only()
        elif k == "ENTER":
            nxt = self.state.copy()
            nxt.today.note = self.draft
            if self.commit(nxt):
                self.go(S_HOME)
        else:
            self.draft = self.edit(self.draft, k, L.NOTE_LENGTH)

    def _key_Weight(self, k):
        if self.days_back:
            if k == "ENTER":
                self.go(S_HOME)
            else:
                self.read_only()
        elif k in ("UP", "DOWN", "TAB"):
            self.field = 1 - self.field
        elif k == "ENTER":
            try:
                exp, act = L.parse_weight(self.weights[0]), L.parse_weight(self.weights[1])
            except ValueError:
                self.say(self.tr("Invalid weight (example: 110.500)", "Poids invalide (exemple : 110,500)"))
                return
            nxt = self.state.copy()
            nxt.today.expected, nxt.today.actual = exp, act
            if self.commit(nxt):
                self.go(S_HOME)
        elif k == "BS" or (len(k) == 1 and (k.isdigit() or k in ".,")):
            self.weights[self.field] = self.edit(self.weights[self.field], k, 7)

    def _key_Kanban(self, k):
        idx = self.tasks_in(self.kanban_column)
        n = len(idx)
        if n and self.selection >= n:
            self.selection = n - 1
        cols = L.TASK_STATUSES
        if k in ("LEFT", "RIGHT"):
            c = cols.index(self.kanban_column)
            self.kanban_column = cols[(c + (1 if k == "RIGHT" else 2)) % 3]
            self.selection = 0
        elif k == "UP" and n:
            self.selection = (self.selection + n - 1) % n
        elif k == "DOWN" and n:
            self.selection = (self.selection + 1) % n
        elif k in ("n", "N"):
            if len(self.state.tasks) >= L.TASK_CAPACITY:
                self.say(self.tr("Kanban is full (24 tasks)", "Kanban complet (24 tâches)"))
            else:
                self._begin_task_edit(L.TASK_CAPACITY)
        elif k == "ENTER" and n:
            self._begin_task_edit(idx[self.selection])
        elif k in ("DEL", "x", "X") and n:
            nxt = self.state.copy()
            del nxt.tasks[idx[self.selection]]
            if self.commit(nxt) and self.selection >= len(self.tasks_in(self.kanban_column)) and self.selection:
                self.selection -= 1
        elif k in ("F1", "F2", "F3", "1", "2", "3") and n:
            self._move_task(cols[int(k[-1]) - 1])
        elif k in ("F4", "-") and n:
            self._reorder(False)
        elif k in ("F5", "+") and n:
            self._reorder(True)

    def _begin_task_edit(self, index):
        self.edited_task = index
        self.task_draft = self.state.tasks[index].name if index < L.TASK_CAPACITY else ""
        self.go(S_TASK_EDIT)

    def _move_task(self, target):
        index = self.tasks_in(self.kanban_column)[self.selection]
        if self.state.tasks[index].status == target:
            self.say(self.tr("Task is already in this column", "Tâche déjà dans cette colonne"))
            return
        nxt = self.state.copy()
        moved = nxt.tasks.pop(index)
        moved.status = target
        nxt.tasks.append(moved)
        if self.commit(nxt):
            self.kanban_column = target
            self.selection = len(self.tasks_in(target)) - 1
            self.say(self.tr("Task moved", "Tâche déplacée"))

    def _reorder(self, downward):
        idx = self.tasks_in(self.kanban_column)
        n = len(idx)
        if n < 2:
            return
        nxt = self.state.copy()
        names = [nxt.tasks[i] for i in idx]
        sel = self.selection
        if not downward and sel == 0:
            names = names[1:] + names[:1]
            dest = n - 1
        elif downward and sel == n - 1:
            names = names[-1:] + names[:-1]
            dest = 0
        else:
            dest = sel + 1 if downward else sel - 1
            names[sel], names[dest] = names[dest], names[sel]
        for i, t in zip(idx, names):
            nxt.tasks[i] = t
        if self.commit(nxt):
            self.selection = dest

    def _key_TaskEdit(self, k):
        if k == "ENTER":
            if not self.task_draft:
                self.say(self.tr("Task name cannot be empty", "Le nom ne peut pas être vide"))
                return
            nxt = self.state.copy()
            if self.edited_task < L.TASK_CAPACITY:
                nxt.tasks[self.edited_task].name = self.task_draft
                dest = self.edited_task
            elif len(nxt.tasks) >= L.TASK_CAPACITY:
                self.say(self.tr("Kanban is full (24 tasks)", "Kanban complet (24 tâches)"))
                return
            else:
                nxt.tasks.append(L.Task(self.kanban_column, self.task_draft))
                dest = len(nxt.tasks) - 1
            if self.commit(nxt):
                self.screen, self.message = S_KANBAN, ""
                self.selection = self.tasks_in(self.kanban_column).index(dest)
        else:
            self.task_draft = self.edit(self.task_draft, k, L.TASK_NAME_LENGTH)

    def _key_DailyReview(self, k):
        maximum = max(0, len(self.review_lines()) - 8)
        if k == "DOWN" and self.review_offset < maximum:
            self.review_offset += 1
        elif k == "UP" and self.review_offset:
            self.review_offset -= 1
        elif k == "RIGHT":
            self.review_offset = min(self.review_offset + 8, maximum)
        elif k == "LEFT":
            self.review_offset = max(self.review_offset - 8, 0)

    def _key_Config(self, k):
        if k == "UP":
            self.selection = (self.selection + 3) % 4
        if k == "DOWN":
            self.selection = (self.selection + 1) % 4
        if len(k) == 1 and "1" <= k <= "4":
            self.selection, k = int(k) - 1, "ENTER"
        if k == "ENTER":
            if self.selection == 0:
                self.go(S_LANGUAGE)
            elif self.selection == 1:
                self.go(S_COLORS)
            elif self.selection == 2:
                nxt = self.state.copy()
                nxt.language = self.pending_language
                if self.commit(nxt):
                    self.go(S_HOME)
                    self.say(self.tr("Configuration saved", "Configuration enregistrée"))
            else:
                self.go(S_IMPORT)

    def _key_Language(self, k):
        if k in ("UP", "DOWN"):
            self.selection = 1 - self.selection
        if k in ("1", "2"):
            self.selection, k = int(k) - 1, "ENTER"
        if k == "ENTER":
            self.pending_language = L.LANGUAGES[self.selection]
            self.go(S_CONFIG)

    def _key_Colors(self, k):
        if k == "UP":
            self.selection = (self.selection + 4) % 5
        if k == "DOWN":
            self.selection = (self.selection + 1) % 5
        if len(k) == 1 and "1" <= k <= "5":
            self.selection = int(k) - 1
        self.pending_palette = self.selection
        if k == "ENTER":
            nxt = self.state.copy()
            nxt.palette = self.pending_palette
            if self.commit(nxt):
                self.go(S_HOME)
                self.say(self.tr("Color saved", "Couleur enregistrée"))

    def _key_Import(self, k):
        if k == "TAB":
            self.import_save = not self.import_save
        elif k == "ENTER":
            self.run_import(self.import_path, self.import_save)
        else:
            self.import_path = self.edit(self.import_path, k, 200) if k == "BS" or len(k) == 1 else self.import_path

    def run_import(self, path: str, include_save: bool) -> Optional[L.ImportReport]:
        if not path.strip():
            self.say(self.tr("Type the SD card or LPS folder path", "Saisissez le chemin de la carte SD ou du dossier LPS"))
            return None
        nxt, rep = L.import_companion(self.state, path.strip(), include_save)
        nxt.settings["import_dir"] = path.strip()
        nxt.settings["import_save"] = include_save
        if not L.find_lps_dir(path.strip()).is_dir():
            self.import_lines = rep.summary(self.lang)
        elif self.commit(nxt):
            self.import_lines = rep.summary(self.lang)
        else:
            self.import_lines = [self.message]
        self.go(S_IMPORT_DONE)
        return rep

    def _key_ImportResult(self, k):
        if k == "ENTER":
            self.go(S_HOME)

    def _key_RosaryMode(self, k):
        if k == "UP":
            self.selection = (self.selection + 3) % 4
        if k == "DOWN":
            self.selection = (self.selection + 1) % 4
        if len(k) == 1 and "1" <= k <= "4":
            self.selection, k = int(k) - 1, "ENTER"
        if k == "ENTER":
            self.rosary_mode = self.selection
            self.go(S_ROSARY_SET)

    def _key_RosarySet(self, k):
        if k == "UP":
            self.selection = (self.selection + 4) % 5
        if k == "DOWN":
            self.selection = (self.selection + 1) % 5
        if len(k) == 1 and "1" <= k <= "5":
            self.selection, k = int(k) - 1, "ENTER"
        if k == "ENTER":
            self.rosary_set = self.selection - 1 if self.selection else self.suggested_mysteries()
            if self.rosary_mode == MODE_DECADE:
                self.go(S_ROSARY_DECADE)
            else:
                self.rosary_step = self.rosary_page = 0
                self.go(S_ROSARY_PRAYER)

    def _key_RosaryDecade(self, k):
        if k == "UP":
            self.selection = (self.selection + 4) % 5
        if k == "DOWN":
            self.selection = (self.selection + 1) % 5
        if len(k) == 1 and "1" <= k <= "5":
            self.selection, k = int(k) - 1, "ENTER"
        if k == "ENTER":
            self.rosary_decade = self.selection
            self.rosary_step = self.rosary_page = 0
            self.go(S_ROSARY_PRAYER)

    def _key_RosaryPrayer(self, k):
        pages = len(self.rosary_pages(self.rosary_text(self.rosary_view(self.rosary_step))))
        if k == "LEFT":
            if self.rosary_page:
                self.rosary_page -= 1
            elif self.rosary_step:
                self.rosary_step -= 1
                self.rosary_page = len(self.rosary_pages(self.rosary_text(self.rosary_view(self.rosary_step)))) - 1
        elif k in ("RIGHT", "ENTER", " "):
            if self.rosary_page + 1 < pages:
                self.rosary_page += 1
            elif self.rosary_step + 1 < self.rosary_steps():
                self.rosary_step += 1
                self.rosary_page = 0
            else:
                self.go(S_ROSARY_DONE)

    def _key_RosaryComplete(self, k):
        if k == "ENTER":
            self.go(S_HOME)

    def _key_Finish(self, k):
        if k != "ENTER":
            return
        if self.blocked:
            self.say(self.tr("Unreadable save: writes blocked", "Sauvegarde illisible : écriture bloquée"))
            return
        try:
            res = L.close_day(self.state, self.export_dir)
        except L.CloseError as ex:
            self.say(self.tr("Set Date before closing the day", "Renseignez Date avant de terminer le jour")
                     if str(ex) == "date" else self.tr("Calendar ends on 2099-12-31", "Le calendrier s'arrête au 2099-12-31"))
            return
        except OSError:
            self.say(self.tr("ICS/CSV export failed; day remains open", "Export ICS/CSV échoué ; journée reste ouverte"))
            return
        if self.commit(res.state):
            self.earned, self.unlocked = res.gain, res.unlocked
            self.last_export = res.ics_path
            self.go(S_REWARD)

    def _key_Reward(self, k):
        if k == "ENTER":
            self.go(S_HOME)

    # ------------------------------------------------------------------ render
    def render(self) -> List[Row]:
        rows: List[Row] = []
        tr = self.tr
        line = lambda r, t, style="": rows.append((r, t, style))
        st = self.state
        day = self.viewed_day()
        date = day.date.isoformat() if L.valid_date(day.date) else "%s%d" % (tr("D", "J"), day.number)
        label = VERSION_LABEL if self.width >= 56 else "LPS CONSOLE v0.1"
        line(0, "%-*s%s" % (max(1, self.width - len(date)), label, date), "rev")
        narrow = self.width < 56
        line(1, tr("%d XP earned", "%d XP acquis") % st.xp)
        if self.days_back:
            line(2, tr("ARCHIVED DAY - READ ONLY", "JOUR ARCHIVÉ - LECTURE SEULE"), "bold")
        sc = self.screen
        if sc == S_HOME:
            line(3, tr("CLOSED DAY", "JOUR TERMINÉ") if self.days_back else tr("TODAY", "AUJOURD'HUI"), "bold")
            line(4, tr("%d/%d activities - %d XP %s", "%d/%d activités - %d XP %s") % (
                L.count(day.flags), L.ACTIVITY_COUNT, L.points(day.flags),
                tr("banked", "validés") if self.days_back else tr("pending", "à valider")))
            menu = ["0  Date", tr("1  Activities", "1  Activités"), tr("2  Notepad", "2  Bloc notes"),
                    tr("3  Weight tracker", "3  Suivi du poids"), "4  Kanban", tr("5  Christian Rosary", "5  Rosaire chrétien"),
                    tr("6  Daily review", "6  Revue journalière"), "7  Config", tr("8  Reminders", "8  Rappels"),
                    tr("9  Timer", "9  Minuteur"), tr("10 Close the day", "10 Terminer le jour")]
            items = menu[1:4] if self.days_back else menu
            for i, t in enumerate(items):
                line(6 + i, t, "rev" if i == self.selection else "")
            line(17, tr("<- Previous day   -> Current day", "<- Jour précédent   -> Jour actuel")
                 + ("" if narrow else tr("   q: quit", "   q : quitter")))
        elif sc == S_TIMER_SELECT:
            line(3, tr("TIMER", "MINUTEUR"), "bold")
            for i, t in enumerate(["5 minutes", "15 minutes", "30 minutes"]):
                line(6 + i * 2, t, "rev" if i == self.selection else "")
        elif sc in (S_TIMER_RUN, S_TIMER_DONE):
            line(3, tr("TIMER", "MINUTEUR"), "bold")
            s = self.timer_seconds()
            line(7, "%02d:%02d" % (s // 60, s % 60), "bigred" if s < 60 else "big")
            if sc == S_TIMER_DONE:
                line(14, tr("TIMER FINISHED!", "MINUTEUR TERMINÉ !"), "bold")
            elif self.timer_paused:
                line(14, tr("PAUSED", "EN PAUSE"), "bold")
            line(17, tr("Enter: Again   Esc: Back", "Entrée : Nouveau   Échap : Retour") if sc == S_TIMER_DONE else
                 (tr("Enter: Resume   Esc: Cancel", "Entrée : Reprendre   Échap : Annuler") if self.timer_paused
                  else tr("Enter: Pause   Esc: Cancel", "Entrée : Pause   Échap : Annuler")))
        elif sc == S_POPUP:
            line(3, tr("REMINDERS", "RAPPELS"), "bold")
            for i, r in enumerate(st.active_reminders()):
                line(6 + 2 * i, "%d. %s" % (i + 1, r.text))
            line(15, tr("Press any key to continue", "Appuyez sur une touche"))
        elif sc == S_REMINDERS:
            line(3, tr("REMINDERS (max 3)", "RAPPELS (max 3)"), "bold")
            if not st.reminders:
                line(6, tr("No reminders", "Aucun rappel"))
            for i, r in enumerate(st.reminders):
                line(6 + 3 * i, "%s  %d %s" % (r.start.isoformat(), r.duration, tr("days", "jours")),
                     "rev" if i == self.selection else "")
                line(7 + 3 * i, r.text)
            line(16, tr("N New  Enter Edit  Del Delete", "N Nouveau  Entrée Modifier  Suppr Effacer"))
        elif sc == S_REMINDER_EDIT:
            line(3, tr("EDIT REMINDER", "MODIFIER LE RAPPEL"), "bold")
            f = self.reminder_field
            line(6, tr("From: %s", "Début : %s") % self.reminder_date, "rev" if f == 0 else "")
            line(8, tr("Days: %s", "Jours : %s") % self.reminder_duration, "rev" if f == 1 else "")
            line(10, tr("Text:", "Texte :"))
            line(11, self.reminder_text or " ", "rev" if f == 2 else "")
            line(15, tr("Up/Down fields  Enter save  Esc cancel", "Haut/Bas champs  Entrée valider  Échap annuler"))
        elif sc == S_DATE:
            line(3, "DATE", "bold")
            line(6, self.date_draft or " ", "rev")
            line(9, tr("Calendar: 2000-01-01 to 2099-12-31", "Calendrier : 2000-01-01 au 2099-12-31"))
            line(12, tr("Enter: save / Esc: cancel", "Entrée : valider / Échap : annuler"))
            line(14, tr("Next day is suggested after closing.", "Jour suivant proposé après clôture."))
        elif sc == S_ACTIVITIES:
            pages = (L.ACTIVITY_COUNT + L.PAGE_SIZE - 1) // L.PAGE_SIZE
            line(3, "%-32s%d/%d" % (tr("ACTIVITIES", "ACTIVITÉS"), self.page + 1, pages), "bold")
            for i in range(self.page * L.PAGE_SIZE, min((self.page + 1) * L.PAGE_SIZE, L.ACTIVITY_COUNT)):
                line(5 + i - self.page * L.PAGE_SIZE, "%2d  %-24s [%s]" % (
                    i + 1, L.activity_name(i, self.lang), "x" if day.flags & (1 << i) else " "),
                     "rev" if i - self.page * L.PAGE_SIZE == self.selection else "")
            line(15, tr("<- -> pages   Esc: day menu", "<- -> pages   Échap : menu du jour") if self.days_back else
                 tr("Enter/Space: toggle  1-9: keys  <- -> pages", "Entrée/Espace : cocher  1-9 : racc.  <- -> pages"))
        elif sc == S_NOTE:
            line(3, tr("NOTEPAD", "BLOC NOTES"), "bold")
            text = self.draft if not self.days_back else day.note
            w = max(20, self.width)
            for i in range(3):
                chunk = text[i * w:(i + 1) * w]
                if chunk or i == 0:
                    line(5 + i, chunk + ("_" if not self.days_back and len(text) < (i + 1) * w and len(text) >= i * w else ""))
            line(10, tr("%d/96 characters", "%d/96 caractères") % len(text))
            line(14, tr("Enter / Esc: day menu", "Entrée / Échap : menu du jour") if self.days_back else
                 tr("Enter: save / Esc: cancel", "Entrée : valider / Échap : annuler"))
        elif sc == S_WEIGHT:
            if self.days_back:
                self.weights = [L.format_weight(day.expected), L.format_weight(day.actual)]
            line(3, tr("WEIGHT TRACKER (kg)", "SUIVI DU POIDS (kg)"), "bold")
            line(6, tr("Expected : %s", "Attendu  : %s") % self.weights[0], "rev" if self.field == 0 else "")
            line(9, tr("Actual   : %s", "Effectif : %s") % self.weights[1], "rev" if self.field == 1 else "")
            try:
                a, c = L.parse_weight(self.weights[0]), L.parse_weight(self.weights[1])
                ok = a is not None and c is not None
            except ValueError:
                ok = False
            if ok:
                d = c - a
                line(12, tr("Difference: %s%d.%03d kg", "Écart : %s%d.%03d kg") % ("-" if d < 0 else "+", abs(d) // 1000, abs(d) % 1000))
            else:
                line(12, tr("Difference: --", "Écart : --"))
            line(14, tr("Blank = not recorded", "Vide = non renseigné"))
            line(15, tr("Enter / Esc: day menu", "Entrée / Échap : menu du jour") if self.days_back else
                 tr("Up/Down: field - Enter: save", "Haut/Bas : champ - Entrée : valider"))
        elif sc == S_KANBAN:
            idx = self.tasks_in(self.kanban_column)
            n = len(idx)
            if n and self.selection >= n:
                self.selection = n - 1
            line(3, "KANBAN  < %s >  (%d)   [%d/24]" % (self.status_name(self.kanban_column), n, len(st.tasks)), "bold")
            if not n:
                line(6, tr("No tasks. Press N to create one.", "Aucune tâche. Appuyez sur N."))
            first = self.selection - 8 if self.selection >= 9 else 0
            for pos in range(first, min(first + 9, n)):
                line(5 + pos - first, "%2d  %-32s" % (pos + 1, st.tasks[idx[pos]].name), "rev" if pos == self.selection else "")
            line(14, tr("N New  Enter Edit  Del/x Delete", "N Nouveau  Entrée Modifier  Suppr/x Effacer"))
            line(15, tr("F1/1 TODO  F2/2 DOING  F3/3 DONE", "F1/1 À FAIRE  F2/2 EN COURS  F3/3 FINI"))
            line(16, tr("F4/- Up  F5/+ Down  <- -> Columns", "F4/- Monter  F5/+ Descendre  <- -> Colonnes"))
        elif sc == S_TASK_EDIT:
            line(3, tr("EDIT TASK", "MODIFIER LA TÂCHE") if self.edited_task < L.TASK_CAPACITY else tr("NEW TASK", "NOUVELLE TÂCHE"), "bold")
            line(6, (self.task_draft + "_").ljust(33), "rev")
            line(9, tr("%d/32 characters", "%d/32 caractères") % len(self.task_draft))
            line(13, tr("Enter: save task", "Entrée : enregistrer la tâche"))
            line(15, tr("Backspace: erase  Esc: cancel", "Retour arrière : effacer  Échap : annuler"))
        elif sc == S_REVIEW:
            line(3, "%s  %s" % (tr("DAILY REVIEW", "REVUE JOURNALIÈRE"), date), "bold")
            w = L.format_weight(st.today.actual) or "--"
            line(4, tr("Actual weight: %s kg", "Poids effectif : %s kg") % w)
            lines = self.review_lines()
            for i, t in enumerate(lines[self.review_offset:self.review_offset + 8]):
                line(7 + i, t)
            line(16, tr("Up/Down scroll  %d-%d/%d  Esc back", "Haut/Bas défiler  %d-%d/%d  Échap retour") % (
                self.review_offset + 1, min(self.review_offset + 8, len(lines)), len(lines)))
        elif sc == S_CONFIG:
            line(3, "CONFIGURATION", "bold")
            line(6, "%s: %s" % (tr("1  Language", "1  Langue"), "English" if self.pending_language == "en" else "Français"),
                 "rev" if self.selection == 0 else "")
            line(7, "%s: PAL%d" % (tr("2  Colors", "2  Couleurs"), st.palette + 1), "rev" if self.selection == 1 else "")
            line(8, tr("3  Save", "3  Enregistrer"), "rev" if self.selection == 2 else "")
            line(9, tr("4  Import from Companion SD", "4  Importer depuis la SD Companion"), "rev" if self.selection == 3 else "")
            line(11, tr("Language: select, then Save.", "Langue : choisir, puis enregistrer."))
            line(12, tr("Esc: discard changes", "Échap : annuler les modifications"))
            line(14, tr("Exports: %s", "Exports : %s") % self.export_dir)
        elif sc == S_LANGUAGE:
            line(3, tr("LANGUAGE", "LANGUE"), "bold")
            line(6, "1  English", "rev" if self.selection == 0 else "")
            line(7, "2  Français", "rev" if self.selection == 1 else "")
            line(10, tr("Enter: select / Esc: back", "Entrée : choisir / Échap : retour"))
            line(11, tr("Then Save in Configuration.", "Puis enregistrer dans Configuration."))
        elif sc == S_COLORS:
            line(3, tr("COLORS", "COULEURS"), "bold")
            names = [tr("red", "rouge"), tr("green", "vert"), tr("blue", "bleu"), "Orange", tr("Pink", "Rose")]
            for i, nm in enumerate(names):
                line(6 + i, "%d  PAL%d  %s" % (i + 1, i + 1, nm), "rev" if i == self.selection else "")
            line(13, tr("Enter: save color", "Entrée : enregistrer couleur"))
        elif sc == S_IMPORT:
            line(3, tr("IMPORT FROM LPS COMPANION", "IMPORT DEPUIS LPS COMPANION"), "bold")
            line(5, tr("SD card root or LPS folder:", "Racine de la carte SD ou dossier LPS :"))
            line(6, (self.import_path + "_")[-max(10, self.width):], "rev")
            line(8, "[%s] %s" % ("x" if self.import_save else " ", tr("Also read SAVE_A/B.BIN (Tab)", "Lire aussi SAVE_A/B.BIN (Tab)")))
            line(10, tr("Reads EXPORT/*.ics and WEIGHTS/*.csv.", "Lit EXPORT/*.ics et WEIGHTS/*.csv."))
            line(11, tr("Merge by date, no duplicates.", "Fusion par date, sans doublon."))
            line(13, tr("Enter: import   Esc: back", "Entrée : importer   Échap : retour"))
        elif sc == S_IMPORT_DONE:
            line(3, tr("IMPORT RESULT", "RÉSULTAT DE L'IMPORT"), "bold")
            for i, t in enumerate(self.import_lines[:10]):
                line(5 + i, t)
            line(16, tr("Enter: home   Esc: back", "Entrée : accueil   Échap : retour"))
        elif sc == S_ROSARY_MODE:
            line(3, tr("CHOOSE ROSARY MODE", "CHOISIR LE MODE DU ROSAIRE"), "bold")
            modes = [tr("1  One decade             3-5 min", "1  Une dizaine            3-5 min"),
                     tr("2  Full Rosary          15-20 min", "2  Rosaire complet      15-20 min"),
                     tr("3  Scripture + guidance 20-30 min", "3  Écriture + méditation 20-30 min"),
                     tr("4  Slow contemplation   30-40 min", "4  Contemplation lente  30-40 min")]
            for i, t in enumerate(modes):
                line(6 + 2 * i, t, "rev" if i == self.selection else "")
            line(15, tr("Choose before every new session.", "Choix demandé à chaque nouvelle séance."))
        elif sc == S_ROSARY_SET:
            line(3, tr("CHOOSE THE MYSTERIES", "CHOISIR LES MYSTÈRES"), "bold")
            line(6, tr("1  Today: %s", "1  Aujourd'hui : %s") % self.set_name(self.suggested_mysteries()),
                 "rev" if self.selection == 0 else "")
            for i in range(4):
                line(7 + i, "%d  %s" % (i + 2, self.set_name(i)), "rev" if self.selection == i + 1 else "")
            line(14, tr("Today's cycle is selected by date.", "Cycle du jour proposé automatiquement."))
        elif sc == S_ROSARY_DECADE:
            line(3, tr("ONE DECADE - %s MYSTERIES", "UNE DIZAINE - MYSTÈRES %s") % self.set_name(self.rosary_set), "bold")
            for i in range(5):
                line(6 + i, "%d  %s" % (i + 1, R.mysteryName[self.lang][self.rosary_set][i]), "rev" if i == self.selection else "")
            line(14, tr("Choose the mystery to pray.", "Choisissez le mystère à prier."))
        elif sc == S_ROSARY_PRAYER:
            view = self.rosary_view(self.rosary_step)
            part, decade, cnt = view
            text = self.rosary_text(view)
            line(3, "%s  %d/%d" % (R.PARTNAME[part][self.lang], self.rosary_step + 1, self.rosary_steps()), "rev")
            single = self.rosary_mode == MODE_DECADE
            if part in ("Mystery", "Scripture", "Meditation", "Silence"):
                line(4, tr("Mystery %d/%d - %s", "Mystère %d/%d - %s") % (1 if single else decade + 1, 1 if single else 5,
                                                                        self.set_name(self.rosary_set)))
            elif part == "Hail":
                line(4, tr("Decade %d/%d    Hail Mary %d/10", "Dizaine %d/%d    Ave Maria %d/10") % (
                    1 if single else decade + 1, 1 if single else 5, cnt))
                line(5, "".join("*" if i < cnt else "o" for i in range(10)))
            elif part == "OpeningHail":
                line(4, tr("Opening Hail Mary %d/3", "Ave Maria d'ouverture %d/3") % cnt)
            pages = self.rosary_pages(text)
            for i, t in enumerate(pages[min(self.rosary_page, len(pages) - 1)]):
                line(6 + i, t)
            if len(pages) > 1:
                line(15, tr("Page %d/%d  Enter: continue", "Page %d/%d  Entrée : continuer") % (self.rosary_page + 1, len(pages)))
            else:
                line(15, tr("Enter: next prayer   <- previous", "Entrée : suite       <- précédent"))
            line(16, tr("Esc: leave session", "Échap : quitter la séance"))
        elif sc == S_ROSARY_DONE:
            line(3, tr("ROSARY COMPLETED", "ROSAIRE TERMINÉ"), "rev")
            line(6, tr("The prayer session is complete.", "La séance de prière est terminée."))
            line(9, tr("Remain for a moment in God's peace.", "Demeurez un instant dans la paix de Dieu."))
            line(13, tr("Enter: return to LPS Console", "Entrée : retour à LPS Console"))
        elif sc == S_FINISH:
            n = L.count(st.today.flags)
            line(3, tr("CLOSE THE DAY?", "TERMINER LE JOUR ?"), "bold")
            line(6, tr("Activities: %d", "Activités : %d") % n)
            line(8, tr("Variety bonus: %d XP", "Bonus variété : %d XP") % (10 if n >= 3 else 0))
            line(10, "Total : +%d XP" % L.points(st.today.flags))
            line(13, tr("Enter: save and advance", "Entrée : enregistrer et avancer"))
            line(15, tr("An empty day has no penalty.", "Une journée vide ne coûte rien."))
        elif sc == S_REWARD:
            line(3, tr("DAY SAVED", "JOUR ENREGISTRÉ"), "bold")
            line(5, "+%d XP" % self.earned)
            r = 8
            for i in self.unlocked:
                line(r, tr("NEW KEEPSAKE", "NOUVEAU SOUVENIR"), "bold")
                line(r + 1, L.GIFTS[i][1] if self.lang == "en" else L.GIFTS[i][0])
                r += 2
            if getattr(self, "last_export", None):
                line(14, tr("ICS: %s", "ICS : %s") % self.last_export.name)
            line(15, tr("Enter: start the new day", "Entrée : commencer le nouveau jour"))
        status_row = 18 if sc == S_HOME else (16 if sc in (S_TIMER_RUN, S_TIMER_DONE) else 17)
        if self.message:
            line(status_row, self.message, "alert" if self.blocked else "bold")
        if sc not in (S_TIMER_RUN, S_TIMER_DONE):
            footer = tr("Up/Down  Enter:OK  Esc:Back", "Haut/Bas  Entrée:OK  Échap:Retour")
            if sc not in TEXT_SCREENS and not narrow:
                footer += tr("  q:Back/Quit", "  q:Retour/Quitter")
            line(19, footer, "rev")
        return rows
