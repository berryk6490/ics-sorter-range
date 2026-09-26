#include <assert.h>
#include <stdio.h>
#include "POUS.h"
TIME __CURRENT_TIME;
#define __LOCATED_VAR(type, name, ...) type name##_storage; type *name = &name##_storage;
#include "LOCATED_VARIABLES.h"
#undef __LOCATED_VAR
#define WORD(a) (*__QW##a)
#define INPUT(a) (*__IW##a)
#define BIT(a,b) (*__QX##a##_##b)
static SORTER plc;
static int scans, heartbeat_enabled = 1;
static void scan(void) {
    ++scans;
    if (heartbeat_enabled && scans % 5 == 0) {
        WORD(565) = WORD(558); WORD(566) = WORD(559);
        WORD(567) = WORD(567) % 30000 + 1;
    }
    WORD(590) = WORD(590) % 30000 + 1;
    SORTER_body__(&plc);
}
static void scanner_response(int request, int status, int nonce) {
    INPUT(158)=INPUT(169)=INPUT(180)=request;
    INPUT(160)=INPUT(171)=INPUT(182)=status;
    INPUT(164)=INPUT(175)=INPUT(186)=nonce;
    scan();
}
static void plant_event(int seq, int type) {
    WORD(578)=1; WORD(580)=type;
    WORD(581)=WORD(530); WORD(582)=WORD(531);
    WORD(583)=0; WORD(585)=seq; scan();
    assert(WORD(586)==seq);
}
int main(void) {
    SORTER_init__(&plc, 0); scan();
    scanner_response(32766,7,1);
    scanner_response(32767,6,1);
    assert(WORD(255)==0);
    WORD(555)=77; WORD(557)=1; scan();
    WORD(587)=77; WORD(589)=1;
    BIT(114,2)=BIT(114,3)=BIT(114,6)=1;
    for (int i=0;i<10;i++) scan();
    assert(WORD(591)==0 && WORD(569)==0);
    BIT(110,0)=1; WORD(207)=3;
    for (int i=0;i<4;i++) scan();
    assert(WORD(530)==1 && WORD(534)==1);
    plant_event(1,1);
    assert(WORD(220)==1);
    heartbeat_enabled=0;
    for (int i=0;i<55;i++) scan();
    assert(WORD(569)==1 && WORD(220)==1 && WORD(591)==0);
    plant_event(2,2);
    INPUT(158)=1; INPUT(159)=6001; INPUT(160)=0; scan();
    assert(WORD(534)==2 && WORD(533)==6001);
    plant_event(3,3);
    assert(WORD(534)==4 && WORD(535)==0);
    plant_event(4,5);
    assert(WORD(534)==6 && WORD(218)==1 && WORD(222)==0);
    BIT(110,0)=0; BIT(113,6)=1; scan();
    assert(WORD(220)==0 && WORD(218)==0 && WORD(534)==0 && WORD(591)==1);
    scanner_response(32766,7,2);
    scanner_response(32767,6,2);
    assert(WORD(255)==0 && WORD(509)==2);
    WORD(555)=78; WORD(557)=2; scan();
    heartbeat_enabled=1;
    BIT(114,2)=BIT(114,3)=BIT(114,6)=1;
    scan();
    assert(WORD(558)==78 && WORD(591)==2 && !BIT(110,0));
    WORD(587)=78; WORD(588)=0; WORD(589)=2;
    for (int i=0;i<10;i++) scan();
    assert(WORD(558)==78 && WORD(591)==0 && WORD(220)==0 && WORD(222)==0);
    scan(); /* old event sequence 4 was marked seen by the reset */
    assert(WORD(586)==0 && WORD(591)==0 && WORD(218)==0);
    /* Old helper dropped XLe mode while plant mode remained on. */
    BIT(114,2)=0; scan(); assert(WORD(591)==2 && !BIT(110,0));
    BIT(114,6)=0; scan(); assert(WORD(591)==2); /* idle retains it */
    /* A valid fresh handshake restores health; safe teardown preserves it. */
    BIT(114,2)=BIT(114,3)=BIT(114,6)=1;
    for (int i=0;i<5;i++) scan();
    assert(WORD(591)==0);
    BIT(114,6)=0; scan();
    BIT(114,2)=BIT(114,3)=0; scan();
    assert(WORD(591)==0 && !BIT(110,0) && WORD(534)==0);
    puts("plant recovery order: fallback, reset, stale event, fresh identity, unsafe/safe teardown OK");
    return 0;
}
