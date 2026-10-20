// Generates golden fixtures with the REAL LPS Companion PicoCalc adapter
// (src/main.cpp: exportIcs, exportWeightCsv, A/B SAVE records), compiled on
// the host with the Companion's own FatFs stand-ins (tests/stubs).
//
//   COMPANION=/path/to/LPS-Companion-PicoCalc
//   g++ -std=c++17 -I"$COMPANION/tests/stubs" -I"$COMPANION/src" \
//       -DCOMPANION_MAIN="\"$COMPANION/src/main.cpp\"" gen_fixtures.cpp -o gen
//   (cd an-empty-dir && ./gen)
#define main pico_firmware_main
#include COMPANION_MAIN
#undef main
#include <cassert>
#include <string>
unsigned char fontStub[4+224*12]{};
extern "C" {
unsigned char* MainFont=fontStub;
void draw_bitmap_spi(int,int,int,int,int,int,int,unsigned char*){}
}
int main(){
    using namespace lps;
    PicoCalc disk;disk.init();
    // 1. English day: Walk, Diet, Bible (3 activities -> 40 XP), weights, escaped note.
    Day en;en.date={2026,10,1};en.flags=(1u<<0)|(1u<<1)|(1u<<2);en.expected=110500;en.actual=111200;
    std::strcpy(en.note.data(),"Good day, rain; path C:\\tmp");
    assert(disk.exportIcs(en,140,40,Language::English));assert(disk.exportWeightCsv(en));
    // 2. French day: Régime, Congé, Lecture, Shopping (4 -> 50 XP), only actual weight.
    Day fr;fr.date={2026,10,2};fr.flags=(1u<<1)|(1u<<9)|(1u<<14)|(1u<<15);fr.actual=109750;
    std::strcpy(fr.note.data(),"Journee calme");
    assert(disk.exportIcs(fr,190,50,Language::French));assert(disk.exportWeightCsv(fr));
    // 3. Empty English day at a month/year boundary: no activities, no weight, no note.
    Day empty;empty.date={2026,12,31};
    assert(disk.exportIcs(empty,190,0,Language::English));assert(disk.exportWeightCsv(empty));
    // Repeated CSV export must not duplicate the date.
    assert(disk.exportWeightCsv(fr));
    // 4. A real A/B save driven through the application state machine.
    App app(disk);app.start();
    app.key('0');for(int i=0;i<10;++i)app.key(Backspace);for(char c:std::string("2026-10-05"))app.key(c);app.key(Enter);
    app.key('1');app.key('1');app.key('3');app.key('5');app.key(Escape);
    app.key('2');for(char c:std::string("Saved on PicoCalc"))app.key(c);app.key(Enter);
    app.key('3');for(char c:std::string("100,5"))app.key(c);app.key(Down);for(char c:std::string("101.25"))app.key(c);app.key(Enter);
    app.key('4');app.key('N');for(char c:std::string("Write console app"))app.key(c);app.key(Enter);
    app.key('N');for(char c:std::string("Test fixtures"))app.key(c);app.key(Enter);app.key(F2);app.key(Escape);
    app.key('8');app.key('N');app.key(Down);for(int i=0;i<3;++i)app.key(Backspace);app.key('7');app.key(Down);for(char c:std::string("Buy bread"))app.key(c);app.key(Enter);app.key(Escape);
    // Close the day (item 10: Up from the first Home entry), then confirm.
    app.key(Up);app.key(Enter);app.key(Enter);app.key(Enter);
    assert(app.data().count==1&&app.data().xp==40&&app.data().today.date.day==6);
    app.key('1');app.key('7');app.key(Escape);
    std::puts("fixtures generated");
}
