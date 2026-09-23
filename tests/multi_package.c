#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "POUS.h"
TIME __CURRENT_TIME;
#define __LOCATED_VAR(type, name, ...) type name##_storage; type *name = &name##_storage;
#include "LOCATED_VARIABLES.h"
#undef __LOCATED_VAR
#define WORD(address) (*__QW##address)
#define INPUT(address) (*__IW##address)
#define BIT(address, bit) (*__QX##address##_##bit)

static SORTER plc;
static void scan(void) { SORTER_body__(&plc); }
static int token(int slot) { return slot ? WORD(542) : WORD(530); }
static int serial(int slot) { return slot ? WORD(543) : WORD(531); }
static int seq(int slot) { return slot ? WORD(544) : WORD(532); }
static int barcode(int slot) { return slot ? WORD(545) : WORD(533); }
static int state(int slot) { return slot ? WORD(546) : WORD(534); }
static int actual(int slot) { return slot ? WORD(548) : WORD(536); }
static int reason(int slot) { return slot ? WORD(549) : WORD(537); }
static int scan_tick(int slot) { return slot ? WORD(550) : WORD(538); }
static int accept_tick(int slot) { return slot ? WORD(551) : WORD(539); }
static int divert_tick(int slot) { return slot ? WORD(552) : WORD(540); }

static void command(int id, int op, int slot, int wrong_token, int dest) {
    WORD(520) = op; WORD(521) = slot;
    WORD(522) = token(slot) + wrong_token;
    WORD(523) = serial(slot); WORD(524) = seq(slot);
    WORD(525) = barcode(slot); WORD(526) = dest;
    WORD(527) = WORD(509); WORD(528) = id;
    scan();
}

int main(int argc, char **argv) {
    assert(argc == 2);
    int repeat = !strcmp(argv[1], "repeat");
    int timeout = !strcmp(argv[1], "timeout");
    int reuse = !strcmp(argv[1], "reuse");
    assert(repeat || timeout || reuse || !strcmp(argv[1], "different"));
    SORTER_init__(&plc, 0); scan();
    INPUT(158) = INPUT(169) = INPUT(180) = 32766;
    INPUT(160) = INPUT(171) = INPUT(182) = 7;
    INPUT(164) = INPUT(175) = INPUT(186) = 1;
    scan();
    INPUT(158) = INPUT(169) = INPUT(180) = 32767;
    INPUT(160) = INPUT(171) = INPUT(182) = 6;
    scan();
    assert(WORD(255) == 0);
    BIT(114, 2) = BIT(114, 3) = 1;
    BIT(110, 3) = BIT(110, 4) = 0;
    BIT(110, 0) = 1;
    INPUT(105) = INPUT(132) = INPUT(141) = INPUT(150) = 1750;
    WORD(207) = 14;
    int last_trigger = 0, commanded[2] = {0, 0};
    for (int step = 0; step < 100; ++step) {
        scan();
        if (WORD(220) >= 2) WORD(207) = 1000;
        if (WORD(118) != last_trigger && WORD(118) < 32766) {
            last_trigger = WORD(118);
            INPUT(158) = last_trigger;
            INPUT(159) = WORD(119) == 3 ? 7003 :
                         (repeat || WORD(119) == 1) ? 6001 : 5002;
            INPUT(160) = 0; INPUT(164) = 1;
        }
        for (int slot = 0; slot < 2; ++slot) {
            if (state(slot) != 2 || commanded[slot]) continue;
            commanded[slot] = 1;
            if (slot == 1) {
                command(10, 1, slot, 1, 9);
                assert(WORD(529) == 2 && WORD(554) == 10 && state(slot) == 2);
            }
            if (!timeout || slot == 0) {
                command(slot + 1, 1, slot, 0, slot ? 5 : 2);
                assert(WORD(529) == 1 && WORD(554) == slot + 1 && state(slot) >= 3);
                command(slot + 5, 1, slot, 0, 9);
                assert(WORD(529) == 2 && (slot ? WORD(547) : WORD(535)) == (slot ? 5 : 2));
            }
        }
        if ((state(0) == 5 || state(0) == 7) &&
            (state(1) == 5 || state(1) == 6 || state(1) == 7)) break;
    }
    assert(WORD(220) == 2);
    assert(state(0) == 5 && actual(0) == 2 && reason(0) == 0);
    assert(scan_tick(0) && accept_tick(0) >= scan_tick(0));
    assert(divert_tick(0) > accept_tick(0));
    if (timeout) {
        assert(state(1) == 6 && actual(1) == 0 && reason(1) == 1);
        assert(WORD(218) == 1 && WORD(223) == 1);
    } else {
        assert(state(1) == 5 && actual(1) == 5 && reason(1) == 0);
        assert(WORD(226) == 1 && WORD(223) == 1);
        assert(divert_tick(1) > accept_tick(1));
    }
    if (repeat) assert(barcode(0) == barcode(1));
    else assert(barcode(0) != barcode(1));
    printf("%s: tokens %d/%d barcodes %d/%d outcomes %d/%d trailers %d/%d scan-to-divert %d/%d scans\n",
           argv[1], token(0), token(1), barcode(0), barcode(1),
           state(0), state(1), actual(0), actual(1),
           divert_tick(0) - scan_tick(0), divert_tick(1) - scan_tick(1));
    command(20, 2, 0, 0, 0);
    assert(WORD(529) == 3 && state(0) == 0);
    if (reuse) {
        WORD(207) = 14;
        for (int step = 0; step < 100 && WORD(220) < 3; ++step) scan();
        assert(WORD(220) == 3 && token(0) == 3 && serial(0) == 3);
        WORD(207) = 1000;
        for (int step = 0; step < 25 && state(0) != 2; ++step) {
            scan();
            if (WORD(118) != last_trigger && WORD(118) < 32766) {
                last_trigger = WORD(118);
                INPUT(158) = last_trigger; INPUT(159) = 7003;
                INPUT(160) = 0; INPUT(164) = 1;
            }
        }
        assert(state(0) == 2 && barcode(0) == 7003);
        command(21, 1, 0, -2, 9); /* old token 1 cannot route new token 3 */
        assert(WORD(529) == 2 && state(0) == 2);
        command(22, 1, 0, 0, 8);
        assert(WORD(529) == 1);
        for (int step = 0; step < 35 && state(0) != 5; ++step) scan();
        assert(state(0) == 5 && actual(0) == 8 && WORD(229) == 1);
        puts("reuse: released slot accepted token 3; stale token 1 rejected; trailer 8 loaded without reset");
    }
    return 0;
}
