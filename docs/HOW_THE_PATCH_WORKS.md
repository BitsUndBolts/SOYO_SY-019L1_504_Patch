# How the ECHS Patch Works

A walk-through of every byte that differs between the original ROM
(`binary/SY019L1_27C512_ORIGINAL.BIN`) and the ECHS-patched ROM
(`binary/SY019L1_27C512_CHSPATCH.BIN`). Offsets are file offsets, which
equal offsets in segment `F000`. Paths are relative to the repository
root.

BUBios is built on top of this ROM and leaves all of these bytes
unchanged; its own changes are described in
[`BUBios/README.md`](../BUBios/README.md).

| Offset | Size | Change |
|---|---|---|
| `76F5–7855` | 353 B | **New code** in previously empty (zero) space: the translation routines from `asm/xlate.asm` |
| `7900` | 2 B | Checksum-balancing word (`C6FEh`) |
| `A659–A685` | 45 B | AH=08h reporting body → `CALL xlate_getparams` + `JMP A686` + 40 NOPs |
| `A965` | 3 B | `AND DH,0Fh` → `CALL xlate_headcyl` |
| `A60E–A621` | 20 B | Cylinder-high builder (FORMAT) → `MOV AL,[BP+6]` + 17 NOPs |
| `A7EA–A7F9` | 16 B | Cylinder-high builder (SEEK) → `MOV AL,[BP+6]` + 13 NOPs |
| `AB3D–AB4C` | 16 B | Cylinder-high builder (READ/WRITE/VERIFY) → `MOV AL,[BP+6]` + 13 NOPs |

---

## 1. The problem

DOS talks to the BIOS through INT 13h in CHS: at most 1024 cylinders,
256 heads and 63 sectors. The IDE drive accepts up to 65,536 cylinders
but only 16 heads. The original BIOS passes the CHS values straight
through to the drive's task-file registers, so it is limited to the
smaller value of each field: 1024 × 16 × 63 × 512 = **504 MiB**.

## 2. The idea: ECHS ("Large") translation

The BIOS shows DOS a made-up **logical** geometry with fewer cylinders
and more heads, and converts every logical address back to the drive's
real **physical** geometry before it touches the hardware. Capacity stays
the same; only the shape changes.

## 3. The new code (`asm/xlate.asm`)

### `calc_factor` — choose the logical head count

```
function calc_factor(realCyl, realHeads):
    cyl   = realCyl
    heads = realHeads
    while cyl > 1024:
        if heads > 127:          // doubling again would give 256 or more
            return 255           // 256 heads breaks DOS/FDISK (bug #6)
        heads = heads * 2
        cyl   = cyl / 2
    return heads
```

This is the standard "Large"/ECHS ladder: 16 → 32 → 64 → 128 → 255 heads.

### `xlate_getparams` — what INT 13h AH=08h now reports

```
function xlate_getparams(FDPT):
    transHeads = calc_factor(FDPT.cyl, FDPT.heads)
    logCyl     = FDPT.cyl * FDPT.heads / transHeads   // same capacity, more heads
    maxCyl     = min(logCyl - 2, 1023)                // "-2" = original ROM convention
    CH = maxCyl & 0xFF
    CL = ((maxCyl >> 8) << 6) | FDPT.sectors
    DH = transHeads - 1
    AL = FDPT.sectors
    // the original tail at A686 still sets DL = number of drives, AH = 0
```

Example: the 8 GB CF card is 16000/16/63. The loop goes 16 → 32 → 64 →
128 heads and then jumps to 255. Logical cylinders = 16000 × 16 / 255 =
1003, so DOS is told **1002/255/63** (7.67 GiB).

### `xlate_headcyl` — runs on every read, write, verify, format and seek

Called from the shared routine that prepares the IDE task file, in place
of `AND DH,0Fh`. Instead of bit-shifting, it goes through a linear sector
number:

```
function xlate_headcyl():
    if function not in {02,03,04,05,0A,0B,0C}:     // reset, recal, park, ...
        do exactly what the original ROM did
        return

    // 1. decode the logical CHS the caller passed in CX/DX
    logS = CL & 0x3F
    logC = ((CL & 0xC0) << 2) | CH                 // 10-bit cylinder
    logH = DH

    // 2. logical CHS -> absolute sector number (LBA)
    transHeads = calc_factor(realCyl, realHeads)
    lba = (logC * transHeads + logH) * sectors + (logS - 1)

    // 3. LBA -> physical CHS in the drive's real geometry
    physS = lba % sectors + 1
    t     = lba / sectors
    physH = t % realHeads                          // always < 16
    physC = t / realHeads                          // up to 16 bits

    // 4. hand the results back to the original code
    saved CX     = pack(physC low 8 bits, physC bits 9:8, physS)  // -> ports 1F3h/1F4h
    frame [bp+6] = physC >> 8                                     // -> port 1F5h
    DH           = physH                                          // ORed into port 1F6h
```

The LBA math uses 32-bit registers (`imul`/`div`), which the 386 has; all
of them are saved with `PUSHAD`/`POPAD` (bug #5). Going through the LBA
also handles the 255-head case, which a pure bit-shift cannot, because
255 is not a power of two.

Worked example on the 8 GB card: logical C=500, H=100, S=1 →
LBA = (500×255 + 100)×63 = 8,038,800 → physical **C=7975, H=0, S=1**,
a cylinder the original ROM could never have sent.

---

## 4. The changed original code

**Why NOPs?** Each replacement is shorter than the code it replaces.
Padding with `90h` keeps every later instruction at its original address,
so no jumps or calls anywhere else in the ROM need fixing. At the AH=08h
site the NOPs are never executed (the `JMP` skips them); at the three
port sites they execute, costing well under a microsecond on a 386.

### a) AH=08h reporting body (`A659–A685`)

Original code — reports the physical geometry:

```asm
mov  dh,es:[bx+2] / dec dh        ; DH = heads-1
mov  ax,es:[bx]   / sub ax,2      ; AX = cylinders-2
cmp  ax,3FFh / jbe / mov ax,3FFh  ; clamp to 1023   <- the 504 MB wall
...                               ; pack CH / CL, OR sectors into CL
```

Replaced by `CALL xlate_getparams` / `JMP A686`, which report the logical
geometry. The tail at `A686–A690` (DL = drive count, AH = 0, `RET`) is
deliberately left intact; overwriting it was bugs #2 and #3.

### b) Head mask (`A965`: `AND DH,0Fh` → `CALL xlate_headcyl`)

The IDE drive/head register (1F6h) has only 4 head bits; the mask kept a
caller's DH from spilling into the drive-select bits, and it is also
what silently discarded any head number of 16 or more. The translated
path now produces the physical head, which is always below 16, so the
mask is not needed there. Non-translated functions still get `DH & 0Fh`
inside `xlate_headcyl`.

### c) The three cylinder-high builders (`A60E`, `A7EA`, `AB3D`)

Before each write to port 1F5h (cylinder high byte), the original code
built that byte like this:

```asm
mov  al,cl       / shr al,6                   ; cylinder bits 9:8 from CL
mov  dl,[bp+15h] / shr dl,4 / and dl,0Ch      ; bits 11:10 from caller's DH bits 7:6
or   al,dl                                    ; (AMI extended-cylinder convention)
```

That can express at most 4096 cylinders, and in practice only 1024
because DOS never sets those DH bits; the 8 GB card needs cylinders up to
15,999 (high byte up to `3Eh`). Each block is now just `mov al,[bp+6]`,
reading the full high byte that `xlate_headcyl` computed. For reset,
recalibrate and similar functions `xlate_headcyl` stores the original
formula's result in `[bp+6]`, so they behave exactly as before.
`[bp+15h]` — the caller's saved DH — is only read, never written (bug #4).

### d) Checksum word (`7900`)

POST verifies that the 16-bit word sum of the whole 64 KB ROM is zero.
`py/build.py` sets this word so that the sum balances after all changes.

---

## 5. Reproducing the build and tests

Run from the repository root:

```sh
python3 py/build.py        # rebuilds the ROM, checks the MD5
python3 py/regress.py      # AH=08h geometry + 44 reads for 10 geometries
python3 py/boot_test.py    # ROM's own INT 19h boot path
python3 py/verify_model.py # the math, in plain Python
```

To see the byte differences with a disassembler:

```sh
objdump -D -b binary -m i8086 --start-address=0xa640 --stop-address=0xa692 \
        binary/SY019L1_27C512_CHSPATCH.BIN
```
