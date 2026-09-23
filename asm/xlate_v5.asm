BITS 16
CPU 386
ORG 0x76F5          ; free-space block inside the ROM image (segment F000 implied)

; =====================================================================
;  CHS TRANSLATION PATCH for Soyo 386 AMI BIOS (27C512, dated 11/11/92)
;
;  Adds bit-shift ("Large"/ECHS-style) CHS translation to the legacy
;  hard-disk INT13h handler so drives up to the ~7.88 GB INT13 CHS
;  ceiling can be addressed, instead of the native 504 MB CHS wall
;  (1024 cyl x 16 heads x 63 sect, imposed by raw CHS pass-through
;  to the IDE task-file registers with no translation logic).
;
;  Two call sites in the original ROM are patched to reach this code
;  (see build.py for the exact byte patches applied at 0xA659 and
;  0xA965); nothing else in the ROM is modified.
; =====================================================================

; ---------------------------------------------------------------
; calc_factor                                            -- v5
;   In:  AX = RealCyl (word), CL = RealHeads (byte, CH=0)
;   Out: AX = TranslatedHeads  (RealHeads * 2^n, or 255)
;   Preserves: BX, CX, DX, SI, DI, ES, DS
;
;   v5 change: the head count is never allowed to reach 256.
;   256 heads (DH=255 reported as "max head index") is fatal for
;   MS-DOS/Win9x: FDISK reports the right size but fails to write the
;   partition table, and some geometries hang the machine. The classic
;   BIOS answer is used instead: head counts go 16,32,64,128 and then
;   jump straight to 255 (Phoenix/AMI "Large"/ECHS table), which also
;   lifts the ceiling to 1024 x 255 x 63 x 512 = 7.88 GB.
; ---------------------------------------------------------------
calc_factor:
    push bx
    mov  bx, ax            ; bx = working cylinder count
    mov  ax, cx            ; ax = candidate head count = RealHeads
.loop:
    cmp  bx, 1024
    jbe  .done             ; cylinders already fit -> done
    cmp  ax, 127
    ja   .max              ; doubling would pass 255 -> use 255 heads
    shl  ax, 1             ; heads *= 2
    shr  bx, 1             ; cylinders /= 2
    jmp  .loop
.max:
    mov  ax, 255
.done:
    pop  bx
    ret

; ---------------------------------------------------------------
; xlate_getparams  (replaces the CHS-reporting body of AH=08h,
;                   original bytes at 0xA659-0xA685)
;   In:  ES:BX -> FDPT  (word +0h = RealCyl, byte +2h = RealHeads,
;                         byte +0Eh = RealSectors)
;   Out: CH = translated max-cyl low byte
;        CL = (max-cyl hi bits << 6) | sectors/track
;        DH = translated max-head index (TranslatedHeads - 1)
;        AL = sectors/track   (what the original code left in AL)
;   DL/AH are set by the original tail at 0xA686, which v4+ preserves.
;   All 32-bit registers are untouched (16-bit math only).
; ---------------------------------------------------------------
xlate_getparams:
    push bx
    push si
    push di
    mov  dx, word [es:bx]         ; dx = RealCyl
    xor  ch, ch
    mov  cl, byte [es:bx+2]       ; cx = RealHeads
    mov  al, byte [es:bx+0x0e]    ; al = RealSectors
    xor  ah, ah
    push ax                        ; stash RealSectors
    push cx                        ; stash RealHeads
    push dx                        ; stash RealCyl
    mov  ax, dx
    call calc_factor               ; ax = TranslatedHeads
    mov  di, ax                    ; di = TranslatedHeads

    pop  ax                        ; ax = RealCyl
    pop  cx                        ; cx = RealHeads
    mul  cx                        ; dx:ax = RealCyl * RealHeads
    div  di                        ; ax = RealCyl*RealHeads / TransHeads
                                   ; (<= RealCyl, so it always fits)

    sub  ax, 2                     ; match original "-2" reporting convention
    cmp  ax, 0x3ff
    jbe  .cylok
    mov  ax, 0x3ff
.cylok:
    mov  ch, al                    ; CH = low 8 bits of max cyl index
    mov  bl, ah
    shl  bl, 6
    mov  cl, bl                    ; CL bits 7:6 = cyl high bits
    pop  ax                        ; ax = RealSectors
    or   cl, al                    ; CL bits 5:0 = sectors/track

    push ax                        ; keep RealSectors for AL
    mov  ax, di                    ; ax = TranslatedHeads
    dec  ax
    mov  dh, al                    ; DH = translated max head index
    pop  ax                        ; AL = sectors/track, AH = 0

    pop  di
    pop  si
    pop  bx
    ret

