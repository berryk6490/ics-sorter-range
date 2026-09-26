#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "POUS.h"
TIME __CURRENT_TIME;
#define __LOCATED_VAR(type, name, ...) type name##_storage; type *name = &name##_storage;
#include "LOCATED_VARIABLES.h"
#undef __LOCATED_VAR
#define W(a) (*__QW##a)
#define I(a) (*__IW##a)
#define B(a,b) (*__QX##a##_##b)
static SORTER plc;
static int scans;
static void scan(void) {
    if (++scans % 5 == 0) {
        W(565)=W(558); W(566)=W(559); W(567)=W(567)%30000+1;
    }
    W(590)=W(590)%30000+1;
    SORTER_body__(&plc);
}
static void setup(void) {
    SORTER_init__(&plc,0); scan(); assert(W(249)==24115);
    I(158)=I(169)=I(180)=32766;
    I(160)=I(171)=I(182)=7;
    I(164)=I(175)=I(186)=1; scan();
    I(158)=I(169)=I(180)=32767;
    I(160)=I(171)=I(182)=6; scan();
    assert(W(255)==0);
    W(555)=77; W(556)=0; W(557)=1; scan();
    W(587)=77; W(588)=0; W(589)=1;
    B(114,2)=B(114,3)=B(114,6)=B(114,7)=B(115,0)=B(115,1)=1;
    W(784)=7; W(785)=1;
    for(int n=0;n<10;n++) scan();
    assert(W(591)==0 && W(561)==0 && W(825)==2 && W(826)==3);
}
static void sample(int seq,int occupied,int reserved,int full,int epoch_bad) {
    W(802)=0; W(790)=1; W(791)=2; W(792)=occupied;
    W(793)=reserved; W(794)=full;
    W(795)=W(558)+epoch_bad; W(796)=W(559); W(797)=W(509);
    W(798)=seq; W(799)=0; W(800)=0; W(801)=0;
    W(830)=0; W(831)=0; W(832)=0;
    W(802)=seq; scan();
}
int main(int argc,char **argv) {
    assert(argc==2); setup();
    if(!strcmp(argv[1],"clear")) {
        sample(1,0,0,0,0);
        assert(W(803)==1 && W(804)==2 && W(805)==0);
        assert(W(806)==0 && W(807)==1 && W(809)==1);
        assert(W(223)==0 && W(827)==0);
    } else if(!strcmp(argv[1],"full")) {
        sample(1,0,0,0,0);
        B(110,0)=1; W(207)=3; for(int n=0;n<4;n++) scan();
        assert(W(530)==1);
        W(802)=0; W(790)=1; W(791)=2; W(792)=0;
        W(793)=3; W(794)=1; W(795)=W(558); W(796)=W(559);
        W(797)=W(509); W(798)=2; W(799)=W(530); W(800)=W(531);
        W(801)=0; W(802)=2; scan();
        assert(W(806)==1 && W(807)==1 && W(809)==0);
        assert(W(223)==0 && W(534)==1);
        W(817)=2; W(818)=W(558); W(819)=W(559); W(820)=W(509);
        W(821)=1; W(822)=1; scan();
        assert(W(806)==2 && W(823)==1 && W(824)==1);
        W(821)=2; W(822)=2; scan();
        assert(W(824)==2 && W(814)==0 && W(809)==0);
    } else if(!strcmp(argv[1],"mismatch")) {
        sample(1,0,0,0,1);
        assert(W(807)==3 && W(806)==4 && W(809)==0);
    } else if(!strcmp(argv[1],"stale")) {
        sample(1,0,0,0,0);
        for(int n=0;n<16;n++) scan();
        assert(W(807)==2 && W(806)==4 && W(809)==0);
    } else if(!strcmp(argv[1],"restart")) {
        sample(1,0,0,0,0);
        W(805)=3; W(806)=1; W(809)=0;
        sample(2,0,0,0,0); /* lost physical inventory, no Empty ACK */
        assert(W(807)==3 && W(806)==4 && W(809)==0);
        assert(W(805)==3 && W(223)==0);
    } else if(!strcmp(argv[1],"replay")) {
        sample(1,0,0,0,0);
        W(802)=1; W(790)=1; W(791)=2; W(792)=0;
        W(793)=3; W(794)=1; scan(); /* duplicate sample sequence */
        assert(W(803)==1 && W(806)==0 && W(809)==1);
    } else if(!strcmp(argv[1],"duplicate_acceptance")) {
        sample(1,0,0,0,0);
        W(827)=41; W(828)=7; W(829)=19;
        W(802)=0; W(792)=1; W(798)=2;
        W(799)=41; W(800)=7; W(801)=19;
        W(830)=41; W(831)=7; W(832)=19;
        W(802)=2; scan();
        assert(W(805)==1 && W(807)==1);
        W(802)=0; W(792)=2; W(798)=3; W(802)=3; scan();
        assert(W(805)==1 && W(807)==3 && W(806)==4 && W(809)==0);
    } else if(!strcmp(argv[1],"operator")) {
        sample(1,0,0,0,0);
        /* Establish the precondition after separate terminal-outcome tests.
           This case isolates the operator protocol, not terminal acceptance. */
        W(805)=3;
        sample(2,3,0,1,0);
        assert(W(806)==1 && W(809)==0);
        W(817)=2; W(818)=W(558); W(819)=W(559); W(820)=W(509);
        W(821)=1; W(822)=1; scan();
        assert(W(806)==2 && W(824)==1);
        W(821)=2; W(822)=2; scan();
        assert(W(814)==1 && W(824)==1 && W(809)==0);
        W(815)=1; W(816)=W(814);
        sample(3,0,0,0,0);
        assert(W(806)==3 && W(807)==1 && W(809)==0);
        W(821)=3; W(822)=3; scan();
        assert(W(806)==0 && W(824)==1 && W(809)==1);
    } else if(!strcmp(argv[1],"resume_handoff") ||
              !strcmp(argv[1],"resume_expiry") ||
              !strcmp(argv[1],"resume_wrong_identity") ||
              !strcmp(argv[1],"resume_wrong_zone")) {
        sample(1,0,0,0,0);
        B(110,0)=1; W(207)=3; for(int n=0;n<4;n++) scan();
        assert(W(530)==1);
        plc.ST_STATE.value.table[0]=3;
        plc.ST_DEST.value.table[0]=2;
        W(805)=3; sample(2,3,0,1,0);
        assert(W(806)==1 && W(809)==0);
        W(600)=W(558); W(601)=W(559); W(602)=W(509);
        W(603)=W(530); W(604)=W(531); W(605)=1; W(606)=140;
        W(609)=1; W(751)=4; W(752)=3; W(753)=5;
        W(754)=0; W(755)=1; W(785)=2; scan();
        W(609)=W(755)=2; W(785)=3; scan();
        assert(W(788)==0 && W(768)==5);
        W(817)=2; W(818)=W(558); W(819)=W(559); W(820)=W(509);
        W(821)=1; W(822)=1; scan();
        W(821)=2; W(822)=2; scan();
        W(815)=1; W(816)=W(814); sample(3,0,0,0,0);
        assert(W(806)==3 && W(809)==0);
        /* The plant's last committed hold can cross the PLC's Resume scan.
           It belongs to the same slot and must remain valid for a bound. */
        W(609)=W(755)=3; W(785)=4;
        W(821)=3; W(822)=3; scan();
        assert(W(806)==0 && W(809)==1);
        assert(W(788)==0 && W(781)==1);
        if(!strcmp(argv[1],"resume_expiry")) {
            /* Fresh chute and zone samples that never release the same
               physical hold must fail once the handoff bound expires. */
            for(int n=0;n<16 && W(788)==0;n++) {
                W(609)=W(755)=4+n; W(785)=5+n;
                sample(4+n,0,0,0,0);
            }
            assert(W(788)==1 && W(781)==3 && !B(110,0));
        } else if(!strcmp(argv[1],"resume_wrong_identity")) {
            W(609)=W(755)=4; W(603)=W(530)+1; W(785)=5; scan();
            assert(W(788)==1 && W(781)==3 && !B(110,0));
        } else if(!strcmp(argv[1],"resume_wrong_zone")) {
            /* A forward, geometrically valid zone must still leave the
               old held-sample grace rather than inheriting it. */
            W(609)=W(755)=4; W(751)=5; W(605)=2; W(606)=80;
            W(785)=5; scan();
            assert(W(788)==1 && W(781)==3 && !B(110,0));
        } else {
            W(609)=W(755)=4; W(751)=5; W(752)=5; W(753)=0;
            W(605)=2; W(606)=80; W(785)=5; scan();
            assert(W(788)==0 && W(766)==5 && W(768)==0);
        }
    } else assert(0);
    printf("chute PLC %s OK\n",argv[1]);
}
