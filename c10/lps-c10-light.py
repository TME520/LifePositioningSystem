
#!/usr/bin/env python3
import gi, os, json
from datetime import datetime
gi.require_version('Gtk', '3.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gtk, Gst, Gdk, GLib

import csv
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

# ---- Schedule support ----
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
    random: int        # 0..15
    duration: int      # seconds, 1..600
    text: str
    action: str
    data: str          # expected "FF"

def _resolve_schedule_path() -> Optional[str]:
    """Try common locations for schedule.csv and return the first that exists."""
    from pathlib import Path as _P
    candidates = [
        "schedule.csv",
        str(_P(__file__).with_name("schedule.csv")),
        str(_P.cwd() / "schedule.csv"),
    ]
    for c in candidates:
        try:
            if _P(c).exists():
                return c
        except Exception:
            pass
    return None

def load_schedule() -> Tuple[List[ScheduleEntry], Dict[int, List[ScheduleEntry]]]:
    """Load schedule.csv (semicolon-delimited) and build entries + per-weekday index.
    Skips the first row (header). Returns (entries, by_weekday).
    """
    import csv as _csv
    schedule_path = _resolve_schedule_path()
    entries: List[ScheduleEntry] = []
    by_wd: Dict[int, List[ScheduleEntry]] = {i: [] for i in range(7)}
    if not schedule_path:
        print("[Schedule] schedule.csv not found (searched CWD and script dir).")
        return entries, by_wd

    try:
        with open(schedule_path, newline="", encoding="utf-8") as f:
            reader = _csv.reader(f, delimiter=";")
            # Skip header explicitly
            try:
                next(reader)
            except StopIteration:
                return entries, by_wd

            for row_num, row in enumerate(reader, start=2):
                if not row or all((c.strip() == "" for c in row)):
                    continue
                # Pad/truncate to expected length 14
                row = (row + [""] * 14)[:14]
                try:
                    m, tu, w, th, fr, sa, su = [int((v or "0").strip() or "0") for v in row[:7]]
                    hour = int((row[7] or "0").strip() or "0")
                    minute = int((row[8] or "0").strip() or "0")
                    rnd = int((row[9] or "0").strip() or "0")
                    dur = int((row[10] or "0").strip() or "0")
                    text = (row[11] or "").strip()
                    action = (row[12] or "").strip()
                    data = (row[13] or "").strip()
                    e = ScheduleEntry(m, tu, w, th, fr, sa, su, hour, minute, rnd, dur, text, action, data)
                    entries.append(e)
                    # index by weekday(s) that are enabled
                    flags = [m, tu, w, th, fr, sa, su]
                    for wd, flag in enumerate(flags):  # Monday=0 .. Sunday=6
                        if flag:
                            by_wd[wd].append(e)
                except Exception as ex:
                    print(f"[Schedule] Row {row_num} parse error: {ex} | {row}")
    except Exception as ex:
        print(f"[Schedule] Failed reading schedule.csv: {ex}")
        return entries, by_wd

    print(f"[Schedule] Loaded {len(entries)} entries from {schedule_path}.")
    return entries, by_wd

# -------------------------- Config support --------------------------

def _resolve_config_path() -> str:
    return os.path.join(os.path.dirname(__file__), "lps.rc")

def load_config() -> Dict[str, str]:
    path = _resolve_config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                print(f"[DEBUG] Loaded config from {path}")
                return data
    except Exception as ex:
        print(f"[WARN] Failed reading config {path}: {ex}")
    return {}

def save_config(data: Dict[str, str]) -> None:
    path = _resolve_config_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        print(f"[INFO] Saved config to {path}")
    except Exception as ex:
        print(f"[ERROR] Failed writing config {path}: {ex}")

Gst.init(None)

# Fallback if the hour-mapped file doesn't exist
FALLBACK_PATH = "/home/tme520/Videos/LPS/R/c10 - cheeky curious.mp4"

def path_for_hour(hour: int) -> str:
    base_dir = "/home/tme520/Videos/LPS/H/HD/FR"
    candidate = os.path.join(base_dir, f"c10 - {hour:02d}h.mp4")
    return candidate if os.path.exists(candidate) else FALLBACK_PATH

class FullscreenPlayer(Gtk.Window):
    def __init__(self):
        super().__init__(title="LPS - C10")
        self.connect("destroy", self.on_destroy)

        # Load schedule at startup
        self.schedule, self.schedule_by_weekday = load_schedule()
        self.config = load_config()
        self.selected_language = self.config.get("language", "English")

        # Minimal/invisible window chrome
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.SPLASHSCREEN)

        # Place on primary monitor and size to fill it
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        geo = mon.get_geometry()
        self.move(geo.x, geo.y)
        self.resize(geo.width, geo.height)

        # Hide cursor when realized, then request fullscreen
        self.connect("realize", self.on_window_realize)

        # Prepare an Overlay so we can stack a clock label above the video
        self.overlay = Gtk.Overlay()
        self.add(self.overlay)

        # --- GStreamer pipeline
        self.pipe = Gst.ElementFactory.make("playbin", None)

        # Prefer gtksink; fall back otherwise
        self.video_widget = None
        self.using_overlay = False

        gtk_sink = Gst.ElementFactory.make("gtksink", None)
        if gtk_sink:
            if gtk_sink.find_property("force-aspect-ratio"):
                gtk_sink.set_property("force-aspect-ratio", False)  # cover the screen
            self.pipe.set_property("video-sink", gtk_sink)

            # Embed the gtk widget from gtksink
            self.video_widget = gtk_sink.props.widget
            self.video_widget.set_hexpand(True)
            self.video_widget.set_vexpand(True)

            self.overlay.add(self.video_widget)
        else:
            # Fallback: DrawingArea + manual handle sink
            self.da = Gtk.DrawingArea()
            self.da.set_hexpand(True)
            self.da.set_vexpand(True)
            self.da.set_size_request(geo.width, geo.height)
            self.overlay.add(self.da)

            sink = None
            for name in ("waylandsink", "glimagesink", "autovideosink", "ximagesink"):
                s = Gst.ElementFactory.make(name, None)
                if s:
                    sink = s
                    break

            if sink and sink.find_property("force-aspect-ratio"):
                sink.set_property("force-aspect-ratio", False)
            if sink and sink.find_property("fullscreen"):
                try:
                    sink.set_property("fullscreen", True)
                except Exception:
                    pass

            self.pipe.set_property("video-sink", sink)
            self.using_overlay = True
            self.da.connect("realize", self.on_da_realize)

        # Add the clock label as an overlayed widget
        self.clock_label = Gtk.Label()
        self.clock_label.set_name("clock-label")
        self.clock_label.set_halign(Gtk.Align.END)   # right align
        self.clock_label.set_valign(Gtk.Align.START) # top
        self.clock_label.set_margin_top(16)
        self.clock_label.set_margin_end(24)
        self.clock_label.set_margin_start(24)
        self.clock_label.set_margin_bottom(16)
        self.overlay.add_overlay(self.clock_label)
        # ensure it receives no input (pass events through)
        try:
            self.overlay.set_overlay_pass_through(self.clock_label, True)
        except Exception:
            pass

        # Style via CSS (semi-transparent backdrop, rounded corners, larger text)
        css = b"""        #clock-label {
            font-size: 28pt;
            font-weight: 700;
            color: white;
            padding: 10px 14px;
            background-color: rgba(0, 0, 0, 0.35);
            border-radius: 10px;
            text-shadow: 0 1px 2px rgba(0,0,0,0.7);
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # ---- Schedule panel CSS ----
        css2 = b".schedule-panel { background-color: rgba(0,0,0,0.45); border-radius: 10px; padding: 8px; }"
        provider2 = Gtk.CssProvider()
        provider2.load_from_data(css2)
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider2, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # ---- Config panel CSS ----
        css3 = b"""
        .config-panel { background-color: rgba(0,0,0,0.7); border-radius: 12px; padding: 20px 28px; }
        #config-title { font-size: 20pt; font-weight: 700; color: white; margin-bottom: 8px; }
        .config-section-title { font-size: 14pt; font-weight: 600; color: white; }
        .config-option { font-size: 12pt; color: white; }
        .config-save-button { font-size: 12pt; font-weight: 700; padding: 8px 16px; }
        """
        provider3 = Gtk.CssProvider()
        provider3.load_from_data(css3)
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider3, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Build and fill the schedule table
        self.build_schedule_view()
        self.build_config_view()
        self.populate_schedule_view()
        self.highlight_next_upcoming()
        GLib.timeout_add_seconds(60, self._periodic_highlight)

        # Start with a plain black background so when video hides you still see the clock cleanly
        try:
            self.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0,0,0,1))
        except Exception:
            pass

        # State for hour-change playback
        now = datetime.now()
        self.last_seen_hour = now.hour      # hour we've last observed (prevents multiple triggers in the same hour)
        self.last_played_hour = None        # last hour for which we started playback
        self.pending_hour_to_play = None    # when set, indicates a new hour just started

        GLib.idle_add(self.play_for_hour, now.hour)
        # Initial clock update + schedule tick every second (updates clock AND watches for hour change)
        self.update_clock()
        GLib.timeout_add_seconds(1, self.tick)

        # GStreamer bus
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self.on_eos)
        bus.connect("message::error", self.on_error)

        # Optional: allow ESC to quit at any time
        self.connect("key-press-event", self.on_key)

        # Show only the clock initially
        self.show_clock_only()

        # --- Startup 2-step sequence
        self.in_startup = False
        self.startup_queue = []
        self.prepare_startup_sequence()
        if self.in_startup:
            GLib.idle_add(self.start_next_in_queue)

    def on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.quit_cleanly()
        elif event.keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.toggle_schedule_visibility()
        elif event.keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self.schedule, self.schedule_by_weekday = load_schedule()
            self.populate_schedule_view()
        elif event.keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.toggle_config_visibility()
        self.highlight_next_upcoming()
        GLib.timeout_add_seconds(60, self._periodic_highlight)

    def tick(self):
        """Called every second: refresh clock and detect hour changes."""
        self.update_clock()

        now = datetime.now()
        if now.hour != self.last_seen_hour:
            # Hour just changed
            self.last_seen_hour = now.hour
            self.pending_hour_to_play = now.hour
            # Start playback immediately on the change
            self.play_for_hour(now.hour)
        return True  # keep timer running

    def update_clock(self):
        now = datetime.now()
        # Day of week (e.g., Monday) + 24h time HH:MM
        text = now.strftime("%A  %H:%M")
        self.clock_label.set_text(text)

    def show_clock_only(self):
        # Hide the video layer so only the clock remains
        if self.video_widget is not None:
            self.video_widget.hide()
        elif hasattr(self, "da"):
            self.da.hide()

    def show_video_layer(self):
        if self.video_widget is not None:
            self.video_widget.show()
        elif hasattr(self, "da"):
            self.da.show()

    # -------------------------- Config view --------------------------

    def build_config_view(self):
        self.config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.config_box.get_style_context().add_class("config-panel")
        self.config_box.set_halign(Gtk.Align.CENTER)
        self.config_box.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label(label="Configuration")
        title.set_name("config-title")
        title.set_halign(Gtk.Align.CENTER)
        self.config_box.pack_start(title, False, False, 0)

        language_label = Gtk.Label(label="Language")
        language_label.get_style_context().add_class("config-section-title")
        language_label.set_halign(Gtk.Align.START)
        self.config_box.pack_start(language_label, False, False, 0)

        language_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        language_box.set_halign(Gtk.Align.CENTER)
        self.language_en_button = Gtk.RadioButton.new_with_label_from_widget(None, "English")
        self.language_fr_button = Gtk.RadioButton.new_with_label_from_widget(self.language_en_button, "French")
        self.language_en_button.get_style_context().add_class("config-option")
        self.language_fr_button.get_style_context().add_class("config-option")
        self.language_en_button.connect("toggled", self.on_language_toggled, "English")
        self.language_fr_button.connect("toggled", self.on_language_toggled, "French")
        if self.selected_language == "French":
            self.language_fr_button.set_active(True)
        else:
            self.language_en_button.set_active(True)
        language_box.pack_start(self.language_en_button, False, False, 0)
        language_box.pack_start(self.language_fr_button, False, False, 0)
        self.config_box.pack_start(language_box, False, False, 0)

        save_button = Gtk.Button(label="Save")
        save_button.get_style_context().add_class("config-save-button")
        save_button.set_halign(Gtk.Align.CENTER)
        save_button.connect("clicked", self.on_save_config)
        self.config_box.pack_start(save_button, False, False, 8)

        self.overlay.add_overlay(self.config_box)
        try:
            self.overlay.set_overlay_pass_through(self.config_box, False)
        except Exception:
            pass
        self.config_box.hide()
        self.config_visible = False

    def on_language_toggled(self, button, language: str):
        if button.get_active():
            self.selected_language = language

    def on_save_config(self, _button):
        save_config({"language": self.selected_language})
        print("[INFO] Configuration saved")

    def toggle_config_visibility(self):
        self.config_visible = not getattr(self, "config_visible", False)
        if self.config_visible:
            self.config_box.show_all()
        else:
            self.config_box.hide()

    # Window realize: hide cursor & go fullscreen
    def on_window_realize(self, *_):
        gdk_win = self.get_window()
        if gdk_win:
            disp = gdk_win.get_display()
            blank = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.BLANK_CURSOR)
            gdk_win.set_cursor(blank)
        self.fullscreen()

    # Bind native window handle for fallback sinks
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
            handle = gdk_win.get_handle()  # Wayland

        sink = self.pipe.get_property("video-sink")
        if handle and sink and hasattr(sink, "set_window_handle"):
            try:
                sink.set_window_handle(handle)
            except Exception:
                pass

    def play_for_hour(self, hour: int):
        """Start playback for the given hour, once per hour."""
        if getattr(self, "in_startup", False):
            print("[HourChange] Skipped due to startup sequence in progress")
            return
        if self.last_played_hour == hour:
            return  # already played this hour
        path = path_for_hour(hour)
        if not path or not os.path.exists(path):
            print(f"[Error] File not found for hour {hour:02d}: {path}")
            self.stop_to_clock()
            return
        # ensure video widget is visible
        self.show_video_layer()
        # set pipeline
        self.pipe.set_state(Gst.State.NULL)
        uri = Gst.filename_to_uri(os.path.abspath(path))
        self.pipe.set_property("uri", uri)
        self.pipe.set_state(Gst.State.PLAYING)
        self.last_played_hour = hour
        print(f"[HourChange] {hour:02d}: started {path}")

    def play_file(self, path: str):
        """Play a specific file path immediately."""
        if not path or not os.path.exists(path):
            print(f"[Startup] File not found: {path}")
            self.stop_to_clock()
            return
        self.show_video_layer()
        try:
            self.pipe.set_state(Gst.State.NULL)
        except Exception:
            pass
        uri = Gst.filename_to_uri(os.path.abspath(path))
        self.pipe.set_property("uri", uri)
        self.pipe.set_state(Gst.State.PLAYING)
        print(f"[Startup] Playing {path}")

    def prepare_startup_sequence(self):
        base_dir = "/home/tme520/Videos/LPS/R/HD/FR"
        queue = []
        hello = os.path.join(base_dir, "c10 - wave hello.mp4")
        if os.path.exists(hello):
            queue.append(hello)
        # weekday: Monday=0 .. Sunday=6
        names = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
        try:
            wd = datetime.now().weekday()
        except Exception:
            wd = 0
        day_name = names[wd]
        after = os.path.join(base_dir, f"c10 - nice {day_name}.mp4")
        if os.path.exists(after):
            queue.append(after)
        self.startup_queue = queue
        self.in_startup = bool(self.startup_queue)
        print(f"[Startup] Queue: {self.startup_queue}")

    def start_next_in_queue(self):
        if not getattr(self, "startup_queue", None):
            self.in_startup = False
            return False  # stop idle handler
        next_path = self.startup_queue.pop(0)
        self.play_file(next_path)
        # If queue now empty, keep in_startup True until EOS arrives for last item
        return False  # run once

    def stop_to_clock(self):
        """Stop playback and show only the clock on a black background."""
        try:
            self.pipe.set_state(Gst.State.NULL)
        except Exception:
            pass
        self.show_clock_only()

    # Bus handlers
    def on_eos(self, *_):
        # If in startup sequence, chain to next item if any
        if getattr(self, 'in_startup', False):
            if getattr(self, 'startup_queue', None):
                GLib.idle_add(self.start_next_in_queue)
                return
            else:
                # startup finished
                self.in_startup = False
        # End of the video: return to idle clock view
        self.stop_to_clock()

    def on_error(self, bus, msg):
        err, debug = msg.parse_error()
        print(f"[GStreamer] Error: {err}; debug: {debug}")
        self.stop_to_clock()

    def on_destroy(self, *_):
        self.quit_cleanly()

    def quit_cleanly(self):
        try:
            self.pipe.set_state(Gst.State.NULL)
        except Exception:
            pass
        Gtk.main_quit()

    # ---- Schedule table (TreeView) ----
    def _format_days(self, e):
        flags = [e.monday, e.tuesday, e.wednesday, e.thursday, e.friday, e.saturday, e.sunday]
        letters = ["M","T","W","T","F","S","S"]
        return "".join(l if f else "-" for l, f in zip(letters, flags))

    def build_schedule_view(self):
        """Create a scrolled TreeView overlay showing schedule.csv entries."""
        # ListStore columns: Days(str), Time(str), Rand(str), Dur(str), Text(str), Action(str)
        self.schedule_store = Gtk.ListStore(str, str, str, str, str, str)

        self.schedule_view = Gtk.TreeView(model=self.schedule_store)
        self.schedule_view.set_headers_visible(True)
        self.schedule_view.set_enable_search(True)

        # Helper to add text column
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

        # Put the scroller inside a frame-like box for styling
        self.schedule_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.schedule_box.get_style_context().add_class("schedule-panel")
        self.schedule_box.pack_start(self.schedule_scroller, True, True, 0)

        # Align bottom-left as an overlay
        self.schedule_box.set_halign(Gtk.Align.CENTER)
        self.schedule_box.set_valign(Gtk.Align.END)

        # Add to overlay above video
        self.overlay.add_overlay(self.schedule_box)

        # Make it visible by default (as asked)
        self.schedule_box.show_all()
        self.schedule_visible = True

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

    
    def find_next_event_index(self):
        """Return (index, datetime) of the next upcoming event considering weekday flags and HH:MM.
        If none found, return (None, None)."""
        try:
            from datetime import datetime, timedelta
        except Exception:
            return (None, None)
        if not self.schedule:
            return (None, None)

        now = datetime.now()
        # Determine weekday index mapping consistent with parser (likely Monday=0 ... Sunday=6)
        today_idx = now.weekday()  # Monday=0
        # Build list of (abs_datetime, idx) candidates across next 7 days
        candidates = []
        for day_offset in range(0, 7):
            day_idx = (today_idx + day_offset) % 7
            for idx, e in enumerate(self.schedule):
                flags = [e.monday, e.tuesday, e.wednesday, e.thursday, e.friday, e.saturday, e.sunday]
                if not flags[day_idx]:
                    continue
                # target date for this candidate
                target_date = (now + timedelta(days=day_offset)).date()
                cand_dt = datetime(target_date.year, target_date.month, target_date.day, e.hour, e.minute)
                if cand_dt >= now:
                    candidates.append((cand_dt, idx))
        if not candidates:
            return (None, None)
        cand_dt, idx = min(candidates, key=lambda t: t[0])
        return (idx, cand_dt)

    def highlight_next_upcoming(self):
        """Select and scroll to the next upcoming event; keep it visually obvious."""
        if not hasattr(self, "schedule_view") or not hasattr(self, "schedule_store"):
            return
        idx, _ = self.find_next_event_index()
        if idx is None:
            return
        # Select and scroll
        selection = self.schedule_view.get_selection()
        selection.unselect_all()
        path = Gtk.TreePath.new_from_string(str(idx))
        selection.select_path(path)
        self.schedule_view.set_cursor(path, None, False)
        # Scroll so the selected row is vertically centered within the scroller viewport
        self.schedule_view.scroll_to_cell(path, None, True, 0.5, 0.0)

    def _periodic_highlight(self):
        # Called by GLib.timeout_add_seconds to re-evaluate which event is next
        try:
            self.highlight_next_upcoming()
        finally:
            return True  # keep the timer running
def toggle_schedule_visibility(self):
        self.schedule_visible = not getattr(self, "schedule_visible", True)
        if self.schedule_visible:
            self.schedule_box.show_all()
        else:
            self.schedule_box.hide()


if __name__ == "__main__":
    player = FullscreenPlayer()
    player.show_all()
    Gtk.main()
