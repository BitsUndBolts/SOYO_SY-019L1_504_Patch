#!/usr/bin/env python3
"""
Behavioural checks of the BUBios 2.0 POST screen, run in the POST emulator
(post_emu.py: the whole power-on self test from the reset vector).

    python3 py/test_post.py        (from the BUBios 2.0 folder; needs unicorn)

The emulated CPU is a modern x86 core, so the Processor/Clock values are not
checked here (86Box, and the real board, show 80386 / 40.0 MHz).
"""
import os, re, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from post_emu import Post, make_cmos, fix_cmos

ROOT = os.path.dirname(HERE)
ROM = open(os.path.join(ROOT, 'binary', 'BUBIOS2_SY019L1.BIN'), 'rb').read()
ROM10 = os.path.join(ROOT, '..', 'BUBios', 'binary', 'BUBIOS_SY019L1.BIN')
MAP = open(os.path.join(ROOT, 'asm', 'bubios.map')).read()
SYM = {m.group(2): int(m.group(1), 16)
       for m in re.finditer(r'^\s+[0-9A-F]+\s+([0-9A-F]+)\s+(\w+)\s*$', MAP, re.M)}

fails = 0
def check(name, cond, info=''):
    global fails
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond:
        fails += 1
        if info: print('     ', str(info).replace('\n', '\n      '))

def boot(**kw):
    p = Post(ROM, **kw)
    r = p.run(max_ms=kw.pop('ms', 30000) if False else 30000)
    return p, r

def row(p, n): return p.text().split('\n')[n]

# ---------------------------------------------------------------- the normal case
p, r = boot()
t = p.text()
check('POST runs to INT 19h', r == 'int19', r)
check('title bar', 'BUBios 2.0' in row(p, 0) and 'Power-On Self Test' in row(p, 0), row(p, 0))
check('memory 16384 KB, full bar', '16384 KB' in row(p, 7) and '░' not in row(p, 7), row(p, 7))
check('memory map: 16 cells of 1 MB', row(p, 8).count('■') == 16 and '1 MB blocks' in row(p, 8), row(p, 8))
check('drive model from IDENTIFY', 'SanDisk SDCFB-8192' in row(p, 11), row(p, 11))
check('geometry, ECHS and size', all(x in row(p, 12) for x in ('Type 47', '16000/16/63', 'DOS 1002/255/63', '7875 MB')), row(p, 12))
check('floppy A: 1.44 MB', '1.44 MB' in row(p, 10), row(p, 10))
check('cache / shadow / battery', '64 KB' in row(p, 3) and 'Video + System' in row(p, 4) and 'OK' in row(p, 5), t)
check('option ROM list (C000)', 'C000 2K' in row(p, 16), row(p, 16))
check('colour text mode (B800)', p.vbase == 0xB8000)
check('AMI copyright line kept on screen (row 21)', row(p, 21).startswith('(C) American Megatrends Inc.,'), row(p, 21))
check('status: boot order + POST time', 'Booting: A: then C:' in row(p, 24) and 'POST' in row(p, 24), row(p, 24))
check('BIOS ID string in the status bar', '40-040A-001102-00101111-111192-OP495SLC' in row(p, 24))
check('BDA 40:F0-40:FF cleared before the boot', bytes(p.mu.mem_read(0x4F0, 16)) == bytes(16))
check('cursor left below the summary (row 18)', bytes(p.mu.mem_read(0x450, 2)) == bytes([0, 18]))
ct = p.curshape
check('cursor visible again for DOS', ct == 0x0607, hex(ct))
check('no AMI config box any more', 'System Configuration' not in t)

# ---------------------------------------------------------------- same POST flow as 1.0
def codes(romfile):
    q = Post(open(romfile, 'rb').read()); q.run(max_ms=30000)
    out = []
    for c, _ in q.p80:
        if not out or out[-1] != c: out.append(c)
    return out
if os.path.exists(ROM10):
    a, b = codes(ROM10), codes(os.path.join(ROOT, 'binary', 'BUBIOS2_SY019L1.BIN'))
    check('POST checkpoint sequence identical to BUBios 1.0', a == b, (a, b))

# ---------------------------------------------------------------- live memory display
snap = {}
def grab(q):
    u = struct.unpack('<H', bytes(q.mu.mem_read(0x4F6, 2)))[0]
    if 0x80 <= u < 0x90 and 'mid' not in snap: snap['mid'] = q.text()
    return False
q = Post(ROM); q.run(max_ms=30000, until=grab)
m = snap.get('mid', '').split('\n')
check('mid-test: bar partly filled', len(m) > 8 and '█' in m[7] and '░' in m[7], m[7] if len(m) > 7 else m)
check('mid-test: status says DEL enters setup', len(m) > 24 and 'Press DEL to run Setup' in m[24], m[24] if len(m) > 24 else '')
calls = []
q = Post(ROM)
q.mu.hook_add(__import__('unicorn').UC_HOOK_CODE, lambda uc, a, s, u: calls.append(1),
              begin=0xF0000 + SYM['bu_memshow'], end=0xF0000 + SYM['bu_memshow'])
