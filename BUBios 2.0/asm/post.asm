; =====================================================================
;  BUBios 2.0 -- POST screen
;
;  Replaces AMI's start-up text (logo line, "Hit <DEL>", the memory
;  count, "WAIT......", the configuration box and the end-of-POST beep)
;  with one screen that is drawn as soon as the video BIOS is up and is
;  filled in while POST runs.  AMI's POST itself is not changed: every
;  hook replaces a print, or calls the AMI routine it replaces first.
;
;  Hook sites (patched and verified by py/build_bubios.py):
;    0x593B  MOV SI,8100 / CALL F52E (logo)  -> NOP x6
;    0x5945  CALL F42E (BIOS ID lines)       -> CALL bu_post_init
;    0x12BF  MOV SI,8100 / CALL F52E (logo)  -> NOP x6  (redraw after SETUP)
;    0x12C6  CALL F42E                       -> CALL bu_post_init
;    0x12CD  MOV SI,76AC / CALL F52E (WAIT)  -> CALL bu_wait + NOP x3
;    0x4F04  MOV SI,7688 / CALL F52E (DEL)   -> CALL bu_delmsg + NOP x3
;    0x4EA1  memory count print (49 bytes)   -> MOV AX,BX / ADD AX,BP /
;                                               CALL bu_memshow / JMP 4ED2
;    0x4CB4  CALL 4CD3 / MOV SI,76AC / CALL F52E (WAIT)
;                                            -> CALL bu_wait + NOP x6
;    0x1610  CALL F571 (end-of-POST beep)    -> CALL bu_chime
;    0x1613  CALL 16B7 (clear screen)        -> CALL bu_refresh
;    0x164C  CALL 39C0 (AMI config box)      -> CALL bu_final
;    0x120D, 0x158C  CALL 1656 (error list)  -> CALL bu_errors
;
;  All of it runs from segment F000 in real mode, with DS = 0040h (BDA)
;  on AMI's POST stack (0030:0100).  State between the hooks lives in
;  the BDA inter-application area 40:F0-40:FF; bu_final clears it again
;  before the boot.  Strings shared with the setup pages are read from
;  the BUBios data block (F000:79xx) with CS overrides.
;
;  The code fills the space of AMI's old configuration box
;  (0x39C0-0x3B83 and 0x3B8F-0x4033; AMI's shared CR/LF routine at
;  0x3B84-0x3B8E stays where it is).
; =====================================================================

; ---- AMI POST routines ----------------------------------------------
P_BIOSID   equ 0xF42E     ; BIOS ID lines (also sets the A20 gate, keep it)
P_CMOSRD   equ 0xEE94     ; AL = CMOS register AL
P_CHIPRD   equ 0x8EE3     ; AL = OPTi register AL (ports 22h/24h)
P_OPTION   equ 0x8300     ; ZF=1 if AMI CMOS option AL is set
P_IDEID    equ 0xAF07     ; AH=0: IDENTIFY drive DL into ES:BX, CF=error
S_BIOSID   equ 0x8078     ; "40-040A-001102-00101111-111192-OP495SLC"
O_BOOTSEQ  equ 0xB870     ; byte: AMI option code of "System Boot Up Sequence"

; ---- state in the BDA inter-application area (DS = 0040h) ----------
ST_FLAG    equ 0xF0       ; FLAG_UP = our screen is up (this POST)
FLAG_UP    equ 0xB2
ST_CPU     equ 0xF1       ; 3/4/5/6 = family, +80h = Cyrix core
ST_FPU     equ 0xF2       ; 1 = coprocessor answered
ST_SCH     equ 0xF3       ; colour scheme 0..5, 6 = monochrome
ST_T0      equ 0xF4       ; word: RTC minute*60+second at the start
ST_UNITS   equ 0xF6       ; word: 64 KB blocks counted so far
ST_TOTAL   equ 0xF8       ; word: 64 KB blocks in total (0 = unknown)
ST_STAT    equ 0xFA       ; word: status line text
ST_MHZ     equ 0xFC       ; word: measured clock x10

; ---- low memory used only at the very end of POST ---------------------
TLOOP_SEG  equ 0x07C0     ; timing loop copied to 07C0:0000 (= 0:7C00)
IDBUF      equ 0x7E00     ; IDENTIFY data at 0000:7E00

; ---- attribute indices ------------------------------------------------
AT_BG       equ 0
AT_TITLE    equ 1
AT_LABEL    equ 2
AT_VALUE    equ 3
AT_FILL     equ 4
AT_EMPTY    equ 5
AT_ACCENT   equ 6
AT_ERR      equ 7
AT_STAT     equ 8
NATTR      equ 9

