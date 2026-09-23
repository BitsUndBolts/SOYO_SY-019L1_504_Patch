#!/usr/bin/env python3
"""
Build the CHS-translation-patched ROM from the original dump.

    python3 py/build.py          (from the repository root, or from py/)

Requires NASM on the PATH.

Steps:
  0. Assemble asm/xlate.asm with NASM (-> asm/xlate.bin + asm/xlate.lst).
  1. Splice the assembled translation routines into free space at 0x76F5.
  2. Patch the AH=08h (Get Drive Parameters) reporting body at
     0xA659-0xA685 to CALL xlate_getparams, then JMP to the original
     handler tail at 0xA686 (which must survive -- see bugs #2/#3).
  3. Patch the head-mask instruction at 0xA965 ("AND DH,0Fh") to
     CALL xlate_headcyl instead.
  4. Replace the three cylinder-high-byte builders (FORMAT, SEEK and the
     shared READ/WRITE/VERIFY path) with "MOV AL,[BP+6]" + NOP fill.
  5. Fix the checksum: this ROM's POST verifies that the 16-bit word-sum
     of the whole 64 KB image is zero (mod 0x10000). A spare word at
     0x7900 inside the free-space block is set to balance the sum.

The original bytes at every patch site are asserted before they are
overwritten, so the script fails loudly on an unexpected ROM.
"""
import hashlib
import os
import struct
import subprocess
import sys

ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC   = os.path.join(ROOT, "binary", "SY019L1_27C512_ORIGINAL.BIN")
ASM   = os.path.join(ROOT, "asm", "xlate.asm")
PATCH = os.path.join(ROOT, "asm", "xlate.bin")      # build intermediate (git-ignored)
LST   = os.path.join(ROOT, "asm", "xlate.lst")
OUT   = os.path.join(ROOT, "binary", "SY019L1_27C512_CHSPATCH.BIN")

ORIG_MD5     = "e80a824d8f3c39d40b98a4be5d8d9cff"
EXPECTED_MD5 = "8e520e91100f932d3f054883b08d37da"   # the ROM confirmed on hardware

CODE_BASE       = 0x76F5
CHECKSUM_FILLER = 0x7900   # even-aligned word slot, past the 353-byte patch
                           # code and still inside the free-space block
                           # (alignment matters: the checksum is a word-sum)

# ---------------------------------------------------------------
# 0. assemble
# ---------------------------------------------------------------
try:
    subprocess.run(["nasm", "-f", "bin", ASM, "-o", PATCH, "-l", LST], check=True)
except FileNotFoundError:
    sys.exit("nasm not found -- install NASM (https://www.nasm.us) and retry")


def label_offsets(path):
    """Read the entry offsets straight out of nasm's listing, so the call
    sites can never drift out of sync with the assembled code."""
    out, want = {}, None
    for line in open(path):
        body = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
        stripped = body.strip()
        if stripped.endswith(":") and stripped[:-1].replace("_", "").isalnum():
            want = stripped[:-1]
        elif want:
            parts = body.split()
            if parts and len(parts[0]) == 8:
                try:
                    out[want] = int(parts[0], 16); want = None
                except ValueError:
                    pass
    return out


_lbl = label_offsets(LST)
GETPARAMS_ADDR = CODE_BASE + _lbl["xlate_getparams"]
HEADCYL_ADDR   = CODE_BASE + _lbl["xlate_headcyl"]

data = bytearray(open(SRC, "rb").read())
assert len(data) == 0x10000
assert hashlib.md5(data).hexdigest() == ORIG_MD5, "unexpected original ROM"

patch_code = open(PATCH, "rb").read()
assert CODE_BASE + len(patch_code) <= CHECKSUM_FILLER, "patch grew into the checksum word"
assert not any(data[CODE_BASE:CHECKSUM_FILLER + 2]), "free-space block is not empty"

# sanity-check the exact bytes we are about to overwrite
assert bytes(data[0xA659:0xA65D]) == bytes.fromhex("268a7702"), "AH=08h body"   # mov dh,es:[bx+2]
assert bytes(data[0xA965:0xA968]) == bytes.fromhex("80e60f"), "AND DH,0Fh"

