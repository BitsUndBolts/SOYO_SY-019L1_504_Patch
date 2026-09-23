#!/usr/bin/env python3
"""setup_emu.py -- run the ROM's own SETUP program in a Unicorn real-mode
emulator and capture the 80x25 text screen as PNG / plain text.

The setup is entered exactly the way POST does it (F000:2968: copy F000:0000-
DFFF to segment 0100h, zero the data area, jump into the main menu).  Around
it the harness provides just enough of a PC:

  * INT 10h text services and a real B800h text buffer (the setup writes the
    buffer directly; INT 10h is used for the cursor and a few reads)
  * INT 16h keyboard fed from a scripted key list; when the list runs dry the
    emulator stops, so every screen state can be captured and resumed
  * CMOS / RTC at ports 70h/71h (128 bytes, checksum kept valid)
  * OPTi chipset index/data ports 22h/24h (plain register file)
  * INT 13h goes through the ROM's real dispatcher (IDE model from
    ide_harness-style port hooks) so drive screens read real geometry
  * BDA with 640 KB base memory, colour text mode 3

Typical use:
    from setup_emu import *
    s = Setup(load_rom(ROM))
    s.run()                 # runs until the setup waits for a key
    s.png('shots/main.png'); print(s.text())
    s.keys(RIGHT, ENTER); s.run()
"""
import os, struct, sys
from unicorn import *
from unicorn.x86_const import *
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from vgafont import GLYPHS

ROOT = os.path.dirname(HERE)
def load_rom(p): return open(p, 'rb').read()
def lin(s, o): return ((s << 4) + o) & 0xFFFFF

# key codes (scan<<8 | ascii)
ESC, ENTER, TAB = 0x011B, 0x1C0D, 0x0F09
UP, DOWN, LEFT, RIGHT = 0x4800, 0x5000, 0x4B00, 0x4D00
PGUP, PGDN, HOME, END = 0x4900, 0x5100, 0x4700, 0x4F00
F1, F2, F3, F5, F6, F7, F10 = 0x3B00, 0x3C00, 0x3D00, 0x3F00, 0x4000, 0x4100, 0x4400
def ch(c): return ord(c)   # scan code 0 is fine for letters in this setup
def Y(): return 0x1559
def N(): return 0x314E

CGA = [(0,0,0),(0,0,170),(0,170,0),(0,170,170),(170,0,0),(170,0,170),(170,85,0),(170,170,170),
       (85,85,85),(85,85,255),(85,255,85),(85,255,255),(255,85,85),(255,85,255),(255,255,85),(255,255,255)]

def default_cmos():
    """CMOS resembling the real board: 1.44M + 1.2M floppies, VGA, 640K+15M,
    drive C: = type 47 16000/16/63 (the 8 GB CF card), shadow + cache on."""
    c = bytearray(128)
    c[0x00:0x0A] = bytes([0x00, 0, 0x30, 0, 0x12, 0, 0x03, 0x23, 0x09, 0x26])  # 12:30:00, Wed 23 Sep 26
    c[0x0A] = 0x26; c[0x0B] = 0x02; c[0x0D] = 0x80
    c[0x10] = 0x42          # A: 1.44M, B: 1.2M
    c[0x12] = 0xF0          # C: type 47 (user), D: none
    c[0x14] = 0x41          # 2 floppies, VGA/EGA
    c[0x15:0x17] = struct.pack('<H', 640)
    c[0x17:0x19] = struct.pack('<H', 15360)
    c[0x19] = 47            # extended type byte for C:
    # type 47 user parameters for C: cyl, heads, wpcom, control, lzone, sectors
    c[0x1B:0x24] = struct.pack('<HBHBHB', 16000, 16, 0xFFFF, 0x08, 16000, 63)
    c[0x2D] = 0x08 | 0x20   # external cache on, boot A:, C:
    c[0x30:0x32] = struct.pack('<H', 15360)
    c[0x32] = 0x19          # century
    c[0x35] = 0x0C          # video C000 + system F000 shadow
    return c

