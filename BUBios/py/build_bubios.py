#!/usr/bin/env python3
"""
Build the BUBios 2.1 ROM on top of the hardware-confirmed ECHS ROM.

    python3 py/build_bubios.py        (from the BUBios folder; needs NASM)

Input : binary/base/SY019L1_27C512_CHSPATCH.BIN  (the ECHS ROM, MD5 checked)
Output: binary/BUBIOS2_SY019L1.BIN

Every patch site asserts the bytes it replaces, every reused AMI string or
option record is checked by content, and both free-space blocks must be
all-zero in the input.  The ROM checksum (16-bit word sum of the whole
64 KB == 0) is balanced with the last word of the data block (0x7EFE).
"""
import hashlib, os, re, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # BUBios/
SRC  = os.path.join(ROOT, 'binary', 'base', 'SY019L1_27C512_CHSPATCH.BIN')
ASM  = os.path.join(ROOT, 'asm', 'bubios.asm')
BIN  = os.path.join(ROOT, 'asm', 'bubios.bin')
LST  = os.path.join(ROOT, 'asm', 'bubios.lst')
MAP  = os.path.join(ROOT, 'asm', 'bubios.map')
OUT  = os.path.join(ROOT, 'binary', 'BUBIOS2_SY019L1.BIN')

ECHS_MD5  = '8e520e91100f932d3f054883b08d37da'
CODE_BLK  = (0xDA84, 0xE000)       # executed from F000 only
DATA_BLK  = (0x7902, 0x7EFE)       # copied to 0100:xxxx by the setup
CODE2_BLK = (0x7F14, 0x8000)       # date/time entry (code only)
CODE3_BLK = (0x7856, 0x7900)       # date/time commit (code only); 7900 = ECHS checksum word
CHECKSUM  = 0x7EFE

def die(msg): sys.exit('BUILD FAILED: ' + msg)

rom = bytearray(open(SRC, 'rb').read())
if hashlib.md5(rom).hexdigest() != ECHS_MD5:
    die('input is not the hardware-confirmed ECHS ROM (%s)' % SRC)

# ------------------------------------------------------------------ assemble
subprocess.run(['nasm', '-f', 'bin', os.path.basename(ASM), '-o', os.path.basename(BIN),
                '-l', os.path.basename(LST)],        # relative names: the map file
               check=True, cwd=os.path.dirname(ASM))  # carries no machine-specific path
blob = open(BIN, 'rb').read()
mp = open(MAP).read()
sec = {m.group(5): (int(m.group(1), 16), int(m.group(2), 16), int(m.group(4), 16))
       for m in re.finditer(r'^\s*([0-9A-F]+)\s+([0-9A-F]+)\s+([0-9A-F]+)\s+([0-9A-F]+)\s+progbits\s+(\w+)', mp, re.M)}
sym = {m.group(2): int(m.group(1), 16)
       for m in re.finditer(r'^\s+[0-9A-F]+\s+([0-9A-F]+)\s+(\w+)\s*$', mp, re.M)}
# ---- space reclaimed from dead AMI code since 2.0 (see README) ------------
# AMI's configuration box (replaced by the POST screen), minus the shared
# CR/LF routine at 3B84-3B8E, and the Hard Disk Utility (format /
# interleave / media analysis / bad-track editor, unreachable since 1.0),
# minus the drive-info routine 4168-429C that auto-detect still uses.
POST1_BLK = (0x39C0, 0x3B84)
POST2_BLK = (0x3B8F, 0x4168)
POST3_BLK = (0x429D, 0x49DB)
DEAD = ((0x39C0, 0x3B84, 'config box routine'),
        (0x3B8F, 0x4034, 'config box routines + texts'),
        (0x4034, 0x4168, 'Hard Disk Utility code'),
        (0x429D, 0x49DB, 'Hard Disk Utility code'),
        (0x3292, 0x3482, 'Hard Disk Utility texts'),
        (0x19B0, 0x1A7A, 'config box cache/clock message'))
KEEP = ((0x3B84, 0x3B8F, 'CR/LF routine'), (0x4168, 0x429D, 'auto-detect drive info'),
        (0x49DB, 0x4A44, 'error dialog'), (0x3482, 0x36D4, 'error/auto-detect texts'))
def md5(b): return hashlib.md5(bytes(b)).hexdigest()
DEAD_MD5 = {0x39C0: 'af60f3187a1558c9c8eab7bfc13fd401', 0x3B8F: '912b9280272e738cf8ff98de3f74c221',
            0x4034: 'd26aa59d54e50fe3bf24f899b5b3386b', 0x429D: 'c830bae155cbee454957b147f014835d',
            0x3292: '5fc66dc4fd0322c544300e246ac2b922', 0x19B0: 'f90d0376e0b9c23a3eec63e1594f65ec'}
