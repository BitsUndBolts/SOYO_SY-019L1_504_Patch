; =====================================================================
;  BUBios -- MR-BIOS-style front end for the AMIBIOS 11/11/92 setup
;  (SOYO SY-019L1, OPTi 82C495SLC, 27C512) -- trial version 0.1
;
;  Built on top of the hardware-confirmed ECHS ROM
;  (binary/SY019L1_27C512_CHSPATCH.BIN of the parent project).
;
;  What this adds (all patch sites are applied and verified by
;  py/build_bubios.py, which asserts the original bytes first):
;
;    0x36D4  AMI main menu            -> JMP bu_main   (tab bar + pages)
;    0x38B5  AMI screen header        -> JMP bu_hdr    (title bar + tabs)
;    0x0D3D  option-editor getkey     -> CALL bu_edkey (Tab / Shift-Tab)
;    0x08D5  Standard-CMOS getkey     -> CALL bu_stdkey + NOP
;    0x5037  16 colour schemes        -> BUBios schemes (F2/F3 cycles them)
;    0x4FF6  "bright" OR-mask 0Fh     -> 08h (intensity only, keeps hue)
;    0x309A / 0x6D3A  footer texts    -> Tab/ESC hints
;
;  Code lives in the free block 0xDA84-0xDFFF (executed from F000).
;  Data (and the small timing loop) live in the free block 0x7902-0x7EFD.
;  The setup program copies F000:0000-DFFF to 0100:0000 and runs with
;  DS=SS=0100h, so DS-relative data must sit below 0xE000 -- and not in
;  0xDA84-0xDFFF, which is the setup's stack in that RAM copy.  That is
;  why every string/table is in the 0x79xx block.
; =====================================================================
[map all bubios.map]
BITS 16
CPU 386

; ---- AMI setup routines (all near, segment F000) --------------------
A_RDCMOS   equ 0x84CB     ; read CMOS into the image at DS:E000
A_VIDINIT  equ 0x5077     ; video mode + colour scheme -> E1A2..E1A9
A_CUROFF   equ 0x0995     ; hide cursor
A_SETUPOK  equ 0x54AE     ; AX<>0 if menu item [E187] is available
A_NOSETUP  equ 0x3704     ; "NO CMOS SETUP PRESENT" path (exits setup)
A_BOX      equ 0x5199     ; box  AX=top-left DX=bottom-right BX=style
A_FILL     equ 0x520E     ; fill AX=top-left DX=bottom-right, attr [E19F]
A_HLINE    equ 0x526E     ; horizontal line AX=start DL=len BX=style
A_VLINE    equ 0x5286     ; vertical line   AX=start DL=len BX=style
A_PRINT    equ 0x52DF     ; print DS:BX at AX=row:col, attr [E19F]
A_PRINTB   equ 0x52BB     ; same, bright
A_PRINTC   equ 0x52D2     ; same, centred on column AL
A_PUTCH    equ 0x52FA     ; print char AL at DX=row:col
A_FOOTER   equ 0x390F     ; framed footer caption DS:BX centred at AX
A_GETKEY   equ 0x530E     ; AX = ASCII, or 100h+scan for extended keys
A_COLOUR   equ 0x50BA     ; F2/F3 colour cycling (reads [E182])
A_SAVEEXIT equ 0x29DD     ; "Write to CMOS and Exit (Y/N)?"
A_QUIT     equ 0x29EC     ; "Want to Quit Without Saving (Y/N)?"
A_VALSTR   equ 0x5B21     ; AX = value string of option record [E18F]
H_STD      equ 0x00EC     ; Standard CMOS setup page
H_ADV      equ 0x0BDE     ; Advanced CMOS setup page
H_CHIP     equ 0x0C05     ; Advanced chipset setup page
H_DEF      equ 0x5486     ; load BIOS / power-on defaults ([E187]=5/6)
H_PWD      equ 0xF1C8     ; change password
H_DETECT   equ 0x4A44     ; auto-detect hard disk
CALC_FACTOR equ 0x76F5    ; ECHS head-factor routine of the xlate patch