; ---------------------------------------------------------------
; xlate_headcyl  (replaces "AND DH,0Fh" at 0xA965)   -- v4
;
;   Entry context (exactly as at the original instruction):
;     DH    = original caller HEAD (logical)
;     DI    = INT13h function index * 2 (dispatcher's DI, or the value
;             the reset path loads explicitly: 0x22 recal, 0x12 init)
;     ES:BX = FDPT for the active drive
;     Stack: [ret][orig CX][orig DX] (pushed by 0xA93C..0xA93F)
;
;   Exit:
;     DH           = physical head (0..RealHeads-1)
;     saved CX     = physical CX (cyl lo / cyl bits 9:8 / sector)
;     outer [bp+6] = physical cylinder HIGH BYTE (IDE port 1F5h value);
;                    the three port-write sites now read it from there.
;     The caller's saved DX in the INT13h frame ([bp+14h/15h]) is NOT
;     touched any more (v3 wrote [bp+15h] = caller's DH, so every
;     translated call returned a corrupted DH to the caller).
;     All 32-bit registers preserved (PUSHAD/POPAD).
;
;   Only the functions that actually carry a CHS address are translated:
;     AH=02,03,04,05,0A,0B,0C  (DI=04,06,08,0A,14,16,18)
;   Everything else (reset/recal/test-ready/alt-reset/park...) gets the
;   ORIGINAL behaviour: head = DH & 0Fh, cyl-high byte computed exactly
;   like the original ROM did ((CL>>6) | ((frameDH>>4)&0Ch)).
; ---------------------------------------------------------------
xlate_headcyl:
    push bp
    mov  bp, sp                  ; [bp+0]=outer bp [bp+4]=origCX [bp+6]=origDX
    cmp  di, 0x04
    je   .xl
    cmp  di, 0x06
    je   .xl
    cmp  di, 0x08
    je   .xl
    cmp  di, 0x0a
    je   .xl
    cmp  di, 0x14
    je   .xl
    cmp  di, 0x16
    je   .xl
    cmp  di, 0x18
    je   .xl

    ; ---- untranslated: reproduce original ROM behaviour ----
    push ax
    push bx
    mov  bx, [bp+0]               ; outer INT13h frame bp
    mov  al, [bp+4]               ; orig CL
    shr  al, 6
    mov  ah, [ss:bx+0x15]         ; frame DH (ECHS cyl bits 11:10 in bits 7:6)
    shr  ah, 4
    and  ah, 0x0c
    or   al, ah
    mov  [ss:bx+6], al            ; cyl-high byte for port 1F5h
    pop  bx
    pop  ax
    and  dh, 0x0f
    pop  bp
    ret

.xl:
    sub  sp, 12                   ; locals: -2 logC -4 logH -6 logS
                                  ;         -8 realH -10 realS -12 transH
    pushad                        ; preserve ALL 32-bit registers

    mov  si, word [es:bx]         ; si = RealCyl
    mov  cl, byte [es:bx+2]       ; cl = RealHeads
    xor  ch, ch
    mov  [bp-8], cx
    mov  dl, byte [es:bx+0x0e]    ; dl = RealSectors
    xor  dh, dh
    mov  [bp-10], dx
    mov  ax, si
    call calc_factor              ; ax = TranslatedHeads, si = factor
    mov  [bp-12], ax

    mov  ax, [bp+4]               ; origCX
    mov  bl, al
    and  bl, 0x3f
    xor  bh, bh
    mov  [bp-6], bx               ; logS
    mov  bl, al
    and  bl, 0xc0
    shr  bl, 6
    xor  bh, bh
    mov  dl, ah
    xor  dh, dh
    shl  bx, 8
    or   bx, dx
    mov  [bp-2], bx               ; logC (10 bit)
    mov  al, [bp+7]               ; orig DH
    xor  ah, ah
    mov  [bp-4], ax               ; logH

    movzx eax, word [bp-2]
    movzx ecx, word [bp-12]
    imul  eax, ecx
    movzx edx, word [bp-4]
    add   eax, edx
    movzx ecx, word [bp-10]
    imul  eax, ecx
    movzx edx, word [bp-6]
    dec   edx
    add   eax, edx                ; LBA

    movzx ecx, word [bp-10]
    xor   edx, edx
    div   ecx
    inc   edx
    mov   [bp-6], dx              ; physSect
    movzx ecx, word [bp-8]
    xor   edx, edx
    div   ecx                     ; eax = physCyl, edx = physHead
    mov   [bp-4], dx
    mov   [bp-2], ax

    mov   bx, [bp+0]              ; outer INT13h frame bp
    mov   [ss:bx+6], ah           ; physCyl high byte -> frame local [bp+6]

    mov   ax, [bp-2]
    mov   bh, al
    mov   bl, ah
    shl   bl, 6
    mov   cx, [bp-6]
    or    bl, cl
    mov   [bp+4], bx              ; saved CX := physical CX

    popad
    mov  dh, byte [bp-4]          ; DH = physical head
    add  sp, 12
    pop  bp
    ret
