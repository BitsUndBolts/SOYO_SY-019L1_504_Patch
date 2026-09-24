; DSKCHG.COM -- floppy disk-change line test for DOS (BUBios project)
;
;   nasm -f bin dskchg.asm -o DSKCHG.COM
;
; Shows, several times a second, for drive A:
;   15h  INT 13h AH=15h result: 02 = drive has a change line
;   16h  INT 13h AH=16h result: 06 = disk changed, 00 = not changed
;        (what MS-DOS asks the BIOS before it trusts its cached directory)
;   3F7  raw Digital Input Register, read with the motor on; bit 7 (80h)
;        is the change line itself, bits 0-6 are not the floppy's
;   IDE  which IDE device is selected while 3F7 is read (see keys)
;
; How to use: put a disk in A:, run DIR A: (that clears the line), then
; start DSKCHG and swap the disk. 16h should go 00 -> 06 and 3F7 bit 7
; 0 -> 1. The line stays set until the next DIR A:.
;
; Keys: M = select the IDE master before reading 3F7, S = select the IDE
;       slave (nobody, if there is no slave), N = leave IDE alone,
;       Esc = quit.  If 3F7 bit 7 only works with S, an IDE device drives
;       bit 7 of port 3F7 and hides the floppy change line.
bits 16
org 0x100
start:
    mov  si, banner
    call puts
main:
    mov  ah, 0x15                  ; drive type / change-line support
    xor  dl, dl
    int  0x13
    mov  [v15], ah
    mov  ah, 0x16                  ; change line status
    xor  dl, dl
    int  0x13
    mov  [v16], ah
    mov  al, [idesel]              ; optional IDE device select
    or   al, al
    jz   .raw
    mov  dx, 0x1F6
    out  dx, al
.raw:
    mov  dx, 0x3F7                 ; motor is on after AH=16h
    in   al, dx
    mov  [r3f7], al
    mov  al, [idesel]
    or   al, al
    jz   .show
    mov  al, 0xA0                  ; back to the master
    mov  dx, 0x1F6
    out  dx, al
.show:
    mov  al, 13
    call putc
    mov  si, t15
    call puts
    mov  al, [v15]
    call phex
    mov  si, t16
    call puts
    mov  al, [v16]
    call phex
    mov  si, t3f7
    call puts
    mov  al, [r3f7]
    call phex
    mov  si, tbit
    call puts
    mov  al, '0'
    test byte [r3f7], 0x80
    jz   .b
    inc  al
.b: call putc
    mov  si, tide
    call puts
    mov  si, s_none
    mov  al, [idesel]
    cmp  al, 0xA0
    jne  .s
    mov  si, s_master
.s: cmp  al, 0xB0
    jne  .p
    mov  si, s_slave
.p: call puts
    ; wait about 1/6 s (3 timer ticks) and look at the keyboard
    push ds
    xor  ax, ax
    mov  ds, ax
    mov  bx, [0x46C]
.w: mov  ax, [0x46C]
    sub  ax, bx
    cmp  ax, 3
    jb   .w
    pop  ds
    mov  ah, 1
    int  0x16
    jz   main
    mov  ah, 0
    int  0x16
    cmp  al, 27
    je   quit
    and  al, 0xDF                  ; upper case
    mov  ah, 0
    cmp  al, 'N'
    je   .set
    mov  ah, 0xA0
    cmp  al, 'M'
    je   .set
    mov  ah, 0xB0
    cmp  al, 'S'
    jne  main
.set:
    mov  [idesel], ah
    jmp  main
quit:
    mov  al, 13
    call putc
    mov  al, 10
    call putc
    mov  ax, 0x4C00
    int  0x21

puts:
    lodsb
    or   al, al
    jz   .r
    call putc
    jmp  puts
.r: ret
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
    shr  al, 4
    call .d
    pop  ax
    and  al, 15
.d: add  al, '0'
    cmp  al, '9'
    jbe  putc
    add  al, 7
    jmp  putc

banner: db "DSKCHG - floppy A: change line.  M/S/N: IDE master/slave/none, Esc: quit", 13, 10, 0
t15:    db "15h=", 0
t16:    db "  16h=", 0
t3f7:   db "  3F7=", 0
tbit:   db "  line=", 0
tide:   db "  IDE sel=", 0
s_none:   db "none  ", 0
s_master: db "master", 0
s_slave:  db "slave ", 0
idesel: db 0
v15:    db 0
v16:    db 0
r3f7:   db 0