; ---- AMI option records used on the Summary page ----------------------
R_EXTCACHE equ 0xBDE7     ; External Cache Memory
R_VSHADOW  equ 0xBE8F     ; Video ROM Shadow C000,16K
R_SSHADOW  equ 0xC057     ; System ROM Shadow F000,64K
R_BOOTSEQ  equ 0xBDA3     ; System Boot Up Sequence
R_NUMLOCK  equ 0xBD1D     ; System Boot Up Num Lock
R_PASSWORD equ 0xBE6D     ; Password Checking Option

; ---- AMI strings reused (verified by the build script) ----------------
S_FLOPPY   equ 0x6F35     ; "360  KB, 5.."  stride 17
S_MONO     equ 0x6F8A
S_COL80    equ 0x6F9B
S_COL40    equ 0x6FAC
S_VGA      equ 0x6FBD
S_HELP_DEF equ 0x2D4F
S_HELP_PON equ 0x2D9B
S_HELP_PWD equ 0x2DE5
S_HELP_DET equ 0x2E11
S_HELP_SAV equ 0x2E7C
S_HELP_QUT equ 0x2E75

; ---- setup data segment (DS = 0100h) ---------------------------------
CMOS       equ 0xE000     ; CMOS image, CMOS+n = register n
V_KEY      equ 0xE182
V_ITEM     equ 0xE187     ; current AMI main-menu item (handlers read it)
V_REC      equ 0xE18F
V_VSTR     equ 0xE197
V_ATTR     equ 0xE19F     ; current drawing attribute
C_CONTENT  equ 0xE1A2
C_SEL      equ 0xE1A3
C_HDR      equ 0xE1A8
AMI_VSTR   equ 0x8292     ; word: base of the option value strings
AMI_VIDSEG equ 0x4FF4     ; word: B800h / B000h

; BUBios state -- in the setup's zero-initialised, otherwise unused area
BU_TAB     equ 0xEF80     ; current tab 0..NTABS-1
BU_PEND    equ 0xEF81     ; Tab (+1) / Shift-Tab (-1) pressed inside a page
BU_SEL     equ 0xEF82     ; selected entry on the Tools / Exit pages
BU_MHZ     equ 0xEF84     ; word: measured clock in 1/10 MHz
BU_CPU     equ 0xEF86     ; CPU family 3/4/5/6
BU_BUF     equ 0xEF90     ; 40-byte text buffer

NTABS      equ 6
LBLW       equ 18         ; label field width on the Summary page
ITEMSZ     equ 7          ; size of a Tools/Exit list entry

; =====================================================================
section code start=0 vstart=0xDA84
; =====================================================================

; ---------------------------------------------------------------------
; bu_main -- replaces the AMI main menu (entered by JMP from 0x2997)
; ---------------------------------------------------------------------
bu_main:
    call A_RDCMOS
    call A_VIDINIT
    call A_CUROFF
    call bu_detect
    mov  byte [V_ITEM], 0
    call A_SETUPOK
    or   ax, ax
    jnz  .loop
    mov  ax, s_foot_main
    call bu_hdr
    jmp  A_NOSETUP

.loop:
    mov  al, [BU_TAB]
    cmp  al, 1
    jb   .menu
    cmp  al, 3
    ja   .menu
    ; ---- AMI pages: Standard / Advanced / Chipset ----
    dec  al
    mov  [V_ITEM], al
    cbw
    mov  bx, ax
    shl  bx, 1
    mov  byte [BU_PEND], 0
    call [bx+page_handlers]
    mov  al, [BU_PEND]
    or   al, al
    jnz  .step
    mov  byte [BU_TAB], 0          ; plain ESC -> back to Summary
    jmp  .loop

.menu:
    call bu_draw_page
    call A_GETKEY
    mov  [V_KEY], ax
    cmp  ax, 0x001B
    je   .esc
    cmp  ax, 0x0009
    je   .next
    cmp  ax, 0x014D
    je   .next
    cmp  ax, 0x010F
    je   .prev
    cmp  ax, 0x014B
    je   .prev
    cmp  ax, 0x0148
    je   .up
    cmp  ax, 0x0150
    je   .down
    cmp  ax, 0x000D
    je   .enter
    cmp  ax, 0x0144
    je   .f10
    cmp  ax, 0x013C
    je   .colour
    cmp  ax, 0x013D
    je   .colour
    jmp  .loop

