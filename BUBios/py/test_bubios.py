#!/usr/bin/env python3
"""Behavioural tests for the BUBios setup front end (Unicorn emulator).

    python3 py/test_bubios.py            -> PASS/FAIL per check, exit code 1 on failure
"""
import os, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from setup_emu import *
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROM = load_rom(os.path.join(ROOT, 'binary', 'BUBIOS_SY019L1.BIN'))
mp = open(os.path.join(ROOT, 'asm', 'bubios.map')).read()
sym = {m.group(2): int(m.group(1), 16) for m in re.finditer(r'^\s+[0-9A-F]+\s+([0-9A-F]+)\s+(\w+)\s*$', mp, re.M)}
LOOP = (sym['bu_tloop'], sym['bu_tloop_end'])
SHIFT_TAB = 0x0F00
fails = []

def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '   ' + str(detail)))
    if not cond: fails.append(name)

def new(**kw):
    s = Setup(ROM, timing_loop=LOOP, **kw)
    assert s.run() == 'key', s.where()
    return s

def tab(s):
    """name of the highlighted tab (attribute differs from the frame colour)"""
    row = s.cells()[2 * 80:3 * 80]
    frame = row[0][1]
    return bytes(c for c, a in row if a != frame).decode('cp437').strip()

def cmos_ok(c):
    return (sum(c[0x10:0x2E]) & 0xFFFF) == (c[0x2E] << 8 | c[0x2F])

# ---- summary contents
s = new()
t = s.text()
check('summary shown on entry', tab(s) == 'Summary', tab(s))
for want in ('OPTi 82C495SLC', '15360K', '16384K', '640K', '7875 MB  [47]', '16000/16/63',
             '1002/255/63', '1.44 MB', '1.2  MB', 'VGA/PGA/EGA', 'A:, C:', 'AMIBIOS 11/11/92'):
    check('summary shows ' + want, want in t)
check('clock measured', re.search(r'CPU Clock .* \d+\.\d MHz', t) is not None)

# ---- tab navigation
s.step(RIGHT);      check('Right -> Standard', tab(s) == 'Standard', tab(s))
check('Standard page drawn', 'Hard Disk C: Type' in s.text())
s.step(TAB);        check('Tab in Standard -> Advanced', tab(s) == 'Advanced', tab(s))
check('Advanced page drawn', 'Typematic Rate Programming' in s.text())
s.step(SHIFT_TAB);  check('Shift-Tab in Advanced -> Standard', tab(s) == 'Standard', tab(s))
s.step(TAB, TAB);   check('Tab x2 -> Chipset', tab(s) == 'Chipset', tab(s))
check('Chipset page drawn', 'AT BUS Clock Selection' in s.text())
s.step(TAB);        check('Tab in Chipset -> Tools', tab(s) == 'Tools', tab(s))
s.step(TAB);        check('Tab -> Exit', tab(s) == 'Exit', tab(s))
s.step(TAB);        check('Tab wraps -> Summary', tab(s) == 'Summary', tab(s))
s.step(LEFT);       check('Left wraps -> Exit', tab(s) == 'Exit', tab(s))
s.step(ESC);        check('ESC on Exit -> Summary', tab(s) == 'Summary', tab(s))
s.step(ESC);        check('ESC on Summary -> Exit', tab(s) == 'Exit', tab(s))
s.step(LEFT, LEFT, LEFT); check('Left x3 -> Chipset', tab(s) == 'Chipset', tab(s))
s.step(ESC);        check('ESC in Chipset -> Summary', tab(s) == 'Summary', tab(s))

# ---- edit an option, see it on the Summary, save it
s = new()
s.step(RIGHT, TAB)                                      # Standard -> Advanced
s.step(*([DOWN] * 12))                                  # External Cache Memory
s.step(PGDN)
check('edited value shown in page', 'External Cache Memory      : Disabled' in s.text())
s.step(ESC)
check('Summary reflects edit', re.search(r'External Cache .* Disabled', s.text()) is not None)
before = bytes(s.cmos)
s.step(F10, Y(), ENTER)
check('F10 + Y leaves setup', s.stopped_reason == 'exit', s.stopped_reason)
check('cache bit cleared in CMOS 2Dh', not (s.cmos[0x2D] & 0x08), hex(s.cmos[0x2D]))
check('CMOS checksum valid after save', cmos_ok(s.cmos))
check('drive C: type-47 parameters kept', s.cmos[0x1B:0x24] == before[0x1B:0x24])

# ---- quit without saving
s = new()
orig = bytes(s.cmos)
s.step(RIGHT, TAB, PGDN, ESC)                           # change typematic, back
s.step(LEFT, DOWN, ENTER, Y(), ENTER)                   # Exit page, "Exit Without Saving"
check('quit leaves setup', s.stopped_reason == 'exit', s.stopped_reason)
check('quit does not touch CMOS 10h-3Fh', bytes(s.cmos[0x10:0x40]) == orig[0x10:0x40])

# ---- F10 answered N stays in setup
s = new()
s.step(F10, N(), ENTER)
check('F10 + N stays in setup', s.stopped_reason == 'key' and tab(s) == 'Summary')

# ---- colour cycling
s = new()
a0 = s.cells()[5 * 80 + 3][1]
s.step(F2)
a1 = s.cells()[5 * 80 + 3][1]
check('F2 changes colour scheme', a0 != a1, (hex(a0), hex(a1)))

# ---- Tools: load BIOS defaults
s = new()
s.step(LEFT, LEFT)                                      # Tools
check('Tools page lists 4 entries', all(x in s.text() for x in
      ('Load BIOS Setup Defaults', 'Load Power-On Defaults', 'Change Password', 'Auto-Detect Hard Disk')))
s.step(ENTER)
check('defaults prompt appears', '(Y/N)' in s.text(), s.text())
s.step(Y(), ENTER)
check('defaults loaded message', 'Default values loaded' in s.text())
s.step(0x3920)
check('back on Tools after defaults', tab(s) == 'Tools', tab(s))

# ---- other drive geometries on the Summary page
def summary_with(cyl, heads, sect, typ=47):
    c = default_cmos()
    c[0x1B:0x24] = struct.pack('<HBHBHB', cyl, heads, 0xFFFF, 8, cyl, sect)
    if typ != 47:
        c[0x12] = (typ << 4) & 0xF0 if typ < 15 else 0xF0; c[0x19] = typ
    fix_cmos_checksum(c)
    return new(cmos=c).text()
t = summary_with(8912, 15, 63)
check('WD 24300: 4112 MB, 556/240/63', '4112 MB' in t and '556/240/63' in t, t[1000:1600])
t = summary_with(3875, 16, 63)
check('2 GB CF: 967/64/63', '967/64/63' in t)
t = summary_with(980, 10, 17)
check('small disk: no translation', 'not needed' in t)
t = summary_with(0, 0, 0, typ=0)
c = default_cmos(); c[0x12] = 0x00; fix_cmos_checksum(c)
t = new(cmos=c).text()
check('no drive: C: None, no CHS rows', re.search(r'Hard Disk C: .* None', t) is not None and 'Physical' not in t)

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
