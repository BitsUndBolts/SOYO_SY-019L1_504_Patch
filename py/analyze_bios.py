#!/usr/bin/env python3
"""
SOYO SY-019L1 (AMI 386/486) BIOS analysis helper.
 
Regenerates the findings in BIOS_ADDRESS_MAP.md directly from BIOS_HEX.txt,
so they can be re-verified (or re-run against a different AMI ROM of the
same generation) without re-doing the manual reverse-engineering.
 
Usage:
    python3 analyze_bios.py BIOS_HEX.txt
"""
import sys
import struct
import re
 
 
def load_hex(path):
    data = open(path).read().split()
    return bytes(int(x, 16) for x in data)
 
 
def check_reset_vector(b):
    print("== Reset vector (0xFFF0) ==")
    rv = b[0xFFF0:0x10000]
    opcode, off, seg = rv[0], struct.unpack('<H', rv[1:3])[0], struct.unpack('<H', rv[3:5])[0]
    print(f"  bytes: {rv.hex(' ')}")
    print(f"  opcode 0x{opcode:02X} -> JMP FAR {seg:04X}:{off:04X}")
    date = rv[5:13].split(b'\x00')[0].decode(errors='replace')
    print(f"  release date string: {date!r}")
    print(f"  model ID byte @0xFFFE: 0x{b[0xFFFE]:02X}")
    print()
 
 
def verify_checksum(b):
    print("== ROM checksum (verified against code at 0xD759-0xD779) ==")
    words = struct.unpack('<32768H', b)
    s = sum(words) & 0xFFFF
    print(f"  16-bit word-sum over entire 64KB image: 0x{s:04X} "
          f"({'PASS - image is self-consistent' if s == 0 else 'FAIL - image modified/corrupt'})")
    print("  Algorithm (from disassembly @ CS:0xD759):")
    print("    xor bx,bx ; xor si,si ; mov cx,0x8000")
    print("    loop: cs lodsw ; add bx,ax ; loop")
    print("    jz ok  (BX must be 0 after summing all 32768 words)")
    print()
 
 
def dump_disk_type_table(b, start=0xE401, n=46):
    print(f"== Fixed Disk Parameter Table (types 1-{n}) @ 0x{start:04X} ==")
    print("  record = 16 bytes: cyl(u16) heads(u16) pad(u8)=0 precomp(u16) "
          "control(u8) reserved(u32) landing_zone(u16) sectors(u16)")
    for i in range(n):
        off = start + i * 16
        rec = b[off:off + 16]
        cyl, heads = struct.unpack('<HH', rec[0:4])
        precomp = struct.unpack('<H', rec[5:7])[0]
        ctl = rec[7]
        landz, sect = struct.unpack('<HH', rec[12:16])
        size_mb = cyl * heads * sect * 512 / (1024 * 1024)
        print(f"  type {i+1:2d} off=0x{off:04X}  cyl={cyl:5d} head={heads:2d} "
              f"precomp={precomp:6d} ctl={ctl:3d} landz={landz:5d} sect={sect:2d} "
              f"~{size_mb:6.1f} MB")
    end = start + n * 16
    print(f"  type 47 (user-definable) placeholder @ 0x{end:04X}: "
          f"{b[end:end+16].hex(' ')}  (all-zero in ROM; filled by Setup at runtime)")
    print()
 
 
def find_int13_call_sites(b):
    print("== INT 13h call sites (CD 13 bytes found in ROM) ==")
    print("  NOTE: these are the BIOS calling *itself* (e.g. boot loader, drive")
    print("  auto-detect). The handler's real entry point is only wired up at")
    print("  runtime via the IVT (int 0x13 -> 0000:004C) and is not yet located.")
    offs = [m.start() for m in re.finditer(b'\xcd\x13', b)]
    for o in offs:
        print(f"    0x{o:04X}")
    print()
 
 
def find_padding(b, minlen=48):
    print(f"== Candidate free/padding space (runs of 0x00, >= {minlen} bytes) ==")
    i, n = 0, len(b)
    while i < n:
        if b[i] == 0:
            j = i
            while j < n and b[j] == 0:
                j += 1
            if j - i >= minlen:
                print(f"    0x{i:04X}-0x{j:04X}  ({j - i} bytes)")
            i = j
        else:
            i += 1
    print()
 
 
def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'BIOS_HEX.txt'
    b = load_hex(path)
    print(f"Loaded {len(b)} (0x{len(b):X}) bytes from {path}\n")
    check_reset_vector(b)
    verify_checksum(b)
    dump_disk_type_table(b)
    find_int13_call_sites(b)
    find_padding(b)
 
 
if __name__ == '__main__':
    main()