; ---- layout -------------------------------------------------------------
R_SYS      equ 2          ; rows 2..5: processor / system block
R_MEM      equ 7          ; row 7 bar, row 8 block map
R_FDD      equ 10
R_HDC      equ 11         ; rows 11-12 disk C:, 13-14 disk D:
R_PORTS    equ 15
R_ROMS     equ 16
R_LINE     equ 17
R_MSG      equ 18         ; rows 18-20 and 22-23: messages (AMI's errors)
R_AMI      equ 21         ; AMI copyright line: POST checks that it is on
                          ; screen at row 21, column 0 (F3DB) and crashes
                          ; on purpose if it is not
R_STAT     equ 24
C_LBL      equ 2
C_VAL      equ 15
C_RLBL     equ 42
C_RVAL     equ 55
BAR_W      equ 40
MAP_MAX    equ 32

%define POS(r,c) (((r) << 8) | (c))

; =====================================================================
section post1 start=0x1800 vstart=0x39C0
; =====================================================================

; ---------------------------------------------------------------------
; hook entry points
; ---------------------------------------------------------------------
bu_post_init:                      ; replaces CALL F42E once video is up
    call P_BIOSID                  ; keep AMI's side effects (A20 gate)
    call enter
    call cpu_detect
    mov  word [ST_STAT], s_st_mem
    cmp  byte [ST_FLAG], FLAG_UP   ; second call (after SETUP): keep the
    jne  .first                    ; memory count; the POST time would
    mov  word [ST_T0], 0xFFFF      ; include the time spent in SETUP, so
    jmp  short .draw               ; it is not shown
.first:
    mov  byte [ST_FLAG], FLAG_UP
    call rtc_now
    mov  [ST_T0], ax
    xor  ax, ax
    mov  [ST_UNITS], ax
    mov  [ST_TOTAL], ax
.draw:
    call draw_all
    mov  dx, POS(R_LINE-2, 0)       ; AMI keeps row+2 as its print row
    jmp  leave_cur

bu_refresh:                        ; replaces the clear-screen before boot
    call enter
    mov  ah, 0x0F                  ; as AMI's 16B7: set the current mode again
    int  0x10
    mov  ah, 0
    int  0x10
    cmp  byte [ST_FLAG], FLAG_UP
    jne  hook_ret                  ; our screen was never set up
    mov  word [ST_STAT], s_st_dev
    call draw_all
    jmp  short leave_line

bu_delmsg:
    call enter
    mov  ax, s_st_del
    jmp  short status
bu_wait:
    call enter
    mov  ax, s_st_dev
status:
    cmp  byte [ST_FLAG], FLAG_UP
    jne  hook_ret
    mov  [ST_STAT], ax
    call vid_es
    call draw_status
leave_line:
    mov  dx, POS(R_LINE, 0)         ; AMI's messages start on the next row
leave_cur:
    call set_cursor
hook_ret:
    pop  es
    pop  ds
    popa
    add  sp, 2                     ; the return address of 'enter'
    ret

; enter: saves all registers and sets DS = 0040h (BX is changed).
; The hook then leaves through hook_ret.
enter:
    pusha
    push ds
    push es
    push 0x40
    pop  ds
    mov  bx, sp
    jmp  word [ss:bx+20]

; ---------------------------------------------------------------------
; bu_errors -- replaces CALL 1656 (AMI's error list) at 0x120D and 0x158C:
; gives the messages all six rows, including the copyright row (POST no
; longer checks it at this point), then runs AMI's routine
; ---------------------------------------------------------------------
bu_errors:
    call enter
    cmp  byte [ST_FLAG], FLAG_UP
    jne  .go
    call vid_es
    mov  al, ' '
    mov  bl, AT_ERR
    mov  cx, 80*6
    mov  dx, POS(R_MSG, 0)
    call fill_at
    mov  word [ST_STAT], s_st_err
    call draw_status
    mov  dx, POS(R_LINE, 0)
    call set_cursor
.go:pop  es
    pop  ds
    popa
    add  sp, 2
    jmp  0x1656

; ---------------------------------------------------------------------
; bu_memshow -- memory count (replaces AMI's "nnnnnn KB OK")
;   In: AX = 64 KB blocks counted, DX = blocks still to go in this
;       pass, BP = first block of the pass (1 = base memory, 0Ah = 640K-1M,
;       10h = above 1 MB)
; ---------------------------------------------------------------------
bu_memshow:
    call enter
    cmp  byte [ST_FLAG], FLAG_UP
    jne  hook_ret
    mov  [ST_UNITS], ax
    cmp  bp, 0x10
    jb   .base
    add  ax, dx                    ; above 1 MB: AX+DX-1 = last block
    dec  ax
    jmp  short .set
.base:                             ; below 1 MB the total is not known yet:
    mov  al, 0x31                  ; use the extended memory found last time
    call P_CMOSRD                  ; (CMOS 31h:30h, KB)
    mov  ah, al
    mov  al, 0x30
    call P_CMOSRD
    shr  ax, 6
    add  ax, 16
.set:
    mov  [ST_TOTAL], ax
    call vid_es
    call draw_memory
    jmp  short hook_ret

post1_end:

; =====================================================================
section post2 start=0x1C00 vstart=0x3B8F
; =====================================================================

; ---------------------------------------------------------------------
; bu_chime -- two short notes instead of the end-of-POST beep
; ---------------------------------------------------------------------
bu_chime:
    pusha
    mov  bx, 1140                  ; ~1047 Hz (C6)
    call .note
    mov  bx, 761                   ; ~1568 Hz (G6)
    call .note
    popa
    ret
.note:
    mov  al, 0xB6
    out  0x43, al
    mov  al, bl
    out  0x42, al
    mov  al, bh
    out  0x42, al
    in   al, 0x61
    or   al, 3
    out  0x61, al
    mov  cx, 4000                  ; 4000 refresh toggles x 15 us = 60 ms
.t: in   al, 0x61
    and  al, 0x10
    mov  ah, al
.t2:in   al, 0x61
    and  al, 0x10
    cmp  al, ah
    je   .t2
    loop .t
    in   al, 0x61
    and  al, 0xFC
    out  0x61, al
    ret

; ---------------------------------------------------------------------
; cpu_detect -> [ST_CPU], [ST_FPU]
; ---------------------------------------------------------------------
cpu_detect:
    xor  ax, ax                    ; Cyrix cores leave the flags alone on DIV
    sahf
    mov  ax, 5
    mov  bl, 2
    div  bl
    lahf
    xor  cl, cl
    cmp  ah, 2
    jne  .ac
    mov  cl, 0x80
.ac:mov  ch, 3
    mov  ebx, 0x40000              ; AC flag: 486 and later
    call flip
    jz   .fam
    mov  ch, 4
    mov  ebx, 0x200000             ; ID flag: CPUID
    call flip
    jz   .fam
    push cx
    mov  eax, 1
    db   0x0F, 0xA2                ; CPUID
    pop  cx
    mov  ch, ah
    and  ch, 0x0F
.fam:
    or   ch, cl
    mov  [ST_CPU], ch
    mov  byte [ST_FPU], 0          ; coprocessor: FNINIT/FNSTSW/FNSTCW probe
    mov  eax, cr0
    push eax
    and  al, 0xF3                  ; EM and TS clear
    mov  cr0, eax
    fninit
    mov  word [ST_MHZ], 0x5A5A
    fnstsw [ST_MHZ]
    cmp  byte [ST_MHZ], 0
    jne  .nofpu
    fnstcw [ST_MHZ]
    mov  ax, [ST_MHZ]
    and  ax, 0x103F
    cmp  ax, 0x003F
    jne  .nofpu
    inc  byte [ST_FPU]
.nofpu:
    pop  eax
    mov  cr0, eax
    ret

; ZF=0 if EFLAGS bit EBX can be toggled
flip:
    pushfd
    pop  eax
    mov  edx, eax
    xor  eax, ebx
    push eax
    popfd
    pushfd
    pop  eax
    push edx
    popfd
    xor  eax, edx
    test eax, ebx
    ret

; ---------------------------------------------------------------------
; rtc_now -> AX = minutes * 60 + seconds within the hour (RTC)
; ---------------------------------------------------------------------
rtc_now:
    mov  al, 0x02
    call bcd_cmos                  ; minutes
    mov  cl, 60
    mul  cl
    xchg ax, bx
    xor  al, al
    call bcd_cmos                  ; seconds
    add  ax, bx
    ret
bcd_cmos:                          ; AL = CMOS register -> AX = binary
    call P_CMOSRD
    mov  ah, al
    shr  ah, 4
    and  al, 0x0F
    aad
    ret

; ---------------------------------------------------------------------
; video helpers
; ---------------------------------------------------------------------
vid_es:                            ; ES = text buffer, [ST_SCH] = scheme
    mov  ax, 0xB000
    mov  bl, 6
    cmp  byte [0x49], 7
    je   .set
    mov  al, 0x37                  ; BUBios colour scheme (CMOS 37h)
    call P_CMOSRD
    and  ax, 0x0F
    mov  bl, 6
    div  bl
    mov  bl, ah
    mov  ax, 0xB800
.set:
    mov  es, ax
    mov  [ST_SCH], bl
    ret

attr:                              ; AL = attribute index -> AH = attribute
    push bx
    mov  bl, al
    mov  al, NATTR
    mul  byte [ST_SCH]
    add  al, bl
    mov  bx, ax
    mov  ah, [cs:bx+schemes]
    pop  bx
    ret

goto:                              ; DX = row:column -> DI
    push ax
    mov  al, 160
    mul  dh
    mov  di, ax
    movzx ax, dl
    add  di, ax
    add  di, ax
    pop  ax
    ret

set_cursor:                        ; DX = row:column
    mov  ah, 2
    xor  bh, bh
    int  0x10
    ret

text_at:                           ; CS:SI at DX, attribute index AL
    call attr
    call goto
pstr:                              ; CS:SI -> ES:DI, attribute AH
    cs lodsb
    or   al, al
    jz   .r
    stosw
    jmp  short pstr
.r: ret

fill_at:                           ; CX x char AL at DX, attribute index BL
    push ax
    mov  al, bl
    call attr
    pop  bx
    mov  al, bl
    call goto
    rep  stosw
    ret

; EAX unsigned -> ES:DI in attribute BH, right-aligned to CX columns
pdec:
    push si
    mov  si, sp
.d: xor  edx, edx
    push ecx
    mov  ecx, 10
    div  ecx
    pop  ecx
    push dx
    dec  cx
    or   eax, eax
    jnz  .d
    mov  ah, bh
    mov  al, ' '
    or   cx, cx
    jle  .o
    rep  stosw
.o: cmp  sp, si
    je   .e
    pop  ax
    add  al, '0'
    mov  ah, bh
    stosw
    jmp  short .o
.e: pop  si
    ret

pnum:                              ; AX unsigned, as is
    movzx eax, ax
    xor  cx, cx
    jmp  pdec

; AX -> CX hex digits (from the top) at ES:DI, attribute BH
phex:
.l: rol  ax, 4
    push ax
    and  al, 0x0F
    add  al, 0x90                  ; classic 0-9/A-F trick
    daa
    adc  al, 0x40
    daa
    mov  ah, bh
    stosw
    pop  ax
    loop .l
    ret

value_attr:                        ; BH = value attribute
    mov  al, AT_VALUE
    call attr
    mov  bh, ah
    ret

; ---------------------------------------------------------------------
; draw_all -- the complete screen from the current state
; ---------------------------------------------------------------------
draw_all:
    call vid_es
    mov  ah, 1                     ; hide the cursor
    mov  cx, 0x2000
    int  0x10
    mov  al, ' '                   ; clear
    mov  bl, AT_BG
    mov  cx, 80*25
    xor  dx, dx
    call fill_at
    mov  al, ' '                   ; title bar
    mov  bl, AT_TITLE
    mov  cx, 80
    call fill_at
    mov  si, labels                ; titles and labels
.lab:
    cs lodsw
    or   ax, ax
    jz   .labd
    xchg ax, dx                    ; DX = row:column
    cs lodsb                       ; AL = attribute index
    push si
    mov  si, [cs:si]               ; text
    call text_at
    pop  si
    inc  si
    inc  si
    jmp  short .lab
.labd:
    mov  al, 0xC4                  ; separator above the messages
    mov  bl, AT_EMPTY
    mov  cx, 80
    mov  dx, POS(R_LINE, 0)
    call fill_at
    mov  al, ' '                   ; message rows: AMI's teletype keeps the
    mov  bl, AT_ERR                 ; attribute, so errors come out red
    mov  cx, 80*6
    mov  dx, POS(R_MSG, 0)
    call fill_at
    mov  si, s_amicopy             ; AMI's copyright line, where POST checks it
    mov  dx, POS(R_AMI, 0)
    mov  al, AT_LABEL
    call text_at
    mov  si, s_bucopy
    mov  dx, POS(R_AMI, 45)
    mov  al, AT_LABEL
    call text_at
    ; processor, coprocessor
    mov  al, [ST_CPU]
    mov  si, s_cx486dlc
    test al, 0x80
    jz   .intel
    cmp  al, 0x83
    je   .cpu
    mov  si, s_cyrix
    jmp  short .cpu
.intel:
    sub  al, 3
    cmp  al, 3
    jb   .i
    mov  al, 2
.i: cbw
    mov  bx, ax
    shl  bx, 1
    mov  si, [cs:bx+cpu_names]
.cpu:
    mov  dx, POS(R_SYS, C_VAL)
    call value_at
    mov  si, s_none
    cmp  byte [ST_FPU], 0
    je   .fpu
    mov  si, s_fpu387
    test byte [ST_CPU], 0x7C       ; family 4 and up: on-chip
    jz   .fpu
    mov  si, s_fpuint
.fpu:
    mov  dx, POS(R_SYS, C_RVAL)
    call value_at
    call draw_memory
    call draw_floppies
    xor  bl, bl
    call draw_disk
    mov  bl, 1
    call draw_disk
draw_status:
    mov  al, ' '
    mov  bl, AT_STAT
    mov  cx, 80
    mov  dx, POS(R_STAT, 0)
    call fill_at
    mov  si, [ST_STAT]
    mov  dx, POS(R_STAT, 1)
    mov  al, AT_STAT
    call text_at
    mov  si, S_BIOSID
    mov  dx, POS(R_STAT, 41)
    mov  al, AT_STAT
    jmp  text_at

value_at:
    mov  al, AT_VALUE
    jmp  text_at

; ---------------------------------------------------------------------
; draw_memory -- bar, block map and count from [ST_UNITS] / [ST_TOTAL]
; ---------------------------------------------------------------------
draw_memory:
    mov  cx, [ST_TOTAL]
    mov  bx, [ST_UNITS]
    xor  ax, ax                    ; filled part of the bar
    jcxz .bar
    mov  ax, BAR_W
    mul  bx
    div  cx
    cmp  ax, BAR_W
    jbe  .bar
    mov  ax, BAR_W
.bar:
    push cx
    push bx
    push ax
    xchg ax, cx
    mov  al, 0xDB
    mov  bl, AT_FILL
    mov  dx, POS(R_MEM, C_VAL)
    call fill_at
    pop  ax
    mov  cx, BAR_W
    sub  cx, ax
    mov  al, AT_EMPTY
    call attr
    mov  al, 0xB0
    rep  stosw
    call value_attr                ; count
    movzx eax, word [ST_UNITS]
    shl  eax, 6
    mov  cx, 8
    call pdec
    mov  si, s_kb
    mov  ah, bh
    call pstr
    pop  bx
    pop  cx
    ; block map: one cell per 1, 2, 4 ... MB, at most MAP_MAX cells
    push cx
    push bx
    mov  al, ' '                   ; clear the row first (the scale can change)
    mov  bl, AT_BG
    mov  cx, 80 - C_LBL
    mov  dx, POS(R_MEM+1, C_LBL)
    call fill_at
    pop  bx
    pop  cx
    jcxz .done
    mov  ax, cx
    add  ax, 15
    shr  ax, 4                     ; MB to show
    mov  si, 16                    ; blocks per cell
.sc:cmp  ax, MAP_MAX
    jbe  .scok
    inc  ax
    shr  ax, 1
    shl  si, 1
    jmp  short .sc
.scok:
    xchg ax, cx                    ; CX = cells
    mov  dx, POS(R_MEM+1, C_VAL)
    call goto
    xor  dx, dx                    ; DX = first block of the cell
.cell:
    mov  al, AT_FILL                ; whole cell counted
    lea  bp, [si]
    add  bp, dx
    cmp  bp, bx
    jbe  .c1
    mov  al, AT_ACCENT              ; being counted
    cmp  dx, bx
    jb   .c1
    mov  al, AT_EMPTY               ; still to come
.c1:call attr
    mov  al, 0xFE
    stosw
    mov  al, ' '
    stosw
    add  dx, si
    loop .cell
    xchg ax, si                    ; label = legend: "1 MB blocks"
    shr  ax, 4
    mov  dx, POS(R_MEM+1, C_LBL)
    call goto
    push ax
    mov  al, AT_LABEL
    call attr
    mov  bh, ah
    pop  ax
    call pnum
    mov  si, s_blocks
    mov  ah, bh
    call pstr
.done:
    ret

; ---------------------------------------------------------------------
; floppies from CMOS 10h (AMI's own drive names, 17-byte records)
; ---------------------------------------------------------------------
draw_floppies:
    mov  al, 0x10
    call P_CMOSRD
    push ax
    shr  al, 4
    mov  dx, POS(R_FDD, C_VAL)
    call .one
    pop  ax
    and  al, 0x0F
    mov  dx, POS(R_FDD, C_RVAL)
.one:
    mov  si, s_none
    or   al, al
    jz   .p
    cmp  al, 5
    ja   .p
    dec  al
    mov  ah, 17
    mul  ah
    add  ax, S_FLOPPY
    xchg ax, si
.p: jmp  value_at

; ---------------------------------------------------------------------
; disk_geo: BL = 0 (C:) / 1 (D:) -> CF=1 none, else AL = type,
;           CX = cylinders, BL = heads, BH = sectors
; ---------------------------------------------------------------------
disk_geo:
    mov  al, 0x12
    call P_CMOSRD
    or   bl, bl
    jnz  .d
    shr  al, 4
.d: and  al, 0x0F
    cmp  al, 0x0F
    jne  .h
    mov  al, 0x19
    add  al, bl
    call P_CMOSRD
.h: or   al, al
    jz   .none
    cmp  al, 47
    jne  .rom
    mov  ah, 0x1B                  ; type 47: CMOS 1Bh (C:) / 24h (D:)
    or   bl, bl
    jz   .c
    mov  ah, 0x24
.c: push ax
    mov  al, ah
    call P_CMOSRD                  ; cylinders low
    mov  cl, al
    pop  ax
    push ax
    mov  al, ah
    inc  ax
    call P_CMOSRD                  ; cylinders high
    mov  ch, al
    pop  ax
    push ax
    mov  al, ah
    add  al, 2
    call P_CMOSRD                  ; heads
    mov  bl, al
    pop  ax
    push ax
    mov  al, ah
    add  al, 8
    call P_CMOSRD                  ; sectors
    mov  bh, al
    pop  ax
    jmp  short .chk
.rom:
    cmp  al, 46
    ja   .none
    push ax
    mov  ah, 16
    mul  ah
    add  ax, 0xE3F1                ; ROM drive table, type 1 at E401
    xchg ax, si
    pop  ax
    mov  cx, [cs:si]
    mov  bl, [cs:si+2]
    mov  bh, [cs:si+0x0E]
.chk:
    jcxz .none
    or   bl, bl
    jz   .none
    or   bh, bh
    jz   .none
    clc
    ret
.none:
    stc
    ret

; ---------------------------------------------------------------------
; draw_disk: BL = 0 (C:) / 1 (D:).  Row 1 (model) is filled by
; bu_final; row 2: "Type 47  16000/16/63 > DOS 1002/255/63  7875 MB"
; ---------------------------------------------------------------------
draw_disk:
    mov  dh, R_HDC
    add  dh, bl
    add  dh, bl
    mov  dl, C_VAL
    push dx
    call disk_geo
    pop  dx
    jnc  .yes
    mov  si, s_none
    jmp  value_at
.yes:
    inc  dh
    push bx                        ; BL = heads, BH = sectors
    push cx                        ; cylinders
    push ax                        ; AL = type
    mov  si, s_type
    mov  al, AT_LABEL
    call text_at
    call value_attr                ; BH = value attribute from here on
    pop  ax
    movzx ax, al
    call pnum                      ; type number
    add  di, 4
    pop  cx
    pop  ax                        ; AL = heads, AH = sectors
    push ax
    push cx
    call pchs                      ; physical geometry
    pop  cx
    pop  ax
    push ax
    push cx
    cmp  cx, 1024
    jbe  .size
    push ax                        ; DOS geometry through ECHS
    mov  si, s_arrow
    mov  ah, bh
    call pstr
    pop  ax
    push ax
    movzx bp, al                   ; BP = physical heads
    push cx
    mov  ax, cx
    mov  cx, bp
    call 0x76F5                    ; ECHS calc_factor: AX = heads DOS sees
    mov  si, ax
    pop  ax
    mul  bp                        ; cylinders * heads / DOS heads - 1,
    div  si                        ; as the setup's Summary page shows it
    dec  ax
    cmp  ax, 1024
    jbe  .lc
    mov  ax, 1024
.lc:xchg ax, cx
    pop  ax                        ; AH = sectors
    xchg ax, si                    ; AL = DOS heads
    xchg ax, dx
    mov  ax, si
    mov  al, dl
    call pchs
.size:
    add  di, 4                     ; capacity in MB = C*H*S / 2048
    pop  cx
    pop  ax
    movzx edx, al                  ; heads
    movzx esi, ah                  ; sectors
    movzx eax, cx
    imul eax, edx
    imul eax, esi
    shr  eax, 11
    xor  cx, cx
    call pdec
    mov  si, s_mb
    mov  ah, bh
    jmp  pstr

; CX = cylinders, AL = heads, AH = sectors -> "c/h/s" at ES:DI, attr BH
pchs:
    push ax
    xchg ax, cx
    call pnum
    call slash
    pop  ax
    push ax
    xor  ah, ah
    call pnum
    call slash
    pop  ax
    shr  ax, 8
    jmp  pnum
slash:
    mov  al, '/'
    mov  ah, bh
    stosw
    ret



post2_end:

; =====================================================================
section post3 start=0x2400 vstart=0x429D
; =====================================================================

; ---------------------------------------------------------------------
; bu_final -- replaces AMI's configuration box, last thing before INT 19h
; ---------------------------------------------------------------------
bu_final:
    call enter
    cmp  byte [ST_FLAG], FLAG_UP
    jne  .out
    call vid_es
    mov  al, 0x31                  ; memory: as found by POST (CMOS 31h:30h)
    call P_CMOSRD
    mov  ah, al
    mov  al, 0x30
    call P_CMOSRD
    shr  ax, 6
    add  ax, 16
    mov  [ST_UNITS], ax
    mov  [ST_TOTAL], ax
    call draw_memory
    call measure_clock
    ; clock
    mov  dx, POS(R_SYS+1, C_VAL)
    call goto
    call value_attr
    mov  ax, [ST_MHZ]
    xor  dx, dx
    mov  cx, 10
    div  cx
    push dx
    call pnum
    mov  al, '.'
    stosw
    pop  ax
    add  al, '0'
    mov  ah, bh
    stosw
    mov  si, s_mhz
    call pstr
    ; external cache (OPTi register 21h, as AMI reads it)
    mov  dx, POS(R_SYS+1, C_RVAL)
    call goto
    mov  al, 0x21
    call P_CHIPRD
    mov  si, s_disabled
    test al, 0x10
    jz   .nocache
    and  al, 0x0C
    shr  al, 2
    mov  cl, al
    mov  ax, 64
    shl  ax, cl
    call pnum
    mov  si, s_kb
.nocache:
    mov  ah, bh
    call pstr
    ; shadow RAM (CMOS 35h: 04h video C000, 08h system F000)
    mov  dx, POS(R_SYS+2, C_RVAL)
    call goto
    mov  al, 0x35
    call P_CMOSRD
    and  al, 0x0C
    mov  si, s_disabled
    jz   .shp
    mov  cl, al
    mov  si, s_video
    test cl, 0x04
    jz   .sys
    mov  ah, bh
    call pstr
    mov  si, s_plus
    test cl, 0x08
    jz   .shd
    mov  ah, bh
    call pstr
.sys:
    mov  si, s_system
.shp:
    mov  ah, bh
    call pstr
.shd:
    ; RTC battery (CMOS 0Dh bit 7 = valid)
    mov  al, 0x0D
    call P_CMOSRD
    mov  si, s_ok
    test al, 0x80
    jnz  .bat
    mov  si, s_low
.bat:
    mov  dx, POS(R_SYS+3, C_RVAL)
    call value_at
    ; disk model names
    xor  bl, bl
    call disk_name
    mov  bl, 1
    call disk_name
    ; serial and parallel ports from the BDA
    mov  dx, POS(R_PORTS, C_VAL)
    call goto
    call value_attr
    xor  si, si
    xor  bp, bp
.port:
    mov  ax, [si]
    or   ax, ax
    jz   .pn
    inc  bp
    push si
    shl  si, 1
    add  si, port_names
    mov  cx, 4
.pc:cs lodsb
    mov  ah, bh
    stosw
    loop .pc
    pop  si
    mov  al, ' '
    stosw
    mov  ax, [si]
    shl  ax, 4
    mov  cx, 3
    call phex
    add  di, 4
.pn:inc  si
    inc  si
    cmp  si, 14
    jb   .port
    or   bp, bp
    jnz  .roms
    mov  si, s_none
    call pstr_v
.roms:
    ; option ROMs: C000-EFFF in 2 KB steps
    mov  dx, POS(R_ROMS, C_VAL)
    call goto
    xor  bp, bp
    mov  ax, 0xC000
.rom:
    mov  gs, ax
    mov  cx, 0x80
    cmp  word [gs:0], 0xAA55
    jne  .rn
    inc  bp
    push ax
    mov  cx, 4
    call phex
    mov  ax, ' '
    mov  ah, bh
    stosw
    movzx ax, byte [gs:2]
    shr  ax, 1
    call pnum
    mov  al, 'K'
    mov  ah, bh
    stosw
    add  di, 4
    pop  ax
    movzx cx, byte [gs:2]
    add  cx, 3
    shr  cx, 2
    jnz  .r2
    inc  cx
.r2:shl  cx, 7
.rn:add  ax, cx
    cmp  ax, 0xF000
    jb   .rom
    or   bp, bp
    jnz  .stat
    mov  si, s_none
    call pstr_v
.stat:
    ; status: boot order and the time POST took
    mov  word [ST_STAT], s_st_bootc
    mov  al, [cs:O_BOOTSEQ]
    call P_OPTION
    jz   .bc
    mov  word [ST_STAT], s_st_boota
.bc:call draw_status
    mov  dx, POS(R_STAT, 23)
    call goto
    mov  al, AT_STAT
    call attr
    mov  bh, ah
    push bx
    call rtc_now
    pop  bx
    cmp  word [ST_T0], 0xFFFF
    je   .notime
    sub  ax, [ST_T0]
    jns  .t
    add  ax, 3600
.t: cmp  ax, 600                   ; an implausible time (RTC not set) is
    jae  .notime                   ; not shown
    push ax
    mov  si, s_post
    mov  ah, bh
    call pstr
    pop  ax
    call pnum
    mov  si, s_sec
    mov  ah, bh
    call pstr
.notime:
    ; message rows back to normal for the boot, cursor below the summary
    mov  al, ' '
    mov  bl, AT_BG
    mov  cx, 80*3
    mov  dx, POS(R_MSG, 0)
    call fill_at
    mov  al, ' '
    mov  bl, AT_BG
    mov  cx, 80*2
    mov  dx, POS(R_AMI+1, 0)
    call fill_at
    mov  cx, 0x0607
    cmp  byte [ST_SCH], 6
    jne  .cs
    mov  cx, 0x0B0C
.cs:mov  ah, 1
    int  0x10
    mov  dx, POS(R_MSG, 0)
    call set_cursor
.out:
    push ds                        ; clear the BDA inter-application area
    pop  es
    mov  di, ST_FLAG
    mov  cx, 8
    xor  ax, ax
    rep  stosw
    jmp  hook_ret

pstr_v:
    mov  ah, bh
    jmp  pstr

; ---------------------------------------------------------------------
; disk_name: BL = 0 (C:) / 1 (D:) -- IDENTIFY the drive, show its model
; ---------------------------------------------------------------------
disk_name:
    mov  dh, R_HDC
    add  dh, bl
    add  dh, bl
    mov  dl, C_VAL
    push dx
    push bx
    call disk_geo
    pop  bx
    pop  dx
    jc   .r
    push es
    push 0
    pop  es
    mov  dl, bl
    or   dl, 0x80
    push bx
    mov  bx, IDBUF
    mov  ah, 0
    call P_IDEID
    pop  bx
    pop  es
    mov  dh, R_HDC
    add  dh, bl
    add  dh, bl
    mov  dl, C_VAL
    mov  si, s_noid
    mov  al, AT_LABEL
    jc   text_at
    call goto
    call value_attr
    push 0
    pop  fs
    mov  cx, 40                    ; model: words 27-46, bytes swapped
.trim:
    mov  si, cx
    dec  si
    xor  si, 1
    mov  al, [fs:si+IDBUF+54]
    cmp  al, ' '
    ja   .show
    loop .trim
.r: ret
.show:
    xor  si, si
.ch:push si
    xor  si, 1
    mov  al, [fs:si+IDBUF+54]
    pop  si
    mov  ah, bh
    stosw
    inc  si
    loop .ch
    ret

; ---------------------------------------------------------------------
; measure_clock -> [ST_MHZ] (MHz x 10), with the setup's method: the
; timing loop runs from RAM (copied to 0:7C00), timed by PIT channel 2
; ---------------------------------------------------------------------
measure_clock:
    push ds
    push es
    push cs
    pop  ds
    push TLOOP_SEG
    pop  es
    mov  si, bu_tloop
    xor  di, di
    mov  cx, bu_tloop_end - bu_tloop
    rep  movsb
    pop  es
    pop  ds
    cli
    in   al, 0x61
    push ax
    and  al, 0xFC
    out  0x61, al
    mov  al, 0xB0
    out  0x43, al
    mov  al, 0xFF
    out  0x42, al
    out  0x42, al
    in   al, 0x61
    or   al, 1
    out  0x61, al
    call TLOOP_SEG:0
    mov  al, 0x80
    out  0x43, al
    in   al, 0x42
    mov  ah, al
    in   al, 0x42
    xchg al, ah
    not  ax
    xchg ax, bx                    ; BX = elapsed PIT ticks
    pop  ax
    out  0x61, al
    sti
    xor  si, si                    ; K for 386 / 486 / 586+
    mov  al, [ST_CPU]
    and  al, 0x7F
    cmp  al, 4
    jb   .k
    mov  si, 4
    je   .k
    mov  si, 8
.k: mov  eax, [cs:si+clock_k]
    xor  edx, edx
    movzx ebx, bx
    or   bx, bx
    jz   .st
    div  ebx
    mov  si, clock_std             ; snap to a standard clock within 1/16
.sn:mov  cx, [cs:si]
    jcxz .st
    inc  si
    inc  si
    mov  dx, ax
    sub  dx, cx
    jns  .abs
    neg  dx
.abs:
    mov  bx, cx
    shr  bx, 4
    cmp  dx, bx
    ja   .sn
    xchg ax, cx
.st:mov  [ST_MHZ], ax
    ret

; ---------------------------------------------------------------------
; tables and texts
; ---------------------------------------------------------------------
; attributes per scheme: BG TITLE LABEL VALUE FILL EMPTY ACCENT ERR STAT
schemes:
    db 0x07, 0x30, 0x03, 0x0B, 0x0B, 0x08, 0x0A, 0x0C, 0x30   ; teal
    db 0x07, 0x60, 0x06, 0x0E, 0x0E, 0x08, 0x0F, 0x0C, 0x60   ; amber
    db 0x07, 0x20, 0x02, 0x0A, 0x0A, 0x08, 0x0F, 0x0C, 0x20   ; green
    db 0x17, 0x70, 0x17, 0x1F, 0x1E, 0x19, 0x1A, 0x1C, 0x70   ; classic blue
    db 0x07, 0x70, 0x07, 0x0F, 0x0F, 0x08, 0x0A, 0x0C, 0x70   ; grey
    db 0x17, 0x70, 0x17, 0x1F, 0x1E, 0x19, 0x1A, 0x1C, 0x70   ; AMI
    db 0x07, 0x70, 0x07, 0x0F, 0x0F, 0x07, 0x0F, 0x0F, 0x70   ; monochrome

; row:column, attribute index, text
labels:
    dw POS(0, 1)
    db AT_TITLE
    dw ps_title
    dw POS(0, 46)
    db AT_TITLE
    dw ps_title_r
    dw POS(R_SYS, C_LBL)
    db AT_LABEL
    dw pl_cpu
    dw POS(R_SYS, C_RLBL)
    db AT_LABEL
    dw pl_fpu
    dw POS(R_SYS+1, C_LBL)
    db AT_LABEL
    dw pl_clock
    dw POS(R_SYS+1, C_RLBL)
    db AT_LABEL
    dw pl_cache
    dw POS(R_SYS+2, C_LBL)
    db AT_LABEL
    dw l_chip
    dw POS(R_SYS+2, C_VAL)
    db AT_VALUE
    dw s_chipset
    dw POS(R_SYS+2, C_RLBL)
    db AT_LABEL
    dw pl_shadow
    dw POS(R_SYS+3, C_LBL)
    db AT_LABEL
    dw pl_bios
    dw POS(R_SYS+3, C_VAL)
    db AT_VALUE
    dw s_bioscore
    dw POS(R_SYS+3, C_RLBL)
    db AT_LABEL
    dw pl_rtc
    dw POS(R_MEM, C_LBL)
    db AT_LABEL
    dw pl_mem
    dw POS(R_FDD, C_LBL)
    db AT_LABEL
    dw l_fda
    dw POS(R_FDD, C_RLBL)
    db AT_LABEL
    dw l_fdb
    dw POS(R_HDC, C_LBL)
    db AT_LABEL
    dw pl_diskc
    dw POS(R_HDC+2, C_LBL)
    db AT_LABEL
    dw pl_diskd
    dw POS(R_PORTS, C_LBL)
    db AT_LABEL
    dw pl_ports
    dw POS(R_ROMS, C_LBL)
    db AT_LABEL
    dw pl_roms
    dw 0

port_names: db "COM1COM2COM3COM4LPT1LPT2LPT3"

ps_title:    db "BUBios 2.0  ", 0xFE, "  Power-On Self Test", 0
ps_title_r: db "Bits und Bolts  ", 0xFE, "  OPTi 82C495SLC", 0
pl_cpu:      db "Processor", 0
pl_fpu:      db "Coprocessor", 0
pl_clock:    db "Clock", 0
pl_cache:    db "Cache", 0
pl_shadow:   db "Shadow RAM", 0
pl_bios:     db "BIOS", 0
pl_rtc:      db "RTC battery", 0
pl_mem:      db "Memory", 0
pl_diskc:    db "Disk C:", 0
pl_diskd:    db "Disk D:", 0
pl_ports:    db "Ports", 0
pl_roms:     db "Option ROMs", 0
s_bioscore: db "AMI 11/11/92 ", 0xFE, " BUBios 2.0", 0
s_cx486dlc: db "Cyrix 486DLC", 0
s_cyrix:    db "Cyrix 486", 0
s_fpu387:   db "387 present", 0
s_fpuint:   db "On-chip", 0
s_kb:       db " KB", 0
s_mb:       db " MB", 0
s_blocks:   db " MB blocks", 0
s_type:     db "Type ", 0
s_arrow:    db " ", 0x1A, " DOS ", 0
s_disabled: db "Disabled", 0
s_video:    db "Video", 0
s_system:   db "System", 0
s_plus:     db " + ", 0
s_ok:       db "OK", 0
s_low:      db "Low", 0
s_noid:     db "(no IDENTIFY data)", 0
s_st_mem:   db "Testing memory ...", 0
s_st_del:   db "Press DEL to run Setup", 0
s_st_dev:   db "Checking devices ...", 0
s_st_err:   db "POST found a problem", 0
s_st_bootc: db "Booting: C: then A:", 0
s_st_boota: db "Booting: A: then C:", 0
s_post:     db 0xB3, " POST ", 0
s_amicopy:  db "(C) American Megatrends Inc.,", 0
s_bucopy:   db "BUBios 2.0 (C) 2026 Bits und Bolts", 0
s_sec:      db " s", 0

post3_end:
