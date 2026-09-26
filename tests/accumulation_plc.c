#include <assert.h>
#include <stdio.h>
#include <string.h>
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
static void scan(void) {
    if (++scans % 5 == 0) {
        WORD(565)=WORD(558); WORD(566)=WORD(559);
        WORD(567)=WORD(567)%30000+1;
    }
    WORD(590)=WORD(590)%30000+1;
    SORTER_body__(&plc);
}
static void setup(void) {
    SORTER_init__(&plc,0); scan();
    assert(WORD(249)==24115);
    INPUT(158)=INPUT(169)=INPUT(180)=32766;
    INPUT(160)=INPUT(171)=INPUT(182)=7;
    INPUT(164)=INPUT(175)=INPUT(186)=1; scan();
    INPUT(158)=INPUT(169)=INPUT(180)=32767;
    INPUT(160)=INPUT(171)=INPUT(182)=6; scan();
    assert(WORD(255)==0);
    WORD(555)=77; WORD(557)=1; scan();
    WORD(587)=77; WORD(589)=1;
    BIT(114,2)=BIT(114,3)=BIT(114,6)=BIT(115,0)=1;
    WORD(784)=7; WORD(785)=1;
    for(int i=0;i<10;i++) scan();
    assert(WORD(561)==0 && WORD(591)==0 && WORD(786)==7);
    BIT(110,0)=1; WORD(207)=WORD(208)=WORD(209)=3;
    for(int i=0;i<4;i++) scan();
    assert(WORD(530)==1);
}
static void row0(int belt, int pos, int zone, int seq) {
    WORD(600)=WORD(558); WORD(601)=WORD(559); WORD(602)=WORD(509);
    WORD(603)=WORD(530); WORD(604)=WORD(531);
    WORD(605)=belt; WORD(606)=pos; WORD(607)=1; WORD(609)=seq;
    WORD(751)=zone; WORD(752)=1; WORD(753)=0; WORD(754)=0; WORD(755)=seq;
    WORD(785)=seq+1; scan();
}
static void row1(int belt, int pos, int zone, int seq) {
    WORD(610)=WORD(558); WORD(611)=WORD(559); WORD(612)=WORD(509);
    WORD(613)=WORD(542); WORD(614)=WORD(543);
    WORD(615)=belt; WORD(616)=pos; WORD(617)=1; WORD(619)=seq;
    WORD(756)=zone; WORD(757)=1; WORD(758)=0; WORD(759)=0; WORD(760)=seq;
}
static void event(int seq, int kind, int actual) {
    WORD(578)=WORD(644); WORD(580)=kind; WORD(581)=WORD(530);
    WORD(582)=WORD(531); WORD(583)=actual; WORD(585)=seq; scan();
    assert(WORD(586)==seq);
}
static void held_tick(int zone, int motion, int mask, int seq) {
    WORD(605)=1; WORD(606)=zone==2?100:10;
    WORD(751)=zone; WORD(752)=motion; WORD(753)=motion==2?1:0;
    WORD(754)=seq; WORD(609)=WORD(755)=seq; WORD(785)=seq+1;
    WORD(690)=WORD(558); WORD(691)=WORD(559); WORD(692)=WORD(509);
    WORD(693)=WORD(530); WORD(694)=WORD(531); WORD(695)=1;
    WORD(696)=mask; WORD(697)=seq;
    scan();
}
int main(int argc,char **argv) {
    assert(argc==2); setup();
    row0(1,10,1,1);
    assert(WORD(781)==0);
    row0(1,10,1,2);
    assert(WORD(781)==1 && WORD(766)==1 && WORD(788)==0);
    if(!strcmp(argv[1],"normal")) {
        WORD(752)=2; WORD(753)=1; WORD(754)=25;
        WORD(609)=WORD(755)=3; WORD(785)=4; scan();
        assert(WORD(781)==1 && WORD(767)==2 && WORD(768)==1 && WORD(769)==25);
        assert(WORD(788)==0 && BIT(110,0));
    } else if(!strcmp(argv[1],"jump")) {
        row0(2,20,5,3);
        assert(WORD(781)==3 && WORD(788)==2 && !BIT(110,0));
    } else if(!strcmp(argv[1],"stale")) {
        WORD(785)=4; scan();
        assert(WORD(781)==3 && WORD(788)==1 && !BIT(110,0));
    } else if(!strcmp(argv[1],"lane")) {
        row0(5,20,1,3);
        assert(WORD(781)==3 && WORD(788)==2 && !BIT(110,0));
    } else if(!strcmp(argv[1],"overlap")) {
        BIT(110,3)=BIT(110,4)=0;
        WORD(578)=1; WORD(580)=1; WORD(581)=WORD(530);
        WORD(582)=WORD(531); WORD(585)=1; scan();
        assert(WORD(586)==1);
        for(int i=0;i<4;i++) scan();
        assert(WORD(542)==2);
        row1(1,25,1,1);
        row0(1,10,1,3);
        assert(WORD(782)==0 && WORD(788)==0);
        row1(1,25,1,2);
        row0(1,10,1,4);
        assert(WORD(781)==3 && WORD(782)==3 && WORD(788)==3 && !BIT(110,0));
    } else if(!strcmp(argv[1],"clearance")) {
        BIT(110,3)=BIT(110,4)=0;
        WORD(578)=1; WORD(580)=1; WORD(581)=WORD(530);
        WORD(582)=WORD(531); WORD(585)=1; scan();
        for(int i=0;i<4;i++) scan();
        assert(WORD(542)==2);
        row1(1,41,1,1);
        row0(1,10,1,3);
        row1(1,41,1,2);
        row0(1,10,1,4);
        assert(WORD(788)==3 && !BIT(110,0));
    } else if(!strcmp(argv[1],"terminal_expiry")) {
        event(1,1,0);
        row0(1,100,2,3);
        event(2,2,0);
        INPUT(158)=1; INPUT(159)=6001; INPUT(160)=0; scan();
        assert(WORD(534)==2);
        WORD(520)=1; WORD(521)=0; WORD(522)=WORD(530);
        WORD(523)=WORD(531); WORD(524)=WORD(532); WORD(525)=WORD(533);
        WORD(526)=2; WORD(527)=WORD(509); WORD(562)=WORD(558);
        WORD(563)=WORD(559); WORD(528)=1; scan();
        assert(WORD(529)==1);
        row0(1,130,3,4);
        row0(2,20,5,5);
        event(3,3,1);
        row0(2,150,6,6);
        event(4,4,2);
        assert(WORD(534)==5);
        WORD(605)=0; WORD(606)=0; WORD(751)=0;
        WORD(752)=WORD(753)=WORD(754)=0;
        WORD(609)=WORD(755)=7; WORD(785)=8; scan();
        assert(WORD(781)==2 && WORD(766)==6 && WORD(788)==0);
    } else if(!strcmp(argv[1],"recirc_tail")) {
        event(1,1,0);
        row0(1,100,2,3);
        event(2,2,0);
        INPUT(158)=1; INPUT(159)=6001; INPUT(160)=0; scan();
        assert(WORD(534)==2 && WORD(535)==0);
        row0(1,139,3,4);
        assert(WORD(781)==1 && WORD(788)==0);
        row0(1,140,7,5);
        assert(WORD(781)==1 && WORD(766)==7 && WORD(788)==0 && BIT(110,0));
        event(3,3,0);
        assert(WORD(534)==4 && WORD(535)==0);
        row0(1,141,7,6);
        row0(1,190,7,7);
        assert(WORD(781)==1 && WORD(766)==7 && WORD(788)==0 && BIT(110,0));
        event(4,5,0);
        assert(WORD(534)==6 && WORD(218)==1 && WORD(222)==0);
    } else if(!strcmp(argv[1],"recirc_old_zone")) {
        event(1,1,0); row0(1,100,2,3); event(2,2,0);
        row0(1,130,3,4); row0(1,140,3,5);
        assert(WORD(781)==3 && WORD(788)==2 && !BIT(110,0));
    } else if(!strcmp(argv[1],"recirc_wrong_route")) {
        event(1,1,0); row0(1,100,2,3); event(2,2,0);
        INPUT(158)=1; INPUT(159)=6001; INPUT(160)=0; scan();
        assert(WORD(534)==2);
        WORD(520)=1; WORD(521)=0; WORD(522)=WORD(530);
        WORD(523)=WORD(531); WORD(524)=WORD(532); WORD(525)=WORD(533);
        WORD(526)=2; WORD(527)=WORD(509); WORD(562)=WORD(558);
        WORD(563)=WORD(559); WORD(528)=1; scan();
        assert(WORD(534)==3 && WORD(535)==2);
        row0(1,130,3,4); row0(1,140,7,5);
        assert(WORD(781)==3 && WORD(788)==2 && !BIT(110,0));
    } else if(!strcmp(argv[1],"recirc_wrong_identity")) {
        event(1,1,0); row0(1,100,2,3); event(2,2,0);
        row0(1,130,3,4);
        WORD(600)=WORD(558)+1; WORD(606)=140;
        WORD(751)=7; WORD(609)=WORD(755)=5; WORD(785)=6; scan();
        assert(WORD(781)==3 && WORD(788)==1 && !BIT(110,0));
    } else if(!strcmp(argv[1],"recirc_stale")) {
        event(1,1,0); row0(1,100,2,3); event(2,2,0);
        INPUT(158)=1; INPUT(159)=6001; INPUT(160)=0; scan();
        row0(1,130,3,4); row0(1,140,7,5);
        assert(WORD(788)==0);
        WORD(785)=7; scan();
        assert(WORD(781)==3 && WORD(788)==1 && !BIT(110,0));
    } else if(!strcmp(argv[1],"held_beam")) {
        BIT(114,7)=1; WORD(746)=6; WORD(747)=8;
        scan();
        for(int seq=3;seq<7;seq++) held_tick(1,1,1,seq);
        for(int seq=7;seq<11;seq++) held_tick(1,1,0,seq);
        assert(WORD(727)==0 && WORD(726)==0);
        for(int seq=11;seq<31;seq++) held_tick(2,2,2,seq);
        assert(WORD(781)==1 && WORD(767)==2 && WORD(727)==0);
        assert(WORD(748)==0 && BIT(110,0));
        for(int seq=31;seq<35;seq++) held_tick(2,1,0,seq);
        assert(WORD(727)==0 && WORD(726)==0);
    } else assert(0);
    printf("accumulation PLC %s OK\n",argv[1]);
}
