# Runs the ROM's own INT 19h boot path (F758) against a simulated drive whose
# sector 0 carries an AA55 signature; shows whether the BIOS accepts the MBR.
import os
if not os.path.exists('orig.bin'): open('orig.bin','wb').write(bytes.fromhex(''.join(open('BIOS_HEX.txt').read().split())))
from ide_harness2 import *
o=open('orig.bin','rb').read(); v4=open('SY019L1_27C512_CHSPATCH_v5.BIN','rb').read()
mbr=bytearray(512); mbr[0x1FE:0x200]=b'\x55\xaa'; mbr[0:2]=b'\xfa\x33'
for nm,rom,geo in (('ORIG',o,(998,16,32)),('v5',v4,(998,16,32)),('v5',v4,(3875,16,63)),('v5',v4,(15506,16,63))):
    e=Emu(rom,*geo); e.d.img[0]=bytes(mbr)
    mu=e.mu
    # CMOS: any read returns 0 (0x0E bit3 clear)
    mu.mem_write(lin(0x30,0x100-6),struct.pack('<HHH',0x602,0xF000,0x0202))
    mu.reg_write(UC_X86_REG_SS,0x30); mu.reg_write(UC_X86_REG_SP,0x100)
    mu.reg_write(UC_X86_REG_CS,0xF000); mu.reg_write(UC_X86_REG_DS,0x40); mu.reg_write(UC_X86_REG_ES,0)
    e.d.log=[]; e.n=0; e.minsp=0xffff
    trace=[]
    try:
        mu.emu_start(lin(0xF000,0xF758), 0x7C00, count=2000000)   # stop when it jumps to 0:7C00
        ip=mu.reg_read(UC_X86_REG_IP); cs=mu.reg_read(UC_X86_REG_CS)
        print(nm,geo,'stopped at %04x:%04x'%(cs,ip),'ide:',e.d.log,'mem@7C00:',bytes(mu.mem_read(0x7C00,4)).hex(), 'sig:',bytes(mu.mem_read(0x7C00+0x1FE,2)).hex())
    except UcError as ex:
        print(nm,geo,'ERR',ex,'ip=%04x'%mu.reg_read(UC_X86_REG_IP),e.d.log)
