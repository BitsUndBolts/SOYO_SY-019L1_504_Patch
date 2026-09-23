#!/usr/bin/env python3
"""post_emu.py -- run the ROM's complete power-on self test in Unicorn.

    python3 py/post_emu.py binary/BUBIOS2_SY019L1.BIN [ms]   -> final screen as text

Starts at the reset vector (F000:FFF0) and models just enough of a 386 AT
board for the AMI POST to run all the way to INT 19h:

  * RAM (16 MB by default); addresses above it read FFh, so AMI's memory
    sizing finds the right size
  * both 8259 PICs with real interrupt delivery, in real and protected mode
  * 8254 PIT against a virtual clock, port 61h refresh toggle and OUT2
  * 8042 keyboard controller: self test, command byte, output port / A20,
    keyboard reset, scripted keystrokes (type_key), and the CPU reset
    (command FEh / port 92h) AMI uses to leave protected mode
  * 8237 DMA register files and page registers, POST code port 80h
  * MC146818 RTC/CMOS with both AMI checksums (make_cmos builds a valid image)
  * OPTi 82C495 index/data ports 22h/24h as a register file
  * floppy controller: reset, SPECIFY, RECALIBRATE, SEEK, SENSE INTERRUPT
  * master and slave IDE drives with IDENTIFY data (REP INSW/OUTSW sites are
    handled by hooks, Unicorn has no string-I/O hooks)
  * a colour VGA (a 2 KB stub option ROM at C000 plus a CRTC at 3D4h) or,
    with video='mono', an MDA-style card; INT 10h text services are served
    by the emulator into B800h / B000h

Virtual time: every executed instruction counts 100 ns, every I/O access
1 us.  Unicorn reports itself as a modern CPU, so the Processor/Clock values
on the POST screen are not meaningful here (86Box shows them correctly).
"""
import os, struct, sys, time
from unicorn import *
from unicorn.x86_const import *

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from vgafont import GLYPHS
except ImportError:
    GLYPHS = None

def lin(s, o): return ((s << 4) + o) & 0xFFFFF

CGA = [(0,0,0),(0,0,170),(0,170,0),(0,170,170),(170,0,0),(170,0,170),(170,85,0),(170,170,170),
       (85,85,85),(85,85,255),(85,255,85),(85,255,255),(255,85,85),(255,85,255),(255,255,85),(255,255,255)]

NS_PER_INSN = 100
NS_PER_IO = 1000
PIT_HZ = 1193182

# ------------------------------------------------------------------ CMOS
def bcd(v): return ((v // 10) << 4) | (v % 10)

def make_cmos(drive=(16000, 16, 63), floppies=0x40, video='vga', ext_kb=15360, cache=True,
              drive_d=None, scheme=0):
    """A valid AMI CMOS image.  drive / drive_d: (C, H, S) as type 47, or an
    int for a ROM drive type 1-46, or None.  video: 'vga', 'cga' or 'mono'."""
    c = bytearray(128)
    c[0x00:0x0A] = bytes([0x00, 0, 0x30, 0, 0x12, 0, 0x03, 0x23, 0x09, 0x26])
    c[0x0A] = 0x26; c[0x0B] = 0x02; c[0x0D] = 0x80
    c[0x10] = floppies
    nfd = (1 if floppies & 0xF0 else 0) + (1 if floppies & 0x0F else 0)
    equip = 0x02                                     # coprocessor
    if nfd: equip |= 0x01 | ((nfd - 1) << 6)
    equip |= {'vga': 0x00, 'cga': 0x20, 'mono': 0x30}[video]
    c[0x14] = equip
    nib = 0
    for i, d in enumerate((drive, drive_d)):
        if d is None: continue
        t = 47 if isinstance(d, tuple) else d
        nib |= (0x0F if t >= 15 else t) << (4 * (1 - i))
        c[0x19 + i] = t if t >= 15 else 0
        if isinstance(d, tuple):
            C, H, S = d
            at = 0x1B + 9 * i
            c[at:at + 9] = struct.pack('<HBHBHB', C, H, 0xFFFF, 0x08 if H > 8 else 0, C, S)
    c[0x12] = nib
    c[0x15:0x17] = struct.pack('<H', 640)
    c[0x17:0x19] = struct.pack('<H', ext_kb)
    c[0x11] = 0x4B          # memory test above 1 MB, Hit <DEL> message, wait for F1, NumLock
    c[0x2D] = (0x08 if cache else 0) | 0x20
    c[0x30:0x32] = struct.pack('<H', ext_kb)
    c[0x32] = 0x19
    c[0x35] = 0x0C
    c[0x37] = scheme
    fix_cmos(c)
    return c

def fix_cmos(c):
    s = sum(c[0x10:0x2E]) & 0xFFFF
    c[0x2E] = s >> 8; c[0x2F] = s & 0xFF
    # AMI extended checksum: 34h-6Eh without 3Eh/3Fh, stored at 3Eh (hi) / 3Fh (lo)
    s = (sum(c[0x34:0x3E]) + sum(c[0x40:0x6F])) & 0xFFFF
    c[0x3E] = s >> 8; c[0x3F] = s & 0xFF

# ------------------------------------------------------------------ IDE drive
class IdeDrive:
    def __init__(s, C, H, S, model='SanDisk SDCFB-8192', serial='BU20TEST0001', fw='HDX 4.04'):
        s.C, s.H, s.S = C, H, S
        s.model, s.serial, s.fw = model, serial, fw
        s.r = {1: 0, 2: 1, 3: 1, 4: 0, 5: 0, 6: 0xA0}
        s.status = 0x50
        s.buf = b''; s.bufpos = 0
        s.sectors = {}
        s.log = []
        s.irq = False
        s.cur_lba = 0
    def identify(s):
        w = [0] * 256
        w[0] = 0x848A if 'CF' in s.model or 'SanDisk' in s.model else 0x0040
        w[1] = s.C; w[3] = s.H; w[6] = s.S
        def put(i, txt, n):
            t = txt.ljust(n * 2)[:n * 2]
            for k in range(n): w[i + k] = (ord(t[2 * k]) << 8) | ord(t[2 * k + 1])
        put(10, s.serial, 10); put(23, s.fw, 4); put(27, s.model, 20)
        w[47] = 0x8001; w[49] = 0x0200
        w[53] = 1; w[54] = s.C; w[55] = s.H; w[56] = s.S
        tot = s.C * s.H * s.S
        w[57] = tot & 0xFFFF; w[58] = tot >> 16; w[60] = tot & 0xFFFF; w[61] = tot >> 16
        return struct.pack('<256H', *w)
    def lba(s):
        c = s.r[4] | (s.r[5] << 8); h = s.r[6] & 0xF; sec = s.r[3]
        return (c * s.H + h) * s.S + sec - 1
    def command(s, cmd):
        s.log.append(cmd)
        if cmd == 0xEC:
            s.buf = s.identify(); s.bufpos = 0; s.status = 0x58; return True
        if cmd in (0x20, 0x21):
            s.cur_lba = s.lba()
            s.buf = b''.join(s.sectors.get(s.cur_lba + i, b'\0' * 512) for i in range(s.r[2] or 256))
            s.bufpos = 0; s.status = 0x58; return True
        if cmd in (0x30, 0x31):
            s.status = 0x58; s.buf = b''; return True
        s.status = 0x50; return True
    def read_block(s, n):
        d = s.buf[s.bufpos:s.bufpos + n]; s.bufpos += n
        if s.bufpos >= len(s.buf): s.status = 0x50
        return d.ljust(n, b'\0')

# ------------------------------------------------------------------ the machine
class Post:
    def __init__(s, rom, cmos=None, mem_mb=16, drive=(16000, 16, 63), drive_model='SanDisk SDCFB-8192',
                 keys=None, trace_ports=False, fpu=True, slave=None, slave_model='WDC AC24300L', video='vga'):
        s.rom = rom
        s.mu = mu = Uc(UC_ARCH_X86, UC_MODE_16)
        s.mem_mb = mem_mb
        mu.mem_map(0, mem_mb << 20)
        # nothing beyond installed memory: reads FF, writes vanish
        def _mr(uc, off, size, ud): return (1 << (8 * size)) - 1
        def _mw(uc, off, size, val, ud): pass
        mu.mmio_map(mem_mb << 20, (128 - mem_mb) << 20, _mr, None, _mw, None)
        mu.mem_map(0xFFFF0000, 0x10000)
        mu.mem_write(0xFFFF0000, rom)
        mu.mem_write(0xF0000, rom)
        mu.mem_write(0xC0000, b'\xff' * 0x30000)       # no option ROMs, no E000 ROM
        # a minimal 2 KB 'VGA BIOS' so POST finds a video option ROM at C000
        # (video='mono': an MDA-style card, no ROM, CRTC at 3B4h)
        s.video = video
        vrom = bytearray(2048); vrom[0:3] = b'\x55\xaa\x04'
        # init: hook INT 10h (to an IRET; the emulator serves INT 10h itself), set mode 3
        code = bytes.fromhex('1e31c08ed8c70640003000c7064200' '00c01fb80300cd10cb')
        vrom[3:3 + len(code)] = code; vrom[0x30] = 0xCF
        vrom[0x1E:0x22] = b'IBM '; vrom[-1] = (-sum(vrom)) & 0xFF
        if video == 'vga': mu.mem_write(0xC0000, bytes(vrom))
        s.cmos = bytearray(cmos) if cmos else make_cmos(drive, drive_d=slave)
        s.cidx = 0
        s.opti = bytearray(256); s.oidx = 0
        s.cyrix = bytearray(256)
        s.p80 = []                 # POST checkpoints
        s.t = 0                    # virtual ns
        # PIC
        s.pic = [dict(imr=0xFF, irr=0, isr=0, base=8, icw=0, read_isr=False, init=0) for _ in range(2)]
        s.pic[1]['base'] = 0x70
        # PIT
        s.pit = [dict(mode=3, reload=0x10000, start=0, rw=3, latch=None, flip=0, wflip=0, gate=True) for _ in range(3)]
        s.p61 = 0
        s.last_irq0 = 0
        # KBC
        s.kbc_out = []             # (byte, from_aux)
        s.kbc_cmdbyte = 0x45
        s.kbc_pending = None
        s.kbc_outport = 0xDF
        s.keyq = list(keys or [])
        s.key_at = []              # (time_ns, scancode)
        # DMA
        s.dma = {}
        s.dmaff = [0, 0]
        s.page = bytearray(16)
        # IDE
        s.ides = [IdeDrive(*drive, model=drive_model) if drive else None,
                  IdeDrive(*slave, model=slave_model) if slave else None]
        s.ide_r6 = 0xA0
        s.ide_ctl = 0
        # FDC (82077-style: reset, SPECIFY, RECALIBRATE, SEEK, SENSE)
        s.fdc_dor = 0x0C; s.fdc_res = []; s.fdc_cmd = []; s.fdc_reset_sense = 0
        s.fdc_st0 = None; s.fdc_pcn = 0
        # video: a colour (or, with mono=True, a monochrome) adapter's CRTC
        s.cur = (0, 0); s.curshape = 0x0607; s.vmode = 3
        s.crtc = bytearray(32); s.crtc_idx = 0
        s.crtc_base = 0x3B0 if video == 'mono' else 0x3D0
        s.halted_at = None
        s.stop = None
        s.trace_ports = trace_ports
        s.portlog = []
        s.int_log = []
        s.insn_total = 0
        s.ins_hooks = {}
        mu.hook_add(UC_HOOK_INSN, s._in, aux1=UC_X86_INS_IN)
        mu.hook_add(UC_HOOK_INSN, s._out, aux1=UC_X86_INS_OUT)
        mu.hook_add(UC_HOOK_INTR, s._intr)
        # REP INSW / OUTSW sites in the ROM (Unicorn has no string-I/O hooks)
        for off in (0xA733, 0xAF81, 0xA63D, 0xA797):
            if rom[off:off + 2] in (b'\xf3\x6d', b'\xf3\x6f'):
                mu.hook_add(UC_HOOK_CODE, s._repio, begin=0xF0000 + off, end=0xF0000 + off)
        mu.reg_write(UC_X86_REG_CS, 0xF000)
        mu.reg_write(UC_X86_REG_EDX, 0x0308)    # 386DX reset signature
        s.pc = 0xFFFF0

    # ---------------------------------------------------------- time helpers
    def now(s): return s.t
    def pit_count(s, ch):
        p = s.pit[ch]
        el = (s.t - p['start']) * PIT_HZ // 1_000_000_000
        r = p['reload']
        if p['mode'] in (2, 3):
            if p['mode'] == 3: return (r - (2 * el) % r) & 0xFFFF
            return (r - el % r) & 0xFFFF
        return (r - el) & 0xFFFF if el < r else (0x10000 - (el - r) % 0x10000) & 0xFFFF
    def pit_out(s, ch):
        p = s.pit[ch]
        el = (s.t - p['start']) * PIT_HZ // 1_000_000_000
        r = p['reload']
        if p['mode'] == 3: return 1 if (el % r) < r // 2 else 0
        if p['mode'] == 2: return 0 if (el % r) == r - 1 else 1
        if p['mode'] == 0: return 1 if el >= r else 0
        return 1

    # ---------------------------------------------------------- PIC
    def raise_irq(s, n):
        c = s.pic[n >> 3]; c['irr'] |= 1 << (n & 7)
        if n >= 8: s.pic[0]['irr'] |= 4
    def pending_vector(s):
        m = s.pic[0]
        req = m['irr'] & ~m['imr'] & 0xFF
        for i in range(8):
            b = 1 << i
            if m['isr'] & b: return None          # a higher/equal priority in service
            if req & b:
                if i == 2:
                    sl = s.pic[1]
                    sreq = sl['irr'] & ~sl['imr'] & 0xFF
                    for j in range(8):
                        bb = 1 << j
                        if sl['isr'] & bb: return None
                        if sreq & bb:
                            sl['irr'] &= ~bb; sl['isr'] |= bb
                            if not (sl['irr'] & ~sl['imr'] & 0xFF): m['irr'] &= ~4
                            m['isr'] |= 4
                            return sl['base'] + j
                    m['irr'] &= ~4
                    continue
                m['irr'] &= ~b; m['isr'] |= b
                return m['base'] + i
        return None

    # ---------------------------------------------------------- KBC
    def kbc_put(s, b, aux=False):
        s.kbc_out.append(b)
        if s.kbc_cmdbyte & 1: s.raise_irq(1)
    def kbc_cmd(s, v):
        if v == 0xAA: s.kbc_out = [0x55]
        elif v == 0xAB: s.kbc_out.append(0x00)
        elif v == 0x20: s.kbc_out.append(s.kbc_cmdbyte)
        elif v == 0x60: s.kbc_pending = 'cmdbyte'
        elif v == 0xD0: s.kbc_out.append(s.kbc_outport)
        elif v == 0xD1: s.kbc_pending = 'outport'
        elif v == 0xC0: s.kbc_out.append(0xBF if s.crtc_base == 0x3B0 else 0xFF)   # input port: bit 6 = colour
        elif v == 0xFE or (v & 0xF1) == 0xF0 and not (v & 1): s.request_reset()
        elif v in (0xAD, 0xAE, 0xA7, 0xA8, 0xFF, 0xDD, 0xDF): pass
        elif v == 0xA9: s.kbc_out.append(0x00)
        elif v == 0xA1: s.kbc_out.append(0x00)      # version
        elif 0x00 <= v <= 0x1F or 0x40 <= v <= 0x5F:
            if v < 0x20: s.kbc_out.append(0)
            else: s.kbc_pending = 'ram'
        else:
            s.portlog.append(('kbc?', hex(v)))
    def kbd_data(s, v):
        if s.kbc_pending == 'cmdbyte':
            s.kbc_cmdbyte = v; s.kbc_pending = None; return
        if s.kbc_pending == 'outport':
            s.kbc_outport = v; s.kbc_pending = None; return
        if s.kbc_pending == 'ram':
            s.kbc_pending = None; return
        # to the keyboard
        if v == 0xFF: s.kbc_put(0xFA); s.kbc_put(0xAA)
        elif v == 0xF2: s.kbc_put(0xFA); s.kbc_put(0xAB); s.kbc_put(0x83)
        elif v == 0xEE: s.kbc_put(0xEE)
        else: s.kbc_put(0xFA)

    # ---------------------------------------------------------- ports
    def _in(s, uc, port, size, ud):
        s.t += NS_PER_IO
        v = s.inb(port)
        if size == 2: v = v | (s.inb(port + 1) << 8)
        if s.trace_ports: s.portlog.append(('in', hex(port), hex(v)))
        return v
    def inb(s, port):
        if port == 0x71:
            i = s.cidx & 0x7F
            if i == 0x0A:
                # UIP high for ~2 ms out of every second
                return (s.cmos[0x0A] & 0x7F) | (0x80 if (s.t % 1_000_000_000) < 2_000_000 else 0)
            if i == 0x0C: return 0x00
            return s.cmos[i]
        if port == 0x70: return s.cidx
        if port == 0x60:
            if s.kbc_out:
                b = s.kbc_out.pop(0)
                if s.kbc_out and s.kbc_cmdbyte & 1: s.raise_irq(1)
                return b
            return 0
        if port == 0x64:
            if not s.kbc_out and s.key_at and s.key_at[0][0] <= s.t:
                s.kbc_put(s.key_at.pop(0)[1])
            return 0x1C | (1 if s.kbc_out else 0)
        if port == 0x61:
            r = (s.t // 15000) & 1          # refresh toggles every 15 us
            return (s.p61 & 0x0F) | (r << 4) | (s.pit_out(2) << 5)
        if port in (0x40, 0x41, 0x42):
            ch = port - 0x40; p = s.pit[ch]
            v = p['latch'] if p['latch'] is not None else s.pit_count(ch)
            if p['rw'] == 1: p['latch'] = None; return v & 0xFF
            if p['rw'] == 2: p['latch'] = None; return v >> 8
            p['flip'] ^= 1
            if p['flip']: return v & 0xFF
            p['latch'] = None; return v >> 8
        if port in (0x20, 0xA0):
            c = s.pic[port == 0xA0]
            return c['isr'] if c['read_isr'] else c['irr']
        if port in (0x21, 0xA1): return s.pic[port == 0xA1]['imr']
        if 0x00 <= port <= 0x0F or 0xC0 <= port <= 0xDF:
            if port in (0x08, 0xD0): return 0
            key = port
            if (port <= 0x07) or (0xC0 <= port <= 0xCF):
                ctl = 0 if port <= 0x07 else 1
                ff = s.dmaff[ctl]; s.dmaff[ctl] ^= 1
                return s.dma.get((port, ff), 0)
            return s.dma.get((port, 0), 0)
        if 0x80 <= port <= 0x8F: return s.page[port - 0x80]
        if port == 0x24: return s.opti[s.oidx]
        if port == 0x23: return s.cyrix[s.oidx]
        if port == 0x92: return 0x02
        if 0x1F0 <= port <= 0x1F7 or port == 0x3F6:
            d = s.ide
            if d is None: return 0x00 if s.ides[0] else 0xFF
            if port in (0x1F7, 0x3F6): return d.status
            if port == 0x1F1: return 0x01 if d.log and d.log[-1] == 0x90 else 0
            if port == 0x1F0: return int.from_bytes(d.read_block(1), 'little')
            return d.r.get(port - 0x1F0, 0)
        if port == 0x3F4:
            return 0xD0 if s.fdc_res else 0x80
        if port == 0x3F5:
            return s.fdc_res.pop(0) if s.fdc_res else 0
        if port == 0x3F7: return 0x00
        if port == 0x3DA or port == 0x3BA:
            return 0x09 if (s.t // 16000) & 1 else 0x00
        if port in (0x3D5, 0x3B5):
            base = 0x3D0 if port == 0x3D5 else 0x3B0
            if base != s.crtc_base: return 0xFF          # no adapter at that address
            return s.crtc[s.crtc_idx & 0x1F]
        return 0xFF
    def _out(s, uc, port, size, val, ud):
        s.t += NS_PER_IO
        if s.trace_ports: s.portlog.append(('out', hex(port), hex(val)))
        s.outb(port, val & 0xFF)
        if size == 2: s.outb(port + 1, (val >> 8) & 0xFF)
    def outb(s, port, v):
        if port == 0x80:
            s.p80.append((v, s.where_ip())); s.page[0] = v; return
        if port == 0x70: s.cidx = v & 0x7F; return
        if port == 0x71: s.cmos[s.cidx & 0x7F] = v; return
        if port == 0x64: s.kbc_cmd(v); return
        if port == 0x60: s.kbd_data(v); return
        if port == 0x61: s.p61 = v; return
        if port == 0x43:
            ch = v >> 6
            if ch == 3: return
            p = s.pit[ch]
            if (v >> 4) & 3 == 0:
                p['latch'] = s.pit_count(ch); p['flip'] = 0; return
            p['rw'] = (v >> 4) & 3; p['mode'] = (v >> 1) & 7
            if p['mode'] > 5: p['mode'] -= 4
            p['flip'] = 0; p['wflip'] = 0; p['latch'] = None
            return
        if port in (0x40, 0x41, 0x42):
            ch = port - 0x40; p = s.pit[ch]
            if p['rw'] == 1: p['reload'] = v or 0x100; p['start'] = s.t
            elif p['rw'] == 2: p['reload'] = (v << 8) or 0x10000; p['start'] = s.t
            else:
                if p['wflip'] == 0: p['lo'] = v; p['wflip'] = 1
                else:
                    r = p['lo'] | (v << 8); p['reload'] = r or 0x10000; p['start'] = s.t; p['wflip'] = 0
                    if ch == 0: s.last_irq0 = s.t
            return
        if port in (0x20, 0xA0):
            c = s.pic[port == 0xA0]
            if v & 0x10: c['init'] = 1; c['imr'] = 0; c['isr'] = 0; c['irr'] = 0; c['icw4'] = v & 1; return
            if v & 0x08:                    # OCW3
                if v & 2: c['read_isr'] = bool(v & 1)
                return
            if v & 0x20:                    # EOI
                if v & 0x40: c['isr'] &= ~(1 << (v & 7))
                else:
                    for i in range(8):
                        if c['isr'] & (1 << i): c['isr'] &= ~(1 << i); break
            return
        if port in (0x21, 0xA1):
            c = s.pic[port == 0xA1]
            if c['init'] == 1: c['base'] = v & 0xF8; c['init'] = 2; return
            if c['init'] == 2: c['init'] = 3 if c.get('icw4') else 0; return
            if c['init'] == 3: c['init'] = 0; return
            c['imr'] = v; return
        if 0x00 <= port <= 0x0F or 0xC0 <= port <= 0xDF:
            if port in (0x0C, 0xD8): s.dmaff[0 if port == 0x0C else 1] = 0; return
            if port in (0x0D, 0xDA): s.dmaff[0 if port == 0x0D else 1] = 0; return
            if (port <= 0x07) or (0xC0 <= port <= 0xCF):
                ctl = 0 if port <= 0x07 else 1
                ff = s.dmaff[ctl]; s.dmaff[ctl] ^= 1
                s.dma[(port, ff)] = v; return
            s.dma[(port, 0)] = v; return
        if 0x81 <= port <= 0x8F: s.page[port - 0x80] = v; return
        if port == 0x92:
            if v & 1: s.request_reset()
            return
        if port in (0x3D4, 0x3B4): s.crtc_idx = v; return
        if port in (0x3D5, 0x3B5):
            if (0x3D0 if port == 0x3D5 else 0x3B0) == s.crtc_base: s.crtc[s.crtc_idx & 0x1F] = v
            return
        if port == 0x22: s.oidx = v; return
        if port == 0x24: s.opti[s.oidx] = v; return
        if port == 0x23: s.cyrix[s.oidx] = v; return
        if 0x1F1 <= port <= 0x1F7 or port == 0x3F6:
            if port == 0x1F6: s.ide_r6 = v
            d = s.ide
            if port == 0x3F6:
                for dd in s.ides:
                    if dd is None: continue
                    if (v & 4) and not (s.ide_ctl & 4): dd.status = 0x80
                    if not (v & 4) and (s.ide_ctl & 4): dd.status = 0x50; dd.log.append(0x90)
                s.ide_ctl = v; return
            if d is None: return
            if port == 0x3F6:
                if (v & 4) and not (s.ide_ctl & 4): d.status = 0x80
                if not (v & 4) and (s.ide_ctl & 4): d.status = 0x50; d.log.append(0x90)
                s.ide_ctl = v; return
            if port == 0x1F7:
                if d.command(v) and not (s.ide_ctl & 2): s.raise_irq(14)
                return
            d.r[port - 0x1F0] = v; return
        if port == 0x3F2:
            if (v & 4) and not (s.fdc_dor & 4):          # leaving reset: IRQ6, 4 x SENSE INT
                s.fdc_res = []; s.fdc_cmd = []; s.fdc_reset_sense = 4; s.raise_irq(6)
            s.fdc_dor = v; return
        if port == 0x3F5:
            s.fdc_cmd.append(v)
            c = s.fdc_cmd[0] & 0x1F
            need = {0x03: 3, 0x04: 2, 0x07: 2, 0x08: 1, 0x0F: 3, 0x10: 1, 0x0A: 2, 0x13: 4}.get(c, 1)
            if len(s.fdc_cmd) < need: return
            cmd = s.fdc_cmd; s.fdc_cmd = []
            if c == 0x08:
                if s.fdc_reset_sense:
                    d = 4 - s.fdc_reset_sense; s.fdc_reset_sense -= 1
                    s.fdc_res = [0xC0 | d, 0]
                elif s.fdc_st0 is not None:
                    s.fdc_res = [s.fdc_st0, s.fdc_pcn]; s.fdc_st0 = None
                else:
                    s.fdc_res = [0x80]
            elif c == 0x07:
                s.fdc_pcn = 0; s.fdc_st0 = 0x20 | (cmd[1] & 3); s.raise_irq(6)
            elif c == 0x0F:
                s.fdc_pcn = cmd[2]; s.fdc_st0 = 0x20 | (cmd[1] & 3); s.raise_irq(6)
            elif c == 0x04:
                s.fdc_res = [0x28 | (cmd[1] & 3) | (0x10 if s.fdc_pcn == 0 else 0)]
            elif c == 0x10:
                s.fdc_res = [0x80]
            elif c == 0x0A:
                s.fdc_res = [0x40 | (cmd[1] & 3), 0x01, 0, s.fdc_pcn, 0, 1, 2]; s.raise_irq(6)
            return

    def request_reset(s):
        s.reset_pending = True; s.resets = getattr(s, 'resets', 0) + 1; s.mu.emu_stop()
    def cpu_reset(s):
        uc = s.mu
        uc.reg_write(UC_X86_REG_CR0, 0x10)
        uc.reg_write(UC_X86_REG_IDTR, (0, 0, 0x3FF, 0))
        for r in (UC_X86_REG_DS, UC_X86_REG_ES, UC_X86_REG_SS, UC_X86_REG_FS, UC_X86_REG_GS):
            uc.reg_write(r, 0)
        uc.reg_write(UC_X86_REG_EFLAGS, 2)
        uc.reg_write(UC_X86_REG_CS, 0xF000); uc.reg_write(UC_X86_REG_EIP, 0xFFF0)
        uc.reg_write(UC_X86_REG_EDX, 0x0308)
        s.halted_at = None; s.reset_pending = False
    @property
    def ide(s):
        return s.ides[1 if s.ide_r6 & 0x10 else 0]
    def where_ip(s):
        return '%04x:%04x' % (s.mu.reg_read(UC_X86_REG_CS), s.mu.reg_read(UC_X86_REG_IP))

    # ---------------------------------------------------------- string I/O
    def _repio(s, uc, addr, size, ud):
        cx = uc.reg_read(UC_X86_REG_CX)
        if s.mu.mem_read(addr, 2) == b'\xf3\x6d':
            di = uc.reg_read(UC_X86_REG_DI); es = uc.reg_read(UC_X86_REG_ES)
            data = s.ide.read_block(cx * 2) if s.ide else b'\xff' * (cx * 2)
            uc.mem_write(lin(es, di), data)
            uc.reg_write(UC_X86_REG_DI, (di + cx * 2) & 0xFFFF)
        else:
            si = uc.reg_read(UC_X86_REG_SI); uc.reg_write(UC_X86_REG_SI, (si + cx * 2) & 0xFFFF)
            if s.ide: s.ide.status = 0x50; s.raise_irq(14)
        uc.reg_write(UC_X86_REG_CX, 0)
        uc.reg_write(UC_X86_REG_IP, (addr - 0xF0000 + 2) & 0xFFFF)
        s.t += cx * 1000

    # ---------------------------------------------------------- interrupts
    def seg_base(s, reg):
        uc = s.mu
        sel = uc.reg_read(reg)
        if not (uc.reg_read(UC_X86_REG_CR0) & 1): return sel << 4
        g = uc.reg_read(UC_X86_REG_GDTR)
        d = bytes(uc.mem_read(g[1] + (sel & ~7), 8))
        return d[2] | (d[3] << 8) | (d[4] << 16) | (d[7] << 24)
    def push16(s, v):
        uc = s.mu
        sp = (uc.reg_read(UC_X86_REG_SP) - 2) & 0xFFFF
        uc.mem_write(s.seg_base(UC_X86_REG_SS) + sp, struct.pack('<H', v & 0xFFFF)); uc.reg_write(UC_X86_REG_SP, sp)
    def push32(s, v):
        uc = s.mu
        sp = (uc.reg_read(UC_X86_REG_ESP) - 4) & 0xFFFFFFFF
        uc.mem_write(s.seg_base(UC_X86_REG_SS) + (sp & 0xFFFF), struct.pack('<I', v & 0xFFFFFFFF)); uc.reg_write(UC_X86_REG_ESP, sp)
    def deliver(s, vec):
        uc = s.mu
        fl = uc.reg_read(UC_X86_REG_EFLAGS)
        if uc.reg_read(UC_X86_REG_CR0) & 1:
            idt = uc.reg_read(UC_X86_REG_IDTR)
            g = bytes(uc.mem_read(idt[1] + vec * 8, 8))
            off = g[0] | (g[1] << 8) | (g[6] << 16) | (g[7] << 24)
            sel = g[2] | (g[3] << 8); typ = g[5] & 0x1F
            if typ in (0x0E, 0x0F):
                s.push32(fl); s.push32(uc.reg_read(UC_X86_REG_CS)); s.push32(uc.reg_read(UC_X86_REG_EIP))
            else:
                s.push16(fl); s.push16(uc.reg_read(UC_X86_REG_CS)); s.push16(uc.reg_read(UC_X86_REG_EIP))
                off &= 0xFFFF
            if typ in (0x06, 0x0E): fl &= ~0x200
            uc.reg_write(UC_X86_REG_EFLAGS, fl & ~0x100)
            uc.reg_write(UC_X86_REG_CS, sel); uc.reg_write(UC_X86_REG_EIP, off)
            s.pm_ints = getattr(s, 'pm_ints', 0) + 1
            return
        s.push16(fl); s.push16(uc.reg_read(UC_X86_REG_CS)); s.push16(uc.reg_read(UC_X86_REG_IP))
        uc.reg_write(UC_X86_REG_EFLAGS, fl & ~0x300)
        off, seg = struct.unpack('<HH', bytes(uc.mem_read(vec * 4, 4)))
        uc.reg_write(UC_X86_REG_CS, seg); uc.reg_write(UC_X86_REG_IP, off)
    def _intr(s, uc, intno, ud):
        ax = uc.reg_read(UC_X86_REG_AX); ah = ax >> 8
        if intno == 0x10:
            s._int10(uc, ah, ax & 0xFF); return
        if intno == 0x19 and s.stop_at_int19:
            s.stop = 'int19'; uc.emu_stop(); return
        if intno == 0x18:
            s.stop = 'int18'; uc.emu_stop(); return
        s.int_log.append((hex(intno), hex(ax), s.where_ip()))
        s.deliver(intno)
    stop_at_int19 = True

    # ---------------------------------------------------------- INT 10h (text)
    vbase = 0xB8000
    def _vram(s): return bytearray(s.mu.mem_read(s.vbase, 4000))
    def _int10(s, uc, ah, al):
        bx = uc.reg_read(UC_X86_REG_BX); cx = uc.reg_read(UC_X86_REG_CX); dx = uc.reg_read(UC_X86_REG_DX)
        if ah == 0x00:
            s.vmode = al & 0x7F
            s.vbase = 0xB0000 if s.vmode == 7 else 0xB8000
            uc.mem_write(s.vbase, b'\x20\x07' * 2000); s.cur = (0, 0)
            uc.mem_write(0x449, bytes([7 if s.vmode == 7 else 3])); uc.mem_write(0x44A, struct.pack('<H', 80))
            uc.mem_write(0x484, bytes([24]))
        elif ah == 0x01: s.curshape = cx
        elif ah == 0x02: s.cur = (dx >> 8, dx & 0xFF); uc.mem_write(0x450, bytes([dx & 0xFF, dx >> 8]))
        elif ah == 0x03:
            r, c = s.cur; uc.reg_write(UC_X86_REG_DX, (r << 8) | c); uc.reg_write(UC_X86_REG_CX, s.curshape)
        elif ah in (0x06, 0x07):
            v = s._vram(); attr = bx >> 8
            r0, c0, r1, c1 = cx >> 8, cx & 0xFF, min(dx >> 8, 24), min(dx & 0xFF, 79)
            n = al
            new = {}
            def cell(r, c): return v[(r * 80 + c) * 2:(r * 80 + c) * 2 + 2]
            for r in range(r0, r1 + 1):
                src = r + n if ah == 6 else r - n
                for c in range(c0, c1 + 1):
                    new[(r, c)] = bytes([0x20, attr]) if (n == 0 or src < r0 or src > r1) else bytes(cell(src, c))
            for (r, c), b in new.items(): v[(r * 80 + c) * 2:(r * 80 + c) * 2 + 2] = b
            uc.mem_write(s.vbase, bytes(v))
        elif ah == 0x08:
            r, c = s.cur; b = uc.mem_read(s.vbase + (r * 80 + c) * 2, 2)
            uc.reg_write(UC_X86_REG_AX, b[0] | (b[1] << 8))
        elif ah in (0x09, 0x0A):
            r, c = s.cur; p = r * 80 + c
            for i in range(cx):
                if p + i >= 2000: break
                if ah == 0x09: uc.mem_write(s.vbase + (p + i) * 2, bytes([al, bx & 0xFF]))
                else: uc.mem_write(s.vbase + (p + i) * 2, bytes([al]))
        elif ah == 0x0E:
            r, c = s.cur
            if al == 0x0D: c = 0
            elif al == 0x0A: r += 1
            elif al == 0x08: c = max(0, c - 1)
            elif al == 0x07: pass
            else:
                uc.mem_write(s.vbase + (r * 80 + c) * 2, bytes([al])); c += 1
                if c >= 80: c = 0; r += 1
            if r >= 25:
                v = s._vram(); v = v[160:] + bytearray(b'\x20\x07' * 80); uc.mem_write(s.vbase, bytes(v)); r = 24
            s.cur = (r, c); uc.mem_write(0x450, bytes([c, r]))
        elif ah == 0x0F: uc.reg_write(UC_X86_REG_AX, 0x5000 | (7 if s.vmode == 7 else 3)); uc.reg_write(UC_X86_REG_BX, bx & 0xFF)
        elif ah == 0x12: uc.reg_write(UC_X86_REG_BX, 0x0003); uc.reg_write(UC_X86_REG_CX, 0x0009)
        elif ah == 0x1A:
            if al == 0: uc.reg_write(UC_X86_REG_AX, 0x001A); uc.reg_write(UC_X86_REG_BX, 0x0008)
        elif ah == 0x13:
            es = uc.reg_read(UC_X86_REG_ES); bp = uc.reg_read(UC_X86_REG_BP)
            txt = bytes(uc.mem_read(lin(es, bp), cx))
            r, c = dx >> 8, dx & 0xFF
            for ch in txt:
                if r * 80 + c < 2000: uc.mem_write(s.vbase + (r * 80 + c) * 2, bytes([ch, bx & 0xFF]))
                c += 1
            if al & 1: s.cur = (r, c)

    # ---------------------------------------------------------- run loop
    def type_key(s, scancode, at_ms=None):
        """Queue a make+break pair to arrive at virtual time at_ms (default: now)."""
        t = s.t if at_ms is None else int(at_ms * 1e6)
        s.key_at.append((t, scancode)); s.key_at.append((t + 20_000_000, scancode | 0x80))
        s.key_at.sort()
    def run(s, max_ms=60000, chunk=2000, until=None):
        mu = s.mu
        t_end = s.t + max_ms * 1_000_000
        s.stop = None
        while s.stop is None and s.t < t_end:
            # timer
            p0 = s.pit[0]
            period = p0['reload'] * 1_000_000_000 // PIT_HZ
            if period > 0 and s.t - s.last_irq0 >= period:
                s.last_irq0 += period * ((s.t - s.last_irq0) // period)
                s.raise_irq(0)
            # keyboard
            if s.key_at and s.key_at[0][0] <= s.t and not s.kbc_out and not (s.kbc_cmdbyte & 0x10):
                s.kbc_put(s.key_at.pop(0)[1])
            fl = mu.reg_read(UC_X86_REG_EFLAGS)
            if fl & 0x200:
                v = s.pending_vector()
                if v is not None:
                    if s.halted_at is not None:
                        mu.reg_write(UC_X86_REG_IP, (mu.reg_read(UC_X86_REG_IP) + 1) & 0xFFFF)
                        s.halted_at = None
                    s.deliver(v)
            elif s.halted_at is not None and not (fl & 0x200):
                s.stop = 'hlt with IF=0 at ' + s.where_ip(); break
            if s.halted_at is not None:
                s.t += 50_000; continue
            start = lin(mu.reg_read(UC_X86_REG_CS), mu.reg_read(UC_X86_REG_IP)) if s.pc is None else s.pc
            if s.pc is not None:
                start = s.pc; s.pc = None
            else:
                # Unicorn (16-bit mode) sets EIP = begin - CS.selector*16, in real and protected mode
                start = (mu.reg_read(UC_X86_REG_CS) << 4) + mu.reg_read(UC_X86_REG_EIP)
            try:
                mu.emu_start(start, -1 & 0xFFFFFFFFFFFF, count=chunk)
            except UcError as e:
                ip = mu.reg_read(UC_X86_REG_EIP)
                if e.errno == UC_ERR_INSN_INVALID or True:
                    op = bytes(mu.mem_read(lin(mu.reg_read(UC_X86_REG_CS), ip), 1)) if not (mu.reg_read(UC_X86_REG_CR0) & 1) else b'?'
                if op == b'\xf4':
                    s.halted_at = s.where_ip(); continue
                s.stop = 'error %s at %s' % (e, s.where_ip()); break
            s.t += chunk * NS_PER_INSN
            s.insn_total += chunk
            if getattr(s, 'reset_pending', False): s.cpu_reset(); continue
            if until and until(s): s.stop = 'until'; break
            # HLT detection: Unicorn stops on HLT; check the byte before IP
            ip = mu.reg_read(UC_X86_REG_EIP)
            if not (mu.reg_read(UC_X86_REG_CR0) & 1):
                cs = mu.reg_read(UC_X86_REG_CS)
                prev = mu.mem_read(lin(cs, (ip - 1) & 0xFFFF), 1)
                cur = mu.mem_read(lin(cs, ip), 1)
                if cur == b'\xf4' and s._stopped_on_hlt(): s.halted_at = s.where_ip()
        return s.stop or 'timeout'
    def _stopped_on_hlt(s):
        return True
    def _csbase(s):
        # protected mode: read the CS descriptor base from the GDT
        gdtr = s.mu.reg_read(UC_X86_REG_GDTR)
        base_gdt = gdtr[1]
        sel = s.mu.reg_read(UC_X86_REG_CS)
        d = bytes(s.mu.mem_read(base_gdt + (sel & ~7), 8))
        return d[2] | (d[3] << 8) | (d[4] << 16) | (d[7] << 24)

    # ---------------------------------------------------------- output
    def cells(s):
        v = s._vram(); return [(v[i], v[i + 1]) for i in range(0, 4000, 2)]
    def text(s):
        cl = s.cells()
        return '\n'.join(bytes(c for c, a in cl[r * 80:(r + 1) * 80]).decode('cp437').rstrip() for r in range(25))
    def png(s, path):
        from PIL import Image
        cl = s.cells(); img = Image.new('RGB', (720, 400)); px = img.load()
        for i, (c, a) in enumerate(cl):
            r, col = divmod(i, 80)
            fg = CGA[a & 0x0F]; bg = CGA[(a >> 4) & 0x07]
            g = GLYPHS[c]
            for y in range(16):
                row = g[y]
                for x in range(9):
                    px[col * 9 + x, r * 16 + y] = fg if (row >> (8 - x)) & 1 else bg
        img = img.resize((1440, 960), Image.NEAREST)
        img.save(path); return path

if __name__ == '__main__':
    rom = open(sys.argv[1], 'rb').read()
    p = Post(rom)
    t0 = time.time()
    r = p.run(max_ms=int(sys.argv[2]) if len(sys.argv) > 2 else 20000)
    print('stop:', r, 'virtual %.2f s' % (p.t / 1e9), 'real %.1f s' % (time.time() - t0), 'insn', p.insn_total)
    print('POST codes:', ' '.join('%02x' % c for c, _ in p.p80[-40:]))
    print('last:', p.p80[-3:])
    print(p.text())
