#!/usr/bin/env python3
"""Run the ROM's own INT 19h boot path (F000:F758) against a simulated drive
whose sector 0 carries an AA55 signature, and show whether the BIOS reads
the MBR from LBA 0, accepts it and jumps to 0000:7C00.

    python3 py/boot_test.py      (needs: pip install unicorn)
"""
from ide_harness import *

orig    = load_rom(ORIG_ROM)
patched = load_rom(PATCHED_ROM)

mbr = bytearray(512); mbr[0x1FE:0x200] = b'\x55\xaa'; mbr[0:2] = b'\xfa\x33'
cases = (('ORIGINAL', orig,    (998, 16, 32)),
         ('PATCHED',  patched, (998, 16, 32)),
         ('PATCHED',  patched, (3875, 16, 63)),
         ('PATCHED',  patched, (15506, 16, 63)),
         ('PATCHED',  patched, (16000, 16, 63)))
for nm, rom, geo in cases:
    e = Emu(rom, *geo); e.d.img[0] = bytes(mbr)
    mu = e.mu
    # CMOS: any read returns 0 (0x0E bit 3 clear = fixed disk passed POST)
    mu.mem_write(lin(0x30, 0x100 - 6), struct.pack('<HHH', 0x602, 0xF000, 0x0202))
    mu.reg_write(UC_X86_REG_SS, 0x30); mu.reg_write(UC_X86_REG_SP, 0x100)
    mu.reg_write(UC_X86_REG_CS, 0xF000); mu.reg_write(UC_X86_REG_DS, 0x40); mu.reg_write(UC_X86_REG_ES, 0)
    e.d.log = []; e.n = 0; e.minsp = 0xffff
    try:
        mu.emu_start(lin(0xF000, 0xF758), 0x7C00, count=2000000)   # stop when it jumps to 0:7C00
        ip = mu.reg_read(UC_X86_REG_IP); cs = mu.reg_read(UC_X86_REG_CS)
        lbas = [entry[-1] for entry in e.d.log]
        sig = bytes(mu.mem_read(0x7C00 + 0x1FE, 2)).hex()
        ok = (cs << 4) + ip == 0x7C00 and sig == '55aa' and lbas and lbas[-1] == 0
        print(f"{'OK  ' if ok else 'FAIL'} {nm:8s} {geo[0]:5d}/{geo[1]}/{geo[2]}  "
              f"stopped at {cs:04x}:{ip:04x}  MBR read from LBA {lbas}  signature {sig}")
    except UcError as ex:
        print(f"FAIL {nm:8s} {geo}  emulator error {ex}  ip={mu.reg_read(UC_X86_REG_IP):04x}")
