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

int main(int argc, char **argv) {
    assert(argc == 2);
    SORTER_init__(&plc, 0);
    scan();
    INPUT(158) = INPUT(169) = INPUT(180) = 32766;
    INPUT(160) = INPUT(171) = INPUT(182) = 7;
    INPUT(164) = INPUT(175) = INPUT(186) = 1;
    scan();
    INPUT(158) = INPUT(169) = INPUT(180) = 32767;
    INPUT(160) = INPUT(171) = INPUT(182) = 6;
    scan();
    assert(WORD(255) == 0);
    BIT(114, 2) = 1;
    BIT(110, 3) = BIT(110, 4) = 0;
    WORD(207) = 1000;
    BIT(110, 0) = 1;
    INPUT(105) = INPUT(132) = 1750;
    WORD(207) = 14;
    for (int i = 0; i < 24; ++i) scan();
    assert(WORD(118) == 1 && WORD(119) == 1 && WORD(270) == 1);
    WORD(207) = 1000;
    INPUT(158) = 1; INPUT(159) = 6001; INPUT(160) = 0; INPUT(164) = 1;
    scan();
    assert(WORD(508) == 6001 && WORD(330) == 0);
    assert(WORD(510) > 0 && WORD(511) == 0 && WORD(512) == 0);

    if (!strcmp(argv[1], "valid") || !strcmp(argv[1], "failed")) {
        WORD(500) = 1; WORD(501) = 6001; WORD(502) = 2; WORD(503) = 1;
        scan();
        assert(WORD(504) == 1 && WORD(505) == 1 && WORD(332) == 14);
        assert(WORD(511) >= WORD(510));
        for (int i = 0; i < 4; ++i) scan();
        assert(WORD(505) == 2);
        assert(WORD(512) > WORD(511));
        if (!strcmp(argv[1], "failed")) BIT(110, 1) = 0;
        for (int i = 0; i < 25; ++i) scan();
        assert(WORD(220) == 1);
        if (!strcmp(argv[1], "valid")) {
            assert(WORD(223) == 1 && WORD(505) == 3 && WORD(507) == 2);
            printf("valid: barcode 6001 command 1 destination 2 trailer 1-2 loaded\n");
        } else {
            assert(WORD(505) == 4 && WORD(506) == 4 && WORD(219) > 0);
            printf("failed: command 1 no-home outcome reason 4\n");
        }
    } else if (!strcmp(argv[1], "stale")) {
        WORD(500) = 1; WORD(501) = 6001; WORD(502) = 2; WORD(503) = 999;
        scan();
        assert(WORD(505) == 4 && WORD(506) == 2 && WORD(330) == 0);
        printf("stale: wrong-run command rejected\n");
    } else {
        assert(!strcmp(argv[1], "unknown"));
        for (int i = 0; i < 10; ++i) scan();
        assert(WORD(218) == 1 && WORD(505) == 5 && WORD(506) == 1);
        assert(WORD(511) == 0 && WORD(512) > WORD(510));
        printf("unknown: no command, explicit recirculation reason 1\n");
    }
    return 0;
}
