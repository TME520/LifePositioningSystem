# Fixtures

`sdcard/LPS/` mirrors a PicoCalc SD card and was produced by the **real**
LPS Companion v1.11 code (`src/main.cpp`: `exportIcs`, `exportWeightCsv`,
A/B `SAVE_*.BIN` records), compiled on the host with the Companion's own
FatFs stand-ins. Source: `gen_fixtures.cpp`.

```sh
COMPANION=/path/to/LPS-Companion-PicoCalc
g++ -std=c++17 -I"$COMPANION/tests/stubs" -I"$COMPANION/src" \
    -DCOMPANION_MAIN="\"$COMPANION/src/main.cpp\"" gen_fixtures.cpp -o /tmp/gen
mkdir -p /tmp/out && cd /tmp/out && /tmp/gen   # writes *.ics, 2026.csv, SAVE_A/B.BIN
```

Contents: an English day with an escaped note, a French day, an empty day on
31 December, a weights CSV with blank fields and a duplicate-protected retry,
and a format-6 save (one closed day, two Kanban tasks, one reminder).
