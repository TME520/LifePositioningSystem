#!/usr/bin/env python3
import gi, os, json, re, random
from datetime import datetime, timedelta
gi.require_version('Gtk', '3.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gtk, Gst, Gdk, GLib

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

# -------------------------- Schedule CSV support --------------------------

@dataclass
class ScheduleEntry:
    monday: int
    tuesday: int
    wednesday: int
    thursday: int
    friday: int
    saturday: int
    sunday: int
    hour: int          # 0..23
    minute: int        # 0..59
    random: int        # 0..15 (minutes of random delay before action starts)
    duration: int      # seconds, 1..600 (informational)
    text: str          # toast at start (optional)
    action: str        # ACT_*
    data: str          # expected "FF"

def _resolve_path(candidates: List[str]) -> Optional[str]:
    from pathlib import Path as _P
    for c in candidates:
        try:
            p = _P(c)
            if p.exists():
                print(f"[DEBUG] Resolved path: {str(p)}")
                return str(p)
        except Exception:
            pass
    return None

def _resolve_schedule_path() -> Optional[str]:
    return _resolve_path([
        "schedule.csv",
        os.path.join(os.path.dirname(__file__), "schedule.csv"),
        os.path.join(os.getcwd(), "schedule.csv"),
        "/mnt/data/schedule.csv",
    ])

def _resolve_scriptjson_path() -> Optional[str]:
    return _resolve_path([
        "script.json",
        os.path.join(os.path.dirname(__file__), "script.json"),
        os.path.join(os.getcwd(), "script.json"),
        "/mnt/data/script.json",
    ])

def load_schedule() -> Tuple[List[ScheduleEntry], Dict[int, List[ScheduleEntry]]]:
    import csv as _csv
    schedule_path = _resolve_schedule_path()
    entries: List[ScheduleEntry] = []
    by_wd: Dict[int, List[ScheduleEntry]] = {i: [] for i in range(7)}
    if not schedule_path:
        print("[ERROR] schedule.csv not found")
        return entries, by_wd

    try:
        with open(schedule_path, newline="", encoding="utf-8") as f:
            print("[DEBUG] Loading schedule.csv")
            reader = _csv.reader(f, delimiter=";")
            # Read header so we can locate HH/MM columns explicitly
            try:
                header = next(reader)
            except StopIteration:
                return entries, by_wd

            header_lookup = {col.strip().upper(): idx for idx, col in enumerate(header)}
            # Default back to legacy positional indices if HH/MM missing
            hh_idx = header_lookup.get("HH", 7)
            mm_idx = header_lookup.get("MM", 8)

            for row_num, row in enumerate(reader, start=2):
                if not row or all((c.strip() == "" for c in row)):
                    continue
                max_len = max(14, hh_idx + 1, mm_idx + 1)
                row = (row + [""] * max_len)[:max_len]
                try:
                    m, tu, w, th, fr, sa, su = [int((v or "0").strip() or "0") for v in row[:7]]
                    hour = int((row[hh_idx] if hh_idx < len(row) else "0").strip() or "0")
                    minute = int((row[mm_idx] if mm_idx < len(row) else "0").strip() or "0")
                    rnd = int((row[9] or "0").strip() or "0")
                    dur = int((row[10] or "0").strip() or "0")
                    text = (row[11] or "").strip()
                    action = (row[12] or "").strip()
                    data = (row[13] or "").strip()
                    e = ScheduleEntry(m, tu, w, th, fr, sa, su, hour, minute, rnd, dur, text, action, data)
                    print(f"[DEBUG] Adding scheduled action: {e}")
                    entries.append(e)
                    flags = [m, tu, w, th, fr, sa, su]
                    for wd, flag in enumerate(flags):  # Monday=0 .. Sunday=6
                        if flag:
                            by_wd[wd].append(e)
                except Exception as ex:
                    print(f"[ERROR] Row {row_num} parse error: {ex} | {row}")
    except Exception as ex:
        print(f"[ERROR] Failed reading schedule.csv: {ex}")
        return entries, by_wd

    print(f"[DEBUG] Loaded {len(entries)} entries from {schedule_path}")
    return entries, by_wd

# -------------------------- Script JSON support --------------------------

_RANGE_RE = re.compile(r"\[(\d+)\s*(?:\.\.|\.)\s*(\d+)\]")  # accepts [1..5] and [1.5]

def expand_play_random(pattern: str) -> str:
    def repl(m):
        a, b = int(m.group(1)), int(m.group(2))
        if a > b: a, b = b, a
        return str(random.randint(a, b))
    return _RANGE_RE.sub(repl, pattern)

def load_actions_script() -> Dict[str, List[Dict[str, str]]]:
    path = _resolve_scriptjson_path()
    if not path:
        print("[ERROR] script.json not found.")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                print("[ERROR] Invalid top-level JSON type.")
                return {}
            print("[DEBUG] Loaded script.json")
            return data
    except Exception as ex:
        print(f"[ERROR] Failed reading script.json: {ex}")
        return {}

# -------------------------- GStreamer init --------------------------

Gst.init(None)

# Fallback if the hour-mapped file doesn't exist
FALLBACK_PATH = "/home/tme520/Videos/LPS/R/c10 - cheeky curious.mp4"

def path_for_hour(hour: int) -> str:
    base_dir = "/home/tme520/Videos/LPS/H"
    candidate = os.path.join(base_dir, f"c10 - {hour:02d}h.mp4")
    return candidate if os.path.exists(candidate) else FALLBACK_PATH

# -------------------------- Player Window --------------------------

class FullscreenPlayer(Gtk.Window):
    def __init__(self):
        super().__init__(title="LPS - C10")
        self.connect("destroy", self.on_destroy)

        # Load schedule + actions at startup
        self.schedule, self.schedule_by_weekday = load_schedule()
        print(f"[DEBUG] schedule: {self.schedule}")
        self.actions_script = load_actions_script()

        # Day roll state (for random offsets + fired flags)
        self._today_key = datetime.now().date()
        self._today_offsets: Dict[int, int] = {}      # idx -> minutes
        self._today_fired: Dict[int, bool] = {}       # idx -> fired
        self._seed_today_offsets()

        # Playback queue and state
        self.play_queue: List[str] = []
        self._playing: bool = False  # True when a video is currently playing

        # Minimal window chrome
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.SPLASHSCREEN)

        # Fullscreen on primary monitor
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        geo = mon.get_geometry()
        self.move(geo.x, geo.y)
        self.resize(geo.width, geo.height)
        self.connect("realize", self.on_window_realize)

        # Overlay stack
        self.overlay = Gtk.Overlay()
        self.add(self.overlay)

        # GStreamer pipeline
        self.pipe = Gst.ElementFactory.make("playbin", None)
        self.video_widget = None
        self.using_overlay = False

        gtk_sink = Gst.ElementFactory.make("gtksink", None)
        if gtk_sink:
            if gtk_sink.find_property("force-aspect-ratio"):
                gtk_sink.set_property("force-aspect-ratio", False)
            self.pipe.set_property("video-sink", gtk_sink)
            self.video_widget = gtk_sink.props.widget
            self.video_widget.set_hexpand(True)
            self.video_widget.set_vexpand(True)
            self.overlay.add(self.video_widget)
        else:
            self.da = Gtk.DrawingArea()
            self.da.set_hexpand(True)
            self.da.set_vexpand(True)
            self.da.set_size_request(geo.width, geo.height)
            self.overlay.add(self.da)
            sink = None
            for name in ("waylandsink", "glimagesink", "autovideosink", "ximagesink"):
                s = Gst.ElementFactory.make(name, None)
                if s: sink = s; break
            if sink and sink.find_property("force-aspect-ratio"):
                sink.set_property("force-aspect-ratio", False)
            if sink and sink.find_property("fullscreen"):
                try: sink.set_property("fullscreen", True)
                except Exception: pass
            self.pipe.set_property("video-sink", sink)
            self.using_overlay = True
            self.da.connect("realize", self.on_da_realize)

        # Clock label
        self.clock_label = Gtk.Label()
        self.clock_label.set_name("clock-label")
        self.clock_label.set_halign(Gtk.Align.END)
        self.clock_label.set_valign(Gtk.Align.START)
        self.clock_label.set_margin_top(16)
        self.clock_label.set_margin_end(24)
        self.clock_label.set_margin_start(24)
        self.clock_label.set_margin_bottom(16)
        self.overlay.add_overlay(self.clock_label)
        try:
            self.overlay.set_overlay_pass_through(self.clock_label, True)
        except Exception:
            pass

        # Toast label
        self.toast_label = Gtk.Label()
        self.toast_label.set_name("toast-label")
        self.toast_label.set_halign(Gtk.Align.CENTER)
        self.toast_label.set_valign(Gtk.Align.CENTER)
        self.toast_label.set_margin_bottom(40)
        self.overlay.add_overlay(self.toast_label)
        try:
            self.overlay.set_overlay_pass_through(self.toast_label, True)
        except Exception:
            pass
        self.toast_hide_source = None

        # CSS styling
        css = b"""
        #clock-label {
            font-size: 28pt; font-weight: 700; color: white;
            padding: 10px 14px; background-color: rgba(0,0,0,0.35);
            border-radius: 10px; text-shadow: 0 1px 2px rgba(0,0,0,0.7);
        }
        #toast-label {
            font-size: 16pt; font-weight: 600; color: white;
            padding: 8px 12px; background-color: rgba(0,0,0,0.55);
            border-radius: 12px; text-shadow: 0 1px 2px rgba(0,0,0,0.8);
        }
        .schedule-panel { background-color: rgba(0,0,0,0.45); border-radius: 10px; padding: 8px; }
        """
        provider = Gtk.CssProvider(); provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Schedule view (for visibility / debugging)
        self.build_schedule_view()
        self.populate_schedule_view()
        self.highlight_next_upcoming()
        GLib.timeout_add_seconds(60, self._periodic_highlight)

        # Black background
        try:
            self.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0,0,0,1))
        except Exception:
            pass

        # Hour-change playback state
        now = datetime.now()
        self.last_seen_hour = now.hour
        self.last_played_hour = None

        # Run the current hour video immediately (enqueue so it won't interrupt startup)
        GLib.idle_add(self.enqueue_hour_video, now.hour)

        # Tick every second: updates clock, hour-change, and checks scheduled actions
        self.update_clock()
        GLib.timeout_add_seconds(1, self.tick)

        # GStreamer bus
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self.on_eos)
        bus.connect("message::error", self.on_error)

        # Key bindings
        self.connect("key-press-event", self.on_key)

        # Show only the clock initially
        self.show_clock_only()

        # Startup sequence: queue wave hello (+ optional day greeting)
        self.enqueue_startup_sequence()

        # Action executor state
        self._action_running = False
        self._current_action_name = None
        self._step_timer_source = None

    # -------------------------- UI helpers --------------------------

    def show_clock_only(self):
        if self.video_widget is not None:
            self.video_widget.hide()
        elif hasattr(self, "da"):
            self.da.hide()

    def show_video_layer(self):
        if self.video_widget is not None:
            self.video_widget.show()
        elif hasattr(self, "da"):
            self.da.show()

    def show_toast(self, message: str, seconds: int = 4):
        if not message:
            return
        self.toast_label.set_text(message)
        self.toast_label.show()
        if self.toast_hide_source:
            GLib.source_remove(self.toast_hide_source)
        def _hide():
            self.toast_label.hide()
            self.toast_hide_source = None
            return False
        self.toast_hide_source = GLib.timeout_add_seconds(seconds, _hide)

    # -------------------------- Window / sink hooks --------------------------

    def on_window_realize(self, *_):
        gdk_win = self.get_window()
        if gdk_win:
            disp = gdk_win.get_display()
            blank = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.BLANK_CURSOR)
            gdk_win.set_cursor(blank)
        self.fullscreen()

    def on_da_realize(self, *_):
        if not self.using_overlay:
            return
        gdk_win = self.da.get_window()
        if not gdk_win:
            return
        handle = None
        if hasattr(gdk_win, "get_xid"):
            handle = gdk_win.get_xid()
        elif hasattr(gdk_win, "get_handle"):
            handle = gdk_win.get_handle()
        sink = self.pipe.get_property("video-sink")
        if handle and sink and hasattr(sink, "set_window_handle"):
            try:
                sink.set_window_handle(handle)
            except Exception:
                pass

    # -------------------------- Playback queue --------------------------

    def enqueue_file(self, path: str):
        if not path or not os.path.exists(path):
            print(f"[ERROR] File not found, skipping: {path}")
            return
        if not self._playing:
            # Nothing is playing; start immediately
            self.play_file(path)
        else:
            self.play_queue.append(path)
            print(f"[INFO] Queued: {path} (queue length: {len(self.play_queue)})")

    def enqueue_hour_video(self, hour: int):
        if self.last_played_hour == hour:
            return False
        path = path_for_hour(hour)
        if not path or not os.path.exists(path):
            print(f"[WARN][HourChange] Missing file for hour {hour:02d}: {path}")
            return False
        self.enqueue_file(path)
        self.last_played_hour = hour
        return False

    def play_file(self, path: str):
        if not path or not os.path.exists(path):
            print(f"[ERROR] File not found: {path}")
            # If this was supposed to start immediately, try next queued item
            self.try_play_next_in_queue()
            return
        print(f"[INFO] Playing {path}")
        self.show_video_layer()
        try: self.pipe.set_state(Gst.State.NULL)
        except Exception: pass
        uri = Gst.filename_to_uri(os.path.abspath(path))
        self.pipe.set_property("uri", uri)
        self.pipe.set_state(Gst.State.PLAYING)
        self._playing = True

    def try_play_next_in_queue(self):
        if self.play_queue:
            next_path = self.play_queue.pop(0)
            self.play_file(next_path)
        else:
            self._playing = False
            self.stop_to_clock()

    def stop_to_clock(self):
        try: self.pipe.set_state(Gst.State.NULL)
        except Exception: pass
        self.show_clock_only()

    # -------------------------- GStreamer bus --------------------------

    def on_eos(self, *_):
        # Video finished; start next if queued
        self._playing = False
        self.try_play_next_in_queue()

    def on_error(self, bus, msg):
        err, debug = msg.parse_error()
        print(f"[ERROR][GStreamer] Error: {err}; debug: {debug}")
        self._playing = False
        self.try_play_next_in_queue()

    # -------------------------- Keyboard --------------------------

    def on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            print("[DEBUG] Escape key pressed")
            self.quit_cleanly()
        elif event.keyval in (Gdk.KEY_s, Gdk.KEY_S):
            print("[DEBUG] S key pressed")
            self.toggle_schedule_visibility()
        elif event.keyval in (Gdk.KEY_r, Gdk.KEY_R):
            print("[DEBUG] R key pressed")
            self.schedule, self.schedule_by_weekday = load_schedule()
            self.populate_schedule_view()
            self._seed_today_offsets(force=True)
        elif event.keyval in (Gdk.KEY_a, Gdk.KEY_A):
            # Quick manual test: run test action if present
            print("[DEBUG] A key pressed")
            self.run_action("ACT_TEST_TIME_WEEKDAY")
        self.highlight_next_upcoming()
        GLib.timeout_add_seconds(60, self._periodic_highlight)

    # -------------------------- Clock + Hour change + Scheduler tick --------------------------

    def tick(self):
        self.update_clock()
        now = datetime.now()
        # New day? reset offsets / fired flags
        if now.date() != self._today_key:
            print("[INFO] New day")
            self._today_key = now.date()
            self._seed_today_offsets(force=True)

        # Hour change trigger: enqueue instead of interrupt
        if now.hour != self.last_seen_hour:
            print("[INFO] Change of hour")
            self.last_seen_hour = now.hour
            self.enqueue_hour_video(now.hour)

        # Check scheduled actions
        print("[DEBUG] Check scheduled actions (tick)")
        self._check_and_fire_scheduled(now)
        return True

    def update_clock(self):
        now = datetime.now()
        text = now.strftime("%A  %H:%M")
        print(f"[DEBUG] Updating clock: {text}")
        self.clock_label.set_text(text)

    # -------------------------- Startup sequence --------------------------

    def enqueue_startup_sequence(self):
        base_dir = "/home/tme520/Videos/LPS/R"
        hello = os.path.join(base_dir, "c10 - wave hello.mp4")
        if os.path.exists(hello):
            self.enqueue_file(hello)
        # Optional “nice {weekday}”
        names = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
        try:
            wd = datetime.now().weekday()
            print(f"[INFO] Day of the week: {wd}")
        except Exception:
            wd = 0
        daymsg = os.path.join(base_dir, f"c10 - nice {names[wd]}.mp4")
        if os.path.exists(daymsg):
            self.enqueue_file(daymsg)
        print(f"[Startup] Enqueued: {[p for p in [hello, daymsg] if p and os.path.exists(p)]}")

    # -------------------------- Schedule view --------------------------

    def _format_days(self, e):
        flags = [e.monday, e.tuesday, e.wednesday, e.thursday, e.friday, e.saturday, e.sunday]
        letters = ["M","T","W","T","F","S","S"]
        return "".join(l if f else "-" for l, f in zip(letters, flags))

    def build_schedule_view(self):
        self.schedule_store = Gtk.ListStore(str, str, str, str, str, str)
        self.schedule_view = Gtk.TreeView(model=self.schedule_store)
        self.schedule_view.set_headers_visible(True)
        self.schedule_view.set_enable_search(True)

        def add_col(title, col_id, align=0.0, width=None):
            renderer = Gtk.CellRendererText()
            renderer.set_property("xalign", align)
            column = Gtk.TreeViewColumn(title, renderer, text=col_id)
            if width:
                column.set_min_width(width)
                column.set_fixed_width(width)
                column.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            self.schedule_view.append_column(column)

        add_col("Days", 0, 0.5, 70)
        add_col("Time", 1, 0.5, 70)
        add_col("±Rand", 2, 0.5, 60)
        add_col("Dur(s)", 3, 0.5, 60)
        add_col("Text", 4, 0.0, 320)
        add_col("Action", 5, 0.0, 160)

        self.schedule_scroller = Gtk.ScrolledWindow()
        self.schedule_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.schedule_scroller.add(self.schedule_view)
        self.schedule_scroller.set_size_request(760, 260)
        self.schedule_scroller.set_margin_start(24)
        self.schedule_scroller.set_margin_end(24)
        self.schedule_scroller.set_margin_bottom(24)
        self.schedule_scroller.set_margin_top(24)

        self.schedule_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.schedule_box.get_style_context().add_class("schedule-panel")
        self.schedule_box.pack_start(self.schedule_scroller, True, True, 0)

        self.schedule_box.set_halign(Gtk.Align.CENTER)
        self.schedule_box.set_valign(Gtk.Align.END)

        self.overlay.add_overlay(self.schedule_box)
        self.schedule_box.show_all()
        self.schedule_visible = True

    def toggle_schedule_visibility(self):
        self.schedule_visible = not getattr(self, "schedule_visible", True)
        if self.schedule_visible:
            self.schedule_box.show_all()
        else:
            self.schedule_box.hide()

    def populate_schedule_view(self):
        if not hasattr(self, "schedule_store"):
            return
        self.schedule_store.clear()
        for e in self.schedule:
            days = self._format_days(e)
            time_str = f"{e.hour:02d}:{e.minute:02d}"
            rand_str = f"{e.random:02d}m"
            dur_str = f"{e.duration:d}"
            text = e.text or ""
            action = e.action or ""
            self.schedule_store.append([days, time_str, rand_str, dur_str, text, action])
            print(f"[INFO] Added {days} | {time_str} | {rand_str} | {dur_str} | {text} | {action} to the schedule")

    def find_next_event_index(self):
        print("[DEBUG] Looking for next event")
        if not self.schedule:
            return (None, None)
        now = datetime.now()
        today_idx = now.weekday()  # Monday=0
        print(f"[DEBUG] now: {now}")
        print(f"[DEBUG] today_idx: {today_idx}")
        candidates = []
        for day_offset in range(0, 7):
            day_idx = (today_idx + day_offset) % 7
            for idx, e in enumerate(self.schedule):
                flags = [e.monday, e.tuesday, e.wednesday, e.thursday, e.friday, e.saturday, e.sunday]
                if not flags[day_idx]:
                    continue
                target_date = (now + timedelta(days=day_offset)).date()
                cand_dt = datetime(target_date.year, target_date.month, target_date.day, e.hour, e.minute)
                if cand_dt >= now:
                    print(f"[INFO] Found: {cand_dt} {idx}")
                    candidates.append((cand_dt, idx))
        if not candidates:
            print("[DEBUG] None found")
            return (None, None)
        cand_dt, idx = min(candidates, key=lambda t: t[0])
        return (idx, cand_dt)

    def highlight_next_upcoming(self):
        if not hasattr(self, "schedule_view") or not hasattr(self, "schedule_store"):
            return
        idx, _ = self.find_next_event_index()
        if idx is None:
            return
        selection = self.schedule_view.get_selection()
        selection.unselect_all()
        path = Gtk.TreePath.new_from_string(str(idx))
        selection.select_path(path)
        self.schedule_view.set_cursor(path, None, False)
        self.schedule_view.scroll_to_cell(path, None, True, 0.5, 0.0)

    def _periodic_highlight(self):
        try:
            self.highlight_next_upcoming()
        finally:
            return True

    # -------------------------- Daily offsets + scheduler --------------------------

    def _seed_today_offsets(self, force: bool = False):
        if force:
            self._today_offsets.clear()
            self._today_fired.clear()
        # Create a stable random delay for each entry for the day
        for idx, e in enumerate(self.schedule):
            if idx not in self._today_offsets:
                rnd = max(0, int(e.random or 0))
                self._today_offsets[idx] = random.randint(0, rnd) if rnd > 0 else 0
            self._today_fired[idx] = False

    def _check_and_fire_scheduled(self, now: datetime):
        wd = now.weekday()
        for idx, e in enumerate(self.schedule):
            # Check weekday flag for *today*
            flags = [e.monday, e.tuesday, e.wednesday, e.thursday, e.friday, e.saturday, e.sunday]
            if not flags[wd]:
                continue
            if self._today_fired.get(idx):
                continue
            # compute scheduled time + random offset (minutes)
            offset_min = self._today_offsets.get(idx, 0)
            fire_dt = now.replace(hour=e.hour, minute=e.minute, second=0, microsecond=0) + timedelta(minutes=offset_min)
            # If schedule time already passed before we started the app today, still run it when we catch up
            # print(f"[DEBUG] now: {now} ({now.strftime('%Y-%m-%d %H:%M:%S')})")
            # print(f"[DEBUG] fire_dt: {fire_dt}")
            if now.strftime('%Y-%m-%d %H:%M:%S') >= fire_dt:
                self._today_fired[idx] = True
                if e.text:
                    self.show_toast(e.text)
                if e.action:
                    self.run_action(e.action)

    # -------------------------- Action runner --------------------------

    def run_action(self, action_name: str):
        steps = self.actions_script.get(action_name)
        if not steps:
            print(f"[Action] Unknown or empty action: {action_name}")
            return
        if self._action_running:
            print(f"[Action] Already running {self._current_action_name}; queuing additional steps alongside.")
        self._action_running = True
        self._current_action_name = action_name
        print(f"[Action] Starting {action_name}")
        self._run_steps_chain(list(steps), 0)

    def _run_steps_chain(self, steps: List[Dict[str, str]], idx: int):
        # If finished
        if idx >= len(steps):
            print(f"[Action] Finished {self._current_action_name}")
            self._action_running = False
            self._current_action_name = None
            # Do not force stop_to_clock here; playback queue may still run.
            return

        step = steps[idx]
        if not isinstance(step, dict) or len(step) != 1:
            print(f"[Action] Malformed step ignored: {step!r}")
            GLib.idle_add(self._run_steps_chain, steps, idx + 1)
            return

        (op, val), = step.items()
        op_u = op.strip().upper()

        if op_u == "PLAY":
            self.enqueue_file(str(val))
            GLib.idle_add(self._run_steps_chain, steps, idx + 1)
            return

        if op_u == "PLAY-RANDOM":
            path = expand_play_random(str(val))
            self.enqueue_file(path)
            GLib.idle_add(self._run_steps_chain, steps, idx + 1)
            return

        if op_u == "WAIT":
            m = re.search(r"(\d+)", str(val).lower())
            minutes = int(m.group(1)) if m else 0
            seconds = minutes * 60
            print(f"[Action] Waiting {minutes} minute(s)")
            self._cancel_step_timer()
            self._step_timer_source = GLib.timeout_add_seconds(
                seconds, self._after_wait_continue, steps, idx + 1
            )
            return

        if op_u == "TOAST-MESSAGE":
            self.show_toast(str(val))
            GLib.idle_add(self._run_steps_chain, steps, idx + 1)
            return

        # Unknown op -> skip
        print(f"[Action] Unknown op '{op}'; skipping.")
        GLib.idle_add(self._run_steps_chain, steps, idx + 1)

    def _after_wait_continue(self, steps, next_idx):
        self._step_timer_source = None
        self._run_steps_chain(steps, next_idx)
        return False

    def _cancel_step_timer(self):
        if self._step_timer_source:
            GLib.source_remove(self._step_timer_source)
            self._step_timer_source = None

    # -------------------------- Quit / destroy --------------------------

    def on_destroy(self, *_):
        self.quit_cleanly()

    def quit_cleanly(self):
        try: self.pipe.set_state(Gst.State.NULL)
        except Exception: pass
        Gtk.main_quit()

# -------------------------- App bootstrap --------------------------

if __name__ == "__main__":
    player = FullscreenPlayer()
    player.show_all()
    Gtk.main()
