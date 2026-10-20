"""Command line entry point: python3 -m lpsconsole [options]."""
from __future__ import annotations

import argparse
import datetime as _dt
import sys

from . import lpsdata as L
from .app import App


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="lps-console",
                                description="LPS Console - LPS Companion features in a Linux terminal.")
    p.add_argument("--data-dir", help="state folder (default: $XDG_DATA_HOME/lps-console)")
    p.add_argument("--export-dir", help="LPS folder receiving EXPORT/*.ics and WEIGHTS/*.csv "
                                        "(default: <data-dir>/LPS)")
    p.add_argument("--import", dest="import_dir", metavar="PATH",
                   help="import a Companion SD card root or LPS folder (ICS + weights CSV) before starting")
    p.add_argument("--import-save", action="store_true", help="with --import, also read SAVE_A/B.BIN")
    p.add_argument("--no-ui", action="store_true", help="do not start the interface (import/report only)")
    p.add_argument("--lang", choices=L.LANGUAGES, help="set and save the interface language")
    args = p.parse_args(argv)

    store = L.Store(args.data_dir)
    load_error = ""
    try:
        state = store.load(new_today=_dt.date.today())
    except ValueError as ex:
        load_error = str(ex)
        print("LPS Console: %s (writes blocked)" % ex, file=sys.stderr)
        state = L.State(today=L.Day(date=_dt.date.today()))
    app = App(store, state, export_dir=args.export_dir, load_error=load_error)
    if args.lang and not load_error:
        nxt = app.state.copy()
        nxt.language = args.lang
        app.commit(nxt)
    if args.import_dir:
        rep = app.run_import(args.import_dir, args.import_save)
        print("\n".join(app.import_lines))
        if rep is not None and not args.no_ui:
            app.go("Home")
    if args.no_ui:
        return 0
    from .tui import run
    run(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