.esc:
    mov  al, NTABS-1               ; ESC on Summary -> Exit page
    cmp  byte [BU_TAB], 0
    je   .set
    xor  al, al                    ; ESC elsewhere -> Summary
.set:
    mov  [BU_TAB], al
    mov  byte [BU_SEL], 0
    jmp  .loop
.next:
    mov  al, 1
    jmp  .step
.prev:
    mov  al, -1
.step:
    add  al, [BU_TAB]
    jns  .s1
    add  al, NTABS
.s1:
    cmp  al, NTABS
    jb   .set
    sub  al, NTABS
    jmp  .set

.up:
    call bu_list
    or   cx, cx
    jz   .loop
    mov  al, [BU_SEL]
    dec  al
    jns  .sel
    mov  al, cl
    dec  al
    jmp  .sel
.down:
    call bu_list
    or   cx, cx
    jz   .loop
    mov  al, [BU_SEL]
    inc  al
    cmp  al, cl
    jb   .sel
    xor  al, al
.sel:
    mov  [BU_SEL], al
    jmp  .loop

.enter:
    call bu_list
    or   cx, cx
    jz   .loop
    call bu_item                   ; BX -> entry
    mov  al, [bx+4]
    mov  [V_ITEM], al
    call [bx+5]
    jmp  .loop

.f10:
    mov  byte [V_ITEM], 10
    call A_SAVEEXIT                ; returns only if the answer was N
    jmp  .loop

.colour:
    call A_COLOUR
    jmp  .loop

; ---------------------------------------------------------------------
; bu_list: SI -> entry list of the current tab, CX = number of entries
; bu_item: BX -> entry [BU_SEL] of list SI
; ---------------------------------------------------------------------
bu_list:
    xor  cx, cx
    mov  si, tools_list
    mov  cl, 4
    cmp  byte [BU_TAB], 4
    je   .r
    mov  si, exit_list
    mov  cl, 2
    cmp  byte [BU_TAB], 5
    je   .r
    xor  cx, cx
.r: ret

bu_item:
    mov  al, [BU_SEL]
    mov  ah, ITEMSZ
    mul  ah
    mov  bx, si
    add  bx, ax
    ret

; ---------------------------------------------------------------------
; bu_hdr -- replaces the AMI screen header (0x38B5).
;   In: AX = footer caption (row 24), BX = page title (unused: the
;       active tab names the page).  All registers preserved.
; ---------------------------------------------------------------------
bu_hdr:
    pusha
    push ax
    mov  al, [C_HDR]
    mov  [V_ATTR], al
    xor  ax, ax                    ; row 0: title bar
    mov  dx, 0x004F
    call A_FILL
    mov  bx, s_title
    mov  ax, 0x0001
    call A_PRINTB
    mov  ax, 0x0100                ; frame rows 1..24
    mov  dx, 0x184F
    mov  bx, 3
    call A_BOX
    mov  ax, 0x0300                ; separator under the tab bar
    mov  dl, 0x50
    mov  bx, 0x2017
    call A_HLINE
    ; tab bar (row 2)
    mov  si, tab_table
    xor  cx, cx
.tab:
    mov  al, [C_HDR]
    cmp  cl, [BU_TAB]
    jne  .t1
    call bu_tabattr
.t1:
    mov  [V_ATTR], al
    lodsb                          ; column
    mov  ah, 2
    mov  bx, [si]
    add  si, 2
    push cx
    call A_PRINT
    pop  cx
    inc  cx
    cmp  cx, NTABS
    jb   .tab
    ; footer and content area
    pop  bx
    mov  ax, 0x1828
    call A_FOOTER
    mov  al, [C_CONTENT]
    mov  [V_ATTR], al
    mov  ax, 0x0401
    mov  dx, 0x174E
    call A_FILL
    popa
    ret

; active-tab attribute: one per colour scheme, inverse video on mono
bu_tabattr:
    mov  al, 0x70
    cmp  word [AMI_VIDSEG], 0xB000
    je   .r
    mov  bl, [CMOS+0x37]
    and  bx, 0x000F
    mov  al, [bx+tab_attrs]
.r: ret

