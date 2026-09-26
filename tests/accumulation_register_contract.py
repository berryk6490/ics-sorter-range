"""Named PLC process registers shared by typed postflight and guest recovery.

Addresses match Sorter.st QW214..242. QW221 is the next serial number, not a
reset-to-zero counter; the typed restoration contract records it as history.
"""

RESET_ZERO_PROCESS_COUNTERS = {
    214: "noread_ct", 215: "coll_ct", 216: "jam_ct", 217: "missort_ct",
    218: "recirc_ct", 219: "nohome_ct", 220: "inducted_ct",
    222: "tr11_ct", 223: "tr12_ct", 224: "tr13_ct",
    225: "tr21_ct", 226: "tr22_ct", 227: "tr23_ct",
    228: "tr31_ct", 229: "tr32_ct", 230: "tr33_ct",
    231: "tr11_bad", 232: "tr12_bad", 233: "tr13_bad",
    234: "tr21_bad", 235: "tr22_bad", 236: "tr23_bad",
    237: "tr31_bad", 238: "tr32_bad", 239: "tr33_bad",
    240: "div_act_1", 241: "div_act_2", 242: "div_act_3",
}
SERIAL_NEXT_ADDRESS = 221
PROCESS_FIRST_ADDRESS = 214
PROCESS_WORD_COUNT = 29
