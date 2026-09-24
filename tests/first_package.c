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
    SORTER_init__(&plc, 0);
    scan();
    assert(WORD(249) == 24113);
    assert(WORD(118) == 32766 && WORD(122) == 32766 && WORD(126) == 32766);
    assert(WORD(119) == 1 && WORD(123) == 1 && WORD(127) == 1);
    assert(WORD(220) == 0);
    assert(WORD(270) == 0);

    BIT(110, 0) = 1;
    for (int i = 0; i < 20; ++i) scan();
    assert(WORD(220) == 0); /* no movement before all three ACKs */
    INPUT(158) = INPUT(169) = INPUT(180) = 32766;
    INPUT(160) = INPUT(171) = INPUT(182) = 7;
    INPUT(164) = INPUT(175) = INPUT(186) = 1;
    scan();
    assert(WORD(118) == 32767 && WORD(122) == 32767 && WORD(126) == 32767);
    INPUT(158) = INPUT(169) = INPUT(180) = 32767;
    INPUT(160) = INPUT(171) = INPUT(182) = 6;
    scan();
    assert(WORD(118) == 0 && WORD(122) == 0 && WORD(126) == 0);
    assert(WORD(220) == 0);
    assert(WORD(100) == 3);
    assert(WORD(101) == 200);
    assert(WORD(220) == 0);

    INPUT(105) = 1750;
    for (int i = 0; i < 14; ++i) scan();
    assert(WORD(220) == 1);
    assert(WORD(260) == 1);
    assert(WORD(118) == 0);

    for (int i = 0; i < 10; ++i) scan();
    assert(WORD(270) == 1);
    /* The trigger is visible on the scan that moves serial 1 into cell 10. */
    assert(WORD(118) == 1);
    assert(WORD(119) == 1);
    assert(WORD(120) == 137);

    INPUT(105) = 0;
    for (int i = 0; i < 3; ++i) scan();
    assert(WORD(118) == 1);
    assert(WORD(254) == 0);

    INPUT(158) = 1;
    INPUT(159) = 1001;
    INPUT(160) = 0;
    INPUT(164) = 0; /* same sequence, prior run: must be ignored */
    scan();
    assert(WORD(270) == 1);
    assert(WORD(211) == 0);
    INPUT(164) = 1;
    scan();
    assert(WORD(270) == 1001);
    assert(WORD(330) == 14);
    assert(WORD(211) == 1001);

    INPUT(105) = 1750;
    for (int i = 0; i < 4; ++i) scan();
    assert(WORD(274) == 0);
    assert(WORD(382) == 1001);
    assert(WORD(240) == 1);

    INPUT(132) = 1750;
    for (int i = 0; i < 10; ++i) scan();
    assert(WORD(222) == 1);
    assert(WORD(231) == 0);
    assert(WORD(217) == 0);

    /* The same arrival-trigger contract applies to lanes 2 and 3. */
    INPUT(105) = 0;
    INPUT(132) = 0;
    INPUT(114) = 1750;
    INPUT(123) = 1750;
    for (int i = 0; i < 24; ++i) scan();
    assert(WORD(122) == 1);
    assert(WORD(126) == 1);
    assert(WORD(123) != 0);
    assert(WORD(127) != 0);
    puts("first package: drive feedback -> cell movement -> sensor trigger -> scanner result -> trailer 1-1 OK; lanes 2/3 triggered");
    return 0;
}