; ---------------------------------------------------------------------
; bu_draw_page -- Summary / Tools / Exit pages
; ---------------------------------------------------------------------
bu_draw_page:
    xor  bx, bx
    mov  ax, s_foot_main
    call bu_hdr
    mov  al, [C_HDR]
    mov  [V_ATTR], al
    mov  ax, 0x1600                ; line above the help row
    mov  dl, 0x50
    mov  bx, 0x2017
    call A_HLINE
    cmp  byte [BU_TAB], 0
    je   bu_summary
    ; ---- list page ----
    call bu_list
    jcxz .done
    xor  bx, bx                    ; bl = index
    mov  dh, 7                     ; first row
.item:
    push cx
    push si
    push bx
    push dx
    mov  al, [C_CONTENT]
    cmp  bl, [BU_SEL]
    jne  .a
    mov  al, [C_SEL]
.a: mov  [V_ATTR], al
    mov  ah, dh                    ; selection bar, columns 22..57
    mov  al, 22
    mov  dl, 57
    call A_FILL
    pop  dx
    pop  bx
    push bx
    push dx
    mov  al, bl
    mov  ah, ITEMSZ
    mul  ah
    mov  bx, si
    add  bx, ax
    mov  bx, [bx]                  ; label
    mov  ah, dh
    mov  al, 24
    call A_PRINT
    pop  dx
    pop  bx
    pop  si
    pop  cx
    add  dh, 2
    inc  bl
    loop .item
    ; help text of the selected entry, row 23
    call bu_list
    call bu_item
    mov  al, [C_CONTENT]
    mov  [V_ATTR], al
    mov  bx, [bx+2]
    mov  ax, 0x1728
    call A_PRINTC
.done:
    ret

; ---------------------------------------------------------------------
; bu_summary -- the entry page
; ---------------------------------------------------------------------
bu_summary:
    mov  ax, 0x0327                ; column divider rows 3..22
    mov  dl, 0x14
    mov  bx, 0x408B
    call A_VLINE
    mov  al, [C_CONTENT]
    mov  [V_ATTR], al
    mov  bx, s_tagline
    mov  ax, 0x1728
    call A_PRINTC
    mov  si, sum_table
.e:
    lodsb
    cmp  al, 0xFF
    je   .done
    mov  dh, al                    ; row
    lodsb
    mov  dl, al                    ; column
    lodsw
    mov  di, ax                    ; label
    lodsw                          ; value descriptor
    push si
    push dx
    push di
    push bp
    call bu_value                  ; BX = value string or 0
    pop  bp
    pop  di
    pop  dx
    or   bx, bx
    jz   .skip
    push bx
    mov  al, [C_CONTENT]
    mov  [V_ATTR], al
    mov  bx, di
    mov  ax, dx
    call A_PRINT
    ; dotted leader
    mov  bx, di
    call A_STRLEN_wrap             ; CX = label length
    push dx
    add  dl, cl
    inc  dl                        ; first dot column
    mov  cx, LBLW
    sub  cl, al                    ; (AL = label length)
    dec  cx
.dot:
    jcxz .dd
    mov  al, 0xFA                  ; middle dot
    push cx
    call A_PUTCH
    pop  cx
    inc  dl
    dec  cx
    jmp  .dot
.dd:
    pop  dx
    pop  bx
    mov  ax, dx
    add  al, LBLW+1
    call A_PRINTB
.skip:
    pop  si
    jmp  .e
.done:
    ret

; label length helper: BX = string -> AX = CX = length (BX preserved)
A_STRLEN_wrap:
    push bx
    xor  cx, cx
.l: cmp  byte [bx], 0
    je   .r
    inc  bx
    inc  cx
    jmp  .l
.r: mov  ax, cx
    pop  bx
    ret

; ---------------------------------------------------------------------
; bu_value -- AX = value descriptor -> BX = string (0 = hide line)
;   < 8000h        : DS string
;   B000h..CFFFh   : AMI option record -> its current value text
;   >= D000h       : routine that returns BX
; ---------------------------------------------------------------------
bu_value:
    mov  bx, ax
    cmp  ax, 0x8000
    jb   .r
    cmp  ax, 0xD000
    jae  .fn
    mov  [V_REC], ax
    mov  ax, [AMI_VSTR]
    mov  [V_VSTR], ax
    call A_VALSTR
    mov  bx, ax
.r: ret
.fn:
    jmp  ax

; ---------------------------------------------------------------------
; text buffer helpers (DS:DI)
; ---------------------------------------------------------------------
buf_begin:
    mov  di, BU_BUF
    ret
buf_end:
    mov  byte [di], 0
    mov  bx, BU_BUF
    ret
put_ch:
    mov  [di], al
    inc  di
    ret
put_str:                           ; DS:SI
    lodsb
    or   al, al
    jz   .r
    call put_ch
    jmp  put_str
.r: ret
put_num:                           ; EAX unsigned decimal
    xor  cx, cx
    mov  ebx, 10
.d: xor  edx, edx
    div  ebx
    push dx
    inc  cx
    or   eax, eax
    jnz  .d
.o: pop  ax
    add  al, '0'
    call put_ch
    loop .o
    ret
put_chs:                           ; CX / BL / BH
    push bx
    movzx eax, cx
    call put_num
    mov  al, '/'
    call put_ch
    pop  bx
    push bx
    movzx eax, bl
    call put_num
    mov  al, '/'
    call put_ch
    pop  bx
    movzx eax, bh
    jmp  put_num
num_k:                             ; AX -> "nnnnK"
    movzx eax, ax
    call buf_begin
    call put_num
    mov  al, 'K'
    call put_ch
    jmp  buf_end

; ---------------------------------------------------------------------
; Summary value routines (return BX)
; ---------------------------------------------------------------------
f_cpu:
    mov  bl, [BU_CPU]
    cmp  bl, 6
    jbe  .ok
    mov  bl, 6
.ok:
    sub  bl, 3
    xor  bh, bh
    shl  bx, 1
    mov  bx, [bx+cpu_names]
    ret

f_mhz:
    call buf_begin
    movzx eax, word [BU_MHZ]
    xor  edx, edx
    mov  ebx, 10
    div  ebx
    push dx
    call put_num
    mov  al, '.'
    call put_ch
    pop  ax
    add  al, '0'
    call put_ch
    mov  si, s_mhz
    call put_str
    jmp  buf_end

f_fpu:
    push ds
    xor  ax, ax
    mov  ds, ax
    mov  al, [0x410]
    pop  ds
    mov  bx, s_present
    test al, 2
    jnz  .r
    mov  bx, s_none
.r: ret

f_base:
    push ds
    xor  ax, ax
    mov  ds, ax
    mov  ax, [0x413]
    pop  ds
    jmp  num_k

ext_kb:
    mov  ah, 0x88
    int  0x15
    jnc  .r
    mov  ax, [CMOS+0x17]
.r: ret
f_ext:
    call ext_kb
    jmp  num_k
f_total:
    call ext_kb
    add  ax, 1024
    jmp  num_k

f_fda:
    mov  al, [CMOS+0x10]
    shr  al, 4
    jmp  floppy
f_fdb:
    mov  al, [CMOS+0x10]
    and  al, 0x0F
floppy:
    mov  bx, s_none
    or   al, al
    jz   .r
    cmp  al, 5
    ja   .r
    dec  al
    mov  ah, 17
    mul  ah
    add  ax, S_FLOPPY
    mov  bx, ax
.r: ret

f_video:
    mov  bl, [CMOS+0x14]
    shr  bl, 3
    and  bx, 0x0006
    mov  bx, [bx+video_names]
    ret

; ---- hard disks --------------------------------------------------------
; drv_get: DL = 0 (C:) / 1 (D:)
;   -> CF=1 not installed, else AL=type CX=cyl BL=heads BH=sectors
drv_get:
    mov  al, [CMOS+0x12]
    or   dl, dl
    jnz  .d
    shr  al, 4
    jmp  .t
.d: and  al, 0x0F
.t: cmp  al, 0x0F
    jne  .h
    xor  bx, bx
    mov  bl, dl
    mov  al, [bx+CMOS+0x19]
.h: or   al, al
    jz   .none
    cmp  al, 47
    jne  .rom
    mov  si, CMOS+0x1B
    or   dl, dl
    jz   .c
    add  si, 9
.c: mov  cx, [si]
    mov  bl, [si+2]
    mov  bh, [si+8]
    jmp  .chk
.rom:
    cmp  al, 46
    ja   .none
    push ax
    mov  ah, 16
    mul  ah
    add  ax, 0xE3F1                ; ROM drive table, entry 1 at E401
    mov  si, ax
    pop  ax
    mov  cx, [cs:si]
    mov  bl, [cs:si+2]
    mov  bh, [cs:si+0x0E]
.chk:
    or   cx, cx                    ; reject empty / corrupt parameters
    jz   .none
    or   bl, bl
    jz   .none
    or   bh, bh
    jz   .none
    clc
    ret
.none:
    stc
    ret

f_c0:
    xor  dl, dl
    jmp  drv_size
f_d0:
    mov  dl, 1
drv_size:
    call drv_get
    jnc  .ok
    mov  bx, s_none
    ret
.ok:
    push ax
    movzx eax, cx
    movzx ecx, bl
    mul  ecx
    movzx ecx, bh
    mul  ecx
    shr  eax, 11                   ; bytes/512/2048 -> MB
    call buf_begin
    call put_num
    mov  si, s_mb_type
    call put_str
    pop  ax
    movzx eax, al
    call put_num
    mov  al, ']'
    call put_ch
    jmp  buf_end

f_c1:
    xor  dl, dl
    jmp  drv_phys
f_d1:
    mov  dl, 1
drv_phys:
    call drv_get
    jc   drv_none
    call buf_begin
    call put_chs
    jmp  buf_end
drv_none:
    xor  bx, bx
    ret

f_c2:
    xor  dl, dl
    jmp  drv_log
f_d2:
    mov  dl, 1
drv_log:
    call drv_get
    jc   drv_none
    cmp  cx, 1024
    ja   .xl
    mov  bx, s_notrans
    ret
.xl:
    mov  si, bx                    ; SI: heads / sectors
    mov  ax, cx
    push ax
    xor  cx, cx
    mov  cl, bl
    call CALC_FACTOR               ; AX = translated heads (CX kept)
    mov  bp, ax
    pop  ax
    mul  cx                        ; DX:AX = cyl * heads
    div  bp
    dec  ax                        ; cylinders DOS sees (AH=08h convention)
    cmp  ax, 1024
    jbe  .ok
    mov  ax, 1024
.ok:
    mov  cx, ax
    mov  bx, si
    mov  ax, bp
    mov  bl, al
    call buf_begin
    call put_chs
    jmp  buf_end

code_end:

; =====================================================================
section data start=0x800 vstart=0x7902
; =====================================================================

; timing loop -- far-called at 0100:bu_tloop (RAM copy of this block)
bu_tloop:
    mov  cx, 2000
    mov  bx, 1
    xor  dx, dx
    mov  ax, 0x1234
.l: div  bx
    div  bx
    div  bx
    div  bx
    div  bx
    div  bx
    div  bx
    div  bx
    loop .l
    retf
bu_tloop_end:

; (code that does not need to sit above D000h lives here too)
; ---------------------------------------------------------------------
; Tab / Shift-Tab inside the AMI pages: remember the direction and hand
; the page an ESC so it closes normally; bu_main then opens the next tab.
; ---------------------------------------------------------------------
bu_edkey:                          ; replaces CALL 530E at 0x0D3D
    call A_GETKEY
    cmp  ax, 0x0009
    je   .fwd
    cmp  ax, 0x010F
    jne  .r
    mov  byte [BU_PEND], -1
    jmp  .esc
.fwd:
    mov  byte [BU_PEND], 1
.esc:
    mov  ax, 0x001B
.r: ret

bu_stdkey:                         ; replaces MOV AH,0 / INT 16h at 0x08D5
    mov  ah, 0
    int  0x16
    cmp  ax, 0x0F09
    je   .fwd
    cmp  ax, 0x0F00
    jne  .r
    mov  byte [BU_PEND], -1
    jmp  .esc
.fwd:
    mov  byte [BU_PEND], 1
.esc:
    mov  ax, 0x011B
.r: ret

