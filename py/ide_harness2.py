"""ide_harness2.py -- Unicorn-based real-mode harness for the SY-019L1 ROM.
Runs real INT 13h calls through the genuine dispatcher against either ROM,
with a simulated IDE drive (optionally backed by sector images), CMOS reads,
software-interrupt dispatch via the IVT, IDE register/LBA logging, and a
stack low-water mark.  BIOS boot stack (SS:SP=0030:0100) is used by default.
"""
import struct, sys
from unicorn import *
from unicorn.x86_const import *
def lin(s,o): return (s<<4)+o
class Drive:
    def __init__(s,C,H,S):
        s.C,s.H,s.S=C,H,S; s.r={2:1,3:1,4:0,5:0,6:0xA0}; s.log=[]; s.img={}; s.cur_lba=None
    def lba(s):
        c=s.r[4]|(s.r[5]<<8); h=s.r[6]&0xF; sec=s.r[3]
        return c,h,sec,(c*s.H+h)*s.S+sec-1
class Emu:
    def __init__(s,rom,C,H,S):
        s.mu=mu=Uc(UC_ARCH_X86,UC_MODE_16); mu.mem_map(0,0x100000)
        mu.mem_write(lin(0xF000,0),rom)
        fd=bytearray(16); struct.pack_into('<H',fd,0,C); fd[2]=H; fd[0xE]=S; struct.pack_into('<H',fd,0xC,C)
        mu.mem_write(lin(0x60,0),bytes(fd))
        mu.mem_write(0x104,struct.pack('<HH',0,0x60)); mu.mem_write(0x118,struct.pack('<HH',0,0x60))
        mu.mem_write(0x4C,struct.pack('<HH',0xA3E7,0xF000))
        mu.mem_write(lin(0x40,0x75),b'\x01')
        s.d=Drive(C,H,S); s.p61=0
        mu.hook_add(UC_HOOK_INSN,s.inp,aux1=UC_X86_INS_IN)
        mu.hook_add(UC_HOOK_INSN,s.outp,aux1=UC_X86_INS_OUT)
        mu.hook_add(UC_HOOK_INTR,s.intr)
        mu.hook_add(UC_HOOK_CODE,s.code)
        mu.reg_write(UC_X86_REG_SS,0x30); mu.reg_write(UC_X86_REG_SP,0x100)
        s.minsp=0xffff
        s.n=0
    def code(s,uc,addr,size,ud):
        s.n+=1
        sp=uc.reg_read(UC_X86_REG_SP)
        if sp<s.minsp: s.minsp=sp
        off=addr-0xF0000
        if off in (0xA733,0xA63D,0xA797):
            # fake the rep insw/outsw data transfer (Unicorn doesn't hook INS/OUTS)
            uc.reg_write(UC_X86_REG_CX,0)
            if off==0xA733:
                di=uc.reg_read(UC_X86_REG_DI); es=uc.reg_read(UC_X86_REG_ES)
                lba=s.d.cur_lba if s.d.cur_lba is not None else 0
                uc.mem_write(lin(es,di), s.d.img.get(lba, b'\x00'*512))
                s.d.cur_lba=lba+1
                uc.reg_write(UC_X86_REG_DI,(di+512)&0xffff)
            else:
                si=uc.reg_read(UC_X86_REG_SI); uc.reg_write(UC_X86_REG_SI,(si+512)&0xffff)
            uc.reg_write(UC_X86_REG_IP,off+2)
    def inp(s,uc,port,size,ud):
        if port==0x1F7: return 0x58
        if port==0x61: s.p61^=0x10; return s.p61
        if port==0x71: return 0
        if port in (0x1F1,): return 1
        if port==0x1F0: return 0
        return 0xFF
    def outp(s,uc,port,size,val,ud):
        if 0x1F2<=port<=0x1F6: s.d.r[port-0x1F0]=val
        if port==0x1F7:
            c,h,sec,l=s.d.lba()
            s.d.log.append((hex(val),'cnt',s.d.r[2],'C',c,'H',h,'S',sec,'drvhd',hex(s.d.r[6]),'LBA',l))
            s.d.cur_lba=l
            uc.mem_write(lin(0x40,0x8E),b'\xff')
    def intr(s,uc,intno,ud):
        if intno in (0x40,0x15,0x10,0x16,0x12,0x11):
            uc.reg_write(UC_X86_REG_EFLAGS,uc.reg_read(UC_X86_REG_EFLAGS)&~1); return
        vec=struct.unpack('<HH',bytes(uc.mem_read(intno*4,4)))
        if vec==(0,0):
            uc.reg_write(UC_X86_REG_EFLAGS,uc.reg_read(UC_X86_REG_EFLAGS)&~1); return
        sp=uc.reg_read(UC_X86_REG_SP); ss=uc.reg_read(UC_X86_REG_SS)
        fl=uc.reg_read(UC_X86_REG_EFLAGS)&0xFFFF
        cs=uc.reg_read(UC_X86_REG_CS); ip=uc.reg_read(UC_X86_REG_IP)
        for v in (fl,cs,ip):
            sp=(sp-2)&0xffff; uc.mem_write(lin(ss,sp),struct.pack('<H',v))
        uc.reg_write(UC_X86_REG_SP,sp)
        uc.reg_write(UC_X86_REG_CS,vec[1]); uc.reg_write(UC_X86_REG_IP,vec[0])
    def call(s,ax,cx,dx,bx=0x1000,es=0x2000,e32=0):
        mu=s.mu
        mu.mem_write(lin(0xF000,0x602),b'\xf4')
        mu.reg_write(UC_X86_REG_SP,0x100)
        mu.mem_write(lin(0x30,0x100-6),struct.pack('<HHH',0x602,0xF000,0x0202))
        mu.reg_write(UC_X86_REG_SP,0x100-6)
        for r,v in ((UC_X86_REG_EAX,ax|e32),(UC_X86_REG_ECX,cx|e32),(UC_X86_REG_EDX,dx|e32),(UC_X86_REG_BX,bx),(UC_X86_REG_ES,es),(UC_X86_REG_DS,0x40),(UC_X86_REG_BP,0x1234)):
            mu.reg_write(r,v)
        s.d.log=[]; s.n=0; s.minsp=0xffff
        mu.reg_write(UC_X86_REG_CS,0xF000)
        mu.emu_start(lin(0xF000,0xA3E7),lin(0xF000,0x602),count=3000000)
        fl=mu.reg_read(UC_X86_REG_EFLAGS)
        regs={n:hex(s.mu.reg_read(r)) for n,r in (('eax',UC_X86_REG_EAX),('ebx',UC_X86_REG_EBX),('ecx',UC_X86_REG_ECX),('edx',UC_X86_REG_EDX),('esi',UC_X86_REG_ESI),('edi',UC_X86_REG_EDI),('ebp',UC_X86_REG_EBP),('esp',UC_X86_REG_ESP),('es',UC_X86_REG_ES),('ds',UC_X86_REG_DS))}
        regs['fl']=hex(fl&0x8d5); regs['ide']=list(s.d.log); regs['bda74']=bytes(s.mu.mem_read(lin(0x40,0x74),4)).hex()
        return regs
        return dict(eax=hex(mu.reg_read(UC_X86_REG_EAX)),ecx=hex(mu.reg_read(UC_X86_REG_ECX)),edx=hex(mu.reg_read(UC_X86_REG_EDX)),bp=hex(mu.reg_read(UC_X86_REG_BP)),cf=fl&1,n=s.n,ide=s.d.log,minsp=s.minsp,depth=0x100-s.minsp)