KEEP_MD5 = {k[0]: md5(rom[k[0]:k[1]]) for k in KEEP}
for lo, hi, what in DEAD:
    if md5(rom[lo:hi]) != DEAD_MD5[lo]:
        die('%s %04X-%04X is not the expected AMI code' % (what, lo, hi))
for lo, hi, what in DEAD:
    rom[lo:hi] = bytes(hi - lo)

LBA_BLK   = (0x3292, 0x3482)       # INT 13h extensions (2.1)
TOOLS_BLK = (0x19B0, 0x1A7A)       # setup: auto-detect switch (2.1)
BLOCKS = (('code', CODE_BLK), ('data', DATA_BLK), ('code2', CODE2_BLK), ('code3', CODE3_BLK),
          ('post1', POST1_BLK), ('post2', POST2_BLK), ('post3', POST3_BLK),
          ('lba', LBA_BLK), ('tools', TOOLS_BLK))
for name, (lo, hi) in BLOCKS:
    v, st, ln = sec[name]
    assert v == lo, name
    if v + ln > hi: die('%s block overflow (%d bytes too long)' % (name, v + ln - hi))
    end = hi + 2 if name == 'data' else hi
    if any(rom[lo:end]): die('free block %04X-%04X is not empty in the input' % (lo, end))