# ---------------------------------------------------------------
# 4. the three cylinder-high-byte builders
# ---------------------------------------------------------------
# The FORMAT, SEEK and shared READ/WRITE/VERIFY paths each build the byte
# for IDE port 1F5h (cylinder high) in-line, just before the OUT:
#
#     mov al,cl / shr al,6                    ; cylinder bits 9:8 from CL
#     mov dl,[bp+15h] / shr dl,4 / and dl,0Ch ; bits 11:10 from the caller's
#                                             ; DH bits 7:6 (AMI convention;
#                                             ; [bp+15h] = saved caller DH)
#     or al,dl  (and store it to [bp+6])
#
# That can express at most 12 cylinder bits, and in practice only 10,
# because DOS never sets DH bits 7:6 -- so the I/O layer itself could only
# reach 1024 physical cylinders, whatever the INT 13h layer did.
#
# xlate_headcyl (called earlier, from the shared head-byte builder at
# 0xA965) now stores the complete physical-cylinder high byte in the
# frame scratch byte [bp+6]. For functions that are not translated
# (reset, recalibrate, park, ...) it stores the value the original
# formula above would have produced, so those behave exactly as before.
# Each site is therefore reduced to "mov al,[bp+6]" (8A 46 06) followed
# by NOP fill, keeping every later instruction at its original address.
# The caller's DH at [bp+15h] is only ever read, never written (bug #4).
LOAD_CYLHI = bytes.fromhex("8a4606")                      # mov al,[bp+6]
sites_cylhigh = {
    #  name           start    end(excl)  original bytes
    "format":      (0xA60E, 0xA622, "8ac1c0e8068ad08a4615c0e804240c0ac2884606"),
    "seek":        (0xA7EA, 0xA7FA, "8ac1c0e8068a5615c0ea0480e20c0ac2"),
    "shared_rwv":  (0xAB3D, 0xAB4D, "8ac1c0e8068a5615c0ea0480e20c0ac2"),
}
# (FORMAT stored [bp+6] itself inside the replaced block; SEEK and the
#  shared path do "mov [bp+6],al" after "mov dx,1F5h", which is kept and
#  now just rewrites the same value.)
for name, (start, end, orig_hex) in sites_cylhigh.items():
    assert bytes(data[start:end]) == bytes.fromhex(orig_hex), name
    data[start:end] = LOAD_CYLHI + bytes([0x90] * (end - start - len(LOAD_CYLHI)))

# ---------------------------------------------------------------
# 1. splice in the new code
# ---------------------------------------------------------------
data[CODE_BASE:CODE_BASE + len(patch_code)] = patch_code


# ---------------------------------------------------------------
# 2. AH=08h body: 0xA659-0xA685 (45 bytes) -> CALL + JMP + NOP fill
# ---------------------------------------------------------------
def rel16_call(from_addr, to_addr):
    # from_addr = address of the CALL opcode byte itself
    disp = (to_addr - (from_addr + 3)) & 0xFFFF
    return bytes([0xE8]) + struct.pack("<H", disp)


def rel8_jmp(from_addr, to_addr):
    disp = to_addr - (from_addr + 2)
    assert -128 <= disp <= 127, disp
    return bytes([0xEB]) + struct.pack("<b", disp)


siteA_start = 0xA659
siteA_end   = 0xA685   # inclusive. 0xA686-0xA690 is the original handler
                       # tail "mov dl,[0x75] / mov ah,0 / mov [0x74],ah / ret"
                       # (DL = number of drives, status = 0) and must
                       # survive -- overwriting it was bugs #2 and #3.
siteA_len   = siteA_end - siteA_start + 1
assert siteA_len == 45
assert bytes(data[0xA686:0xA691]) == bytes.fromhex("8a167500" "b400" "88267400" "c3")

call_bytes = rel16_call(siteA_start, GETPARAMS_ADDR)
jmp_bytes  = rel8_jmp(siteA_start + len(call_bytes), 0xA686)
siteA_patch = call_bytes + jmp_bytes + bytes([0x90] * (siteA_len - 5))
data[siteA_start:siteA_start + siteA_len] = siteA_patch

# ---------------------------------------------------------------
# 3. head mask: 0xA965 (3 bytes) -> CALL xlate_headcyl
# ---------------------------------------------------------------
siteB_patch = rel16_call(0xA965, HEADCYL_ADDR)
data[0xA965:0xA968] = siteB_patch


# ---------------------------------------------------------------
# 5. checksum: whole-ROM 16-bit word sum must be 0 mod 0x10000
# ---------------------------------------------------------------
def word_checksum(buf):
    return sum(struct.unpack(f"<{len(buf)//2}H", bytes(buf))) % 0x10000


struct.pack_into("<H", data, CHECKSUM_FILLER, 0)
fix = (-word_checksum(data)) % 0x10000
struct.pack_into("<H", data, CHECKSUM_FILLER, fix)
assert word_checksum(data) == 0

open(OUT, "wb").write(data)
md5 = hashlib.md5(data).hexdigest()
print(f"patch code : {len(patch_code)} bytes @ {CODE_BASE:#06x}")
print(f"getparams  @ {GETPARAMS_ADDR:#06x}   headcyl @ {HEADCYL_ADDR:#06x}")
print(f"checksum word @ {CHECKSUM_FILLER:#06x} = {fix:#06x}")
print(f"wrote {os.path.relpath(OUT, ROOT)}  MD5 {md5}")
if md5 == EXPECTED_MD5:
    print("MD5 matches the ROM confirmed on real hardware.")
else:
    print("NOTE: MD5 differs from the hardware-confirmed ROM "
          f"({EXPECTED_MD5}) -- expected only if asm/xlate.asm was changed.")
