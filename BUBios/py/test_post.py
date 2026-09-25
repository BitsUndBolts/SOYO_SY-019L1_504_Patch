#!/usr/bin/env python3
"""
Behavioural checks of the BUBios 2.1 POST screen, the boot countdown, disk
auto-detection and the INT 13h extensions, run in the POST emulator
(post_emu.py: the whole power-on self test from the reset vector).

    python3 py/test_post.py        (from the BUBios folder; needs unicorn)

The emulated CPU is a modern x86 core, so the Processor/Clock values are not
checked here (86Box, and the real board, show 80386 / 40.0 MHz).
"""
import os, re, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from post_emu import Post, make_cmos, fix_cmos
from int13x import call13, dap

ROOT = os.path.dirname(HERE)
ROM = open(os.path.join(ROOT, 'binary', 'BUBIOS2_SY019L1.BIN'), 'rb').read()
# BUBios 1.0 (setup only), for the POST checkpoint comparison. It is no longer
# in the repository; the check is skipped unless it is restored with
#   git show 0ccbeb2:BUBios/binary/BUBIOS_SY019L1.BIN > binary/BUBIOS_SY019L1.BIN
ROM10 = os.path.join(ROOT, 'binary', 'BUBIOS_SY019L1.BIN')
MAP = open(os.path.join(ROOT, 'asm', 'bubios.map')).read()
SYM = {m.group(2): int(m.group(1), 16)
       for m in re.finditer(r'^\s+[0-9A-F]+\s+([0-9A-F]+)\s+([\w.]+)\s*$', MAP, re.M)}

# screen rows
R_CLOCK, R_SHADOW, R_RTC, R_MEM, R_MAP, R_FDD, R_C, R_CGEO, R_D, R_DGEO = 3, 4, 5, 7, 8, 9, 10, 11, 12, 13
R_PORTS, R_ROMS, R_MSG, R_COUNT, R_STAT = 15, 16, 18, 19, 24

fails = 0
def check(name, cond, info=''):
    global fails
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond:
        fails += 1
        if info: print('     ', str(info).replace('\n', '\n      '))

COUNTDOWN = 'any other key to boot now'
def boot(ms=40000, lba=None, **kw):
    """Run POST; returns (machine, stop reason, screen at the countdown, ms when the countdown appeared)."""
    p = Post(ROM, **kw)
    if lba: p.ides[0].lba_total = lba
    snap = {}
    def u(q):
        if 'final' not in snap and COUNTDOWN in q.text():
            snap['final'] = q.text(); snap['t'] = q.t / 1e6
        return False
    r = p.run(max_ms=ms, until=u)
    return p, r, snap.get('final', ''), snap.get('t')

def rows(t): return t.split('\n') + [''] * 25
def cold_starts(p):
    seq = []
    for c, _ in p.p80:
        if not seq or seq[-1] != c: seq.append(c)
    return sum(1 for i in range(len(seq) - 1) if seq[i] == 3 and seq[i + 1] == 4)

# ---------------------------------------------------------------- the normal case
p, r, t, t_cd = boot()
tt = rows(t)
check('POST runs to INT 19h', r == 'int19', r)
check('title bar: BUBios 2.1', 'BUBios 2.1' in tt[0] and 'Power-On Self Test' in tt[0], tt[0])
check('memory 16384 KB, full bar', '16384 KB' in tt[R_MEM] and '░' not in tt[R_MEM], tt[R_MEM])
check('no 1 MB block map any more', tt[R_MAP].strip() == '' and 'MB blocks' not in t, tt[R_MAP])
check('drive model from IDENTIFY', 'SanDisk SDCFB-8192' in tt[R_C], tt[R_C])
check('geometry, ECHS and size', all(x in tt[R_CGEO] for x in ('Type 47', '16000/16/63', 'DOS 1002/255/63', '7875 MB')), tt[R_CGEO])
check('D: "None"', 'None' in tt[R_D] and tt[R_DGEO].strip() == '', tt[R_D])
check('floppy A: 1.44 MB', '1.44 MB' in tt[R_FDD], tt[R_FDD])
check('cache / shadow / battery', '64 KB' in tt[R_CLOCK] and 'Video + System' in tt[R_SHADOW] and 'OK' in tt[R_RTC], t)
check('option ROM list (C000)', 'C000 2K' in tt[R_ROMS], tt[R_ROMS])
check('colour text mode (B800)', p.vbase == 0xB8000)
check('no AMI copyright line on screen', 'American Megatrends' not in t, t)
check('no BUBios text at the bottom right', 'Bits und Bolts' not in tt[R_STAT] and '(C) 2026' not in t, tt[R_STAT])
check('status: boot order + POST time', 'Booting: A: then C:' in tt[R_STAT] and 'POST' in tt[R_STAT], tt[R_STAT])
check('BIOS ID string in the status bar, ending in column 78 like the title',
      tt[R_STAT].rstrip().endswith('40-040A-001102-00101111-111192-OP495SLC') and len(tt[R_STAT].rstrip()) == 79
      and len(tt[0].rstrip()) == 79, repr(tt[R_STAT]))
