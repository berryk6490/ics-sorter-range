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
static int scans;
static void scan(void) {
    if (++scans % 5 == 0) {
        WORD(565) = WORD(558); WORD(566) = WORD(559);
        WORD(567) = WORD(567) % 30000 + 1;
    }
    WORD(590) = WORD(590) % 30000 + 1;
    SORTER_body__(&plc);
}
static void event(int seq, int kind, int slot, int actual) {
    WORD(578) = slot ? WORD(645) : WORD(644);
    WORD(580) = kind;
    WORD(581) = slot ? WORD(542) : WORD(530);
    WORD(582) = slot ? WORD(543) : WORD(531);
    WORD(583) = actual; WORD(584) = kind * 10; WORD(585) = seq;
    scan(); assert(WORD(586) == seq && WORD(591) == 0);
}
static void command(int id, int op, int slot, int destination) {
    WORD(520) = op; WORD(521) = slot;
    WORD(522) = slot ? WORD(542) : WORD(530);
    WORD(523) = slot ? WORD(543) : WORD(531);
    WORD(524) = slot ? WORD(544) : WORD(532);
    WORD(525) = slot ? WORD(545) : WORD(533);
    WORD(526) = destination; WORD(527) = WORD(509);
    WORD(562) = WORD(558); WORD(563) = WORD(559); WORD(528) = id;
    scan(); assert(WORD(529) == (op == 1 ? 1 : 3));
}
int main(void) {
    SORTER_init__(&plc, 0); scan();
    INPUT(158) = INPUT(169) = INPUT(180) = 32766;
    INPUT(160) = INPUT(171) = INPUT(182) = 7;
    INPUT(164) = INPUT(175) = INPUT(186) = 1; scan();
    INPUT(158) = INPUT(169) = INPUT(180) = 32767;
    INPUT(160) = INPUT(171) = INPUT(182) = 6; scan();
    assert(WORD(255) == 0);
    WORD(555) = 77; WORD(556) = 0; WORD(557) = 1; scan();
    WORD(587) = 77; WORD(588) = 0; WORD(589) = 1;
    BIT(114, 2) = BIT(114, 3) = BIT(114, 6) = 1;
    for (int i = 0; i < 10; ++i) scan();
    assert(WORD(561) == 0 && WORD(569) == 0 && WORD(591) == 0);
    BIT(110, 0) = 1;
    WORD(207) = WORD(208) = WORD(209) = 3;
    for (int i = 0; i < 4; ++i) scan();
    assert(WORD(574) == 1 && WORD(577) == 1 && WORD(644) == 1);
    event(1, 1, 0, 0);
    scan();
    assert(WORD(574) == 2 && WORD(577) == 2 && WORD(645) == 2);
    event(2, 1, 1, 0);
    assert(WORD(220) == 2 && WORD(574) == 2);
    event(3, 2, 0, 0); event(4, 2, 1, 0);
    assert(WORD(118) == 1 && WORD(122) == 1);
    INPUT(158) = 1; INPUT(159) = 6001; INPUT(160) = 0;
    INPUT(169) = 1; INPUT(170) = 5002; INPUT(171) = 0; scan();
    command(1, 1, 0, 2); command(2, 1, 1, 5);
    event(5, 3, 0, 1); event(6, 3, 1, 2);
    assert(WORD(223) == 0 && WORD(226) == 0); /* decisions do not load */
    event(7, 4, 0, 2);
    assert(WORD(223) == 1 && WORD(226) == 0);
    command(3, 2, 0, 0); /* free slot zero while lane 2 stays occupied */
    scan();
    assert(WORD(574) == 3 && WORD(577) == 3 && WORD(644) == 3);
    event(8, 1, 0, 0);
    assert(WORD(220) == 3 && WORD(546) == 4);
    event(9, 2, 0, 0);
    assert(WORD(126) == 1 && WORD(127) == 3);
    INPUT(180) = 1; INPUT(181) = 9003; INPUT(182) = 0; scan();
    assert(WORD(534) == 2 && WORD(533) == 9003 && WORD(644) == 3);
    command(4, 1, 0, 8);
    event(10, 3, 0, 3);
    assert(BIT(111, 7) == 0 && BIT(112, 0) == 1); /* lane 3 outbound 3 */
    assert(WORD(229) == 0); /* trailer 8 still empty */
#ifdef FAIL_LANE3
    event(11, 6, 0, 0);
    assert(WORD(534) == 7 && WORD(536) == 0 && WORD(537) == 4);
    assert(WORD(229) == 0 && WORD(646) == 3 && WORD(592) == 1);
#else
    event(11, 4, 0, 8);
    assert(WORD(534) == 5 && WORD(536) == 8 && WORD(537) == 0);
    assert(WORD(229) == 1 && WORD(592) == 0);
#endif
    event(12, 4, 1, 5);
    assert(WORD(226) == 1 && WORD(591) == 0);
    WORD(578) = 2; /* wrong lane against the occupied lane 3 token */
    WORD(580) = 4; WORD(581) = WORD(530); WORD(582) = WORD(531);
    WORD(583) = 8; WORD(585) = 13; scan();
    assert(WORD(591) == 3 && !BIT(110, 0));
#ifdef FAIL_LANE3
    assert(WORD(229) == 0);
#else
    assert(WORD(229) == 1);
#endif
    printf("plant lanes 1/2/3: circular priority, slot reuse, tunnel 3, confirmation and wrong-lane fault OK\n");
}
