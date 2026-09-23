#!/usr/bin/env python3
"""
Build the BUBios trial ROM on top of the hardware-confirmed ECHS ROM.

    python3 py/build_bubios.py        (from the BUBios folder; needs NASM)

Input : ../binary/SY019L1_27C512_CHSPATCH.BIN  (parent project, MD5 checked)
Output: binary/BUBIOS_SY019L1.BIN

Every patch site asserts the bytes it replaces, every reused AMI string or
option record is checked by content, and both free-space blocks must be
all-zero in the input.  The ROM checksum (16-bit word sum of the whole
64 KB == 0) is balanced with the last word of the data block (0x7EFE).
"""
import hashlib, os, re, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # BUBios/
PARENT = os.path.dirname(ROOT)                     # repository root
SRC  = os.path.join(PARENT, 'binary', 'SY019L1_27C512_CHSPATCH.BIN')
ASM  = os.path.join(ROOT, 'asm', 'bubios.asm')
BIN  = os.path.join(ROOT, 'asm', 'bubios.bin')
LST  = os.path.join(ROOT, 'asm', 'bubios.lst')
MAP  = os.path.join(ROOT, 'asm', 'bubios.map')
OUT  = os.path.join(ROOT, 'binary', 'BUBIOS_SY019L1.BIN')

ECHS_MD5  = '8e520e91100f932d3f054883b08d37da'
CODE_BLK  = (0xDA84, 0xE000)       # executed from F000 only
DATA_BLK  = (0x7902, 0x7EFE)       # copied to 0100:xxxx by the setup
CHECKSUM  = 0x7EFE

def die(msg): sys.exit('BUILD FAILED: ' + msg)

rom = bytearray(open(SRC, 'rb').read())
if hashlib.md5(rom).hexdigest() != ECHS_MD5:
    die('input is not the hardware-confirmed ECHS ROM (%s)' % SRC)

# ------------------------------------------------------------------ assemble
subprocess.run(['nasm', '-f', 'bin', ASM, '-o', BIN, '-l', LST],
               check=True, cwd=os.path.dirname(ASM))
blob = open(BIN, 'rb').read()
mp = open(MAP).read()
sec = {m.group(5): (int(m.group(1), 16), int(m.group(2), 16), int(m.group(4), 16))
       for m in re.finditer(r'^\s*([0-9A-F]+)\s+([0-9A-F]+)\s+([0-9A-F]+)\s+([0-9A-F]+)\s+progbits\s+(\w+)', mp, re.M)}
sym = {m.group(2): int(m.group(1), 16)
       for m in re.finditer(r'^\s+[0-9A-F]+\s+([0-9A-F]+)\s+(\w+)\s*$', mp, re.M)}
code_v, code_s, code_len = sec['code']
data_v, data_s, data_len = sec['data']
assert code_v == CODE_BLK[0] and data_v == DATA_BLK[0]
if code_v + code_len > CODE_BLK[1]: die('code block overflow (%d bytes too long)' % (code_v + code_len - CODE_BLK[1]))
if data_v + data_len > DATA_BLK[1]: die('data block overflow (%d bytes too long)' % (data_v + data_len - DATA_BLK[1]))

for lo, hi in (CODE_BLK, (DATA_BLK[0], DATA_BLK[1] + 2)):
    if any(rom[lo:hi]): die('free block %04X-%04X is not empty in the input' % (lo, hi))

rom[code_v:code_v + code_len] = blob[code_s:code_s + code_len]
rom[data_v:data_v + data_len] = blob[data_s:data_s + data_len]

# ------------------------------------------------------------------ helpers
def patch(at, old, new, what):
    old = bytes(old); new = bytes(new)
    if rom[at:at + len(old)] != old:
        die('%s: unexpected bytes at %04X: %s' % (what, at, rom[at:at + len(old)].hex()))
    assert len(new) <= len(old) or what.startswith('str'), what
    rom[at:at + len(new)] = new

def rel16(site, target, size=3):
    return struct.pack('<H', (target - (site + size)) & 0xFFFF)   # wraps inside the 64K segment

def cstr(at):
    return bytes(rom[at:rom.index(0, at)])

def expect_str(at, text, what):
    if not cstr(at).startswith(text): die('%s: string at %04X is %r' % (what, at, cstr(at)))

def expect_record(at, label):
    if cstr(at).rstrip() != label.encode(): die('option record %04X is %r, want %r' % (at, cstr(at), label))