check('countdown line: DEL for Setup, any key boots', 'Press DEL to run Setup' in tt[R_COUNT] and '... 3' in tt[R_COUNT], tt[R_COUNT])
wait = p.t / 1e6 - (t_cd or 0)
# (3 s on real hardware; the emulator's refresh-toggle clock runs slow by
#  about a third in this polling loop, 86Box shows 3 s)
check('countdown lasts about 3 s', 2900 <= wait <= 4500, '%.0f ms' % wait)
v = bytes(p.mu.mem_read(0xB8000, 4000))
check('screen cleared for DOS, normal colours (07)', v == b'\x20\x07' * 2000, v[:40])
check('cursor at the top left, normal shape', bytes(p.mu.mem_read(0x450, 2)) == bytes(2) and p.curshape == 0x0607,
      (bytes(p.mu.mem_read(0x450, 2)), hex(p.curshape)))
check('BDA 40:F0-40:FF cleared before the boot', bytes(p.mu.mem_read(0x4F0, 16)) == bytes(16))
check('no AMI config box any more', 'System Configuration' not in t)
check('one cold start only', cold_starts(p) == 1, cold_starts(p))

# clock: the emulator's CPU gives no standard clock, so feed the timing
# a 386DX-40 result (10709 PIT ticks for 500 x 32 DIVs, 386 constant) and check the display
import unicorn
from unicorn.x86_const import UC_X86_REG_BX, UC_X86_REG_ESI
def as386(uc, a, s, u):
    uc.reg_write(UC_X86_REG_BX, 10709); uc.reg_write(UC_X86_REG_ESI, 0)
q = Post(ROM)
q.mu.hook_add(unicorn.UC_HOOK_CODE, as386, None,
              begin=0xF0000 + SYM['measure_clock.k'], end=0xF0000 + SYM['measure_clock.k'])
q.run(max_ms=40000, until=lambda z: COUNTDOWN in z.text())
check('clock shown as 40.0 MHz', '40.0 MHz' in rows(q.text())[R_CLOCK], rows(q.text())[R_CLOCK])
snap = {}
q = Post(ROM)
def cache_mid(z):
    if 'Checking devices' in z.text(): snap.setdefault('t', z.text())
    return COUNTDOWN in z.text()
q.run(max_ms=40000, until=cache_mid)
check('cache "..." until AMI enables it, then the size', '...' in rows(snap.get('t', ''))[R_CLOCK]
      and 'Disabled' not in rows(snap.get('t', ''))[R_CLOCK] and '64 KB' in rows(q.text())[R_CLOCK],
      rows(snap.get('t', ''))[R_CLOCK])

# interrupts: AMI runs this part of POST with IF=0 (PICs not set up yet on a
# cold start); the POST screen code must not turn them on
import unicorn as _uc
from unicorn.x86_const import UC_X86_REG_EFLAGS
iflags = []
q = Post(ROM)
for name in ('bu_post_init', 'early_ports'):
    q.mu.hook_add(_uc.UC_HOOK_CODE, lambda uc, a, s, u: iflags.append((u, uc.reg_read(UC_X86_REG_EFLAGS) & 0x200)),
                  name, begin=0xF0000 + SYM[name], end=0xF0000 + SYM[name])
q.run(max_ms=40000, until=lambda z: COUNTDOWN in z.text())
check('interrupt flag left as AMI had it (off) through the clock measurement', iflags[:2] == [('bu_post_init', 0), ('early_ports', 0)],
      iflags[:4])
check('coprocessor probed at the end of POST', any(x in rows(q.text())[2] for x in ('387 present', 'On-chip')),
      rows(q.text())[2])

# ---------------------------------------------------------------- countdown keys
q = Post(ROM); q.type_key(0x39, at_ms=t_cd + 500); r = q.run(max_ms=40000)
check('any other key boots at once', r == 'int19' and q.t / 1e6 < t_cd + 700, '%s %.0f ms' % (r, q.t / 1e6 - t_cd))
q = Post(ROM); q.type_key(0x53, at_ms=t_cd + 500)
for i, sc in enumerate((0x01, 0x50, 0x1C, 0x15, 0x1C)):            # Esc, Down, Enter: exit without saving, Y
    q.type_key(sc, at_ms=t_cd + 2500 + i * 1500)
q.run(max_ms=t_cd + 2000)
check('DEL in the countdown runs Setup', 'BUBios (tm)' in q.text() and 'Summary' in q.text(), q.text())
r = q.run(max_ms=30000)
check('Setup quit without saving: boots, no restart', r == 'int19' and cold_starts(q) == 1, (r, cold_starts(q)))
q = Post(ROM); q.type_key(0x53, at_ms=t_cd + 500)
for i, sc in enumerate((0x0F, 0x0F, 0x51, 0x01, 0x44, 0x15, 0x1C)):  # Tab Tab PgDn Esc: change typematic; F10 Y Enter
    q.type_key(sc, at_ms=t_cd + 2500 + i * 1500)
c0 = bytes(q.cmos)
r = q.run(max_ms=60000)
check('Setup saved: POST restarts to apply it, then boots', r == 'int19' and cold_starts(q) == 2 and q.cmos[0x13] != c0[0x13],
      (r, cold_starts(q), hex(c0[0x13]), hex(q.cmos[0x13])))

# ---------------------------------------------------------------- same POST flow as 1.0
def codes(romfile):
    q = Post(open(romfile, 'rb').read()); q.run(max_ms=40000)
    out = []
    for c, _ in q.p80:
        if not out or out[-1] != c: out.append(c)
    return out
if os.path.exists(ROM10):
    a, b = codes(ROM10), codes(os.path.join(ROOT, 'binary', 'BUBIOS2_SY019L1.BIN'))
    check('POST checkpoint sequence identical to BUBios 1.0', a == b, (a, b))

# ---------------------------------------------------------------- live memory display, early fields
snap = {}
def grab(q):
    u = struct.unpack('<H', bytes(q.mu.mem_read(0x4F6, 2)))[0]
    if 0x80 <= u < 0x90 and 'mid' not in snap: snap['mid'] = q.text()
    return False
q = Post(ROM); q.run(max_ms=40000, until=grab)
m = rows(snap.get('mid', ''))
check('mid-test: bar partly filled', '█' in m[R_MEM] and '░' in m[R_MEM], m[R_MEM])
check('mid-test: status says DEL enters setup', 'Press DEL to run Setup' in m[R_STAT], m[R_STAT])
check('mid-test: disks, floppies, ROMs, battery already shown',
      'SanDisk SDCFB-8192' in m[R_C] and '7875 MB' in m[R_CGEO] and 'None' in m[R_D] and '1.44 MB' in m[R_FDD]
      and 'C000 2K' in m[R_ROMS] and 'OK' in m[R_RTC] and 'Video + System' in m[R_SHADOW], '\n'.join(m))
check('mid-test: "..." for coprocessor, clock, cache and ports not known yet',
      m[R_CLOCK].count('...') == 2 and '...' in m[R_PORTS] and 'Coprocessor  ...' in m[2], '\n'.join(m))
check('no ports at all: "None" at the end', 'None' in rows(t)[R_PORTS], rows(t)[R_PORTS])
snap = {}
q = Post(ROM, com=(0x3F8, 0x2F8), lpt=(0x378,))
def early(z):
    if '█' in z.text() and '░' in z.text(): snap.setdefault('t', z.text())
    return COUNTDOWN in z.text()
