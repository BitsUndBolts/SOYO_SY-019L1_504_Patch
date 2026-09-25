"""Helper: run INT 13h calls on a Post() machine after POST reached INT 19h."""
import struct
from unicorn.x86_const import *

_slot = 0
def call13(p, ax, bx=0, cx=0, dx=0x80, si=0, dap=None, buf_at=0x1000, buf=None, max_ms=5000):
    """Run one INT 13h at 0:7C00 (DS=ES=0). dap: bytes placed at 0:0500 (SI=0500).
    Returns dict ax,bx,cx,dx,cf and the 0:0500 DAP / buffer afterwards."""
    mu = p.mu
    if dap is not None:
        mu.mem_write(0x500, dap); si = 0x500
    if buf is not None:
        mu.mem_write(buf_at, buf)
    w = lambda v: struct.pack('<H', v & 0xFFFF)
    code = (b'\x31\xc0\x8e\xd8\x8e\xc0'
            + b'\xb8' + w(ax) + b'\xbb' + w(bx) + b'\xb9' + w(cx) + b'\xba' + w(dx) + b'\xbe' + w(si)
            + b'\xcd\x13\x9c'
            + b'\xa3\x00\x06\x89\x1e\x02\x06\x89\x0e\x04\x06\x89\x16\x06\x06\x58\xa3\x08\x06'
            + b'\xcd\x18')
    global _slot
    at = 0x7000 + (_slot % 32) * 0x40; _slot += 1     # fresh address: Unicorn caches translated code
    mu.mem_write(at, code)
    try: mu.ctl_remove_cache(at, at + 0x40)
    except Exception: pass
    mu.mem_write(0x600, b'\xEE' * 10)
    mu.reg_write(UC_X86_REG_CS, 0); mu.reg_write(UC_X86_REG_EIP, at)
    mu.reg_write(UC_X86_REG_SS, 0); mu.reg_write(UC_X86_REG_ESP, 0x7B00)
    mu.reg_write(UC_X86_REG_EFLAGS, 0x202)
    p.pc = None; p.halted_at = None
    r = p.run(max_ms=max_ms)
    ax_, bx_, cx_, dx_, fl = struct.unpack('<5H', bytes(mu.mem_read(0x600, 10)))
    return dict(stop=r, ax=ax_, bx=bx_, cx=cx_, dx=dx_, cf=fl & 1)

def dap(count, lba, seg=0, off=0x1000):
    return struct.pack('<BBHHHQ', 16, 0, count, off, seg, lba)