# ------------------------------------------------------------------ verify reused AMI data
expect_str(0x6F35, b'360  KB, 5', 'floppy 360K')
expect_str(0x6F35 + 17 * 3, b'1.44 MB, 3', 'floppy 1.44M')
expect_str(0x6F35 + 17 * 4, b'2.88 MB, 3', 'floppy 2.88M')
expect_str(0x6F8A, b'Monochrome', 'video mono')
expect_str(0x6F9B, b'Color 80x25', 'video 80')
expect_str(0x6FAC, b'Color 40x25', 'video 40')
expect_str(0x6FBD, b'VGA/PGA/EGA', 'video vga')
expect_str(0x2D4F, b'Load BIOS Setup Default', 'help defaults')
expect_str(0x2D9B, b'Load Power-On Default', 'help power-on')
expect_str(0x2DE5, b'Change the User Password', 'help password')
expect_str(0x2E11, b'Auto Detection of Hard Disk', 'help detect')
expect_str(0x2E7C, b'Write the settings to the CMOS and Exit', 'help save')
expect_str(0x2E75, b'Do Not Write the settings', 'help quit')
for at, lab in ((0xBDE7, 'External Cache Memory'), (0xBE8F, 'Video   ROM Shadow C000,16K'),
                (0xC057, 'System  ROM Shadow F000,64K'), (0xBDA3, 'System Boot Up Sequence'),
                (0xBD1D, 'System Boot Up Num Lock'), (0xBE6D, 'Password Checking Option')):
    expect_record(at, lab)
    if rom[at + 0x1E] & 0x7F: die('record %04X has a value hook' % at)
assert struct.unpack_from('<H', rom, 0x8292)[0] == 0xB898
assert struct.unpack_from('<H', rom, 0xE401)[0] == 306           # drive type 1 = 306 cyl
assert rom[0x76F5:0x76FA] == b'\x53\x89\xc3\x89\xc8'                      # ECHS calc_factor: push bx / mov bx,ax / mov ax,cx

# ------------------------------------------------------------------ code patch sites
patch(0x36D4, b'\x55\x8b\xec', b'\xe9' + rel16(0x36D4, sym['bu_main']), 'main menu -> bu_main')
patch(0x38B5, b'\x55\x8b\xec', b'\xe9' + rel16(0x38B5, sym['bu_hdr']), 'header -> bu_hdr')
patch(0x0D3D, b'\xe8\xce\x45', b'\xe8' + rel16(0x0D3D, sym['bu_edkey']), 'editor getkey -> bu_edkey')
patch(0x08D5, b'\xb4\x00\xcd\x16', b'\xe8' + rel16(0x08D5, sym['bu_stdkey']) + b'\x90', 'standard getkey -> bu_stdkey')

# ------------------------------------------------------------------ colours
# 16 schemes x 4 bytes; each byte b gives an attribute pair (b, b with nibbles swapped):
#   byte0 content / selected menu item, byte1 option fields / selected field,
#   byte2 dialogs (and their inverse), byte3 frame+title / inverse.
OLD_PAL = bytes.fromhex('57601720204730577030576017707017573047202017605717605730304720177020176017171717'
                        '305760177070707007070707700770070770077070171770')
NEW_PAL = bytes.fromhex(
    '03033003'      # 0  BUBios teal  (MR-style: teal on black, green active tab)
    '06066006'      # 1  amber
    '02022002'      # 2  green phosphor
    '17177017'      # 3  classic blue
    '07077007'      # 4  plain grey
    '57601720'      # 5  original AMI scheme 0
    '03033003'      # 6..15 repeat
    '06066006'
    '02022002'
    '17177017'
    '07077007'
    '57601720'
    '03033003'
    '06066006'
    '02022002'
    '17177017')
patch(0x5037, OLD_PAL, NEW_PAL, 'colour schemes')
patch(0x4FF6, b'\x0f', b'\x08', 'bright mask')

# ------------------------------------------------------------------ footers
def footer(text, old_at, fill_to=None):
    old = cstr(old_at)
    new = text
    if fill_to:
        new = new + b'\xcd' * (fill_to - len(new))
    if len(new) > len(old): die('footer at %04X too long (%d > %d)' % (old_at, len(new), len(old)))
    rom[old_at:old_at + len(new)] = new
    rom[old_at + len(new)] = 0
ARROWS = b'\x19\x1a\x18\x1b'
footer(b'\xb5 Tab:Page  ESC:Summary  ' + ARROWS + b':Sel  PgUp/PgDn:Modify  F1:Help \xc6', 0x309A)
footer(b'\xb5 Tab:Page ESC:Summary ' + ARROWS + b':Sel PU/PD:Modify \xc6', 0x6D3A, fill_to=len(cstr(0x6D3A)))

# ------------------------------------------------------------------ checksum
struct.pack_into('<H', rom, CHECKSUM, 0)
s = sum(struct.unpack('<32768H', bytes(rom))) & 0xFFFF
struct.pack_into('<H', rom, CHECKSUM, (-s) & 0xFFFF)
assert sum(struct.unpack('<32768H', bytes(rom))) & 0xFFFF == 0

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, 'wb').write(rom)
title = cstr(sym['s_title'])
print('code  %04X-%04X  %4d bytes (%d free)' % (code_v, code_v + code_len - 1, code_len, CODE_BLK[1] - code_v - code_len))
print('data  %04X-%04X  %4d bytes (%d free)' % (data_v, data_v + data_len - 1, data_len, DATA_BLK[1] - data_v - data_len))
print('title %d chars' % len(title))
print('wrote', os.path.relpath(OUT, ROOT), 'MD5', hashlib.md5(rom).hexdigest())
