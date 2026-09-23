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
static int scans;
static const int base[] = {530,542,647};
static const int lane[] = {644,645,659};
static int reg(int address) {
    switch(address) {
    case 530: return WORD(530); case 531: return WORD(531);
    case 532: return WORD(532); case 533: return WORD(533);
    case 542: return WORD(542); case 543: return WORD(543);
    case 544: return WORD(544); case 545: return WORD(545);
    case 647: return WORD(647); case 648: return WORD(648);
    case 649: return WORD(649); case 650: return WORD(650);
    case 644: return WORD(644); case 645: return WORD(645);
    case 659: return WORD(659);
    default: assert(0); return 0;
    }
}
static void scan(void) {
    if (++scans % 5 == 0) {
        WORD(565)=WORD(558); WORD(566)=WORD(559);
        WORD(567)=WORD(567)%30000+1;
    }
    WORD(590)=WORD(590)%30000+1;
    SORTER_body__(&plc);
}
static void event(int seq,int kind,int slot,int actual) {
    WORD(578)=reg(lane[slot]); WORD(580)=kind;
    WORD(581)=reg(base[slot]); WORD(582)=reg(base[slot]+1);
    WORD(583)=actual; WORD(585)=seq; scan();
    assert(WORD(586)==seq && WORD(591)==0);
}
static void command(int id,int op,int slot,int dest) {
    WORD(520)=op; WORD(521)=slot;
    WORD(522)=reg(base[slot]); WORD(523)=reg(base[slot]+1);
    WORD(524)=reg(base[slot]+2); WORD(525)=reg(base[slot]+3);
    WORD(526)=dest; WORD(527)=WORD(509);
    WORD(562)=WORD(558); WORD(563)=WORD(559); WORD(528)=id; scan();
    assert(WORD(529)==(op==1?1:3));
}
int main(void) {
    SORTER_init__(&plc,0); scan();
    INPUT(158)=INPUT(169)=INPUT(180)=32766;
    INPUT(160)=INPUT(171)=INPUT(182)=7;
    INPUT(164)=INPUT(175)=INPUT(186)=1; scan();
    INPUT(158)=INPUT(169)=INPUT(180)=32767;
    INPUT(160)=INPUT(171)=INPUT(182)=6; scan();
    assert(WORD(255)==0);
    WORD(555)=77; WORD(557)=1; scan();
    WORD(587)=77; WORD(589)=1;
    BIT(114,2)=BIT(114,3)=BIT(114,6)=1;
    for(int i=0;i<10;i++)scan();
    assert(WORD(561)==0 && WORD(569)==0 && WORD(591)==0);
    BIT(110,0)=1; WORD(207)=WORD(208)=WORD(209)=3;
    for(int i=0;i<4;i++)scan();
    for(int slot=0;slot<3;slot++) {
        assert(reg(base[slot])==slot+1 && reg(lane[slot])==slot+1);
        event(slot+1,1,slot,0);
        scan();
    }
    assert(WORD(220)==3 && WORD(574)==3);
    WORD(660)=WORD(558); WORD(661)=WORD(559); WORD(662)=WORD(509);
    WORD(663)=WORD(647); WORD(664)=WORD(648); WORD(665)=6;
    WORD(666)=100; WORD(667)=2; WORD(668)=0; WORD(669)=1;
    scan();
    assert(WORD(680)==1 && WORD(673)==3 && WORD(675)==6 && WORD(676)==100);
    for(int i=0;i<16;i++)scan();
    assert(WORD(680)==2 && WORD(676)==100); /* stale does not move */
    WORD(663)=999; WORD(669)=2; scan();
    assert(WORD(680)==3 && WORD(673)==0); /* wrong identity suppressed */
    for(int i=0;i<8;i++)scan();
    assert(WORD(220)==3 && WORD(574)==3); /* fourth waits */
    for(int slot=0;slot<3;slot++)event(4+slot,2,slot,0);
    INPUT(158)=1; INPUT(159)=6001; INPUT(160)=0;
    INPUT(169)=1; INPUT(170)=6001; INPUT(171)=0;
    INPUT(180)=1; INPUT(181)=6001; INPUT(182)=0; scan();
    assert(WORD(534)==2 && WORD(546)==2 && WORD(651)==2);
    assert(WORD(533)==6001 && WORD(545)==6001 && WORD(650)==6001);
    command(1,1,0,1); command(2,1,1,2); command(3,1,2,3);
    for(int slot=0;slot<3;slot++)event(7+slot,3,slot,1);
    assert(WORD(222)==0 && WORD(223)==0 && WORD(224)==0);
    for(int slot=0;slot<3;slot++)event(10+slot,4,slot,slot+1);
    assert(WORD(222)==1 && WORD(223)==1 && WORD(224)==1);
    command(4,2,0,0);
    assert(WORD(530)==4 && WORD(531)==4 && WORD(574)==4);
    assert(WORD(647)==3 && WORD(659)==3);
    WORD(520)=1; WORD(521)=0; WORD(522)=1; WORD(523)=1;
    WORD(524)=1; WORD(525)=6001; WORD(526)=9; WORD(527)=WORD(509);
    WORD(528)=5; scan(); assert(WORD(529)==2);
    printf("plant three slots: concurrent lanes, repeated barcode identity, fourth waits, confirmation and stale token OK\n");
}
