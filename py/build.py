#!/usr/bin/env python3
"""
Build the CHS-translation-patched ROM from the original dump.

Steps:
  1. Splice the assembled translation routines into free space at 0x76F5.
  2. Patch AH=08h (Get Drive Parameters) call site at 0xA659-0xA690 to
     call xlate_getparams, then jump to the original continuation code.
  3. Patch the head-mask instruction at 0xA965 ("AND DH,0Fh") to call
     xlate_headcyl instead.
  4. Recompute the checksum: this ROM's POST verifies that the 16-bit
     word-sum of the whole 64KB image is zero (mod 0x10000) -- confirmed
     empirically on the original dump. We fix this up using a spare
     filler word placed right after our new code.
"""
import struct

SRC   = "/home/claude/finalbuild/source.bin"
PATCH = "/home/claude/finalbuild/xlate.bin"
OUT   = "/home/claude/finalbuild/PATCHED.BIN"

CODE_BASE       = 0x76F5
GETPARAMS_ADDR  = CODE_BASE + 0x28   # xlate_getparams
HEADCYL_ADDR    = CODE_BASE + 0x68   # xlate_headcyl
CHECKSUM_FILLER = 0x7900             # even-aligned word slot, well past our
                                      # 289-byte routine, still inside the
                                      # free-space block (word alignment
                                      # matters: the ROM's checksum is a
                                      # 16-bit word-sum over the whole image)

data = bytearray(open(SRC, "rb").read())
assert len(data) == 0x10000

patch_code = open(PATCH, "rb").read()
assert len(patch_code) <= 0x200, "patch grew past reserved filler offset"

# sanity-check the exact bytes we are about to overwrite, so we fail loudly
# instead of silently corrupting something we didn't verify
orig_getparams_region = bytes(data[0xA659:0xA691])
expected_getparams_start = bytes([0x26, 0x8A, 0x77, 0x02])   # mov dh, byte es:[bx+2]
assert orig_getparams_region[:4] == expected_getparams_start, orig_getparams_region[:8].hex()

orig_headcyl_instr = bytes(data[0xA965:0xA968])
assert orig_headcyl_instr == bytes([0x80, 0xE6, 0x0F]), orig_headcyl_instr.hex()  # AND DH,0Fh

# The three duplicated "compute cylinder-high byte for IDE port 0x1F5"
# blocks (FORMAT, SEEK, and the shared READ/WRITE/VERIFY routine) only
# ever synthesize 2 real bits (from CL) OR'd with a permanently-dead
# nibble from [bp+0x15] (its only two writers both always produce zero
# -- confirmed by inspection). That means the *hardware I/O layer*
# itself could only ever address 1024 physical cylinders, regardless
# of any INT13-level translation. We repurpose [bp+0x15] (xlate_headcyl
# now writes the true physical-cylinder high byte there) and simplify
# each site to just read it directly.
sites_cylhigh = {
    "format": (0xA60E, 0xA622, bytes([0x8A, 0x46, 0x15, 0x88, 0x46, 0x06])),
    #            start   end(excl, = addr of 'mov dx,1F5h')   mov al,[bp+15] ; mov [bp+6],al
    "seek":   (0xA7EA, 0xA7FA, bytes([0x8A, 0x46, 0x15])),
    #                                   mov al,[bp+15]   ('mov [bp+6],al' already follows in original)
    "shared_ab14": (0xAB3D, 0xAB4D, bytes([0x8A, 0x46, 0x15])),
    #                                   mov al,[bp+15]   ('mov [bp+6],al' already follows in original)
}

for name, (start, end, new_code) in sites_cylhigh.items():
    length = end - start
    assert len(new_code) <= length, (name, len(new_code), length)
    filler = bytes([0x90] * (length - len(new_code)))
    data[start:end] = new_code + filler

# ---------------------------------------------------------------
# 1. splice in the new code
# ---------------------------------------------------------------
data[CODE_BASE:CODE_BASE+len(patch_code)] = patch_code

# ---------------------------------------------------------------
# 2. patch site A: 0xA659-0xA690 (56 bytes) -> CALL + JMP + NOP fill
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
siteA_end   = 0xA68F  # inclusive -- NOT 0xA690: that byte is the original
                       # routine's own "RET" (0xC3). The region previously
                       # extended one byte too far and overwrote that RET
                       # with a NOP, causing AH=08h to fall through into the
                       # entire AH=09h body (drive-select/ready-wait/INT15h
                       # device-busy notify) as an unintended side effect on
                       # every single GetParams call -- this was the actual
                       # cause of the floppy-boot hang whenever a hard disk
                       # was configured, confirmed via emulation + real HW.
siteA_len   = siteA_end - siteA_start + 1
assert siteA_len == 55

call_bytes = rel16_call(siteA_start, GETPARAMS_ADDR)
jmp_addr   = siteA_start + len(call_bytes)
jmp_bytes  = rel8_jmp(jmp_addr, 0xA686)
filler_len = siteA_len - len(call_bytes) - len(jmp_bytes)
assert filler_len >= 0

siteA_patch = call_bytes + jmp_bytes + bytes([0x90] * filler_len)
assert len(siteA_patch) == siteA_len
data[siteA_start:siteA_start+siteA_len] = siteA_patch

# ---------------------------------------------------------------
# 3. patch site B: 0xA965 (3 bytes) -> CALL xlate_headcyl
# ---------------------------------------------------------------
siteB_start = 0xA965
siteB_patch = rel16_call(siteB_start, HEADCYL_ADDR)
assert len(siteB_patch) == 3
data[siteB_start:siteB_start+3] = siteB_patch

# ---------------------------------------------------------------
# 4. fix checksum: whole-ROM 16-bit word sum must be 0 mod 0x10000
# ---------------------------------------------------------------
def word_checksum(buf):
    words = struct.unpack(f"<{len(buf)//2}H", bytes(buf))
    return sum(words) % 0x10000

# zero the filler word first, then compute what it needs to be
struct.pack_into("<H", data, CHECKSUM_FILLER, 0)
current = word_checksum(data)
fix = (-current) % 0x10000
struct.pack_into("<H", data, CHECKSUM_FILLER, fix)

final = word_checksum(data)
print("checksum filler word @", hex(CHECKSUM_FILLER), "=", hex(fix))
print("final word-sum mod 0x10000:", final, "(must be 0)")
assert final == 0

open(OUT, "wb").write(data)
print("wrote", OUT, len(data), "bytes")
print("getparams  @", hex(GETPARAMS_ADDR))
print("headcyl    @", hex(HEADCYL_ADDR))
print("siteA patch:", siteA_patch.hex())
print("siteB patch:", siteB_patch.hex())
