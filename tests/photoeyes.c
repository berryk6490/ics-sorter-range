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
static int scans, raw, writer, wrong;
static void scan(void) {
    if (++scans % 5 == 0) {
        WORD(565) = WORD(558); WORD(566) = WORD(559);
        WORD(567) = WORD(567) % 30000 + 1;
    }
    WORD(590) = WORD(590) % 30000 + 1;
    if (writer && WORD(530)) {
        WORD(690) = WORD(558); WORD(691) = WORD(559);
        WORD(692) = WORD(509); WORD(693) = WORD(530) + wrong;
        WORD(694) = WORD(531); WORD(695) = WORD(644);
        WORD(696) = raw; WORD(697) = WORD(697) % 30000 + 1;
    }
    SORTER_body__(&plc);
}
static void scans_for(int n) { while (n--) scan(); }
static void beam(int sensor, int blocked) {
    raw = blocked ? 1 << (sensor - 1) : 0;
    scans_for(4);
}
static void setup(void) {
    SORTER_init__(&plc, 0); scan();
    INPUT(158) = INPUT(169) = INPUT(180) = 32766;
    INPUT(160) = INPUT(171) = INPUT(182) = 7;
    INPUT(164) = INPUT(175) = INPUT(186) = 1; scan();
    INPUT(158) = INPUT(169) = INPUT(180) = 32767;
    INPUT(160) = INPUT(171) = INPUT(182) = 6; scan();
    assert(WORD(255) == 0);
    BIT(110, 3) = BIT(110, 4) = 0;
    WORD(555) = 77; WORD(556) = 0; WORD(557) = 1; scan();
    WORD(587) = 77; WORD(588) = 0; WORD(589) = 1;
    BIT(114, 2) = BIT(114, 3) = BIT(114, 6) = 1;
    scans_for(10);
    assert(WORD(561) == 0 && WORD(569) == 0 && WORD(591) == 0);
    BIT(114, 7) = 1; BIT(110, 0) = 1; WORD(207) = 3;
    scans_for(4);
    assert(WORD(574) == 1 && WORD(530) == 1);
    writer = 1; scans_for(2);
    assert(WORD(723) == WORD(530) && WORD(727) == 0);
}
static void event(int seq, int type, int actual) {
    WORD(578) = WORD(644); WORD(580) = type;
    WORD(581) = WORD(530); WORD(582) = WORD(531);
    WORD(583) = actual; WORD(584) = type * 10; WORD(585) = seq;
    scan();
}
int main(int argc, char **argv) {
    assert(argc == 2);
    setup();
    if (!strcmp(argv[1], "normal")) {
        for (int sensor = 1; sensor <= 5; sensor++) {
            beam(sensor, 1);
            assert(WORD(726) == (1 << (sensor - 1)));
            beam(sensor, 0);
            assert(WORD(726) == 0 && WORD(727) == 0);
            if (sensor <= 2) {
                event(sensor, sensor, sensor == 3 ? 1 : 0);
                assert(WORD(586) == sensor);
            }
        }
        assert(WORD(220) == 1 && WORD(591) == 0);
    } else if (!strcmp(argv[1], "bounce")) {
        raw = 1; scan(); raw = 0; scan();
        assert(WORD(726) == 0 && WORD(727) == 0);
        beam(1, 1); beam(1, 0);
        assert(WORD(727) == 0);
    } else if (!strcmp(argv[1], "event_gate")) {
        beam(1, 1);
        event(1, 1, 0);
        assert(WORD(586) == 0 && WORD(220) == 0);
        beam(1, 0);
        assert(WORD(586) == 1 && WORD(220) == 1);
    } else if (!strcmp(argv[1], "stuck_clear")) {
        WORD(747) = 8; scans_for(9);
        assert(WORD(727) == 2 && WORD(749) == 1 && !BIT(110, 0));
    } else if (!strcmp(argv[1], "stuck_blocked")) {
        WORD(746) = 6; beam(1, 1); scans_for(6);
        assert(WORD(727) == 3 && WORD(749) == 1 && !BIT(110, 0));
    } else if (!strcmp(argv[1], "order")) {
        beam(2, 1);
        assert(WORD(727) == 5 && WORD(749) == 1 && !BIT(110, 0));
    } else if (!strcmp(argv[1], "mismatch")) {
        wrong = 1; scan();
        assert(WORD(727) == 1 && WORD(748) == 1 && !BIT(110, 0));
    } else if (!strcmp(argv[1], "missing")) {
        beam(1, 1); beam(1, 0); event(1, 1, 0);
        assert(WORD(586) == 1);
        WORD(747) = 8; scans_for(9);
        assert(WORD(727) == 2 && WORD(749) == 2 && !BIT(110, 0));
    } else if (!strcmp(argv[1], "short")) {
        WORD(745) = 8; beam(1, 1); beam(1, 0);
        assert(WORD(727) == 4 && !BIT(110, 0));
    } else if (!strcmp(argv[1], "cleanup")) {
        beam(2, 1); assert(WORD(748) == 1);
        BIT(113, 6) = 1; scan();
        assert(WORD(748) == 0 && !BIT(114, 7));
    } else assert(0);
    printf("photoeye PLC %s OK\n", argv[1]);
}
