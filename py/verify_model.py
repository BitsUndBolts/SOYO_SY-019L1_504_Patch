#!/usr/bin/env python3
"""
Pure-Python re-implementation of the translation math in asm/xlate.asm,
to validate it independently of the assembly and of the emulator.

    python3 py/verify_model.py   (no dependencies)

Checks, for a range of drive geometries:
  * the geometry AH=08h reports (pinned for the hardware-confirmed drives),
  * that sampled logical CHS addresses map to valid, unique physical CHS,
  * that the reported capacity never exceeds the physical capacity.
"""
import random


def calc_factor(real_cyl, real_heads):
    """Head ladder RealHeads * 2^n, jumping to 255 instead of reaching 256."""
    cyl, heads = real_cyl, real_heads
    while cyl > 1024:
        if heads > 127:
            return 255
        heads *= 2
        cyl //= 2
    return heads


def get_params(real_cyl, real_heads, real_sect):
    """What INT 13h AH=08h reports: (max cylinder index, max head index, sectors)."""
    trans_heads = calc_factor(real_cyl, real_heads)
    log_cyl = real_cyl * real_heads // trans_heads
    max_cyl = min(log_cyl - 2, 0x3FF)          # "-2" = the original ROM's convention
    return max_cyl, trans_heads - 1, real_sect


def logical_to_physical(log_c, log_h, log_s, real_cyl, real_heads, real_sect):
    """What xlate_headcyl does on every read/write/verify/format/seek."""
    trans_heads = calc_factor(real_cyl, real_heads)
    lba = (log_c * trans_heads + log_h) * real_sect + (log_s - 1)
    q, phys_sect = divmod(lba, real_sect)
    phys_cyl, phys_head = divmod(q, real_heads)
    return phys_cyl, phys_head, phys_sect + 1


def roundtrip_test(real_cyl, real_heads, real_sect, label, expect=None):
    max_cyl, max_head, sect = get_params(real_cyl, real_heads, real_sect)
    cyls, heads = max_cyl + 1, max_head + 1       # what DOS uses
    capacity = real_cyl * real_heads * real_sect * 512
    reported = cyls * heads * sect * 512
    print(f"--- {label} ---")
    print(f"  physical {real_cyl}/{real_heads}/{real_sect} = {capacity/2**30:.2f} GiB"
          f"  ->  reported {cyls}/{heads}/{sect} = {reported/2**30:.2f} GiB")

    errors = 0
    if expect and (cyls, heads) != expect:
        print(f"  GEOMETRY CHANGED: expected {expect[0]}/{expect[1]}")
        errors += 1
    if reported > capacity:
        print("  REPORTED CAPACITY EXCEEDS DRIVE")
        errors += 1

    random.seed(1)
    cyl_samples = sorted({0, 1, cyls // 2, cyls - 1} |
                         {random.randint(0, cyls - 1) for _ in range(40)})
    head_samples = sorted({0, heads - 1} | {random.randint(0, heads - 1) for _ in range(6)})
    seen, checked = set(), 0
    for lc in cyl_samples:
        for lh in head_samples:
            for ls in (1, real_sect):
                pc, ph, ps = logical_to_physical(lc, lh, ls, real_cyl, real_heads, real_sect)
                checked += 1
                if not (0 <= pc < real_cyl and 0 <= ph < real_heads and 1 <= ps <= real_sect):
                    print(f"  OUT OF RANGE: log({lc},{lh},{ls}) -> phys({pc},{ph},{ps})")
                    errors += 1
                if (pc, ph, ps) in seen:
                    print(f"  COLLISION at phys({pc},{ph},{ps}) from log({lc},{lh},{ls})")
                    errors += 1
                seen.add((pc, ph, ps))
    print(f"  checked {checked} logical CHS samples, {errors} errors")
    return errors


total = 0
total += roundtrip_test(615, 4, 17, "tiny MFM-style drive (no translation)")
total += roundtrip_test(1024, 16, 63, "504 MB boundary (no translation)", (1023, 16))
total += roundtrip_test(2100, 16, 63, "~1 GB drive", (524, 64))
total += roundtrip_test(3875, 16, 63, "2 GB SanDisk CF (hardware-confirmed)", (967, 64))
total += roundtrip_test(8912, 15, 63, "WD Caviar 24300 (hardware-confirmed)", (556, 240))
total += roundtrip_test(8192, 16, 63, "~4.2 GB drive", (1023, 128))
total += roundtrip_test(16000, 16, 63, "8 GB IEI CF (hardware-confirmed)", (1002, 255))
total += roundtrip_test(16383, 16, 63, "largest ATA CHS geometry (clamped)", (1024, 255))
print()
print("TOTAL ERRORS:", total)
raise SystemExit(1 if total else 0)