q.run(max_ms=40000, until=early)
want = 'COM1 3F8  COM2 2F8  LPT1 378'
check('ports found before the memory test, same as AMI finds them', want in rows(snap.get('t', ''))[R_PORTS]
      and want in rows(q.text())[R_PORTS] and bytes(q.mu.mem_read(0x400, 10)) == bytes.fromhex('f803f80200000000' '7803'),
      (rows(snap.get('t', ''))[R_PORTS], rows(q.text())[R_PORTS]))
calls = []
q = Post(ROM)
q.mu.hook_add(__import__('unicorn').UC_HOOK_CODE, lambda uc, a, s, u: calls.append(1),
              begin=0xF0000 + SYM['bu_memshow'], end=0xF0000 + SYM['bu_memshow'])
q.run(max_ms=40000)
check('memory display called once per 64 KB (256 times)', len(calls) == 256, len(calls))

# ---------------------------------------------------------------- memory sizes
for mb in (4, 32, 48, 64):
    q, r, t2, _ = boot(ms=70000, mem_mb=mb, cmos=make_cmos(ext_kb=(mb - 1) * 1024))
    check('%d MB: count %d KB, full bar' % (mb, mb * 1024),
          ('%d KB' % (mb * 1024)) in rows(t2)[R_MEM] and '░' not in rows(t2)[R_MEM], rows(t2)[R_MEM])

c = make_cmos(ext_kb=64512); c[0x30:0x32] = struct.pack('<H', 15360)   # 64 MB fitted, CMOS still says 16 MB
q = Post(ROM, mem_mb=64, cmos=c)
fills = []
def watch(z):
    row = rows(z.text())[R_MEM]
    if 'KB' in row: fills.append(row.count('█'))
    return COUNTDOWN in z.text()
q.run(max_ms=80000, until=watch)
check('64 MB after a RAM upgrade (CMOS still 16 MB): the bar never goes back',
      fills and all(b >= a for a, b in zip(fills, fills[1:])) and fills[-1] == 40, fills[:40])

# ---------------------------------------------------------------- drives
q, r, t2, _ = boot(drive=None)
check('no hard disk: "None", no geometry row', 'None' in rows(t2)[R_C] and rows(t2)[R_CGEO].strip() == '', t2)
q, r, t2, _ = boot(drive=(16000, 16, 63), slave=(8912, 15, 63))
tt = rows(t2)
check('D: slave identified and translated', 'WDC AC24300L' in tt[R_D] and '8912/15/63' in tt[R_DGEO]
      and 'DOS 556/240/63' in tt[R_DGEO] and '4112 MB' in tt[R_DGEO], '\n'.join(tt[R_C:R_DGEO + 1]))
q, r, t2, _ = boot(drive=(615, 4, 17), cmos=make_cmos(drive=2))
tt = rows(t2)
check('small ROM-table drive (type 2): no ECHS arrow', 'Type 2' in tt[R_CGEO] and '615/4/17' in tt[R_CGEO]
      and 'DOS' not in tt[R_CGEO] and '20 MB' in tt[R_CGEO], tt[R_CGEO])

q, r, t2, t_cd = boot(cmos=make_cmos(drive=(1974, 16, 63)), drive=None)
c = q.cmos
check('C: set up in CMOS but not connected: no long wait, dropped from CMOS, "None"',
      r == 'int19' and t_cd and t_cd < 15000 and c[0x12] == 0 and 'None' in rows(t2)[R_C]
      and (sum(c[0x10:0x2E]) & 0xFFFF) == (c[0x2E] << 8 | c[0x2F]), (r, t_cd, hex(c[0x12]), rows(t2)[R_C]))
q, r, t2, t_cd = boot(cmos=make_cmos(drive=(16000, 16, 63), drive_d=(8912, 15, 63)), drive=(16000, 16, 63))
check('D: set up but not connected: D: dropped, C: kept', r == 'int19' and q.cmos[0x12] == 0xF0
      and 'None' in rows(t2)[R_D] and '16000/16/63' in rows(t2)[R_CGEO], (r, hex(q.cmos[0x12])))
