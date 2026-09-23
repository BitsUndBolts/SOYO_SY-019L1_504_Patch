# Regression: AH=08h geometry + 44 reads per geometry, v4 vs v5 (256-head cap).
import os
if not os.path.exists('orig.bin'): open('orig.bin','wb').write(bytes.fromhex(''.join(open('BIOS_HEX.txt').read().split())))
from ide_harness2 import *
import random
o=open('orig.bin','rb').read(); v4=open('SY019L1_27C512_CHSPATCH_v4.BIN','rb').read(); v5=open('SY019L1_27C512_CHSPATCH_v5.BIN','rb').read()
geos=[(998,16,32),(1986,16,63),(3875,16,63),(7899,16,63),(8912,15,63),(15506,16,63),(16000,16,63),(16383,16,63),(1024,16,63),(2100,16,63)]
for C,H,S in geos:
  row=[]
  for nm,rom in (('v4',v4),('v5',v5)):
    e=Emu(rom,C,H,S)
    g=e.call(0x0800,0,0x0080,e32=0x55550000)
    cx=int(g['ecx'],16)&0xffff; dx=int(g['edx'],16)&0xffff
    maxc=(cx>>8)|((cx&0xc0)<<2); ns=cx&0x3f; nh=(dx>>8)+1
    bad=oob=badregs=0
    random.seed(2)
    pts=[(0,0,1),(0,1,1),(maxc,nh-1,ns),(1023 if maxc>1023 else maxc,nh-1,ns)]+[(random.randrange(maxc+1),random.randrange(nh),random.randrange(1,ns+1)) for _ in range(40)]
    for c,h,s in pts:
      cxi=((c&0xff)<<8)|((c>>8)<<6)|s; dxi=(h<<8)|0x80
      r=e.call(0x0201,cxi,dxi,e32=0x55550000)
      exp=(c*nh+h)*ns+s-1
      got=r['ide'][-1][-1] if r['ide'] else None
      if got!=exp: bad+=1
      if got is not None and got>=C*H*S: oob+=1
      if int(r['edx'],16)!=(0x55550000|dxi) or int(r['ecx'],16)!=(0x55550000|cxi): badregs+=1
    cap=(maxc+1)*nh*ns*512/2**30
    row.append(f"{nm}: {maxc+1}c/{nh}h/{ns}s DL={dx&0xff} AL={int(g['eax'],16)&0xff} cap={cap:.2f}GiB LBAerr={bad} oob={oob} regerr={badregs}")
  print(f"{C}/{H}/{S}  ({C*H*S*512/2**30:.2f} GiB real)")
  for r in row: print('   ',r)