; ---------------------------------------------------------------------
; bu_detect -- CPU family and clock, measured once on setup entry
; ---------------------------------------------------------------------
bu_detect:
    pushad
    mov  byte [BU_CPU], 3
    pushfd
    pop  eax
    mov  ecx, eax
    xor  eax, 0x40000              ; AC flag: only 486+ can toggle it
    push eax
    popfd
    pushfd
    pop  eax
    push ecx
    popfd
    xor  eax, ecx
    test eax, 0x40000
    jz   .clock
    mov  byte [BU_CPU], 4
    mov  eax, ecx
    xor  eax, 0x200000             ; ID flag -> CPUID available
    push eax
    popfd
    pushfd
    pop  eax
    push ecx
    popfd
    xor  eax, ecx
    test eax, 0x200000
    jz   .clock
    mov  eax, 1
    db   0x0F, 0xA2                ; CPUID
    shr  ax, 8
    and  al, 0x0F
    mov  [BU_CPU], al
.clock:
    ; PIT channel 2, mode 0, count FFFFh, gated by port 61h bit 0
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
    call 0x0100:bu_tloop           ; runs from the setup's RAM copy
    mov  al, 0x80                  ; latch channel 2
    out  0x43, al
    in   al, 0x42
    mov  ah, al
    in   al, 0x42
    xchg al, ah
    not  ax                        ; elapsed PIT ticks
    mov  bx, ax
    pop  ax
    out  0x61, al
    sti
    ; MHz*10 = K[cpu] / ticks
    xor  si, si
    mov  al, [BU_CPU]
    cmp  al, 4
    jb   .k
    mov  si, 4
    je   .k
    mov  si, 8
.k: mov  eax, [si+clock_k]
    xor  edx, edx
    movzx ebx, bx
    or   bx, bx
    jz   .done
    div  ebx
    ; snap to the nearest standard clock within 1/16
    mov  si, clock_std
.sn:
    mov  cx, [si]
    jcxz .store
    add  si, 2
    mov  dx, ax
    sub  dx, cx
    jns  .abs
    neg  dx
.abs:
    mov  bx, cx
    shr  bx, 4
    cmp  dx, bx
    ja   .sn
    mov  ax, cx
.store:
    mov  [BU_MHZ], ax
.done:
    popad
    ret


; cycles per loop iteration x 2000 x 11.93182 (PIT MHz x 10)
clock_k:
    dd 190 * 23864                 ; 386:  8 x DIV r16 (22) + LOOP
    dd 199 * 23864                 ; 486:  8 x DIV r16 (24) + LOOP
    dd 206 * 23864                 ; 586+: 8 x DIV r16 (25) + LOOP
clock_std:
    dw 160, 200, 250, 333, 400, 500, 600, 666, 750, 800, 1000, 1200, 1330, 1500, 1660, 2000, 0

page_handlers:
    dw H_STD, H_ADV, H_CHIP

tab_table:
    db 3
    dw s_tab0
    db 14
    dw s_tab1
    db 26
    dw s_tab2
    db 38
    dw s_tab3
    db 49
    dw s_tab4
    db 58
    dw s_tab5

; list entries: label, help, AMI item number, handler
tools_list:
    dw s_it_def
    dw S_HELP_DEF
    db 5
    dw H_DEF
    dw s_it_pon
    dw S_HELP_PON
    db 6
    dw H_DEF
    dw s_it_pwd
    dw S_HELP_PWD
    db 7
    dw H_PWD
    dw s_it_det
    dw S_HELP_DET
    db 8
    dw H_DETECT
exit_list:
    dw s_it_sav
    dw S_HELP_SAV
    db 10
    dw A_SAVEEXIT
    dw s_it_qut
    dw S_HELP_QUT
    db 11
    dw A_QUIT