q.run(max_ms=30000)
check('memory display called once per 64 KB (256 times)', len(calls) == 256, len(calls))

# ---------------------------------------------------------------- memory sizes
for mb, cells, scale in ((4, 4, 1), (32, 32, 1), (64, 32, 2), (48, 24, 2)):
    q = Post(ROM, mem_mb=mb, cmos=make_cmos(ext_kb=(mb - 1) * 1024)); q.run(max_ms=60000)
    r8 = q.text().split('\n')[8]
    check('%d MB: count and %d cells of %d MB' % (mb, cells, scale),
          ('%d KB' % (mb * 1024)) in q.text().split('\n')[7] and r8.count('■') == cells and ('%d MB blocks' % scale) in r8,
          q.text().split('\n')[7] + '\n' + r8)

# ---------------------------------------------------------------- drives
q = Post(ROM, drive=None); r = q.run(max_ms=30000)
check('no hard disk: "None", no geometry row', 'None' in q.text().split('\n')[11] and q.text().split('\n')[12].strip() == '', q.text())
q = Post(ROM, drive=(16000, 16, 63), slave=(8912, 15, 63)); r = q.run(max_ms=30000)
tt = q.text().split('\n')
check('D: slave identified and translated', 'WDC AC24300L' in tt[13] and '8912/15/63' in tt[14] and 'DOS 556/240/63' in tt[14] and '4112 MB' in tt[14], '\n'.join(tt[11:15]))
q = Post(ROM, drive=(615, 4, 17), cmos=make_cmos(drive=2)); r = q.run(max_ms=30000)
tt = q.text().split('\n')
check('small ROM-table drive (type 2): no ECHS arrow', 'Type 2' in tt[12] and '615/4/17' in tt[12] and 'DOS' not in tt[12] and '20 MB' in tt[12], tt[12])

# ---------------------------------------------------------------- errors, schemes, mono
bad = bytearray(b'\xff' * 128); bad[0x0F] = 0
q = Post(ROM, cmos=bad, drive=None); r = q.run(max_ms=15000)
tt = q.text().split('\n')
check('bad CMOS: no crash in AMI\'s copyright check', 'Press <F1> to RESUME' in q.text(), q.text())
check('bad CMOS: errors in the message rows', 'CMOS' in tt[18] and 'POST found a problem' in tt[24], '\n'.join(tt[17:25]))
t0 = q.t / 1e6                     # F1 -> SETUP -> quit without saving -> POST goes on
for i, sc in enumerate((0x3B, 0x01, 0x50, 0x1C, 0x15, 0x1C)):
    q.type_key(sc, at_ms=t0 + 2000 + i * 1500)
r = q.run(max_ms=25000)
tt = q.text().split('\n')
check('bad CMOS, F1, SETUP, quit: POST passes AMI\'s later checks and boots', r == 'int19' and 'Booting' in tt[24], r)
check('after SETUP: screen redrawn, memory kept, no POST time', '16384 KB' in tt[7] and 'POST' not in tt[24]
      and tt[21].startswith('(C) American Megatrends Inc.,'), '\n'.join(tt))
for sch, want in ((0, 0x30), (1, 0x60), (2, 0x20), (3, 0x70), (4, 0x70)):
    q = Post(ROM, cmos=make_cmos(scheme=sch)); q.run(max_ms=30000)
    a = q.mu.mem_read(0xB8000 + 3, 1)[0]
    check('colour scheme %d: title attribute %02X' % (sch, want), a == want, hex(a))
q = Post(ROM, cmos=make_cmos(video='mono'), video='mono'); r = q.run(max_ms=30000)
check('monochrome: screen in B000, runs to INT 19h', r == 'int19' and 'Power-On Self Test' in q.text() and q.vbase == 0xB0000, (r, q.vbase))
check('monochrome: inverse title bar', q.mu.mem_read(0xB0000 + 3, 1)[0] == 0x70)
q = Post(ROM, cmos=make_cmos()); q.trace_ports = True; q.run(max_ms=30000)
tones = [e[2] for e in q.portlog if len(e) == 3 and e[0] == 'out' and e[1] == '0x42']
want = [hex(1140 & 255), hex(1140 >> 8), hex(761 & 255), hex(761 >> 8)]
check('chime: two notes (C6, G6) instead of the beep',
      any(tones[i:i + 4] == want for i in range(len(tones))), tones[-8:])

print('\n%d failure(s)' % fails)
sys.exit(1 if fails else 0)
