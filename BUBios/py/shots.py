#!/usr/bin/env python3
"""Drive the BUBios setup in the emulator and save a screenshot per page.

    python3 py/shots.py [rom]         -> shots/*.png
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from setup_emu import *
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rom_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'binary', 'BUBIOS_SY019L1.BIN')
mp = open(os.path.join(ROOT, 'asm', 'bubios.map')).read()
sym = {m.group(2): int(m.group(1), 16) for m in re.finditer(r'^\s+[0-9A-F]+\s+([0-9A-F]+)\s+(\w+)\s*$', mp, re.M)}
loop = (sym['bu_tloop'], sym['bu_tloop_end'])

def new(**kw):
    s = Setup(load_rom(rom_path), timing_loop=loop, **kw)
    r = s.run()
    assert r == 'key', (r, s.where())
    return s

def shot(s, name):
    p = os.path.join(ROOT, 'shots', name + '.png')
    s.png(p)
    print('==', name, s.stopped_reason, s.where()); print(s.text())

s = new()
shot(s, '1_summary')
s.step(RIGHT); shot(s, '2_standard')
s.step(TAB); shot(s, '3_advanced')
s.step(TAB); shot(s, '4_chipset')
s.step(TAB); shot(s, '5_tools')
s.step(DOWN, DOWN); shot(s, '5b_tools_password_selected')
s.step(TAB); shot(s, '6_exit')
s.step(ENTER); shot(s, '6b_exit_save_prompt')

# Tools entries that open their own AMI screens
s = new()
s.step(LEFT, LEFT, DOWN, DOWN, ENTER); shot(s, '7_password_dialog')
s.step(ESC); shot(s, '7b_after_password_esc')
s = new()
s.step(LEFT, LEFT, UP, ENTER); shot(s, '8_autodetect')
s.step(ESC); shot(s, '8b_after_autodetect')
s = new()
s.step(F2); shot(s, '9_colour_scheme_amber')
s.step(F2); shot(s, '9b_colour_scheme_green')
s.step(F2); shot(s, '9c_colour_scheme_blue')