; Summary page: row, column, label, value descriptor
%define LC 3
%define RC 42
sum_table:
    db 5,  LC
    dw l_cpu,    f_cpu
    db 6,  LC
    dw l_clock,  f_mhz
    db 7,  LC
    dw l_fpu,    f_fpu
    db 8,  LC
    dw l_chip,   s_chipset
    db 10, LC
    dw l_base,   f_base
    db 11, LC
    dw l_ext,    f_ext
    db 12, LC
    dw l_total,  f_total
    db 14, LC
    dw l_cache,  R_EXTCACHE
    db 15, LC
    dw l_vshad,  R_VSHADOW
    db 16, LC
    dw l_sshad,  R_SSHADOW
    db 18, LC
    dw l_core,   s_core
    db 19, LC
    dw l_ver,    s_ver
    db 5,  RC
    dw l_fda,    f_fda
    db 6,  RC
    dw l_fdb,    f_fdb
    db 8,  RC
    dw l_hdc,    f_c0
    db 9,  RC
    dw l_phys,   f_c1
    db 10, RC
    dw l_log,    f_c2
    db 12, RC
    dw l_hdd,    f_d0
    db 13, RC
    dw l_phys,   f_d1
    db 14, RC
    dw l_log,    f_d2
    db 16, RC
    dw l_boot,   R_BOOTSEQ
    db 17, RC
    dw l_numl,   R_NUMLOCK
    db 18, RC
    dw l_pwd,    R_PASSWORD
    db 19, RC
    dw l_video,  f_video
    db 0xFF

cpu_names:   dw s_386, s_486, s_586, s_686
video_names: dw S_VGA, S_COL40, S_COL80, S_MONO

; active-tab attribute per colour scheme (index = CMOS 37h low nibble)
tab_attrs:
    db 0x20, 0x60, 0x20, 0x70, 0x70, 0x70, 0x20, 0x60
    db 0x20, 0x70, 0x70, 0x70, 0x20, 0x60, 0x20, 0x70

s_title:   db "BUBios (tm)    Copyright (c) 2026 Bits und Bolts    Ver 0.1    Port OPTi 495SLC", 0
s_tab0:    db " Summary ", 0
s_tab1:    db " Standard ", 0
s_tab2:    db " Advanced ", 0
s_tab3:    db " Chipset ", 0
s_tab4:    db " Tools ", 0
s_tab5:    db " Exit ", 0
s_foot_main: db 0xB5, " ESC:Exit  ", 0x1B, 0x1A, "/Tab:Page  ", 0x18, 0x19, ":Select  Enter:Run  F2/F3:Color  F10:Save & Exit ", 0xC6, 0
s_tagline: db "Bits und Bolts  ", 0xFE, "  ECHS large-disk support up to 8.4 GB", 0

s_it_def:  db "Load BIOS Setup Defaults", 0
s_it_pon:  db "Load Power-On Defaults", 0
s_it_pwd:  db "Change Password", 0
s_it_det:  db "Auto-Detect Hard Disk", 0
s_it_sav:  db "Save Settings and Exit", 0
s_it_qut:  db "Exit Without Saving", 0

l_cpu:     db "CPU Type", 0
l_clock:   db "CPU Clock", 0
l_fpu:     db "Math Unit", 0
l_chip:    db "Chipset", 0
l_base:    db "Base Memory", 0
l_ext:     db "Extended Memory", 0
l_total:   db "Total Memory", 0
l_cache:   db "External Cache", 0
l_vshad:   db "Video Shadow", 0
l_sshad:   db "System Shadow", 0
l_core:    db "BIOS Core", 0
l_ver:     db "BUBios", 0
l_fda:     db "Floppy A:", 0
l_fdb:     db "Floppy B:", 0
l_hdc:     db "Hard Disk C:", 0
l_hdd:     db "Hard Disk D:", 0
l_phys:    db " ", 0xC3, 0xC4, " Physical", 0
l_log:     db " ", 0xC0, 0xC4, " DOS (ECHS)", 0
l_boot:    db "Boot Sequence", 0
l_numl:    db "Boot NumLock", 0
l_pwd:     db "Password Check", 0
l_video:   db "Video Display", 0

s_chipset: db "OPTi 82C495SLC", 0
s_core:    db "AMIBIOS 11/11/92", 0
s_ver:     db "0.1 trial", 0
s_386:     db "80386", 0
s_486:     db "80486", 0
s_586:     db "Pentium", 0
s_686:     db "686 class", 0
s_mhz:     db " MHz", 0
s_present: db "Present", 0
s_none:    db "None", 0
s_notrans: db "not needed", 0
s_mb_type: db " MB  [", 0

data_end:
