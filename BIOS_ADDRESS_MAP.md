# SOYO SY-019L1 BIOS — Address Map / Reference

**Source:** `binary/SY019L1_27C512_ORIGINAL.BIN`, 65,536 bytes (0x10000).
**MD5:** `e80a824d8f3c39d40b98a4be5d8d9cff`.
**See also:** `Project_Overview.md` (status/context), `BIOS_Evaluation.md`
(patch design and bug history), `HOW_THE_PATCH_WORKS.md` (every changed
byte explained).
**Addressing:** file offset == segment offset in `F000`.

**[verified]** = confirmed by disassembly and/or execution (including via
the `py/ide_harness.py` emulator). **[located]** = found by string/pattern
search. **[open]** = unresolved.

---

## 1. Top-level map

| Range (F000:) | Size | Contents | Status |
|---|---|---|---|
| `0000-00A6`ish | ~166 B | BIOS ID/signature block #1 | [located] |
| `3082` | — | `"AMIBIOS SETUP PROGRAM"` | [located] |
| `76F5-7F00` | 2,059 B | Free space — translation patch lives here (`76F5-7855`, 353 bytes) plus the checksum word at `7900` | [verified — in use] |
| `8000` | — | BIOS ID/signature block #2 | [located] |
| `8078/8097` | — | Chipset/board ID: `"40-040A-001102-00101111-111192-OP495SLC"` | [located] |
| **`A44B-A484`** | **52 B** | **INT13h `AH=` dispatch table, 26 entries (AH=00h-19h)** | **[verified]** — see §4 |
| **`A47F`** | — | String `"Format !!!"` — boot-sector-protection warning text | **[verified]** — see §6 |
| **`A4A2`** | — | String `"BootSector Write !!!"` — boot-sector-protection warning text | **[verified]** — see §6 |
| `A4FE` | — | Real `AH=08h` (Get Drive Parameters) entry point | [verified] |
| `A659-A690` | 56 B | `AH=08h` clamp body. **Only `A659-A685` (45 bytes) is patched.** `A686-A690` is the original handler tail (`mov dl,[0x75]` / `mov ah,0` / `mov [0x74],ah` / `RET`) and is **not** part of the patch (overwriting it was bugs #2 and #3) | **[verified]** |
| `A691` | — | `AH=09h` (Initialize Drive Parameters) — a **separate routine**, reached only via its own dispatch-table entry, **not** a fall-through from `AH=08h`. (Earlier notes in this project incorrectly described this as fall-through; that was the root of the off-by-one patch bug — see `BIOS_Evaluation.md` §4.) | **[verified]** |
| `A60E-A621`, `A7EA-A7F9`, `AB3D-AB4C` | — | Cylinder-high-byte builders before `OUT 1F5h` — patched | [verified] |
| `A7CE` | — | Seek handler | [verified] |
| `A965-A968` | 3 B | Shared head-byte builder — patched to call `xlate_headcyl` | [verified] |
| `AB14` | — | Shared "write CHS to IDE ports" routine | [verified] |
| **`AB62`** | — | **Boot-sector virus-protection check.** Called as the very first thing in the `AH=03h` (Write Sectors) handler (`0xA580`). Tests whether the write targets logical cylinder-low/head 0, sector 1 (`OR CH,DH` then `DEC CX`, i.e. "is this cyl0/head0/sector1"); if so, branches into code that prints one of the two warning strings above via `INT 10h` (display routine at `0xABA1`), consistent with an interactive "continue? Y/N" prompt. Present in the **original, unpatched ROM** — not something we introduced. Triggers on writes only; does not explain read failures. | **[verified]** — see `BIOS_Evaluation.md` §5 |
| `AD73-AE09` | — | POST-time hard-disk init/ready-check loop | [verified] |
| `AF3E-AF8D` | — | Shared low-level IDENTIFY DEVICE read routine | [verified] |
| **`E401-E6E0`** | **736 B** | **Fixed Disk Parameter Table, types 1–46** | **[verified]** |
| `E6E1-E6F0` | 16 B | Type 47 (user-definable) slot, all-zero in ROM | [verified] |
| **`FFF0-FFF4`** | 5 B | **Reset vector:** `JMP FAR F000:E05B` | **[verified]** |

---

## 2. ROM checksum (unchanged, verified)

`F000:D759-D77B`. 16-bit word-sum over the entire 64KB image must equal
`0x0000`.

---

## 3. Fixed Disk Parameter Table (unchanged, verified)

`F000:E401-E6E0`, 16-byte records:
`cyl(u16) heads(u16) pad(u8)=0 precomp(u16) control(u8) reserved(u32) landing_zone(u16) sectors(u16)`.
All 46 entries match the standard AMI/AT table. Type 47 slot follows,
all-zero in ROM.

---

## 4. INT 13h dispatch table (unchanged, verified)

Base `CS:0xA44B`, `DI = AH*2`, `CALL [CS:DI+0xA44B]` at `0xA417`, valid for
`AH = 0x00-0x19`.

| AH | Function | Target | Notes |
|---|---|---|---|
| 00 | Reset | `A4F1` | |
| 01 | Get Status | `A565` | |
| 02 | Read Sectors | `A570` | translated |
| 03 | Write Sectors | `A580` | translated; **first calls the boot-sector-protection check at `0xAB62`** |
| 04 | Verify Sectors | `A598` | translated |
| 05 | Format Track | `A5B9` | translated |
| 08 | **Get Drive Parameters** | `A4FE` → body at `A659`, returns via `RET` at `A690` | **patched** (`A659`-`A685` only — tail `A686`-`A690` is untouched) |
| 09 | Initialize Drive Params | `A691` | separate routine, own dispatch entry, **not** reached via fall-through from AH=08h |
| 0C | Seek | `A7CE` | translated |

(Full 26-entry table unchanged from before; see prior revisions if needed
for the unlisted entries — nothing about them has changed.)

---

## 5. Patch sites (final ROM, `binary/SY019L1_27C512_CHSPATCH.BIN`)

| Site | Range | Replacement |
|---|---|---|
| AH=08h reporting body | `A659-A685` (45 B) | `CALL xlate_getparams` + `JMP A686` + NOP fill. **`A686-A690` (the tail: `mov dl,[0x75]` / `mov ah,0` / `mov [0x74],ah` / `RET`) must stay intact** |
| Shared head-byte builder | `A965` (3 B) | `CALL xlate_headcyl` (replaces `AND DH,0Fh`) |
| Cylinder-high builder (FORMAT) | `A60E-A621` | `mov al,[bp+6]` + NOP fill |
| Cylinder-high builder (SEEK) | `A7EA-A7F9` | `mov al,[bp+6]` + NOP fill |
| Cylinder-high builder (shared R/W/verify) | `AB3D-AB4C` | `mov al,[bp+6]` + NOP fill |
| New code | `76F5` + 353 B | calc_factor / xlate_getparams / xlate_headcyl |
| Checksum filler word | `7900` | word that makes the 64 KB word-sum zero |

`[bp+6]` is the frame scratch byte the original code used for the 1F5h
(cylinder-high) value; `xlate_headcyl` now writes the full physical
cylinder high byte there. **`[bp+15h]` is the caller's saved DH and is
never written** (an earlier version did, and corrupted DX for every caller — bug #4).

## 6. INT 13h stack frame (built at `A401-A405`)

| Offset | Contents |
|---|---|
| `[bp+00]` | caller's BP |
| `[bp+02..09]` | scratch: `+2` precomp, `+3` sector count, `+5` cyl lo, `+6` cyl hi, `+7` drive/head, `+8` command |
| `[bp+0A]` ES, `[bp+0C]` DS, `[bp+0E]` DI, `[bp+10]` SI, `[bp+12]` BX | |
| `[bp+14]` DX (`+15` = caller's DH), `[bp+16]` CX, `[bp+18]` AX | |
| `[bp+1A..]` | IP, CS, FLAGS |

## 7. POST and boot path (relevant to "won't boot from C:")

| Address | Meaning |
|---|---|
| `AD66-AE09` | POST disk init: AH=14h diag, then AH=09h + AH=11h (`ADFD`), then AH=08h + AH=04h verify of the last cylinder (`ADC9`) |
| `AD7A` / `ADBB` | sets / clears **CMOS 0Eh bit 3** ("fixed disk failed init") |
| `1586` | POST error display, message table at `72A2` (bit0 FDD controller, bit1 HDD controller, bit2 C: drive error, bit3 D: drive error, bit4 C: drive failure, bit5 D: drive failure) |
| `F758` | INT 19h boot. `F779` boot-order CMOS bit; `F7CA` C:-first branch, which **skips the hard disk silently if CMOS 0Eh bit 3 is set** |
| `F810` | read-MBR-with-retry helper (4 tries, reset between) |
| `F7AD` | INT 18h: "DISKETTE BOOT FAILURE" / "INVALID BOOT DISKETTE" / "DRIVE NOT READY"; an INT 18h from MBR code instead lands on "NO ROM BASIC" + halt |
| `0000:0300` | type-47 FDPT RAM area (CMOS option); BIOS POST/boot stack is `SS:SP=0030:0100` = linear 0x300-0x400, directly above it |

## 8. Tools

`py/analyze_bios.py` reproduces the ROM-structural `[verified]` facts above.
`py/ide_harness.py` executes real INT 13h calls against either ROM through
the actual dispatcher; `py/regress.py` and `py/boot_test.py` build on it.
Open items are listed in `Plan.txt`.
