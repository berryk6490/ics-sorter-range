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
static int plant_heartbeat_enabled = 1;
static void scan(void) {
    if (++scans % 5 == 0) {
        WORD(565) = WORD(558); WORD(566) = WORD(559);
        WORD(567) = WORD(567) % 30000 + 1;
    }
    if (plant_heartbeat_enabled) WORD(590) = WORD(590) % 30000 + 1;
    SORTER_body__(&plc);
}
static void event(int seq, int type, int slot, int actual) {
    int base = slot ? 542 : 530;
    WORD(580) = type; WORD(581) = base == 530 ? WORD(530) : WORD(542);
    WORD(582) = base == 530 ? WORD(531) : WORD(543);
    WORD(578) = slot ? WORD(645) : WORD(644);
    WORD(583) = actual; WORD(584) = type * 10; WORD(585) = seq;
    scan();
    assert(WORD(586) == seq);
}
static void command(int id, int slot, int destination) {
    int base = slot ? 542 : 530;
    WORD(520) = 1; WORD(521) = slot;
    WORD(522) = base == 530 ? WORD(530) : WORD(542);
    WORD(523) = base == 530 ? WORD(531) : WORD(543);
    WORD(524) = base == 530 ? WORD(532) : WORD(544);
    WORD(525) = base == 530 ? WORD(533) : WORD(545);
    WORD(526) = destination; WORD(527) = WORD(509);
    WORD(562) = WORD(558); WORD(563) = WORD(559); WORD(528) = id;
    scan();
    assert(WORD(529) == 1);
}
int main(void) {
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
    for (int i = 0; i < 10; ++i) scan();
    assert(WORD(561) == 0 && WORD(569) == 0 && WORD(591) == 0);
    BIT(110, 0) = 1; WORD(207) = 3;
    for (int i = 0; i < 4; ++i) scan();
    assert(WORD(574) == 1 && WORD(575) == 1 && WORD(576) == 1);
    assert(WORD(220) == 0 && WORD(222) == 0);
    event(1, 1, 0, 0); assert(WORD(220) == 1);
    event(2, 2, 0, 0); assert(WORD(118) == 1 && WORD(119) == 1);
    INPUT(158) = 1; INPUT(159) = 6001; INPUT(160) = 0; scan();
    assert(WORD(534) == 2 && WORD(533) == 6001);
    command(1, 0, 2); assert(WORD(534) == 3);
    event(3, 3, 0, 1); assert(WORD(534) == 4 && WORD(223) == 0);
    event(4, 4, 0, 2); assert(WORD(534) == 5 && WORD(223) == 1);
    WORD(520) = 2; WORD(521) = 0; WORD(528) = 2; scan();
    assert(WORD(529) == 3 && WORD(534) == 0);
    for (int i = 0; i < 5; ++i) scan();
    assert(WORD(574) == 2);
    assert(WORD(542) == 2);
    event(5, 1, 1, 0); event(6, 2, 1, 0);
    INPUT(158) = 2; INPUT(159) = 5002; INPUT(160) = 0; scan();
    command(3, 1, 5);
    event(7, 3, 1, 2);
    event(8, 6, 1, 0);
    assert(WORD(546) == 7 && WORD(549) == 4 && WORD(226) == 0);
    assert(WORD(219) == 1 && WORD(592) == 1 && BIT(113, 5));
    assert(WORD(646) == 1);
    WORD(610) = WORD(558); WORD(611) = WORD(559); WORD(612) = WORD(509);
    WORD(613) = WORD(542); WORD(614) = WORD(543);
    WORD(615) = 3; WORD(616) = 150; WORD(617) = 6; WORD(618) = 0;
    WORD(619) = 1; scan();
    assert(WORD(641) == 1 && WORD(633) == 2 && WORD(636) == 150);
    for (int i = 0; i < 17; ++i) scan();
    assert(WORD(641) == 2 && WORD(636) == 150);
    WORD(616) = 151; WORD(619) = 2; scan();
    assert(WORD(641) == 1 && WORD(636) == 151);
    WORD(610) = WORD(558) + 1; WORD(619) = 3; scan();
    assert(WORD(641) == 3 && WORD(633) == 0);
    plant_heartbeat_enabled = 0;
    for (int i = 0; i < 31; ++i) scan();
    assert(WORD(593) > 30 && WORD(591) == 1 && !BIT(110, 0));
    WORD(580) = 4; WORD(581) = 1234; WORD(582) = 1;
    WORD(583) = 2; WORD(585) = 9; scan();
    assert(WORD(591) == 3 && !BIT(110, 0) && WORD(223) == 1);
    scan(); assert(WORD(591) == 3); /* protocol fault is latched */
    printf("plant PLC: event order, identity, confirmation-only counters, failed confirmation OK\n");
}
