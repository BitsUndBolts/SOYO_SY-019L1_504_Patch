; =====================================================================
;  BUBios 2.1 -- POST screen, drive auto-detection, INT 13h extensions
;
;  POST screen: replaces AMI's start-up text (logo line, "Hit <DEL>", the
;  memory count, "WAIT......", the configuration box and the end-of-POST
;  beep) with one screen that is drawn after the video card's own sign-on
;  and filled in while POST runs.  AMI's POST itself is not changed:
;  every hook replaces a print, or calls the AMI routine it replaces first.
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
;    0x120D, 0x158C  CALL 1656 (error list)  -> CALL bu_errors
;    0x1610  CALL F571 (end-of-POST beep)    -> CALL bu_chime
;    0x1613  CALL 16B7 (clear screen)        -> CALL bu_refresh
;    0x164C  CALL 39C0 (AMI config box)      -> CALL bu_final
;    0xF3DB  AMI's on-screen copyright check -> RET
;    0xA3E7  INT 13h entry (CMP DL,80/STI/CLD) -> JMP bu_int13 + NOP x2
;
;  All POST code runs from segment F000 in real mode, with DS = 0040h
;  (BDA) on AMI's POST stack (0030:0100).  State between the hooks lives
;  in the BDA inter-application area 40:F0-40:FF; bu_final clears it
;  before the boot.  Strings shared with the setup pages are read from the
;  BUBios data block (F000:79xx) with CS overrides.
;
;  Space used (all freed from dead AMI code, see README.md):
;    post1  0x39C0-0x3B83   hooks
;    post2  0x3B8F-0x4167   screen drawing
;    post3  0x429D-0x49DA   end of POST, IDENTIFY, auto-detect, texts
;    lba    0x3292-0x3481   INT 13h extensions (AH=41h-48h)
;    tools  0x19B0-0x1A79   setup: "Auto-Detect Disks at Boot" switch
; =====================================================================

; ---- AMI POST routines ----------------------------------------------
P_BIOSID   equ 0xF42E     ; BIOS ID lines (also sets the A20 gate, keep it)
P_CMOSRD   equ 0xEE94     ; AL = CMOS register AL
P_CMOSWR   equ 0xEE9D     ; CMOS register AL = AH
P_CHIPRD   equ 0x8EE3     ; AL = OPTi register AL (ports 22h/24h)
P_OPTION   equ 0x8300     ; ZF=1 if AMI CMOS option AL is set
P_SETUP    equ 0xF104     ; password check (if set) + SETUP, as POST calls it
S_BIOSID   equ 0x8078     ; "40-040A-001102-00101111-111192-OP495SLC"
O_BOOTSEQ  equ 0xB870     ; byte: AMI option code of "System Boot Up Sequence"
INT13_CONT equ 0xA3EC     ; AMI INT 13h after CMP DL,80h / STI / CLD
INT13_FLOP equ 0xA446     ; (for reference: DL < 80h goes on to INT 40h)

; ---- BUBios CMOS switch (outside both AMI checksums) -----------------
CM_FLAGS   equ 0x7F       ; bit0 = auto-detect disks at boot
CM_CHECK   equ 0x7E       ; = CM_FLAGS xor 0A5h, else the flags are ignored

; ---- state in the BDA inter-application area (DS = 0040h) ----------
ST_FLAG    equ 0xF0       ; FLAG_UP = our screen is up (this POST)
FLAG_UP    equ 0xB2
ST_CPU     equ 0xF1       ; 3/4/5/6 = family, +80h = Cyrix core
ST_FPU     equ 0xF2       ; 1 = coprocessor answered (probed at the end of POST)
ST_SCH     equ 0xF3       ; colour scheme 0..5, 6 = monochrome
ST_T0      equ 0xF4       ; word: RTC minute*60+second at the start
ST_UNITS   equ 0xF6       ; word: 64 KB blocks counted so far
ST_TOTAL   equ 0xF8       ; word: 64 KB blocks in total (0 = unknown)
ST_STAT    equ 0xFA       ; word: status line text
ST_MHZ     equ 0xFC       ; word: measured clock x10 (0 = not shown yet)
ST_DISK    equ 0xFE       ; bit0/1: C:/D: identified, bit4/5: C:/D: absent, bit6: a CF card
ST_MISC    equ 0xFF       ; bit0: POST time not meaningful (SETUP visited), bit1: end of POST
                          ; bit1: memory test done (say 'Disabled' for cache)

; ---- low memory used only while POST runs ---------------------------
TLOOP_SEG  equ 0x07C0     ; timing loop copied to 07C0:0000 (= 0:7C00)

; ---- timing: port 61h bit 4 toggles every 15.09 us --------------------
SPLASH_T   equ 2          ; seconds the video card's sign-on stays visible
BOOT_T     equ 3          ; seconds of countdown before the boot
T_SECOND   equ 66         ; x 1000 toggles = 1 s

; ---- attribute indices ------------------------------------------------
AT_BG      equ 0
AT_TITLE   equ 1
AT_LABEL   equ 2
AT_VALUE   equ 3
AT_FILL    equ 4
AT_EMPTY   equ 5
AT_ACCENT  equ 6
AT_ERR     equ 7
AT_STAT    equ 8
NATTR      equ 9

