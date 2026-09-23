import struct
from unicorn import *
from unicorn.x86_const import *

ROM = open('PATCHED.BIN', 'rb').read()  # adjust path: point this at whichever
                                          # ROM you want to test (original
                                          # source.bin, or a patched build)
assert len(ROM) == 0x10000

def seg_off(seg, off):
    return (seg << 4) + off

class IdeSim:
    """A minimal, 'always cooperative' simulated IDE drive: reports ready/DRQ
    promptly, never errors, so any hang we see is a pure BIOS-logic issue,
    not a real drive rejecting an invalid command."""
    def __init__(self, real_cyl, real_heads, real_sect, backing=None):
        self.real_cyl = real_cyl
        self.real_heads = real_heads
        self.real_sect = real_sect
        self.regs = {'sect_count':1,'sector':1,'cyl_lo':0,'cyl_hi':0,'drvhead':0xA0}
        self.backing = backing if backing is not None else bytearray(real_cyl*real_heads*real_sect*512)
        self.data_fifo = []
        self.last_cmd = None

    def chs_to_lba(self):
        cyl = self.regs['cyl_lo'] | (self.regs['cyl_hi']<<8)
        head = self.regs['drvhead'] & 0x0F  # only low 4 bits are wired on real 4-bit head reg,
                                             # BUT our patch is supposed to bypass that limit via
                                             # the repurposed cyl-high full byte -- so on a REAL
                                             # drive only 0-15 ever reaches here structurally.
        sect = self.regs['sector']
        return (cyl*self.real_heads + head)*self.real_sect + (sect-1)

    def io_read(self, uc, port, size):
        if port == 0x1F7:  # status
            return 0x58  # DRDY|DSC|DRQ, no error, not busy
        if port == 0x1F0:
            if self.data_fifo:
                return self.data_fifo.pop(0)
            return 0
        if port == 0x1F1:
            return 0
        if port == 0x61:
            # simulate the refresh-toggle bit (bit4) flipping every read,
            # like real AT hardware does at a fixed ~15us rate -- otherwise
            # the BIOS's generic microsecond-delay helper spins forever
            self._p61 = getattr(self, '_p61', 0) ^ 0x10
            return self._p61
        return 0xFF

    def io_write(self, uc, port, size, value):
        if port == 0x1F2: self.regs['sect_count'] = value
        elif port == 0x1F3: self.regs['sector'] = value
        elif port == 0x1F4: self.regs['cyl_lo'] = value
        elif port == 0x1F5: self.regs['cyl_hi'] = value
        elif port == 0x1F6: self.regs['drvhead'] = value
        elif port == 0x1F7:
            self.last_cmd = value
            lba = self.chs_to_lba()
            if lba < 0 or lba >= self.real_cyl*self.real_heads*self.real_sect:
                self.oob_hit = getattr(self,'oob_hit',0)+1
        elif port == 0x3F6:
            pass

def run_ah_sequence(real_cyl, real_heads, real_sect, calls, timeout_us=3_000_000, verbose=False):
    """calls: list of dicts like {'ah':8,'dl':0x80} or {'ah':4,'al':1,'dl':0x80,'cx':.., 'dx':..}
       If cx/dx omitted, whatever is currently in the (persistent across calls) register state is used,
       mimicking POST reusing leftover registers between back-to-back INT13h calls."""
    mu = Uc(UC_ARCH_X86, UC_MODE_16)
    mu.mem_map(0, 0x100000)
    mu.mem_write(seg_off(0xF000, 0), ROM)

    fdpt_seg, fdpt_off = 0x0060, 0x0000  # arbitrary low-RAM scratch area for our FDPT
    fdpt = bytearray(16)
    struct.pack_into('<H', fdpt, 0, real_cyl)
    fdpt[2] = real_heads
    fdpt[0xE] = real_sect
    mu.mem_write(seg_off(fdpt_seg, fdpt_off), bytes(fdpt))

    # IVT[0x41] (offset 0x104) -> far ptr to our FDPT, for drive 0 (DL even => this vector per 0xA910)
    mu.mem_write(seg_off(0,0x104), struct.pack('<HH', fdpt_off, fdpt_seg))
    # IVT[0x46] (offset 0x118) -> same, for drive 1 just in case
    mu.mem_write(seg_off(0,0x118), struct.pack('<HH', fdpt_off, fdpt_seg))
    # IVT[0x13] (offset 0x4C) -> the REAL INT13h dispatcher entry we found via disassembly
    INT13_ENTRY = 0xA3E7
    mu.mem_write(seg_off(0,0x4C), struct.pack('<HH', INT13_ENTRY, 0xF000))

    # BDA 0040:0075 = number of hard disks installed. Real POST sets this
    # based on CMOS "Hard Disk Type != None" before any INT13h HDD call is
    # ever made; our isolated harness must too, or the dispatcher's own
    # drive-count sanity check (0xA924) legitimately rejects the call.
    mu.mem_write(seg_off(0x0040, 0x0075), bytes([1]))

    ide = IdeSim(real_cyl, real_heads, real_sect)

    def hook_io_in(uc, port, size, ud):
        return ide.io_read(uc, port, size)
    def hook_io_out(uc, port, size, value, ud):
        ide.io_write(uc, port, size, value)

    mu.hook_add(UC_HOOK_INSN, hook_io_in, aux1=UC_X86_INS_IN)
    mu.hook_add(UC_HOOK_INSN, hook_io_out, aux1=UC_X86_INS_OUT)

    def hook_intr(uc, intno, ud):
        if intno == 0x15:
            # AH=90h/91h "Device Busy/Interrupt Complete" -- an optional
            # multitasking hook. Real BIOSes of this era provide a stub that
            # just returns carry-set ("not supported"); simulate that
            # directly rather than needing to locate/emulate the real stub.
            ah = (uc.reg_read(UC_X86_REG_AX) >> 8) & 0xFF
            if ah in (0x90, 0x91):
                # the caller treats carry-set as a FATAL error (AH=0x80),
                # so the correct default (no OS/TSR hooked) behavior must be
                # carry-clear -- a plain acknowledgment, not "unsupported"
                fl = uc.reg_read(UC_X86_REG_EFLAGS) & ~0x1
                uc.reg_write(UC_X86_REG_EFLAGS, fl)
                return
        # Unicorn doesn't auto-dispatch real-mode software interrupts; do it
        # ourselves: read the far pointer from the IVT, push flags/cs/ip
        # (ip already points PAST the 2-byte "int imm8" since Unicorn has
        # already advanced it by the time this hook fires), jump there.
        ivt_off = intno * 4
        lo = uc.mem_read(ivt_off, 4)
        target_off, target_seg = struct.unpack('<HH', lo)
        sp = uc.reg_read(UC_X86_REG_SP)
        ss = uc.reg_read(UC_X86_REG_SS)
        flags = uc.reg_read(UC_X86_REG_EFLAGS) & 0xFFFF
        cs = uc.reg_read(UC_X86_REG_CS)
        ip = uc.reg_read(UC_X86_REG_IP)
        sp -= 2; uc.mem_write(seg_off(ss, sp), struct.pack('<H', flags))
        sp -= 2; uc.mem_write(seg_off(ss, sp), struct.pack('<H', cs))
        sp -= 2; uc.mem_write(seg_off(ss, sp), struct.pack('<H', ip))
        uc.reg_write(UC_X86_REG_SP, sp)
        uc.reg_write(UC_X86_REG_CS, target_seg)
        uc.reg_write(UC_X86_REG_IP, target_off)

    mu.hook_add(UC_HOOK_INTR, hook_intr)

    # stack + segs, mimicking POST's own SS:SP=0030:0100-ish window (from the reset trace) --
    # use a distinct area so we don't clobber our FDPT/IVT setup
    ss = 0x0070
    sp = 0x0400
    mu.reg_write(UC_X86_REG_SS, ss)
    mu.reg_write(UC_X86_REG_SP, sp)
    mu.reg_write(UC_X86_REG_CS, 0xF000)
    mu.reg_write(UC_X86_REG_DS, 0x0040)
    mu.reg_write(UC_X86_REG_ES, 0x0040)

    # HLT sentinel we jump to after each simulated "INT 13h" returns
    DONE = 0x0500
    mu.mem_write(seg_off(0xF000, DONE), b'\xF4')  # HLT

    trace = []
    def hook_code(uc, address, size, ud):
        if len(trace) < 20000:
            trace.append(address - seg_off(0xF000,0))

    h = mu.hook_add(UC_HOOK_CODE, hook_code)

    results = []
    for i, call in enumerate(calls):
        ah = call['ah']
        dl = call.get('dl', 0x80)
        al = call.get('al', 1)
        cx = call.get('cx', mu.reg_read(UC_X86_REG_CX))
        dx_h = call.get('dh', (mu.reg_read(UC_X86_REG_DX)>>8)&0xFF)

        # Build a tiny stub: mov ax,AHAL / mov cx,CX / mov dx,DXH:DL / int 13h / hlt
        stub = bytearray()
        stub += bytes([0xB8, al, ah])              # mov ax, ah*256+al  (AL=al, AH=ah)
        stub += bytes([0xB9]) + struct.pack('<H', cx)   # mov cx, cx
        stub += bytes([0xBA, dl, dx_h])             # mov dx, dh:dl  (DL=dl,DH=dh)
        stub += bytes([0xCD, 0x13])                 # int 0x13
        stub += bytes([0xF4])                        # hlt

        stub_addr = 0x0600
        mu.mem_write(seg_off(0xF000, stub_addr), bytes(stub))
        mu.reg_write(UC_X86_REG_IP, stub_addr)

        trace.clear()
        try:
            mu.emu_start(seg_off(0xF000, stub_addr), seg_off(0xF000, 0xFFFF), timeout=timeout_us)
        except UcError as e:
            results.append({'call':call, 'error':str(e), 'oob_hits':getattr(ide,'oob_hit',0)})
            continue

        ip = mu.reg_read(UC_X86_REG_IP)
        hlt_addr = stub_addr + len(stub) - 1
        completed = (ip == hlt_addr) or (ip == hlt_addr+1)
        final_ax = mu.reg_read(UC_X86_REG_AX)
        final_cx = mu.reg_read(UC_X86_REG_CX)
        final_dx = mu.reg_read(UC_X86_REG_DX)
        results.append({
            'call': call, 'completed': completed, 'instructions_executed': len(trace),
            'final_ax': hex(final_ax), 'final_cx': hex(final_cx), 'final_dx': hex(final_dx),
            'oob_hits': getattr(ide,'oob_hit',0),
            'last_trace_addrs': [hex(a) for a in trace[-15:]] if not completed else None,
        })
    return results

if __name__ == '__main__':
    print("=== 250MB drive: AH=8 GetParams then AH=4 Verify, EXACTLY as POST's own cluster does ===")
    r = run_ah_sequence(998, 16, 32, [
        {'ah': 8, 'dl': 0x80},
        {'ah': 4, 'al': 1, 'dl': 0x80},   # cx/dh left over from AH=8 call, like real POST code
    ])
    for x in r:
        print(x)
