#include <assert.h>
#include <stdio.h>
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

int main(void) {
    SORTER_init__(&plc, 0); scan();
    INPUT(158) = INPUT(169) = INPUT(180) = 32766;
    INPUT(160) = INPUT(171) = INPUT(182) = 7;
    INPUT(164) = INPUT(175) = INPUT(186) = 1;
    scan();
    INPUT(158) = INPUT(169) = INPUT(180) = 32767;
    INPUT(160) = INPUT(171) = INPUT(182) = 6;
    scan();
    assert(WORD(255) == 0);
    WORD(555) = 73; WORD(556) = 0; WORD(557) = 1; scan();
    assert(WORD(558) == 73);
    BIT(114, 2) = BIT(114, 3) = 1;
    BIT(110, 3) = BIT(110, 4) = 0;
    BIT(110, 0) = 1;
    INPUT(105) = 1750;
    WORD(207) = 1000;
    for (int i = 0; i < 55; i++) scan();
    assert(WORD(569) == 1 && WORD(220) == 0);
    BIT(114, 5) = 1; scan();
    assert(WORD(569) == 1); /* retry without acknowledgement is ignored */
    BIT(114, 4) = 1; scan();
    assert(WORD(569) == 2);
    BIT(114, 5) = 1; scan();
    assert(WORD(569) == 2); /* XLe still absent */
    WORD(565) = 73; WORD(566) = 0; WORD(567) = 1; scan();
    assert(WORD(573) == 1 && WORD(569) == 2);
    BIT(114, 5) = 1; scan();
    assert(WORD(569) == 3);
    WORD(570) = 73; WORD(571) = 0; WORD(572) = 1; scan();
    assert(WORD(569) == 0);
    puts("heartbeat before first package: lost, retry rejected, acknowledged, retried, journal proof cleared");
    return 0;
}