q = Post(ROM); q.type_key(0x53, at_ms=4000); st = {}
def ent(z):
    if 'Entering Setup' in rows(z.text())[R_STAT] and '\u2591' in rows(z.text())[R_MEM]: st['seen'] = True
    return 'BUBios (tm)' in z.text()
q.run(max_ms=40000, until=ent)
check('DEL during the memory test: status says "Entering Setup" until SETUP opens', st.get('seen') and 'BUBios (tm)' in q.text())

# ---------------------------------------------------------------- auto-detection
def auto_cmos(**kw):
    c = make_cmos(**kw); c[0x7F] = 1; c[0x7E] = 0xA4; return c
q, r, t2, _ = boot(cmos=auto_cmos(drive=None), drive=(16000, 16, 63), slave=(8912, 15, 63))
tt = rows(t2)
check('auto: C: and D: found without CMOS entries', r == 'int19' and 'Auto' in tt[R_CGEO] and '16000/16/63' in tt[R_CGEO]
      and 'Auto' in tt[R_DGEO] and '8912/15/63' in tt[R_DGEO], '\n'.join(tt[R_C:R_DGEO + 1]))
c = q.cmos
check('auto: CMOS set to type 47 with the drive geometry, checksum valid',
      c[0x12] == 0xFF and c[0x19] == 47 and c[0x1A] == 47
      and struct.unpack('<HB', bytes(c[0x1B:0x1E])) == (16000, 16) and c[0x23] == 63
      and struct.unpack('<HB', bytes(c[0x24:0x27])) == (8912, 15) and c[0x2C] == 63
      and (sum(c[0x10:0x2E]) & 0xFFFF) == (c[0x2E] << 8 | c[0x2F]), bytes(c[0x10:0x30]).hex())
x = call13(q, 0x0800, dx=0x80)
check('auto: both drives usable in the same boot (INT 13h AH=08h: 2 drives)', x['cf'] == 0 and x['dx'] & 0xFF == 2, x)
x = call13(q, 0x0201, bx=0x1000, cx=0x0001, dx=0x0081)
check('auto: D: readable', x['cf'] == 0 and x['ax'] == 0x0001 or x['ax'] == 0, x)
q, r, t2, _ = boot(cmos=auto_cmos(drive=(1024, 16, 63)), drive=(16000, 16, 63))
check('auto: wrong CMOS geometry corrected', '16000/16/63' in rows(t2)[R_CGEO] and
      struct.unpack('<H', bytes(q.cmos[0x1B:0x1D]))[0] == 16000, rows(t2)[R_CGEO])
q, r, t2, _ = boot(cmos=auto_cmos(drive=None), drive=(16000, 16, 63))
check('auto, no slave: D: "None"', 'None' in rows(t2)[R_D] and 'Auto' in rows(t2)[R_CGEO], '\n'.join(rows(t2)[R_C:R_DGEO + 1]))
q, r, t2, _ = boot(cmos=make_cmos(drive=None), drive=(16000, 16, 63))
check('auto off: a drive not in CMOS stays "None"', 'None' in rows(t2)[R_C] and q.cmos[0x12] == 0, rows(t2)[R_C])

# ---------------------------------------------------------------- INT 13h extensions (LBA)
q = Post(ROM); q.ides[0].lba_total = 30_000_000                    # a 15 GB card
q.run(max_ms=40000)
H = lambda x: {k: (hex(v) if isinstance(v, int) else v) for k, v in x.items()}
x = call13(q, 0x4100, bx=0x55AA)
check('41h: extensions present, EDD 1.1, fixed-disk subset', x['cf'] == 0 and x['bx'] == 0xAA55 and x['ax'] >> 8 == 0x21
      and x['cx'] == 1, H(x))
x = call13(q, 0x4100, bx=0x55AA, dx=0x81)
check('41h on a missing drive: error', x['cf'] == 1, H(x))
x = call13(q, 0x4800, dap=b'\x1a\x00' + bytes(24))
cyl, hd, spt, tot, bps = struct.unpack('<IIIQH', bytes(q.mu.mem_read(0x504, 22)))
check('48h: geometry and 30,000,000 sectors', x['cf'] == 0 and (cyl, hd, spt, tot, bps) == (16000, 16, 63, 30_000_000, 512),
      (H(x), cyl, hd, spt, tot, bps))
x = call13(q, 0x4200, dap=dap(3, 20_000_001))
got = [q.mu.mem_read(0x1000 + i * 512, 1)[0] for i in range(3)]
check('42h: 3 sectors above 8.4 GB, LBA addressing', x['cf'] == 0 and got == [1, 2, 3]
      and ('lba', 20_000_001, 3, 'LBA') in q.ides[0].log, (H(x), got, q.ides[0].log[-2:]))
buf = bytes(range(256)) * 2 + b'\x5a' * 512 + b'\xc3' * 512
x = call13(q, 0x4300, dap=dap(3, 22_000_000), buf=buf)
check('43h: write 3 sectors', x['cf'] == 0 and all(q.ides[0].sectors.get(22_000_000 + i) == buf[i * 512:(i + 1) * 512]
      for i in range(3)), H(x))
q.mu.mem_write(0x1000, bytes(1536))
x = call13(q, 0x4200, dap=dap(3, 22_000_000))
check('42h: read back what was written', x['cf'] == 0 and bytes(q.mu.mem_read(0x1000, 1536)) == buf, H(x))
x = call13(q, 0x4200, dap=dap(2, 29_999_999))
check('42h across the end of the disk: error 04h', x['cf'] == 1 and x['ax'] >> 8 == 0x04, H(x))
x = call13(q, 0x4200, dap=dap(1, 1 << 32))
check('42h beyond 32-bit LBA: error', x['cf'] == 1, H(x))
x = call13(q, 0x4200, dap=dap(128, 1000))
check('42h with more than 127 sectors: error 01h', x['cf'] == 1 and x['ax'] >> 8 == 0x01, H(x))
x = call13(q, 0x4400, dap=dap(4, 1000))
check('44h: verify', x['cf'] == 0 and 0x40 in q.ides[0].log, H(x))
x = call13(q, 0x4700, dap=dap(1, 1000))
check('47h: seek', x['cf'] == 0, H(x))
x = call13(q, 0x0201, bx=0x1000, cx=0x0003, dx=0x0180)
check('02h: CHS read through ECHS unchanged', x['cf'] == 0 and q.mu.mem_read(0x1000, 1)[0] == 65, H(x))
x = call13(q, 0x0800)
check('08h: DOS geometry unchanged (1002/255/63)', x['cx'] == 0xE9FF and x['dx'] >> 8 == 0xFE, H(x))
q, r, t2, _ = boot(lba=30_000_000)
check('LBA capacity shown when larger than CHS', 'LBA 14648 MB' in rows(t2)[R_C], rows(t2)[R_C])

# ---------------------------------------------------------------- SETUP during POST or from the countdown
def keys_at(q, seq, t, gap=900):
    for i, sc in enumerate(seq): q.type_key(sc, at_ms=t + i * gap)
QUIT = [0x01, 0x01, 0x50, 0x1C, 0x15, 0x1C]      # Esc (Summary), Esc (Exit page), Down, Enter: exit without saving, Y, Enter
TOOLS_AUTO = [0x0F] * 4 + [0x50] * 4 + [0x1C]     # Tab x4 (Tools), Down x4, Enter: switch auto-detect
def setup_then(q, seq):
    st = {}
    def u(z):
        if 'BUBios (tm)' in z.text() and 't' not in st:
            st['t'] = z.t / 1e6; keys_at(z, seq, st['t'] + 1500)
        if COUNTDOWN in z.text(): st['final'] = z.text()
        return False
    r = q.run(max_ms=90000, until=u)
    return r, rows(st.get('final', ''))
q = Post(ROM, drive=(16000, 16, 63), slave=(8912, 15, 63)); q.type_key(0x53, at_ms=4000)
r, tt = setup_then(q, QUIT[1:])                  # SETUP opens on the Summary page: one Esc
check('DEL during the memory test, quit SETUP: drive names shown again', r == 'int19' and 'SanDisk SDCFB-8192' in tt[R_C]
      and 'WDC AC24300L' in tt[R_D] and '16000/16/63' in tt[R_CGEO], '\n'.join(tt[R_C:R_DGEO + 1]))
q = Post(ROM, cmos=make_cmos(drive=None), drive=(16000, 16, 63)); q.type_key(0x53, at_ms=4000)
r, tt = setup_then(q, TOOLS_AUTO + QUIT)
check('DEL during the memory test, auto-detect switched on: drive found in the same POST',
      r == 'int19' and 'Auto' in tt[R_CGEO] and q.mu.mem_read(0x475, 1)[0] == 1 and cold_starts(q) == 1,
      ('\n'.join(tt[R_C:R_DGEO + 1]), q.mu.mem_read(0x475, 1)[0], cold_starts(q)))
q = Post(ROM, cmos=make_cmos(drive=None), drive=(16000, 16, 63))
q.run(max_ms=40000, until=lambda z: COUNTDOWN in z.text())
keys_at(q, [0x53], q.t / 1e6 + 300); keys_at(q, TOOLS_AUTO + QUIT, q.t / 1e6 + 2500)
r = q.run(max_ms=90000)
check('countdown DEL, auto-detect switched on, quit: POST restarts and finds the drive',
      r == 'int19' and cold_starts(q) == 2 and q.mu.mem_read(0x475, 1)[0] == 1 and q.cmos[0x12] == 0xF0,
      (r, cold_starts(q), q.mu.mem_read(0x475, 1)[0], hex(q.cmos[0x12])))
def cache_toggle(cmos):
    q = Post(ROM, cmos=cmos)
    q.run(max_ms=40000, until=lambda z: COUNTDOWN in z.text())
    keys_at(q, [0x53], q.t / 1e6 + 300)
    keys_at(q, [0x0F, 0x0F] + [0x50] * 12 + [0x51, 0x01, 0x44, 0x15, 0x1C], q.t / 1e6 + 2500)   # Advanced: External Cache, Esc, F10 Y Enter
    c0 = bytes(q.cmos)
    return q, q.run(max_ms=90000), c0
q, r, c0 = cache_toggle(None)                    # first save: SETUP also tidies bytes in 34h-6Eh
q, r, c0 = cache_toggle(bytes(q.cmos))          # second save: only 2Dh (checksum 2Eh/2Fh) changes
check('countdown DEL, only a setting in 10h-2Dh changed (checksum 2Eh/2Fh): POST restarts',
      r == 'int19' and cold_starts(q) == 2 and (q.cmos[0x2D] ^ c0[0x2D]) == 0x08 and q.cmos[0x3E:0x40] == c0[0x3E:0x40],
      (r, cold_starts(q), hex(c0[0x2D]), hex(q.cmos[0x2D]), bytes(q.cmos[0x3E:0x40]).hex(), bytes(c0[0x3E:0x40]).hex()))

q = Post(ROM)                                     # DEL after AMI's own check (checkpoint 89h), before the countdown
q.run(max_ms=40000, until=lambda z: any(c == 0x90 for c, _ in z.p80[-4:]))
q.type_key(0x53)
r = q.run(max_ms=16000, until=lambda z: 'BUBios (tm)' in z.text())
check('DEL pressed between AMI\'s DEL check and the countdown: SETUP still runs', r == 'until', r)
q = Post(ROM); q.type_key(0x53, at_ms=4000)
r, tt = setup_then(q, QUIT[1:])
check('after AMI\'s own SETUP visit the countdown does not open SETUP again', r == 'int19' and q.cmos[0x0E] & 1 == 0
      and COUNTDOWN in '\n'.join(tt), (r, hex(q.cmos[0x0E])))

# ---------------------------------------------------------------- floppy change line (INT 13h 15h/16h)
# a hard disk: the change line is reported and read through port 3F7h
q = Post(ROM, drive=(8912, 15, 63), drive_model='WDC AC24300L', cmos=make_cmos(drive=(8912, 15, 63))); q.run(max_ms=40000)
x = call13(q, 0x1500, dx=0)
check('hard disk only: INT 13h 15h reports a change line for A: (AH=02)', x['ax'] >> 8 == 2 and x['cf'] == 0, H(x))
q.fdc_dchg = True
x = call13(q, 0x1600, dx=0)
check('floppy A: INT 13h 16h after a disk swap: changed (AH=06)', x['ax'] >> 8 == 6 and x['cf'] == 1, H(x))
q.fdc_dchg = False
x = call13(q, 0x1600, dx=0)
check('floppy A: INT 13h 16h without a swap: not changed (AH=00)', x['ax'] >> 8 == 0 and x['cf'] == 0, H(x))
# a CF card (IDENTIFY word 0 = 848Ah) hides the change line: report none, so DOS checks the disk itself
for label, kw in (('CF card as master', {}),
                  ('CF card as slave', dict(drive=(8912, 15, 63), drive_model='WDC AC24300L', slave=(16000, 16, 63),
                                            slave_model='SanDisk SDCFB-8192',
                                            cmos=make_cmos(drive=(8912, 15, 63), drive_d=(16000, 16, 63))))):
    q = Post(ROM, **kw); q.run(max_ms=40000)
    x = call13(q, 0x1500, dx=0)
    check('%s: INT 13h 15h reports no change line for A: (AH=01)' % label, x['ax'] >> 8 == 1 and x['cf'] == 0
          and q.mu.mem_read(0x48F, 1)[0] & 8, H(x))

# ---------------------------------------------------------------- errors, schemes, mono
bad = bytearray(b'\xff' * 128); bad[0x0F] = 0
q = Post(ROM, cmos=bad, drive=None); r = q.run(max_ms=15000)
tt = rows(q.text())
check('bad CMOS: no crash without the AMI copyright line', 'Press <F1> to RESUME' in q.text()
      and 'American Megatrends' not in q.text(), q.text())
check('bad CMOS: errors in the message rows', 'CMOS' in tt[R_MSG] and 'POST found a problem' in tt[R_STAT],
      '\n'.join(tt[17:25]))
t0 = q.t / 1e6                     # F1 -> SETUP -> quit without saving -> POST goes on
for i, sc in enumerate((0x3B, 0x01, 0x50, 0x1C, 0x15, 0x1C)):
    q.type_key(sc, at_ms=t0 + 2000 + i * 1500)
snap = {}
def fin(z):
    if COUNTDOWN in z.text(): snap['t'] = z.text()
    return False
r = q.run(max_ms=30000, until=fin)
tt = rows(snap.get('t', ''))
check('bad CMOS, F1, SETUP, quit: POST passes AMI\'s later checks and boots', r == 'int19' and 'Booting' in tt[R_STAT], r)
check('after SETUP: screen redrawn, memory kept, no POST time', '16384 KB' in tt[R_MEM] and 'POST' not in tt[R_STAT],
      '\n'.join(tt))
for sch, want in ((0, 0x30), (1, 0x60), (2, 0x20), (3, 0x70), (4, 0x70)):
    q = Post(ROM, cmos=make_cmos(scheme=sch)); q.run(max_ms=40000, until=lambda z: COUNTDOWN in z.text())
    a = q.mu.mem_read(0xB8000 + 3, 1)[0]
    check('colour scheme %d: title attribute %02X' % (sch, want), a == want, hex(a))
q = Post(ROM, cmos=make_cmos(video='mono'), video='mono')
q.run(max_ms=40000, until=lambda z: COUNTDOWN in z.text())
check('monochrome: screen in B000', 'Power-On Self Test' in q.text() and q.vbase == 0xB0000, q.vbase)
check('monochrome: inverse title bar', q.mu.mem_read(0xB0000 + 3, 1)[0] == 0x70)
r = q.run(max_ms=10000)
check('monochrome: boots in mode 7, screen cleared', r == 'int19' and q.vmode == 7
      and bytes(q.mu.mem_read(0xB0000, 4000)) == b'\x20\x07' * 2000, (r, q.vmode))
q = Post(ROM, cmos=make_cmos()); q.trace_ports = True; q.run(max_ms=40000)
tones = [e[2] for e in q.portlog if len(e) == 3 and e[0] == 'out' and e[1] == '0x42']
want = [hex(1140 & 255), hex(1140 >> 8), hex(761 & 255), hex(761 >> 8)]
check('chime: two notes (C6, G6) instead of the beep',
      any(tones[i:i + 4] == want for i in range(len(tones))), tones[-8:])

print('\n%d failure(s)' % fails)
sys.exit(1 if fails else 0)
