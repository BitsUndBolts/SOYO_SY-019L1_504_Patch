#!/usr/bin/env python3
"""Drive the BUBios setup in the emulator and save a screenshot per page.

    python3 py/shots.py [rom]         -> shots/*.png

The emulator's CPU (Unicorn) identifies itself as a modern CPU with CPUID.
For the screenshots it is presented as the board's 386DX-40 with a
coprocessor: the CPU-type probe is overridden to "386", so the clock is
computed with the 386 cycle count against the emulator's 40 MHz PIT model.
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from setup_emu import *
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rom_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'binary', 'BUBIOS2_SY019L1.BIN')
mp = open(os.path.join(ROOT, 'asm', 'bubios.map')).read()
sym = {m.group(2): int(m.group(1), 16) for m in re.finditer(r'^\s+[0-9A-F]+\s+([0-9A-F]+)\s+(\w+)\s*$', mp, re.M)}
loop = (sym['bu_tloop'], sym['bu_tloop_end'])

clock_label = int(re.search(r'^\s+[0-9A-F]+\s+([0-9A-F]+)\s+bu_detect\.clock\s*$', mp, re.M).group(1), 16)

def new(**kw):
    s = Setup(load_rom(rom_path), timing_loop=loop, **kw)
    def as_386(uc, addr, size, ud):                 # CPU family byte BU_CPU (DS:EF86) = 3
        uc.mem_write(0x1000 + 0xEF86, b'\x03')
    s.mu.hook_add(UC_HOOK_CODE, as_386, begin=0xF0000 + clock_label, end=0xF0000 + clock_label)
    r = s.run()
    assert r == 'key', (r, s.where())
    return s

def shot(s, name):
    p = os.path.join(ROOT, 'shots', name + '.png')
    s.png(p)
    print('==', name, s.stopped_reason, s.where()); print(s.text())

s = new()
shot(s, '1_summary')
s.step(TAB); shot(s, '2_standard')
s.step(TAB); shot(s, '3_advanced')
s.step(TAB); shot(s, '4_chipset')
s.step(TAB); shot(s, '5_tools')
s.step(DOWN, DOWN); shot(s, '5b_tools_password_selected')
s.step(TAB); shot(s, '6_exit')
s.step(ENTER); shot(s, '6b_exit_save_prompt')

# Tools entries that open their own AMI screens
s = new()
s.step(0x0F00, 0x0F00, DOWN, DOWN, ENTER); shot(s, '7_password_dialog')
s.step(ESC); shot(s, '7b_after_password_esc')
s = new()
s.step(0x0F00, 0x0F00, UP, ENTER); shot(s, '8_autodetect')
s.step(ESC); shot(s, '8b_after_autodetect')
s = new()
s.step(TAB, *[ord(c) for c in '12'], *[ord(c) for c in '05'], *[ord(c) for c in '2026'], ord('1')); shot(s, '2b_standard_typing_hour')
s = new()
s.step(F2); shot(s, '9_colour_scheme_amber')
s.step(F2); shot(s, '9b_colour_scheme_green')
s.step(F2); shot(s, '9c_colour_scheme_blue')
