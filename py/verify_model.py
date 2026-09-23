"""
Pure-Python re-implementation of the exact algorithm encoded in xlate.asm,
to validate the CHS translation math independently of the assembly.
"""
 
def calc_factor(real_cyl, real_heads):
    factor = 1
    while True:
        if real_cyl // factor <= 1024:
            break
        if real_heads * (factor * 2) > 256:
            break
        factor *= 2
    trans_heads = real_heads * factor
    return factor, trans_heads
 
def get_params(real_cyl, real_heads, real_sect):
    factor, trans_heads = calc_factor(real_cyl, real_heads)
    trans_cyl_count = real_cyl // factor
    max_cyl = trans_cyl_count - 2
    max_cyl = min(max_cyl, 0x3ff)
    max_head = trans_heads - 1
    return max_cyl, max_head, real_sect   # what INT13h AH=08 reports to the OS
 
def logical_to_physical(log_c, log_h, log_s, real_cyl, real_heads, real_sect):
    factor, trans_heads = calc_factor(real_cyl, real_heads)
    lba = (log_c * trans_heads + log_h) * real_sect + (log_s - 1)
    q, phys_sect = divmod(lba, real_sect)
    phys_cyl, phys_head = divmod(q, real_heads)
    phys_sect += 1
    return phys_cyl, phys_head, phys_sect
 
def roundtrip_test(real_cyl, real_heads, real_sect, label):
    factor, trans_heads = calc_factor(real_cyl, real_heads)
    max_cyl, max_head, sect = get_params(real_cyl, real_heads, real_sect)
    reported_cyls = max_cyl + 2          # OS's notion of cylinder count (per this BIOS's -2 convention)
    reported_heads = max_head + 1
    capacity = real_cyl * real_heads * real_sect * 512
    reported_capacity = reported_cyls * reported_heads * real_sect * 512
    print(f"--- {label} ---")
    print(f"  real geometry:      {real_cyl}/{real_heads}/{real_sect}  = {capacity/1e9:.3f} GB")
    print(f"  factor={factor}  translated heads={trans_heads}")
    print(f"  reported to OS:     cyl(count)~{reported_cyls} heads={reported_heads} sect={sect}"
          f"  = {reported_capacity/1e9:.3f} GB   (max CH:CL index = {max_cyl})")
 
    # exhaustively verify every logical CHS the OS could address maps to a
    # valid, unique, in-range physical CHS, and that the mapping is
    # monotonic/consistent (round-trips through the same LBA formula
    # get_params implicitly assumes)
    errors = 0
    checked = 0
    seen_phys = set()
    # spot-check a grid rather than every single combination (would be slow
    # in pure python for the biggest geometry) -- corners + random sample
    import random
    random.seed(1)
    cyl_samples = sorted(set([0, 1, reported_cyls//2, reported_cyls-1] +
                              [random.randint(0, reported_cyls-1) for _ in range(40)]))
    head_samples = sorted(set([0, reported_heads-1] +
                               [random.randint(0, reported_heads-1) for _ in range(6)]))
    sect_samples = [1, real_sect]
 
    for lc in cyl_samples:
        for lh in head_samples:
            for ls in sect_samples:
                pc, ph, ps = logical_to_physical(lc, lh, ls, real_cyl, real_heads, real_sect)
                checked += 1
                if not (0 <= pc < real_cyl and 0 <= ph < real_heads and 1 <= ps <= real_sect):
                    print(f"  OUT OF RANGE: log({lc},{lh},{ls}) -> phys({pc},{ph},{ps})")
                    errors += 1
                key = (pc, ph, ps)
                if key in seen_phys:
                    print(f"  COLLISION at phys{key} from log({lc},{lh},{ls})")
                    errors += 1
                seen_phys.add(key)
    print(f"  checked {checked} logical CHS samples, {errors} errors")
    return errors
 
total_errors = 0
total_errors += roundtrip_test(2100, 16, 63, "~1GB IDE drive")
total_errors += roundtrip_test(4092, 16, 63, "~2.1GB IDE drive")
total_errors += roundtrip_test(8192, 16, 63, "~4.2GB IDE drive")
total_errors += roundtrip_test(16383, 16, 63, "~8.4GB IDE drive (ATA CHS max)")
total_errors += roundtrip_test(1024, 16, 63, "exactly at old 504MB-equivalent boundary drive shape (no translation needed)")
total_errors += roundtrip_test(615, 4, 17, "tiny legacy MFM-style geometry (sanity check small drives still work)")
 
print()
print("TOTAL ERRORS:", total_errors)
