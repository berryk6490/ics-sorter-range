#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
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

static void responses(int seq, int status, int nonce, int mask) {
    if (mask & 1) { INPUT(158) = seq; INPUT(160) = status; INPUT(164) = nonce; }
    if (mask & 2) { INPUT(169) = seq; INPUT(171) = status; INPUT(175) = nonce; }
    if (mask & 4) { INPUT(180) = seq; INPUT(182) = status; INPUT(186) = nonce; }
}

int main(int argc, char **argv) {
    assert(argc == 3);
    int missing = atoi(argv[2]);
    assert(missing >= 1 && missing <= 3);
    int bit = 1 << (missing - 1);
    SORTER_init__(&plc, 0);
    /* Old reset ACK from a prior PLC process with the reused token cannot
       advance the new process past the prepare phase. */
    responses(32767, 6, 1, 7);
    scan();
    assert(WORD(255) == 1 && WORD(118) == 32766);
    INPUT(105) = 1750;
    BIT(110, 0) = 1;
    if (strcmp(argv[1], "reset") == 0) {
        responses(32766, 7, 1, 7);
        scan();
        assert(WORD(255) == 2);
        responses(32767, 6, 1, 7 ^ bit);
    } else {
        assert(strcmp(argv[1], "prepare") == 0);
        responses(32766, 7, 1, 7 ^ bit);
    }
    for (int i = 0; i < 201; ++i) scan();
    assert(WORD(255) == 3 && WORD(256) == bit);
    assert(WORD(220) == 0 && WORD(260) == 0 && !BIT(110, 0));
    responses(strcmp(argv[1], "prepare") == 0 ? 32766 : 32767,
              strcmp(argv[1], "prepare") == 0 ? 7 : 6, 1, bit);
    scan(); /* late ACK does not clear a latched fault */
    assert(WORD(255) == 3 && WORD(256) == bit);
    BIT(114, 1) = 1; scan(); /* retry without acknowledgement is ignored */
    assert(WORD(255) == 3);
    BIT(114, 0) = 1; scan();
    assert(WORD(255) == 4 && WORD(256) == bit);
    BIT(114, 1) = 1; scan();
    assert(WORD(255) == 1 && WORD(256) == bit && WORD(119) == 2);
    responses(32766, 7, 1, 7); scan(); /* stale prepare ACK */
    assert(WORD(255) == 1);
    responses(32766, 7, 2, 7); scan();
    assert(WORD(255) == 2 && WORD(256) == bit);
    responses(32767, 6, 1, 7); scan(); /* stale reset ACK */
    assert(WORD(255) == 2);
    responses(32767, 6, 2, 7); scan();
    assert(WORD(255) == 0 && WORD(256) == 0 && !BIT(110, 0));
    BIT(110, 0) = 1;
    for (int i = 0; i < 14; ++i) scan();
    assert(WORD(220) == 1);
    printf("%s tunnel %d: fault mask %d, no movement, late ACK held, acknowledged, retried, recovered\n",
           argv[1], missing, bit);
    return 0;
}