def fix_cmos_checksum(c):
    s = sum(c[0x10:0x2E]) & 0xFFFF
    c[0x2E] = s >> 8; c[0x2F] = s & 0xFF

class Setup:
    def __init__(s, rom, cmos=None, drive=(16000, 16, 63), chipset=None, timing_loop=None):
        s.rom = rom
        s.mu = mu = Uc(UC_ARCH_X86, UC_MODE_16)
        mu.mem_map(0, 0x100000)
        mu.mem_write(0xF0000, rom)
        s.cmos = bytearray(cmos) if cmos else default_cmos()
        if not cmos: fix_cmos_checksum(s.cmos)
        s.cidx = 0
        s.opti = bytearray(256) if chipset is None else bytearray(chipset)
        s.oidx = 0
        s.keyq = []; s.pending = None; s.polls = 0
        s.cur = (0, 0); s.curshape = 0x0607
        s.p61 = 0; s.p61out = 0
        # PIT channel 2 model for the BUBios clock measurement: time advances only
        # while the CPU executes instructions inside the RAM-copy timing loop
        s.cpu_hz = 40e6; s.cpu_cyc_per_insn = 190 / 9
        s.pit_insn = 0; s.pit_lh = 0; s.pit_latched = 0xFFFF
        s.ext_kb = 15360
        s.drive = drive
        s.ports_log = []
        s.stopped_reason = None
        s.insn = 0
        # BDA
        bda = bytearray(256)
        struct.pack_into('<H', bda, 0x10, 0x4041)   # equipment
        struct.pack_into('<H', bda, 0x13, 640)
        bda[0x49] = 3; struct.pack_into('<H', bda, 0x4A, 80)
        struct.pack_into('<H', bda, 0x4C, 4096)
        struct.pack_into('<H', bda, 0x63, 0x3D4)
        bda[0x75] = 1; bda[0x84] = 24
        mu.mem_write(0x400, bytes(bda))
        # IVT: every vector -> F000:FF53 (an IRET in the ROM), INT 13h -> real handler
        iret = rom.find(b'\xcf', 0xFF00)
        for v in range(256):
            mu.mem_write(v * 4, struct.pack('<HH', iret, 0xF000))
        mu.mem_write(0x13 * 4, struct.pack('<HH', 0xA3E7, 0xF000))
        # FDPT pointers for drive C (type 47 area at 0000:0300)
        C, H, S = drive
        fd = bytearray(16); struct.pack_into('<H', fd, 0, min(C, 1024)); fd[2] = H; fd[0xE] = S
        struct.pack_into('<H', fd, 0xC, min(C, 1024))
        mu.mem_write(0x300, bytes(fd))
        mu.mem_write(0x104, struct.pack('<HH', 0x300, 0))
        # blank screen
        mu.mem_write(0xB8000, b'\x20\x07' * 2000)
        mu.hook_add(UC_HOOK_INSN, s._in, aux1=UC_X86_INS_IN)
        mu.hook_add(UC_HOOK_INSN, s._out, aux1=UC_X86_INS_OUT)
        mu.hook_add(UC_HOOK_INTR, s._intr)
        def _tl(uc, addr, size, ud): s.pit_insn += 1
        if timing_loop: mu.hook_add(UC_HOOK_CODE, _tl, begin=0x1000 + timing_loop[0], end=0x1000 + timing_loop[1] - 1)
        mu.reg_write(UC_X86_REG_CS, 0xF000)
        # POST calls the setup with a near CALL; give it a return address that
        # lands on a sentinel so leaving the setup ends the emulation cleanly
        EXIT_OFF = 0xE3E0                   # inside a zero run of the ROM image
        mu.reg_write(UC_X86_REG_SS, 0x30); mu.reg_write(UC_X86_REG_SP, 0x100 - 2)
        mu.mem_write(lin(0x30, 0x100 - 2), struct.pack('<H', EXIT_OFF))
        def _exit(uc, addr, size, ud):
            s.exited = True; s.stopped_reason = 'exit'; uc.emu_stop()
        mu.hook_add(UC_HOOK_CODE, _exit, begin=0xF0000 + EXIT_OFF, end=0xF0000 + EXIT_OFF)
        mu.reg_write(UC_X86_REG_DS, 0x40)
        s.pc = lin(0xF000, 0x2968)
        s.exited = False

    # ---------------- ports
    def _in(s, uc, port, size, ud):
        if port == 0x71: return s.cmos[s.cidx & 0x7F]
        if port == 0x24: return s.opti[s.oidx]
        if port == 0x61: s.p61 ^= 0x10; return s.p61
        if port == 0x64: return 0x14
        if port == 0x60: return 0
        if port == 0x1F7: return 0x58
        if port == 0x1F1: return 0
        if port == 0x42:
            s.pit_lh ^= 1
            if s.pit_lh == 1:                    # first read after latch: low byte
                el = int(s.pit_insn * s.cpu_cyc_per_insn * 1193182 / s.cpu_hz)
                s.pit_latched = max(0, 0xFFFF - el)
                return s.pit_latched & 0xFF
            return s.pit_latched >> 8
        if port == 0x3DA: s.p61 ^= 0x09; return s.p61 & 0x09
        return 0xFF
    def _out(s, uc, port, size, val, ud):
        if port == 0x70: s.cidx = val & 0x7F
        elif port == 0x71: s.cmos[s.cidx & 0x7F] = val & 0xFF
        elif port == 0x43:
            if val == 0x80: s.pit_lh = 0
            else: s.pit_insn = 0
        elif port == 0x61 and (val & 1) and not (s.p61out & 1): s.pit_insn = 0; s.p61out = val
        elif port == 0x61: s.p61out = val
        elif port == 0x22: s.oidx = val & 0xFF
        elif port == 0x24: s.opti[s.oidx] = val & 0xFF
        elif port not in (0x3D8, 0x80, 0xED, 0xEB, 0x42): s.ports_log.append((hex(port), hex(val)))

    # ---------------- interrupts
    def _flags(s, cf=None, zf=None):
        f = s.mu.reg_read(UC_X86_REG_EFLAGS)
        if cf is not None: f = (f | 1) if cf else (f & ~1)
        if zf is not None: f = (f | 0x40) if zf else (f & ~0x40)
        s.mu.reg_write(UC_X86_REG_EFLAGS, f)
    def _dispatch(s, uc, intno):
        vec = struct.unpack('<HH', bytes(uc.mem_read(intno * 4, 4)))
        sp = uc.reg_read(UC_X86_REG_SP); ss = uc.reg_read(UC_X86_REG_SS)
        fl = uc.reg_read(UC_X86_REG_EFLAGS) & 0xFFFF
        cs = uc.reg_read(UC_X86_REG_CS); ip = uc.reg_read(UC_X86_REG_IP)
        for v in (fl, cs, ip):
            sp = (sp - 2) & 0xFFFF; uc.mem_write(lin(ss, sp), struct.pack('<H', v))
        uc.reg_write(UC_X86_REG_SP, sp)
        uc.reg_write(UC_X86_REG_CS, vec[1]); uc.reg_write(UC_X86_REG_IP, vec[0])
    def _intr(s, uc, intno, ud):
        ax = uc.reg_read(UC_X86_REG_AX); ah = ax >> 8; al = ax & 0xFF
        if intno == 0x10: return s._int10(uc, ah, al)
        if intno == 0x16:
            if ah in (0x00, 0x10):
                s.polls = 0
                if s.pending is None:
                    ip = uc.reg_read(UC_X86_REG_IP)
                    uc.reg_write(UC_X86_REG_IP, (ip - 2) & 0xFFFF)   # re-execute INT 16h on resume
                    s.stopped_reason = 'key'; uc.emu_stop(); return
                uc.reg_write(UC_X86_REG_AX, s.pending); s.pending = None; return
            if ah in (0x01, 0x11):
                # Report "no key" to plain polls (AMI's getch flushes type-ahead with
                # AH=01h before a blocking AH=00h); only a sustained poll loop sees it.
                s.polls += 1
                if s.pending is not None and s.polls > 200:
                    uc.reg_write(UC_X86_REG_AX, s.pending); s._flags(zf=False); return
                if s.pending is None and s.polls > 200:
                    ip = uc.reg_read(UC_X86_REG_IP)
                    uc.reg_write(UC_X86_REG_IP, (ip - 2) & 0xFFFF)
                    s.stopped_reason = 'key'; uc.emu_stop(); return
                s._flags(zf=True); return
            if ah == 0x02: uc.reg_write(UC_X86_REG_AX, ax & 0xFF00); return
            return
        if intno == 0x1A:
            if ah == 0x00: uc.reg_write(UC_X86_REG_CX, 0x0012); uc.reg_write(UC_X86_REG_DX, 0x3456); uc.reg_write(UC_X86_REG_AX, 0)
            if ah == 0x02: uc.reg_write(UC_X86_REG_CX, 0x1230); uc.reg_write(UC_X86_REG_DX, 0x0000); s._flags(cf=False)
            if ah == 0x04: uc.reg_write(UC_X86_REG_CX, 0x2026); uc.reg_write(UC_X86_REG_DX, 0x0923); s._flags(cf=False)
            return
        if intno == 0x11: uc.reg_write(UC_X86_REG_AX, 0x4041); return
        if intno == 0x12: uc.reg_write(UC_X86_REG_AX, 640); return
        if intno == 0x15:
            if ah == 0x88: uc.reg_write(UC_X86_REG_AX, s.ext_kb); s._flags(cf=False); return
            s._flags(cf=True); uc.reg_write(UC_X86_REG_AX, (ax & 0xFF) | 0x8600); return
        if intno == 0x19 or intno == 0x18:
            s.exited = True; s.stopped_reason = 'int%02x' % intno; uc.emu_stop(); return
        s._dispatch(uc, intno)

    def _vram(s): return bytearray(s.mu.mem_read(0xB8000, 4000))
    def _int10(s, uc, ah, al):
        bx = uc.reg_read(UC_X86_REG_BX); cx = uc.reg_read(UC_X86_REG_CX); dx = uc.reg_read(UC_X86_REG_DX)
        if ah == 0x00:
            uc.mem_write(0xB8000, b'\x20\x07' * 2000); s.cur = (0, 0)
        elif ah == 0x01: s.curshape = cx
        elif ah == 0x02: s.cur = (dx >> 8, dx & 0xFF); uc.mem_write(0x450, bytes([dx & 0xFF, dx >> 8]))
        elif ah == 0x03:
            r, c = s.cur; uc.reg_write(UC_X86_REG_DX, (r << 8) | c); uc.reg_write(UC_X86_REG_CX, s.curshape)
        elif ah in (0x06, 0x07):
            v = s._vram(); attr = bx >> 8
            r0, c0, r1, c1 = cx >> 8, cx & 0xFF, dx >> 8, dx & 0xFF
            n = al
            rows = list(range(r0, r1 + 1))
            def cell(r, c): return v[(r * 80 + c) * 2:(r * 80 + c) * 2 + 2]
            new = {}
            for r in rows:
                src = r + n if ah == 6 else r - n
                for c in range(c0, c1 + 1):
                    if n == 0 or src < r0 or src > r1: new[(r, c)] = bytes([0x20, attr])
                    else: new[(r, c)] = bytes(cell(src, c))
            for (r, c), b in new.items():
                if r < 25 and c < 80: v[(r * 80 + c) * 2:(r * 80 + c) * 2 + 2] = b
            uc.mem_write(0xB8000, bytes(v))
        elif ah == 0x08:
            r, c = s.cur; b = s.mu.mem_read(0xB8000 + (r * 80 + c) * 2, 2)
            uc.reg_write(UC_X86_REG_AX, b[0] | (b[1] << 8))
        elif ah in (0x09, 0x0A):
            r, c = s.cur; p = r * 80 + c
            for i in range(cx):
                if p + i >= 2000: break
                if ah == 0x09: uc.mem_write(0xB8000 + (p + i) * 2, bytes([al, bx & 0xFF]))
                else: uc.mem_write(0xB8000 + (p + i) * 2, bytes([al]))
        elif ah == 0x0E:
            r, c = s.cur
            if al == 0x0D: c = 0
            elif al == 0x0A: r += 1
            elif al == 0x08: c = max(0, c - 1)
            elif al == 0x07: pass
            else:
                uc.mem_write(0xB8000 + (r * 80 + c) * 2, bytes([al])); c += 1
                if c >= 80: c = 0; r += 1
            if r >= 25:
                v = s._vram(); v = v[160:] + bytearray(b'\x20\x07' * 80); uc.mem_write(0xB8000, bytes(v)); r = 24
            s.cur = (r, c)
        elif ah == 0x0F: uc.reg_write(UC_X86_REG_AX, 0x5003); uc.reg_write(UC_X86_REG_BX, bx & 0xFF)
        elif ah == 0x12: uc.reg_write(UC_X86_REG_BX, 0x0003)
        elif ah == 0x1A: uc.reg_write(UC_X86_REG_AX, 0x001A); uc.reg_write(UC_X86_REG_BX, 0x0008)

    # ---------------- driving
    def keys(s, *k): s.keyq.extend(k)
    def _run1(s, limit):
        mu = s.mu
        s.stopped_reason = None
        cs = mu.reg_read(UC_X86_REG_CS); ip = mu.reg_read(UC_X86_REG_IP)
        start = s.pc if s.pc is not None else lin(cs, ip)
        s.pc = None
        try:
            mu.emu_start(start, 0xFFFFF + 1, count=limit)
        except UcError as e:
            s.stopped_reason = 'error %s at %04x:%04x' % (e, mu.reg_read(UC_X86_REG_CS), mu.reg_read(UC_X86_REG_IP))
        if s.stopped_reason is None: s.stopped_reason = 'limit'
        return s.stopped_reason
    def run(s, limit=50_000_000):
        """Run until the setup waits for a key and no scripted keys are left."""
        while True:
            r = s._run1(limit)
            if r == 'key' and s.keyq:
                s.pending = s.keyq.pop(0); s.polls = 0; continue
            return r
    def where(s):
        return '%04x:%04x' % (s.mu.reg_read(UC_X86_REG_CS), s.mu.reg_read(UC_X86_REG_IP))
    def step(s, *k, limit=50_000_000):
        s.keys(*k); return s.run(limit)

    # ---------------- output
    def cells(s):
        v = s._vram(); return [(v[i], v[i + 1]) for i in range(0, 4000, 2)]
    def text(s):
        out = []
        cl = s.cells()
        for r in range(25):
            out.append(bytes(c for c, a in cl[r * 80:(r + 1) * 80]).decode('cp437').rstrip())
        return '\n'.join(out)
    def png(s, path, scale=1):
        from PIL import Image
        cl = s.cells()
        W, H = 720, 400
        img = Image.new('RGB', (W, H))
        px = img.load()
        for i, (c, a) in enumerate(cl):
            r, col = divmod(i, 80)
            fg = CGA[a & 0x0F]; bg = CGA[(a >> 4) & 0x07]
            g = GLYPHS[c]
            x0, y0 = col * 9, r * 16
            for y in range(16):
                row = g[y]
                for x in range(9):
                    px[x0 + x, y0 + y] = fg if (row >> (8 - x)) & 1 else bg
        img = img.resize((W * 2 // 1, H * 2 * 12 // 10), Image.NEAREST) if scale else img
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        img.save(path)
        return path

if __name__ == '__main__':
    rom = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, '..', 'binary', 'SY019L1_27C512_CHSPATCH.BIN')
    s = Setup(load_rom(rom))
    print(s.run(), s.where())
    print(s.text())