; ---- layout -------------------------------------------------------------
R_SYS      equ 2          ; rows 2..5: processor / system block
R_MEM      equ 7          ; memory bar
R_FDD      equ 9
R_HDC      equ 10         ; rows 10-11 disk C:, 12-13 disk D:
R_PORTS    equ 15
R_ROMS     equ 16
R_LINE     equ 17
R_MSG      equ 18         ; rows 18..23: messages (AMI's errors)
R_STAT     equ 24
C_LBL      equ 2
C_VAL      equ 15
C_RLBL     equ 42
C_RVAL     equ 55
C_LBA      equ 58         ; "LBA nnnnn MB" on a disk's model row
BAR_W      equ 40

%define POS(r,c) (((r) << 8) | (c))

; =====================================================================
section post1 start=0x1800 vstart=0x39C0
; =====================================================================

; ---------------------------------------------------------------------
; bu_post_init -- replaces CALL F42E once the video BIOS is up
; ---------------------------------------------------------------------
bu_post_init:
    call enter
    call vid_es                    ; the video card's sign-on: leave it on
    cmp  byte [ST_FLAG], FLAG_UP   ; screen for SPLASH_T seconds (only if
    je   .go                       ; there is one, and not after SETUP)
    xor  di, di
    mov  cx, 80*25
.scan:
    mov  al, [es:di]
    inc  di
    inc  di
    cmp  al, ' '
    ja   .splash
    loop .scan
    jmp  short .go
.splash:
    mov  cx, SPLASH_T * T_SECOND
    call wait_units
.go:
    call P_BIOSID                  ; AMI's ID lines: keep its A20 handling
    call cpu_detect
    mov  word [ST_STAT], s_st_mem
    cmp  byte [ST_FLAG], FLAG_UP   ; second call (after SETUP): keep the
    jne  .first                    ; memory count; the POST time would
    or   byte [ST_MISC], 1         ; include the time spent in SETUP
    mov  byte [ST_DISK], 0         ; ask the drives again (names, and
    jmp  short .draw               ; auto-detect if SETUP switched it on)
.first:
    mov  byte [ST_FLAG], FLAG_UP
    call rtc_now
    mov  [ST_T0], ax
    xor  ax, ax
    mov  [ST_UNITS], ax
    mov  [ST_TOTAL], ax
    mov  [ST_MHZ], ax
    mov  [ST_DISK], ax             ; (and ST_MISC)
    call measure_clock             ; shown only if it is a standard clock
    call early_ports
.draw:
    call draw_all
    mov  cx, 20                    ; disks: a quick try (0.3 s) now, a
    call detect_disks              ; longer one after the memory test
    mov  dx, POS(R_LINE-2, 0)      ; AMI keeps row+2 as its print row
    jmp  leave_cur

; ---------------------------------------------------------------------
; bu_refresh -- replaces the clear-screen before the configuration box
; ---------------------------------------------------------------------
bu_refresh:
    call enter
    mov  word [ST_STAT], s_st_dev
    call vid_es
    cmp  byte [ST_FLAG], FLAG_UP
    jne  .mode
    mov  al, AT_TITLE              ; our screen still there? then no flash
    call attr
    cmp  ah, [es:3]
    jne  .mode
    cmp  byte [es:2], 'B'
    je   .keep
.mode:
    and  byte [ST_DISK], 0xFC      ; names are gone: IDENTIFY again later
    mov  ah, 0x0F                  ; as AMI's 16B7: set the current mode again
    int  0x10
    mov  ah, 0
    int  0x10
    cmp  byte [ST_FLAG], FLAG_UP
    jne  hook_ret                  ; our screen was never set up
    call draw_all
    jmp  short .vals
.keep:
    call draw_status
.vals:
    call fill_values
    jmp  short leave_line

; ---------------------------------------------------------------------
; bu_delmsg / bu_wait -- status line texts
; ---------------------------------------------------------------------
bu_delmsg:
    call enter
    mov  ax, s_st_del
    jmp  short status
bu_wait:                           ; memory test done: disks, values
    call enter
    cmp  byte [ST_FLAG], FLAG_UP
    jne  hook_ret
    mov  word [ST_STAT], s_st_dev
    call vid_es
    call draw_status
    cmp  word [ST_MHZ], 0          ; clock not known yet: try again
    jne  .c
    call measure_clock
.c: mov  cx, 20                    ; auto-detect on: give a spinning-up
    call auto_on                   ; drive up to 10 s, it has to be set up
    jz   .d                        ; before AMI's disk initialisation
    mov  cx, 10 * T_SECOND
.d: call detect_disks
    call fill_values
    jmp  short leave_line
status:
    cmp  byte [ST_FLAG], FLAG_UP
    jne  hook_ret
    mov  [ST_STAT], ax
    call vid_es
    call draw_status
leave_line:
    mov  dx, POS(R_LINE, 0)        ; AMI's messages start on the next row
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
; bu_errors -- replaces CALL 1656 (AMI's error list) at 0x120D, 0x158C
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
.base:                             ; below 1 MB the total is not known yet
    xor  ax, ax                    ; (CMOS may still hold the last size):
                                   ; count only, empty bar
.set:
    mov  [ST_TOTAL], ax
    call vid_es
    call draw_status               ; "Entering Setup" as soon as DEL is seen
    call draw_memory
    jmp  short hook_ret

cmos_ext:                          ; AX = 16 + CMOS 31h:30h / 64
    mov  al, 0x31
    call P_CMOSRD
    mov  ah, al
    mov  al, 0x30
    call P_CMOSRD
    shr  ax, 6
    add  ax, 16
    ret

; ide_errcode -> AH = INT 13h status after a failed ATA command:
; 80h timeout, CCh write fault, 04h sector not found, 10h bad sector,
; BBh anything else
ide_errcode:
    mov  dx, 0x1F7
    in   al, dx
    mov  ah, 0x80                  ; no ERR: timeout
    test al, 0x01
    jz   .r
    mov  ah, 0xCC                  ; write fault
    test al, 0x20
    jnz  .r
    mov  dl, 0xF1                  ; error register
    in   al, dx
    mov  ah, 0x04                  ; IDNF
    test al, 0x10
    jnz  .r
    mov  ah, 0x10                  ; UNC
    test al, 0x40
    jnz  .r
    mov  ah, 0xBB
.r: ret

post1_end:

; timing loop parts for measure_clock
tl_head:                           ; MOV CX,500 / MOV BX,1 / XOR DX,DX / MOV AX,1234h
    db 0xB9, 0xF4, 0x01, 0xBB, 0x01, 0x00, 0x31, 0xD2, 0xB8, 0x34, 0x12
tl_head_end:
clock_k32:                         ; 500 passes x clocks per pass x 11.93182
    dd 4283523                     ; 386:  32 x DIV r16 (22) + LOOP (13) + 1
    dd 4623580                     ; 486:  32 x DIV r16 (24) + LOOP (7)
    dd 4808523                     ; 586+: 32 x DIV r16 (25) + LOOP (6)

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
    mov  cx, 4                     ; 4 x 15 ms = 60 ms
    call wait_units
    in   al, 0x61
    and  al, 0xFC
    out  0x61, al
    ret

; wait_units: CX x 1000 refresh toggles (~15 ms each); CX = 0 returns
wait_units:
    jcxz .r
    push ax
    push cx
.o: push cx
    mov  cx, 1000
.t: in   al, 0x61
    and  al, 0x10
    mov  ah, al
.t2:in   al, 0x61
    and  al, 0x10
    cmp  al, ah
    je   .t2
    loop .t
    pop  cx
    loop .o
    pop  cx
    pop  ax
.r: ret

; ---------------------------------------------------------------------
; cpu_detect -> [ST_CPU] (the coprocessor is probed by fpu_probe at the
; end of POST, when interrupts and AMI's IRQ 13 handler are set up)
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
    mov  al, ' '
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

clear_at:                          ; CX blanks at DX in the background colour
    mov  al, ' '
    mov  bl, AT_BG
    jmp  fill_at

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

value_at:
    mov  al, AT_VALUE
    jmp  text_at

pstr_v:
    mov  ah, bh
    jmp  pstr


; early_ports -- serial and parallel ports into the BDA the way AMI finds
; them at checkpoint 9Ah (after the memory test), so that they can be
; shown at once. AMI's port table at 8240h: COM 3F8/2F8/3E8/2E8, then
; LPT 3BC/378/278. AMI repeats this later and gets the same result.
early_ports:
    mov  si, 0x8240
    xor  di, di
.com:
    mov  dx, [cs:si]
    inc  si
    inc  si
    push dx
    inc  dx                        ; IIR: bits 5-3 are 0 on a UART,
    inc  dx                        ; a missing port reads FFh
    in   al, dx
    pop  dx
    test al, 0x38
    jnz  .nc
    mov  [di], dx
    inc  di
    inc  di
.nc:cmp  si, 0x8248
    jb   .com
    mov  di, 8
.lpt:
    mov  dx, [cs:si]
    inc  si
    inc  si
    mov  al, 0xAA                  ; data latch: write, read back
    call .t
    jne  .nl
    mov  al, 0x55
    call .t
    jne  .nl
    mov  [di], dx
    inc  di
    inc  di
.nl:cmp  si, 0x824E
    jb   .lpt
    ret
.t: mov  ah, al
    out  dx, al
    in   al, 0x61                  ; drive the bus with something else
    in   al, dx
    cmp  al, ah
    ret

; ---------------------------------------------------------------------
; draw_all -- the complete screen from the current state
; ---------------------------------------------------------------------
draw_all:
    call vid_es
    mov  ah, 1                     ; hide the cursor
    mov  cx, 0x2000
    int  0x10
    mov  cx, 80*25                 ; clear
    xor  dx, dx
    call clear_at
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
    mov  bl, AT_ERR                ; attribute, so errors come out red
    mov  cx, 80*6
    call fill_at
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
    call draw_memory
    call draw_floppies
    xor  bl, bl
    call draw_disk
    mov  bl, 1
    call draw_disk
    call fill_values
draw_status:
    mov  al, ' '
    mov  bl, AT_STAT
    mov  cx, 80
    mov  dx, POS(R_STAT, 0)
    call fill_at
    call stat_text
    mov  dx, POS(R_STAT, 1)
    mov  al, AT_STAT
    call text_at
    mov  si, S_BIOSID
    mov  dx, POS(R_STAT, 40)
    mov  al, AT_STAT
    jmp  text_at

; ---------------------------------------------------------------------
; draw_memory -- bar and count from [ST_UNITS] / [ST_TOTAL]
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
    jmp  pstr_v

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
; draw_disk: BL = 0 (C:) / 1 (D:), the geometry row:
;   "Type 47  16000/16/63 > DOS 1002/255/63  7875 MB"  ("Auto" when the
;   drive is set up by auto-detection)
; ---------------------------------------------------------------------
draw_disk:
    mov  dh, R_HDC
    add  dh, bl
    add  dh, bl
    mov  dl, C_VAL
    push bx
    push dx
    inc  dh                        ; clear the geometry row
    mov  cx, 80 - C_VAL
    call clear_at
    pop  dx
    pop  bx
    push dx
    call disk_geo
    pop  dx
    jnc  .yes
    mov  al, 1                     ; named by IDENTIFY: keep the name
    mov  cl, bl
    shl  al, cl
    test [ST_DISK], al
    jnz  .r
    mov  si, s_none
    jmp  value_at
.yes:
    inc  dh
    push bx                        ; BL = heads, BH = sectors
    push cx                        ; cylinders
    push ax                        ; AL = type
    mov  si, s_type
    call auto_on
    jz   .t
    mov  si, s_auto
.t: mov  al, AT_LABEL
    call text_at
    call value_attr                ; BH = value attribute from here on
    pop  ax
    call auto_on
    jnz  .nt
    movzx ax, al
    call pnum                      ; type number
.nt:add  di, 4
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
    call pstr_v
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
    jmp  pstr_v
.r: ret

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

; ide_wait -- poll 1F7h until (status AND AH) = AL; CX = timeout (15 ms
; units).  If AH has bit 0, a set ERR bit (with BSY clear) ends the wait.
; CF=1 on timeout or error.  DX = 1F7h on return.
ide_wait:
    push bx
    push cx
    mov  bx, ax
    mov  dx, 0x1F7
.o: push cx
    mov  cx, 1000
.i: in   al, dx
    test bh, 1
    jz   .t
    mov  ah, al
    and  ah, 0x81
    cmp  ah, 0x01
    je   .err
.t: and  al, bh
    cmp  al, bl
    je   .ok
    in   al, 0x61                  ; one refresh toggle (15 us) per try
    and  al, 0x10
    mov  ah, al
.tg:in   al, 0x61
    and  al, 0x10
    cmp  al, ah
    je   .tg
    loop .i
    pop  cx
    loop .o
    push cx
.err:
    pop  cx
    pop  cx
    pop  bx
    stc
    ret
.ok:pop  cx
    pop  cx
    pop  bx
    clc
    ret

; auto_set -- BL = drive, SS:SI = IDENTIFY words: make the drive type 47
; with cylinders/heads/sectors from words 1/3/6 (the same geometry AMI's
; auto-detect uses), unless CMOS already says so
auto_set:
    push bx
    call disk_geo
    mov  dx, bx                    ; DL = heads, DH = sectors
    pop  bx
    jc   .write
    cmp  al, 47
    jne  .write
    cmp  cx, [ss:si]
    jne  .write
    cmp  dl, [ss:si+2]
    jne  .write
    cmp  dh, [ss:si+4]
    je   .r
.write:
    mov  al, 0x12                  ; type nibble = 0Fh: extended type byte
    call P_CMOSRD
    mov  ah, 0xF0
    or   bl, bl
    jz   .c
    mov  ah, 0x0F
.c: or   ah, al
    mov  al, 0x12
    call P_CMOSWR
    mov  al, 0x19
    add  al, bl
    mov  ah, 47
    call P_CMOSWR
    mov  al, 0x1B                  ; parameters at 1Bh (C:) / 24h (D:)
    or   bl, bl
    jz   .p
    mov  al, 0x24
.p: mov  cx, [ss:si]
    mov  ah, cl
    call .wr                       ; cylinders
    mov  ah, ch
    call .wr
    mov  ah, [ss:si+2]
    call .wr                       ; heads
    mov  ah, 0xFF
    call .wr                       ; write precompensation: none
    call .wr
    mov  ah, 0
    cmp  byte [ss:si+2], 8
    jbe  .cb
    mov  ah, 8
.cb:call .wr                       ; control byte
    mov  ah, cl
    call .wr                       ; landing zone = cylinders
    mov  ah, ch
    call .wr
    mov  ah, [ss:si+4]
    call .wr                       ; sectors
cmos_csum:                         ; checksum 10h-2Dh -> 2Eh/2Fh
    xor  dx, dx
    mov  al, 0x10
.s: push ax
    call P_CMOSRD
    xor  ah, ah
    add  dx, ax
    pop  ax
    inc  al
    cmp  al, 0x2E
    jb   .s
    mov  ah, dh
    call P_CMOSWR
    mov  al, 0x2F
    mov  ah, dl
    call P_CMOSWR
auto_set.r:
    ret
auto_set.wr:
    call P_CMOSWR
    inc  al
    ret

s_countdown:db "Press DEL to run Setup, any other key to boot now ... ", 0
; fpu_probe -> [ST_FPU] = 1 if a coprocessor answers FNINIT/FNSTSW/
; FNSTCW. Run at the end of POST only: early in POST (interrupts off, the
; PICs possibly not set up after power-on) a 387 left busy could stall it.
fpu_probe:
    mov  byte [ST_FPU], 0
    xor  al, al                    ; clear the busy latch
    out  0xF0, al
    mov  eax, cr0
    push eax
    and  al, 0xF3                  ; EM and TS clear
    mov  cr0, eax
    fninit
    mov  word [ST_UNITS], 0x5A5A   ; (scratch: bu_final sets ST_UNITS
    fnstsw [ST_UNITS]              ;  right after this)
    cmp  byte [ST_UNITS], 0
    jne  .no
    fnstcw [ST_UNITS]
    mov  ax, [ST_UNITS]
    and  ax, 0x103F
    cmp  ax, 0x003F
    jne  .no
    inc  byte [ST_FPU]
.no:pop  eax
    mov  cr0, eax
    ret

; stat_text -> SI = status line text; once AMI's keyboard poll has seen
; DEL (CMOS 0Eh bit 0) it says so
stat_text:
    mov  si, [ST_STAT]
    push ax
    mov  al, 0x0E
    call P_CMOSRD
    test al, 1
    pop  ax
    jz   .r
    mov  si, s_st_setup
.r: ret

s_video:    db "Video", 0
post2_end:

; =====================================================================
section post3 start=0x2400 vstart=0x429D
; =====================================================================

; ---------------------------------------------------------------------
; fill_values -- everything that can be shown at this point of POST:
;   clock (once measured), cache, shadow RAM, RTC battery, ports, ROMs
; ---------------------------------------------------------------------
fill_values:
    mov  dx, POS(R_SYS, C_RVAL)    ; coprocessor: probed at the end of
    mov  cx, 12                    ; POST (fpu_probe), "..." until then
    call clear_at
    sub  di, 24
    call value_attr
    mov  si, s_none
    cmp  byte [ST_FPU], 0
    je   .fp
    mov  si, s_fpu387
    test byte [ST_CPU], 0x7C       ; family 4 and up: on-chip
    jz   .fp
    mov  si, s_fpuint
.fp:call dots_or
    mov  dx, POS(R_SYS+1, C_VAL)   ; clock
    mov  cx, 12
    call clear_at
    sub  di, 24
    call value_attr                ; (BH = attribute; AX is clobbered)
    mov  ax, [ST_MHZ]
    or   ax, ax
    jnz  .clk
    mov  si, s_dots + 3            ; not measured (yet): "...", at the
    call dots_or                   ; end of POST: nothing
    jmp  short .cache
.clk:
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
.cache:                            ; external cache (OPTi register 21h)
    mov  dx, POS(R_SYS+1, C_RVAL)
    mov  cx, 12
    call clear_at
    sub  di, 24
    call value_attr
    mov  al, 0x21
    call P_CHIPRD
    test al, 0x10
    jz   .nocache
    and  al, 0x0C
    shr  al, 2
    mov  cl, al
    mov  ax, 64
    shl  ax, cl
    call pnum
    mov  si, s_kb
    call pstr_v
    jmp  short .shadow
.nocache:                          ; not (yet) enabled: say so at the
    mov  si, s_disabled            ; end of POST
    call dots_or
.shadow:                           ; CMOS 35h: 04h video C000, 08h system F000
    mov  dx, POS(R_SYS+2, C_RVAL)
    mov  cx, 20
    call clear_at
    sub  di, 40
    call value_attr
    mov  al, 0x35
    call P_CMOSRD
    and  al, 0x0C
    mov  si, s_disabled
    jz   .shp
    mov  cl, al
    mov  si, s_video
    test cl, 0x04
    jz   .sys
    call pstr_v
    mov  si, s_plus
    test cl, 0x08
    jz   .shd
    call pstr_v
.sys:
    mov  si, s_system
.shp:
    call pstr_v
.shd:
    mov  al, 0x0D                  ; RTC battery (CMOS 0Dh bit 7 = valid)
    call P_CMOSRD
    mov  si, s_ok
    test al, 0x80
    jnz  .bat
    mov  si, s_low
.bat:
    mov  dx, POS(R_SYS+3, C_RVAL)
    call value_at
    ; serial and parallel ports from the BDA (filled in by POST after the
    ; memory test)
    mov  dx, POS(R_PORTS, C_VAL)
    mov  cx, 80 - C_VAL
    call clear_at
    call goto
    call value_attr
    push di
    xor  si, si
.port:
    mov  ax, [si]
    or   ax, ax
    jz   .pn
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
    pop  ax
    cmp  ax, di                    ; none found: "..." until AMI has
    jne  .roms0                    ; looked, then "None"
    mov  si, s_none
    call dots_or
.roms0:
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
    mov  al, ' '
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
    jnz  .done
    mov  si, s_none
    jmp  pstr_v
.done:
    ret

; ---------------------------------------------------------------------
; bu_final -- replaces AMI's configuration box, last thing before INT 19h
; ---------------------------------------------------------------------
bu_final:
    call enter
    cmp  byte [ST_FLAG], FLAG_UP
    jne  .out
    or   byte [ST_MISC], 2
    call fpu_probe
    test byte [ST_DISK], 0x40      ; a CF card drives bit 7 of port 3F7h:
    jz   .ncf                      ; tell INT 13h AH=15h (cf_chgline) that
    or   byte [0x8F], 8            ; floppies have no usable change line
.ncf:
    call vid_es
    call cmos_ext                  ; memory: as found by POST (CMOS 31h:30h)
    mov  [ST_UNITS], ax
    mov  [ST_TOTAL], ax
    call draw_memory
    call measure_clock             ; with cache and shadow now as set up
    mov  cx, 60                    ; disks not identified yet: last try
    call detect_disks
    ; status: boot order and the time POST took
    mov  word [ST_STAT], s_st_bootc
    mov  al, [cs:O_BOOTSEQ]
    call P_OPTION
    jz   .bc
    mov  word [ST_STAT], s_st_boota
.bc:call draw_status
    call fill_values
    mov  dx, POS(R_STAT, 23)
    call goto
    mov  al, AT_STAT
    call attr
    mov  bh, ah
    test byte [ST_MISC], 1
    jnz  .notime
    push bx
    call rtc_now
    pop  bx
    sub  ax, [ST_T0]
    jns  .t
    add  ax, 3600
.t: cmp  ax, 600                   ; an implausible time (RTC not set) is
    jae  .notime                   ; not shown
    push ax
    mov  si, s_post
    call pstr_v
    pop  ax
    call pnum
    mov  si, s_sec
    call pstr_v
.notime:
    ; countdown: DEL runs SETUP, any other key boots at once
    mov  dx, POS(R_MSG+1, 2)
    mov  si, s_countdown
    mov  al, AT_LABEL
    call text_at                   ; AH = label attribute, DI = digit cell
    mov  bh, ah
    mov  cx, BOOT_T
.sec:
    mov  al, cl
    add  al, '0'
    mov  ah, bh
    mov  [es:di], ax
    push cx
    mov  cx, T_SECOND
.tick:
    push cx
    mov  cx, 1
    call wait_units
    mov  al, 0x0E                  ; DEL caught by AMI's POST keyboard
    call P_CMOSRD                  ; poll (CMOS 0Eh bit 0), pressed after
    test al, 1                     ; AMI's own check
    jnz  .del
    mov  ah, 1
    int  0x16
    pop  cx
    jnz  .key
    loop .tick
    pop  cx
    loop .sec
    jmp  short .boot
.del:
    pop  cx
    pop  cx
    and  al, 0xFE                  ; clear AMI's DEL flag
    mov  ah, al
    mov  al, 0x0E
    call P_CMOSWR
    jmp  short .setup
.key:
    pop  cx
    mov  ah, 0
    int  0x16
    cmp  ah, 0x53                  ; DEL (either key)
    je   .setup
.boot:
    mov  ah, 0x0F                  ; clean screen, normal colours for DOS
    int  0x10
    mov  ah, 0
    int  0x10
.out:
    push ds                        ; clear the BDA inter-application area
    pop  es
    mov  di, ST_FLAG
    mov  cx, 8
    xor  ax, ax
    rep  stosw
    jmp  hook_ret
.setup:
    call cmos_sum                  ; SETUP (after the password, if one is
    push ax                        ; set), as POST runs it on DEL
    call P_SETUP
    call cmos_sum
    pop  bx
    cmp  ax, bx
    je   .boot                     ; nothing saved: go on booting
    mov  word [0x72], 0            ; settings changed: restart, so that
    mov  byte [ST_FLAG], 0         ; POST applies them (as a first start)
    mov  al, 0xFE
    out  0x64, al
.halt:
    hlt
    jmp  short .halt

bcd_raw:                           ; AX = CMOS AL (AH=0) + CMOS AL-1 * 256
    push ax
    call P_CMOSRD
    mov  ah, al
    pop  bx
    push ax
    mov  al, bl
    dec  al
    call P_CMOSRD
    pop  bx
    mov  ah, al
    mov  al, bh
    ret

; ---------------------------------------------------------------------
; detect_disks -- IDENTIFY C: and D: (unless done, or found absent) with
; a timeout of CX units (15 ms): show the model name (and the LBA size if
; it is larger than what CHS reaches) and, if "Auto-Detect Disks at Boot"
; is on, set the drive up as type 47 with the geometry the drive reports.
; A drive is only asked if CMOS has it or auto-detection is on.
; ---------------------------------------------------------------------
detect_disks:
    xor  bl, bl
    call .one
    mov  bl, 1
.one:
    mov  al, 0x11                  ; identified or absent already?
    push cx
    mov  cl, bl
    shl  al, cl
    pop  cx
    test [ST_DISK], al
    jnz  .r
    push cx
    push bx
    call disk_geo
    pop  bx
    pop  cx
    jnc  .ask
    call auto_on
    jz   .r
.ask:
    push cx
    push bx
    mov  dh, R_HDC                 ; model row: clear it, text goes to ES:DI
    add  dh, bl
    add  dh, bl
    mov  dl, C_VAL
    push dx
    mov  cx, 80 - C_VAL
    call clear_at
    pop  dx
    pop  bx
    call goto
    call value_attr
    pop  cx
    sub  sp, 16
    mov  si, sp
    call ide_id
    jnc  .ok
    or   al, al                    ; AL=1: no drive there. Final only after
    jz   .later                    ; the memory test (a drive may not answer
    cmp  word [ST_STAT], s_st_dev  ; right after power-on): then don't ask
    jne  .later                    ; again, and drop it from CMOS so AMI does
    mov  al, 0x10                  ; not wait for it
    mov  cl, bl
    shl  al, cl
    or   [ST_DISK], al
    call hd_remove
    call draw_disk                 ; "None"
    jmp  short .jx
.later:                            ; no answer yet: "..." (asked again
    mov  si, s_dots + 3            ; after the memory test)
    call dots_or
.jx:jmp  .x
.ok:
    mov  al, 1
    mov  cl, bl
    shl  al, cl
    or   [ST_DISK], al
    test byte [ss:si+12], 4        ; a CompactFlash card? (word 83 bit 2:
    jnz  .cf                       ; CFA feature set, or word 0 = 848Ah)
    cmp  word [ss:si+14], 0x848A
    jne  .ncf
.cf:or   byte [ST_DISK], 0x40      ; (it hides the floppy change line)
.ncf:
    ; LBA size, if the drive has LBA and it is more than CHS reaches
    test byte [ss:si+7], 2         ; word 49 bit 9
    jz   .auto
    mov  eax, [ss:si+8]            ; words 60-61
    movzx ecx, word [ss:si]
    movzx edx, word [ss:si+2]
    imul ecx, edx
    movzx edx, word [ss:si+4]
    imul ecx, edx
    cmp  eax, ecx
    jbe  .auto
    push eax
    mov  dh, R_HDC
    add  dh, bl
    add  dh, bl
    mov  dl, C_LBA
    push si
    mov  si, s_lba
    mov  al, AT_LABEL
    call text_at
    pop  si
    call value_attr
    pop  eax
    shr  eax, 11
    xor  cx, cx
    push si
    call pdec
    mov  si, s_mb
    call pstr_v
    pop  si
.auto:
    call auto_on
    jz   .x
    call auto_set
    call draw_disk                 ; redraw the geometry row
.x: add  sp, 16
.r: ret

; ide_id -- IDENTIFY DEVICE by polling (no IRQ, no buffer)
;   In : BL = drive (0/1), CX = timeout (15 ms units), SS:SI = 12 bytes
;        ES:DI = where the model name goes (DI = 0: nowhere), BH = attribute
;   Out: CF=0: [SI] = cylinders, heads, sectors (words 1, 3, 6), word 49,
;        words 60-61 (LBA sectors); CF=1: AL=1 no drive, AL=0 no answer
ide_id:
    push dx
    mov  dx, 0x1F6
    mov  al, bl
    shl  al, 4
    or   al, 0xA0
    out  dx, al
    mov  dl, 0xF7
    in   al, dx                    ; (400 ns settle)
    in   al, dx
    in   al, dx
    in   al, dx
    cmp  al, 0xFF                  ; floating bus or no device answering
    je   .none
    or   al, al
    jz   .none
    cmp  al, 0x7F
    je   .none
    mov  ax, 0xC040                ; BSY=0, DRDY=1
    call ide_wait
    jc   .fail
    mov  al, 0xEC
    out  dx, al
    mov  ax, 0x8908                ; BSY=0, DRQ=1 (ERR ends the wait)
    call ide_wait
    jc   .fail
    mov  dl, 0xF0
    push cx
    xor  cx, cx
.w: in   ax, dx
    push si
    push bx
    mov  bx, id_words
.f: cmp  cl, [cs:bx]
    je   .keep
    inc  bx
    inc  bx
    cmp  byte [cs:bx], 0xFF
    jne  .f
    jmp  short .m
.keep:
    push bx
    mov  bl, [cs:bx+1]
    xor  bh, bh
    mov  [ss:si+bx], ax
    pop  bx
.m: pop  bx
    pop  si
    cmp  cl, 27                    ; model name, words 27-46
    jb   .n
    cmp  cl, 46
    ja   .n
    or   di, di
    jz   .n
    push ax
    mov  al, ah
    mov  ah, bh
    stosw
    pop  ax
    mov  ah, bh
    stosw
.n: inc  cx
    cmp  cx, 256
    jb   .w
    pop  cx
    clc
    jmp  short .end
.none:
    mov  al, 1
    jmp  short .f2
.fail:
    xor  al, al
.f2:stc
.end:
    pushf                          ; select the master again: a missing
    push ax                        ; slave must not stay selected
    mov  dx, 0x1F6
    mov  al, 0xA0
    out  dx, al
    pop  ax
    popf
    pop  dx
    ret
id_words: db 1, 0,  3, 2,  6, 4,  49, 6,  60, 8,  61, 10,  83, 12,  0, 14,  0xFF
; (16 bytes: detect_disks has room for them; ext48's 12-byte buffer at
;  [bp-20] then runs into [bp-8]/[bp-6], which 48h no longer needs)

; ---------------------------------------------------------------------
; measure_clock -> [ST_MHZ] (MHz x 10): a timing loop of 32 DIVs per
; pass is built in RAM (0:7C00) and timed by PIT channel 2. The setup
; uses 8 DIVs per pass; the longer pass makes the reading much less
; sensitive to slow code fetches while the cache is still off (early in
; POST). A reading not within 1/16 of a standard clock is not shown.
; ---------------------------------------------------------------------
measure_clock:
    push es
    push TLOOP_SEG
    pop  es
    xor  di, di
    mov  si, tl_head
    mov  cx, tl_head_end - tl_head
    rep  cs movsb
    mov  ax, 0xF3F7                ; 32 x DIV BX
    mov  cl, 32
    rep  stosw
    mov  ax, 0xBEE2                ; LOOP back to the first DIV
    stosw
    mov  al, 0xCB                  ; RETF
    stosb
    pop  es
    pushf                          ; keep the caller's interrupt flag:
    cli                            ; early in POST interrupts are off
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
    popf
    xor  si, si                    ; K for 386 / 486 / 586+
    mov  al, [ST_CPU]
    and  al, 0x7F
    cmp  al, 4
    jb   .k
    mov  si, 4
    je   .k
    mov  si, 8
.k: mov  eax, [cs:si+clock_k32]
    xor  edx, edx
    movzx ebx, bx
    or   bx, bx
    jz   .no
    div  ebx
    mov  si, clock_std             ; snap to a standard clock within 1/16
.sn:mov  cx, [cs:si]
    jcxz .no
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
    mov  [ST_MHZ], cx
.no:ret

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
%macro LBL 4
    dw POS(%1, %2)
    db %3
    dw %4
%endmacro
labels:
    LBL 0, 1, AT_TITLE, ps_title
    LBL 0, 46, AT_TITLE, ps_title_r
    LBL R_SYS, C_LBL, AT_LABEL, pl_cpu
    LBL R_SYS, C_RLBL, AT_LABEL, pl_fpu
    LBL R_SYS+1, C_LBL, AT_LABEL, pl_clock
    LBL R_SYS+1, C_RLBL, AT_LABEL, pl_cache
    LBL R_SYS+2, C_LBL, AT_LABEL, l_chip
    LBL R_SYS+2, C_VAL, AT_VALUE, s_chipset
    LBL R_SYS+2, C_RLBL, AT_LABEL, pl_shadow
    LBL R_SYS+3, C_LBL, AT_LABEL, pl_bios
    LBL R_SYS+3, C_VAL, AT_VALUE, s_bioscore
    LBL R_SYS+3, C_RLBL, AT_LABEL, pl_rtc
    LBL R_MEM, C_LBL, AT_LABEL, pl_mem
    LBL R_FDD, C_LBL, AT_LABEL, l_fda
    LBL R_FDD, C_RLBL, AT_LABEL, l_fdb
    LBL R_HDC, C_LBL, AT_LABEL, pl_diskc
    LBL R_HDC+2, C_LBL, AT_LABEL, pl_diskd
    LBL R_PORTS, C_LBL, AT_LABEL, pl_ports
    LBL R_ROMS, C_LBL, AT_LABEL, pl_roms
    dw 0

port_names: db "COM1COM2COM3COM4LPT1LPT2LPT3"

ps_title:   db "BUBios 2.1  ", 0xFE, "  Power-On Self Test", 0
ps_title_r: db "Bits und Bolts  ", 0xFE, "  OPTi 82C495SLC", 0
pl_cpu:     db "Processor", 0
pl_fpu:     db "Coprocessor", 0
pl_clock:   db "Clock", 0
pl_cache:   db "Cache", 0
pl_shadow:  db "Shadow RAM", 0
pl_bios:    db "BIOS", 0
pl_rtc:     db "RTC battery", 0
pl_mem:     db "Memory", 0
pl_diskc:   db "Disk C:", 0
pl_diskd:   db "Disk D:", 0
pl_ports:   db "Ports", 0
pl_roms:    db "Option ROMs", 0
s_bioscore: db "AMI 11/11/92 ", 0xFE, " BUBios 2.1", 0
s_cyrix:    db "Cyrix 486", 0
s_fpuint:   db "On-chip", 0
s_kb:       db " KB", 0
s_mb:       db " MB", 0
s_type:     db "Type ", 0
s_auto:     db "Auto", 0
s_lba:      db "LBA ", 0
s_arrow:    db " ", 0x1A, " DOS ", 0
s_disabled: db "Disabled", 0
s_system:   db "System", 0
s_plus:     db " + ", 0
s_st_del:   db "Press DEL to run Setup", 0
s_st_dev:   db "Checking devices ...", 0
s_st_err:   db "POST found a problem", 0
s_st_bootc: db "Booting: C: then A:", 0
s_st_boota: db "Booting: A: then C:", 0
s_post:     db 0xB3, " POST ", 0
s_sec:      db " s", 0

; ZF=0 if "Auto-Detect Disks at Boot" is on (CMOS 7Fh bit 0, checked)
auto_on:
    push ax
    mov  al, CM_CHECK
    call P_CMOSRD
    mov  ah, al
    mov  al, CM_FLAGS
    call P_CMOSRD
    xor  ah, al
    cmp  ah, 0xA5
    jne  .off
    test al, 1
    pop  ax
    ret
.off:
    cmp  al, al                    ; ZF=1
    pop  ax
    ret

post3_end:

; =====================================================================
section lba start=0x2C00 vstart=0x3292
; =====================================================================
; INT 13h extensions (EDD 1.1 subset) for the hard disks 80h/81h:
;   41h installation check, 42h extended read, 43h extended write,
;   44h verify, 47h seek (no-op), 48h drive parameters.
; Sectors are addressed as CHS through the drive's physical geometry (the
; FDPT at INT 41h/46h, as AMI's own code does) as long as the request
; stays inside it, and as 28-bit LBA beyond it.  The transfer is polled
; PIO, one command per request (up to 127 sectors).  Entered from AMI's
; INT 13h handler at A3E7 (patched to JMP bu_int13).
; Frame: BP -> ES DS DI SI BP SP BX DX CX AX (PUSHA order) ; locals below.
; ---------------------------------------------------------------------
bu_int13:
    sti
    cld
    cmp  dl, 0x80
    jb   .orig
    cmp  ah, 0x41
    jb   .orig
    cmp  ah, 0x48
    jbe  ext13
.orig:
    cmp  dl, 0x80
    jmp  INT13_CONT

ext13:
    pusha
    push ds
    push es
    mov  bp, sp
    sub  sp, 20                    ; [bp-2] select byte, [bp-4] done,
    mov  al, dl                    ; [bp-6] command, [bp-8] LBA mode,
                                   ; [bp-20..bp-9] IDENTIFY words
    and  al, 0x7F
    push 0x40
    pop  fs
    cmp  al, [fs:0x75]             ; that many hard disks?
    jae  ext_bad
    cmp  al, 1
    ja   ext_bad
    mov  bl, al                    ; BL = drive 0/1
    shl  al, 4
    or   al, 0xA0
    mov  [bp-2], al
    mov  ds, [bp+2]                ; caller's DS:SI
    mov  si, [bp+6]
    cmp  ah, 0x41
    je   ext41
    cmp  ah, 0x48
    je   ext48
    cmp  ah, 0x47
    je   ext_ok
    cmp  ah, 0x45
    jae  ext_bad
    ; ---- 42h / 43h / 44h
    mov  cl, 0x20                  ; READ SECTORS
    cmp  ah, 0x43
    jb   .cmd
    mov  cl, 0x30                  ; WRITE SECTORS
    je   .cmd
    mov  cl, 0x40                  ; READ VERIFY SECTORS
.cmd:
    mov  [bp-6], cl
    mov  word [bp-4], 0
    cmp  dword [si+12], 0          ; 28-bit LBA only
    jne  ext_bad
    mov  eax, [si+8]
    cmp  eax, 0x0FFFFFFF
    ja   ext_bad
    movzx ecx, word [si+2]
    or   cx, cx
    jz   ext_ok
    cmp  cx, 127
    ja   ext_bad
    push cx
    ; physical geometry from the FDPT
    xor  dx, dx
    mov  gs, dx
    mov  di, 0x104                 ; INT 41h vector (C:)
    or   bl, bl
    jz   .v
    mov  di, 0x118                 ; INT 46h vector (D:)
.v: lgs  di, [gs:di]
    movzx ebx, byte [gs:di+0x0E]   ; sectors
    movzx edx, word [gs:di]
    movzx ecx, byte [gs:di+2]
    imul edx, ecx
    imul edx, ebx                  ; C*H*S
    push eax
    movzx ecx, word [si+2]
    add  eax, ecx                  ; end of the request
    cmp  eax, edx
    seta byte [bp-8]               ; beyond CHS: LBA addressing
    mov  dx, 0x1F6                 ; select the drive, wait until ready
    mov  al, [bp-2]
    out  dx, al
    mov  ax, 0xC040
    mov  cx, 5 * 66                ; 5 s
    call ide_wait
    pop  eax
    jc   .err
    cmp  byte [bp-8], 0
    jne  .lbamode
    ; CHS: sector = LBA mod S + 1, head = (LBA / S) mod H, cyl = / H
    xor  edx, edx
    div  ebx
    inc  dx
    push ax
    mov  al, dl
    mov  dx, 0x1F3
    out  dx, al
    pop  ax
    xor  edx, edx
    movzx ebx, byte [gs:di+2]
    div  ebx
    push dx
    mov  dx, 0x1F4
    out  dx, al
    inc  dx
    mov  al, ah
    out  dx, al
    pop  ax
    or   al, [bp-2]
    jmp  short .dh
.lbamode:
    mov  dx, 0x1F3
    out  dx, al
    inc  dx
    shr  eax, 8
    out  dx, al
    inc  dx
    shr  eax, 8
    out  dx, al
    shr  eax, 8
    and  al, 0x0F
    or   al, [bp-2]
    or   al, 0x40                  ; LBA
.dh:mov  dx, 0x1F6
    out  dx, al
    pop  cx                        ; count
    mov  dl, 0xF2
    mov  al, cl
    out  dx, al
    mov  dl, 0xF7
    mov  al, [bp-6]
    out  dx, al
    cmp  al, 0x40
    je   .last
    ; data: ES:DI = buffer, normalised; one sector per DRQ
    les  di, [si+4]
    mov  ax, di
    shr  ax, 4
    mov  dx, es
    add  dx, ax
    mov  es, dx
    and  di, 0x0F
.sect:
    push cx
    mov  ax, 0x8908
    mov  cx, 5 * 66
    call ide_wait
    pop  cx
    jc   .err
    push cx
    push di                        ; the segment moves on, not the offset
    mov  cx, 256
    mov  dl, 0xF0
    cmp  byte [bp-6], 0x30
    je   .wr
    rep  insw
    jmp  short .adv
.wr:push ds
    push si
    push es
    pop  ds
    mov  si, di
    rep  outsw
    pop  si
    pop  ds
.adv:
    pop  di
    pop  cx
    mov  ax, es
    add  ax, 0x20
    mov  es, ax
    inc  word [bp-4]
    loop .sect
.last:
    mov  ax, 0x8100                ; BSY=0 (ERR ends the wait)
    mov  cx, 5 * 66
    call ide_wait
    jc   .err
    in   al, dx
    test al, 0x21                  ; ERR or write fault
    jnz  .err
    cmp  byte [bp-6], 0x40
    jne  ext_ok
    mov  ax, [si+2]
    mov  [bp-4], ax
    jmp  ext_ok
.err:
    call ide_errcode
.cnt:
    mov  cx, [bp-4]                ; sectors done
    mov  [si+2], cx
    jmp  ext_done
ext_bad:
    mov  ah, 0x01
    jmp  short ext_done
ext_ok:
    xor  ah, ah
ext_done:
    mov  [bp+19], ah               ; AH
    mov  [fs:0x74], ah             ; last status (AH=01h)
    mov  byte [fs:0x8E], 0         ; no stale disk-IRQ flag for AMI's code
ext_ret:
    cmp  byte [bp+19], 0x21        ; 41h: CF=0 with AH=21h
    je   .c0
    cmp  byte [bp+19], 1           ; CF = (AH <> 0)
    cmc
    jmp  short .cf
.c0:clc
.cf:mov  sp, bp
    pop  es
    pop  ds
    popa
    retf 2

lba_end:

; ---------------------------------------------------------------------
; (continued in the BUBios 1.0 code block, which executes from F000 too)
section code
; ---------------------------------------------------------------------
ext48:                             ; INT 13h AH=48h (part of bu_int13)
    cmp  word [si], 0x1A
    jb   ext_bad
    push si
    lea  si, [bp-20]
    mov  cx, 66                    ; 1 s
    xor  di, di                    ; no model text
    call ide_id
    pop  di                        ; DS:DI = caller's buffer
    mov  ah, 0x80
    jc   ext_done
    mov  word [di], 0x1A
    mov  word [di+2], 0
    xor  eax, eax
    mov  ax, [bp-20]
    mov  [di+4], eax               ; cylinders
    mov  ax, [bp-18]
    mov  [di+8], eax               ; heads
    mov  ax, [bp-16]
    mov  [di+12], eax              ; sectors per track
    movzx ecx, word [bp-20]
    movzx edx, word [bp-18]
    imul ecx, edx
    imul ecx, eax                  ; C*H*S
    test byte [bp-13], 2           ; LBA supported: words 60-61
    jz   .tot
    mov  ecx, [bp-12]
.tot:
    mov  [di+16], ecx
    mov  dword [di+20], 0
    mov  word [di+24], 512
    jmp  ext_ok


; =====================================================================
section tools start=0x3000 vstart=0x19B0
; =====================================================================
; Setup, Tools page: "Auto-Detect Disks at Boot: On/Off".  Runs inside
; the setup program (DS = 0100h, the RAM copy); the texts are DS-relative,
; so they have to sit below E000h, which this block does.
; ---------------------------------------------------------------------
BU_AUTOLBL equ 0xEFC0             ; label buffer in the setup's data area

bu_autolabel:                      ; build the label: "... at Boot: On"
    pusha
    push es
    push ds
    pop  es
    mov  si, s_it_auto
    mov  di, BU_AUTOLBL
.c: lodsb
    stosb
    or   al, al
    jnz  .c
    dec  di
    mov  si, s_off
    call auto_on
    jz   .s
    mov  si, s_on
.s: lodsb
    stosb
    or   al, al
    jnz  .s
    pop  es
    popa
    ret

bu_autotoggle:
    mov  ah, 0
    call auto_on
    jnz  .w
    inc  ah                        ; off -> on
.w: mov  al, CM_FLAGS
    call P_CMOSWR
    mov  [0xE000 + CM_FLAGS], ah   ; the setup's CMOS image too
    xor  ah, 0xA5
    mov  al, CM_CHECK
    call P_CMOSWR
    mov  [0xE000 + CM_CHECK], ah
    ret


ext41:                             ; INT 13h AH=41h (part of bu_int13)
    cmp  word [bp+12], 0x55AA
    jne  ext_bad
    mov  word [bp+12], 0xAA55
    mov  word [bp+16], 0x0001      ; 42h-44h, 47h, 48h
    mov  byte [bp+19], 0x21        ; EDD 1.1
    jmp  ext_ret

s_it_auto:  db "Auto-Detect Disks at Boot: ", 0
s_on:       db "On", 0
s_off:      db "Off", 0
s_help_auto:db "Identify the IDE drives at every boot and set them up", 0

s_st_mem:   db "Testing memory ...", 0
tools_end:

; ---------------------------------------------------------------------
section data
; ---------------------------------------------------------------------
cmos_sum:                          ; AX = both AMI CMOS checksums and the
    mov  al, 0x7F                  ; auto-detect switch (7Eh/7Fh, outside
    call bcd_raw                   ; the checksums) added up
    push ax
    mov  al, 0x3F
    call bcd_raw
    push ax
    mov  al, 0x2F
    call bcd_raw                   ; (bcd_raw changes BX)
    pop  bx
    add  ax, bx
    pop  bx
    add  ax, bx
    ret

; ---------------------------------------------------------------------
section code2
; ---------------------------------------------------------------------
; dots_or: at the end of POST print CS:SI, before it "..." (ES:DI, BH)
dots_or:
    test byte [ST_MISC], 2
    jnz  .p
    mov  si, s_dots
.p: jmp  pstr_v
s_dots: db "...", 0


; ---------------------------------------------------------------------
section data
; ---------------------------------------------------------------------
s_st_setup: db "Entering Setup", 0

; ---------------------------------------------------------------------
; in AMI's old memory-count print (4EAA-4ED1, jumped over since 2.0)
section mem start=0x3200 vstart=0x4EAA
; ---------------------------------------------------------------------
; hd_remove: BL = drive that did not answer after the memory test: if CMOS
; has it set up, set its type to "none" so that AMI's disk setup does not
; wait for it (about a minute, then "HDD controller failure")
hd_remove:
    push bx
    call disk_geo
    pop  bx
    jc   .r                        ; not set up
    mov  al, 0x12
    call P_CMOSRD
    mov  ah, 0x0F                  ; keep the other drive's nibble
    or   bl, bl
    jz   .c
    mov  ah, 0xF0
.c: and  ah, al
    mov  al, 0x12
    call P_CMOSWR
    jmp  cmos_csum
.r: ret
; cf_chgline -- replaces AMI's stub (STC/RET at D385) called by INT 13h
; AH=15h for a floppy: CF=1 = the drive has a change line. With a CF card
; on the IDE bus (40:8F bit 3, set by bu_final; AMI never sets that bit)
; the line cannot be read, so DOS is told there is none and checks the
; disk itself. DS = 40h.
cf_chgline:
    test byte [0x8F], 8
    jnz  .r                        ; (TEST clears CF)
    stc
.r: ret
mem_end:

; ---------------------------------------------------------------------
section code3
; ---------------------------------------------------------------------
s_fpu387:   db "387 present", 0

; ---------------------------------------------------------------------
section code
; ---------------------------------------------------------------------
s_low:      db "Low", 0

; ---------------------------------------------------------------------
section lba
; ---------------------------------------------------------------------
s_ok:       db "OK", 0

; ---------------------------------------------------------------------
; AMI's "WAIT......" text (76AC-76B8), unused since 2.0
section wait start=0x3300 vstart=0x76AC
; ---------------------------------------------------------------------
s_cx486dlc: db "Cyrix 486DLC", 0
