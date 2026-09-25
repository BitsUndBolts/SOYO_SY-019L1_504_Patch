#!/usr/bin/env python3
"""Regression test for the patched ROM, run through the real INT 13h code.

For each simulated drive geometry:
  * AH=08h must report the expected translated geometry, DL = 1 drive,
    AL = sectors/track and CF = 0. The expected values are pinned on
    purpose: changing them would make existing partitions unreadable
    (see Project_Overview.md, section 6).
  * 44 AH=02h reads (corners + random) must hit exactly the LBA the
    logical CHS implies, never beyond the end of the disk, and must
    return CX/DX (including the upper 32-bit halves) unchanged.
  * For drives that need no translation (<= 1024 cylinders) the patched
    ROM must produce the same AH=08h registers and the same IDE task-file
    writes as the ORIGINAL ROM.

    python3 py/regress.py        (needs: pip install unicorn)
"""
import random
import sys
from ide_harness import *

orig    = load_rom(ORIG_ROM)
patched = load_rom(PATCHED_ROM)

# physical C/H/S -> (cylinder count, heads) that AH=08h must report
GEOMETRIES = {
    (998, 16, 32):   (997, 16),
    (1024, 16, 63):  (1023, 16),
    (1986, 16, 63):  (992, 32),
    (2100, 16, 63):  (524, 64),
    (3875, 16, 63):  (967, 64),     # 2 GB SanDisk CF   (hardware-confirmed)
    (7899, 16, 63):  (986, 128),
    (8912, 15, 63):  (556, 240),    # WD Caviar 24300   (hardware-confirmed)
    (15506, 16, 63): (971, 255),
    (16000, 16, 63): (1002, 255),   # 8 GB IEI CF       (hardware-confirmed)
    (16383, 16, 63): (1024, 255),   # largest ATA CHS geometry (clamped)
}
HI = 0x55550000          # marker in the upper 32-bit halves, must survive


def decode_08(r):
    cx = int(r['ecx'], 16) & 0xFFFF
    dx = int(r['edx'], 16) & 0xFFFF
    maxc = (cx >> 8) | ((cx & 0xC0) << 2)
    return maxc, (dx >> 8) + 1, cx & 0x3F, dx & 0xFF


failures = 0
for (C, H, S), (exp_cyls, exp_heads) in GEOMETRIES.items():
    e = Emu(patched, C, H, S)
    g = e.call(0x0800, 0, 0x0080, e32=HI)
    maxc, nh, ns, dl = decode_08(g)
    al = int(g['eax'], 16) & 0xFF
    cf = int(g['fl'], 16) & 1
    problems = []
    if (maxc + 1, nh, ns) != (exp_cyls, exp_heads, S):
        problems.append(f"geometry {maxc+1}/{nh}/{ns}, expected {exp_cyls}/{exp_heads}/{S}")
    if dl != 1 or al != S or cf:
        problems.append(f"DL={dl} AL={al} CF={cf}")

    random.seed(2)
    pts = [(0, 0, 1), (0, 1, 1), (maxc, nh - 1, ns)] + \
          [(random.randrange(maxc + 1), random.randrange(nh), random.randrange(1, ns + 1))
           for _ in range(41)]
    bad_lba = oob = bad_regs = 0
    for c, h, s in pts:
        cxi = ((c & 0xFF) << 8) | ((c >> 8) << 6) | s
        dxi = (h << 8) | 0x80
        r = e.call(0x0201, cxi, dxi, e32=HI)
        got = r['ide'][-1][-1] if r['ide'] else None
        if got != (c * nh + h) * ns + s - 1:
            bad_lba += 1
        if got is not None and got >= C * H * S:
            oob += 1
        if int(r['edx'], 16) != (HI | dxi) or int(r['ecx'], 16) != (HI | cxi):
            bad_regs += 1
    if bad_lba or oob or bad_regs:
        problems.append(f"reads: wrong LBA={bad_lba} beyond end={oob} register damage={bad_regs}")

    # identity geometries: must behave exactly like the original ROM
    if C <= 1024:
        eo = Emu(orig, C, H, S)
        go = eo.call(0x0800, 0, 0x0080, e32=HI)
        keys = ('eax', 'ecx', 'edx', 'fl', 'bda74')
        if any(g[k] != go[k] for k in keys):
            problems.append("AH=08h differs from original ROM")
        for c, h, s in pts[:10]:
            cxi = ((c & 0xFF) << 8) | ((c >> 8) << 6) | s
            dxi = (h << 8) | 0x80
            if e.call(0x0201, cxi, dxi)['ide'] != eo.call(0x0201, cxi, dxi)['ide']:
                problems.append("IDE traffic differs from original ROM"); break

    cap = (maxc + 1) * nh * ns * 512 / 2**30
    status = "OK  " if not problems else "FAIL"
    print(f"{status} {C:5d}/{H:2d}/{S}  ->  {maxc+1:4d}/{nh:3d}/{ns}  {cap:5.2f} GiB  "
          f"({len(pts)} reads)" + ("" if not problems else "  " + "; ".join(problems)))
    failures += bool(problems)

print("\nALL PASS" if not failures else f"\n{failures} GEOMETRIES FAILED")
sys.exit(1 if failures else 0)