for name, (lo, hi) in BLOCKS:
    v, st, ln = sec[name]
    rom[v:v + ln] = blob[st:st + ln]

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
for at, want, what in ((0x08CD, '5351bb5149', 'std key reader'), (0x6180, 'b404cd1a', 'get date'),
                       (0x61BA, 'b402cd1a', 'get time'), (0x5FCB, '51b500', 'set date'),
                       (0x6022, '5583ec0a', 'show date/time')):
    if rom[at:at + len(want) // 2].hex() != want: die('%s routine at %04X not as expected' % (what, at))
assert rom[0x76F5:0x76FA] == b'\x53\x89\xc3\x89\xc8'                      # ECHS calc_factor: push bx / mov bx,ax / mov ax,cx

# ------------------------------------------------------------------ code patch sites
patch(0x36D4, b'\x55\x8b\xec', b'\xe9' + rel16(0x36D4, sym['bu_main']), 'main menu -> bu_main')
patch(0x38B5, b'\x55\x8b\xec', b'\xe9' + rel16(0x38B5, sym['bu_hdr']), 'header -> bu_hdr')
patch(0x0D3D, b'\xe8\xce\x45', b'\xe8' + rel16(0x0D3D, sym['bu_edkey']), 'editor getkey -> bu_edkey')
patch(0x08D5, b'\xb4\x00\xcd\x16', b'\xe8' + rel16(0x08D5, sym['bu_stdkey']) + b'\x90', 'standard getkey -> bu_stdkey')
patch(0x5F94, b'\xe8\x36\xa9', b'\xe8' + rel16(0x5F94, sym['bu_dtkey']), 'date/time getkey -> bu_dtkey')

# ------------------------------------------------------------------ POST screen hooks (2.0)
NOP = b'\x90'
def call(site, name): return b'\xe8' + rel16(site, sym[name])
patch(0x593B, bytes.fromhex('be0081e8ed9b'), NOP * 6, 'logo line -> nothing')
patch(0x5945, bytes.fromhex('e8e69a'), call(0x5945, 'bu_post_init'), 'BIOS ID -> bu_post_init')
patch(0x12BF, bytes.fromhex('be0081e869e2'), NOP * 6, 'logo line (after setup) -> nothing')
patch(0x12C6, bytes.fromhex('e865e1'), call(0x12C6, 'bu_post_init'), 'BIOS ID (after setup) -> bu_post_init')
patch(0x12CD, bytes.fromhex('beac76e85be2'), call(0x12CD, 'bu_wait') + NOP * 3, 'WAIT (after setup) -> bu_wait')
patch(0x4F04, bytes.fromhex('be8876e824a6'), call(0x4F04, 'bu_delmsg') + NOP * 3, 'Hit <DEL> -> bu_delmsg')
patch(0x4CB4, bytes.fromhex('e81c00beac76e871a8'), call(0x4CB4, 'bu_wait') + NOP * 6, 'WAIT -> bu_wait')
# memory count: 4EA1..4ED1 printed leading zeros, the count and " KB OK"
old = bytes.fromhex('8bc303c5503d1b06731db90100' '3d9d00730eb1023c107308b1033c027302b104'
                    'b030e876a6e2f958e820febee874e85ca6')
new = bytes.fromhex('8bc303c5') + call(0x4EA5, 'bu_memshow') + b'\xeb' + bytes([0x4ED2 - 0x4EAA])
patch(0x4EA1, old, new + NOP * (len(old) - len(new) - 0) , 'memory count -> bu_memshow')
# 2.1: the 40 bytes jumped over (4EAA-4ED1) hold code (section mem)
MEM_BLK = (0x4EAA, 0x4ED2)
# 2.1: AMI's "WAIT......" text (76AC-76B8), unused since the 2.0 hooks at
# 12CD/4CB4: 13 bytes for section wait
WAIT_BLK = (0x76AC, 0x76B9)
if bytes(rom[WAIT_BLK[0]:WAIT_BLK[1]]) != b'WAIT......\r\n\x00': die('WAIT text not at 76AC')
rom[WAIT_BLK[0]:WAIT_BLK[1]] = bytes(WAIT_BLK[1] - WAIT_BLK[0])
v, st, ln = sec['wait']
assert v == WAIT_BLK[0]
if v + ln > WAIT_BLK[1]: die('wait block overflow (%d bytes too long)' % (v + ln - WAIT_BLK[1]))
rom[v:v + ln] = blob[st:st + ln]
v, st, ln = sec['mem']
assert v == MEM_BLK[0]
if v + ln > MEM_BLK[1]: die('mem block overflow (%d bytes too long)' % (v + ln - MEM_BLK[1]))
rom[v:v + ln] = blob[st:st + ln]
patch(0x1610, bytes.fromhex('e85edf'), call(0x1610, 'bu_chime'), 'end-of-POST beep -> bu_chime')
patch(0x1613, bytes.fromhex('e8a100'), call(0x1613, 'bu_refresh'), 'clear screen -> bu_refresh')
patch(0x164C, bytes.fromhex('e87123'), call(0x164C, 'bu_final'), 'config box -> bu_final')
patch(0x120D, bytes.fromhex('e84604'), call(0x120D, 'bu_errors'), 'error list (early) -> bu_errors')
patch(0x158C, bytes.fromhex('e8c700'), call(0x158C, 'bu_errors'), 'error list -> bu_errors')
# 2.1: AMI's on-screen copyright check (F3DB, called at checkpoints 40h,
# 60h, 95h) crashes POST on purpose when the line is not on screen: RET
patch(0xF3DB, bytes.fromhex('60b40332ff'), b'\xc3', 'copyright screen check -> RET')
# 2.1: INT 13h entry -> bu_int13 (extensions AH=41h-48h, then AMI's code)
patch(0xA3E7, bytes.fromhex('80fa80fbfc'), b'\xe9' + rel16(0xA3E7, sym['bu_int13']) + NOP * 2, 'INT 13h -> bu_int13')
# 2.1: INT 13h AH=15h (floppy) asks the stub at D385 (STC/RET) whether the
# change line can be used: cf_chgline says no when a CF card is on the IDE bus
patch(0xC6E4, b'\xe8' + rel16(0xC6E4, 0xD385), b'\xe8' + rel16(0xC6E4, sym['cf_chgline']), 'floppy change line -> cf_chgline')
# the old main-menu handler table still names the Hard Disk Utility: point
# that entry at a RET (end of the error dialog routine) instead of freed space
assert rom[0x4A43] == 0xC3
patch(0x2ECE, b'\x34\x40', b'\x43\x4a', 'HDU menu handler -> RET')
for lo, hi, what in KEEP:
    if md5(rom[lo:hi]) != KEEP_MD5[lo]: die('%s %04X-%04X was changed' % (what, lo, hi))

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
for name, (lo, hi) in BLOCKS:
    v, st, ln = sec[name]
    print('%-5s %04X-%04X  %4d bytes (%d free)' % (name, v, v + ln - 1, ln, hi - v - ln))
v, st, ln = sec['wait']
print('%-5s %04X-%04X  %4d bytes (%d free)' % ('wait', v, v + ln - 1, ln, WAIT_BLK[1] - v - ln))
v, st, ln = sec['mem']
print('%-5s %04X-%04X  %4d bytes (%d free)' % ('mem', v, v + ln - 1, ln, MEM_BLK[1] - v - ln))
print('title %d chars' % len(title))
print('wrote', os.path.relpath(OUT, ROOT), 'MD5', hashlib.md5(rom).hexdigest())
