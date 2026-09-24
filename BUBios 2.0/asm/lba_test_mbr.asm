; INT 13h extensions test boot sector (BUBios 2.1)
;
; nasm -f bin lba_test_mbr.asm -o mbr.bin, then write mbr.bin to sector 0 of
; a scratch disk (NOT one with data on it: it overwrites the MBR and writes
; sector 30,000,001), put "LBA-30000000-OK!" at the start of sector
; 30,000,000 and "CHS-SECTOR-2-OK!" at the start of sector 1, and boot it.
; It prints one line per call: 41h (BX, CX), 48h (C/H/S, total sectors),
; 42h (text read from LBA 30,000,000), 43h + 42h (write and read back
; LBA 30,000,001), 02h (CHS read of sector 2) and 08h (CX, DX).
; The disk must be larger than 15.4 GB (30,000,002 sectors).
bits 16
org 0x7C00
start:
    xor  ax, ax
    mov  ds, ax
    mov  es, ax
    mov  ss, ax
    mov  sp, 0x7C00
    mov  si, t41
    call puts
    mov  ah, 0x41
    mov  bx, 0x55AA
    mov  dl, 0x80
    int  0x13
    call st                     ; CF/AH
    mov  ax, bx
    call phex
    mov  al, ' '
    call putc
    mov  ax, cx
    call phex
    call nl
    ; 48h
    mov  si, t48
    call puts
    mov  word [0x7000], 0x1A
    mov  ah, 0x48
    mov  dl, 0x80
    mov  si, 0x7000
    int  0x13
    call st
    mov  ax, [0x7000+4]         ; cylinders (low word)
    call phex
    mov  al, '/'
    call putc
    mov  ax, [0x7000+8]
    call phex
    mov  al, '/'
    call putc
    mov  ax, [0x7000+12]
    call phex
    mov  al, ' '
    call putc
    mov  ax, [0x7000+18]
    call phex
    mov  ax, [0x7000+16]
    call phex
    call nl
    ; 42h read LBA 30,000,000 -> 8000
    mov  si, t42
    call puts
    mov  si, dap1
    mov  ah, 0x42
    mov  dl, 0x80
    int  0x13
    call st
    mov  si, 0x8000
    call put16
    call nl
    ; 43h write LBA 30,000,001 from wmsg, read back to 8400
    mov  si, t43
    call puts
    mov  si, dap2
    mov  ah, 0x43
    mov  al, 0
    mov  dl, 0x80
    int  0x13
    call st
    mov  si, dap3
    mov  ah, 0x42
    mov  dl, 0x80
    int  0x13
    call st
    mov  si, 0x8400
    call put16
    call nl
    ; 02h CHS 0/0/2 -> 8600
    mov  si, t02
    call puts
    mov  ax, 0x0201
    mov  bx, 0x8600
    mov  cx, 0x0002
    mov  dx, 0x0080
    int  0x13
    call st
    mov  si, 0x8600
    call put16
    call nl
    ; 08h
    mov  si, t08
    call puts
    mov  ah, 0x08
    mov  dl, 0x80
    int  0x13
    call st
    mov  ax, cx
    call phex
    mov  al, ' '
    call putc
    mov  ax, dx
    call phex
    call nl
    mov  si, tend
    call puts
    cli
    hlt
    jmp  $

st: pushf                        ; print "ok " or "E<AH> "
    jc   .e
    push si
    mov  si, tok
    call puts
    pop  si
    popf
    ret
.e: push ax
    mov  al, 'E'
    call putc
    mov  al, ah
    call phex8
    mov  al, ' '
    call putc
    pop  ax
    popf
    ret
put16:
    mov  cx, 16
.l: lodsb
    cmp  al, ' '
    jae  .p
    mov  al, '.'
.p: call putc
    loop .l
    ret
puts:
    lodsb
    or   al, al
    jz   .r
    call putc
    jmp  puts
.r: ret
nl: mov  al, 13
    call putc
    mov  al, 10
putc:
    push ax
    push bx
    mov  ah, 0x0E
    mov  bx, 7
    int  0x10
    pop  bx
    pop  ax
    ret
phex:
    push ax
    mov  al, ah
    call phex8
    pop  ax
phex8:
    push ax
    shr  al, 4
    call .d
    pop  ax
    and  al, 15
.d: add  al, '0'
    cmp  al, '9'
    jbe  .o
    add  al, 7
.o: jmp  putc

dap1: db 16, 0
      dw 1, 0x8000, 0
      dq 30000000
dap2: db 16, 0
      dw 1, wmsg, 0
      dq 30000001
dap3: db 16, 0
      dw 1, 0x8400, 0
      dq 30000001
t41:  db "41h ", 0
t48:  db "48h ", 0
t42:  db "42h ", 0
t43:  db "43h+42h ", 0
t02:  db "02h ", 0
t08:  db "08h ", 0
tok:  db "ok ", 0
tend: db "done", 0
wmsg: db "WRITTEN-BY-43h!!"
    times 510-($-$$) db 0
    dw 0xAA55
