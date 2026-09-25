# SOYO SY-019L1 BIOS — Address Map / Reference

**Source:** `binary/SY019L1_27C512_ORIGINAL.BIN`, 65,536 bytes (0x10000).
**MD5:** `e80a824d8f3c39d40b98a4be5d8d9cff`.
**See also:** [`Project_Overview.md`](../Project_Overview.md) (status/context),
[`BIOS_Evaluation.md`](BIOS_Evaluation.md) (ECHS patch design and bug history),
[`HOW_THE_PATCH_WORKS.md`](HOW_THE_PATCH_WORKS.md) (every byte the ECHS patch
changes), [`BUBios/README.md`](../BUBios/README.md) (BUBios in detail).
§1-§8 describe the original ROM and the ECHS patch; §9 lists every
address BUBios hooks or reuses. Paths in this file are relative to the
repository root.
**Addressing:** file offset == segment offset in `F000`.

**[verified]** = confirmed by disassembly and/or execution (including via
the `py/ide_harness.py` emulator). **[located]** = found by string/pattern
search.

---

## 1. Top-level map

| Range (F000:) | Size | Contents | Status |
|---|---|---|---|
| `0000-00A6`ish | ~166 B | BIOS ID/signature block #1 | [located] |
| `3082` | — | `"AMIBIOS SETUP PROGRAM"` | [located] |
| `76F5-7F00` | 2,059 B | Originally free. ECHS patch at `76F5-7855` (353 bytes) and its checksum word at `7900`. BUBios also uses `7856-78FF` and `7902-7EFF` of this block (its checksum word is at `7EFE`), plus the free block `7F14-7FFF` after it (see §9) | [verified — in use] |
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

(Only the entries that matter for the patch are listed. The other entries of
the 26-entry table are unchanged from the original ROM.)

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
The BUBios build script (`BUBios/py/build_bubios.py`) asserts the original
bytes of every site in §9 before it patches it.

---

## 9. Addresses used by BUBios

BUBios is built on top of the ECHS ROM (§5 stays unchanged). Everything
below is taken from `BUBios/py/build_bubios.py` and the headers of
`BUBios/asm/bubios.asm` and `BUBios/asm/post.asm`, where each site is
asserted or documented. [`BUBios/README.md`](../BUBios/README.md) explains
what each hook does.

### 9.1 Setup program

The setup is entered at `F000:2968`. It copies `F000:0000-DFFF` to segment
`0100h` and runs with `DS = SS = 0100h`, using `DA84-DFFF` of that RAM
copy as its stack. Data read through DS must therefore sit below `E000`
and outside `DA84-DFFF`; code in `DA84-DFFF` runs from `F000`.

| Site | Original | BUBios |
|---|---|---|
| `36D4` | main menu | `JMP bu_main` (tab bar, Summary, Tools, Exit) |
| `38B5` | screen header (all pages) | `JMP bu_hdr` (title bar, frame, tabs, footer) |
| `0D3D` | option-editor getkey | `CALL bu_edkey` (Tab / Shift-Tab) |
| `08D5` | Standard CMOS getkey (`MOV AH,0 / INT 16h`) | `CALL bu_stdkey` + `NOP` (Tab, digits) |
| `5F94` | date/time field getkey | `CALL bu_dtkey` (numeric date/time entry) |
| `5037` | 16 colour schemes × 4 bytes | BUBios schemes (F2/F3) |
| `4FF6` | "bright" OR mask `0Fh` | `08h` |
| `309A`, `6D3A` | footer texts | Tab / ESC hints |
| `2ECE` | main-menu handler table entry of the Hard Disk Utility (`4034`) | `4A43` (a `RET`) |

AMI routines and data the setup code reuses: standard key reader `08CD`,
get date `6180`, get time `61BA`, set date `5FCB`, show date/time `6022`;
option records at `BDE7` (external cache), `BE8F` (video shadow), `C057`
(system shadow), `BDA3` (boot sequence), `BD1D` (NumLock), `BE6D`
(password check); floppy type strings from `6F35`, video type strings
`6F8A-6FBD`, Tools help texts `2D4F-2E7C`.

### 9.2 POST

| Site | Original | BUBios |
|---|---|---|
| `593B`, `12BF` | `MOV SI,8100 / CALL F52E` (logo line) | 6 × `NOP` |
| `5945`, `12C6` | `CALL F42E` (BIOS ID lines, also sets A20) | `CALL bu_post_init` |
| `4F04` | "Hit <DEL>" print | `CALL bu_delmsg` |
| `4EA1-4ED1` | memory count print | `CALL bu_memshow` / `JMP 4ED2`; `4EAA-4ED1` holds code |
| `4CB4`, `12CD` | "WAIT......" print | `CALL bu_wait` |
| `120D`, `158C` | `CALL 1656` (error list) | `CALL bu_errors` |
| `1610` | `CALL F571` (end-of-POST beep) | `CALL bu_chime` |
| `1613` | `CALL 16B7` (clear screen) | `CALL bu_refresh` |
| `164C` | `CALL 39C0` (configuration box) | `CALL bu_final` |
| `F3DB` | on-screen copyright check (checkpoints 40h, 60h, 95h) | `RET` |
| `A3E7` | INT 13h entry (`CMP DL,80h / STI / CLD`) | `JMP bu_int13` + 2 × `NOP`; AMI continues at `A3EC` |
| `C6E4` | INT 13h AH=15h (floppy): `CALL D385` (stub `STC/RET`) | `CALL cf_chgline` |

AMI POST routines BUBios calls: `F42E` BIOS ID lines, `EE94` / `EE9D`
CMOS read / write, `8EE3` OPTi register read (ports 22h/24h), `8300`
CMOS option test, `F104` password check + setup (as POST calls it).
Data: BIOS ID string `8078`, option code of "System Boot Up Sequence"
at `B870`. The ROM integrity check at `F419` is left untouched.

### 9.3 Space

| Block | Range | Origin | Used for |
|---|---|---|---|
| `code` | `DA84-DFFF` | free | setup code |
| `data` | `7902-7EFD` (+ checksum word `7EFE`) | free | setup strings and tables |
| `code2` | `7F14-7FFF` | free | date/time entry |
| `code3` | `7856-78FF` | free | date/time commit |
| `post1` | `39C0-3B83` | AMI configuration box | POST hooks |
| `post2` | `3B8F-4167` | configuration box, Hard Disk Utility | POST screen drawing |
| `post3` | `429D-49DA` | Hard Disk Utility | end of POST, IDENTIFY, auto-detect, texts |
| `lba` | `3292-3481` | Hard Disk Utility texts | INT 13h extensions |
| `tools` | `19B0-1A79` | configuration box cache/clock lines | Tools switch |
| `mem` | `4EAA-4ED1` | memory count print (jumped over) | code |
| `wait` | `76AC-76B8` | "WAIT......" text | code |

Kept between the reclaimed ranges: CR/LF routine `3B84-3B8E`, drive
information display `4168-429C` (used by the setup's auto-detect), error
dialog `49DB-4A43`, error and auto-detect texts `3482-36D3`. About 13
bytes are left in the whole ROM.

### 9.4 CMOS, BDA and POST checkpoints

| Item | Meaning |
|---|---|
| CMOS `0Eh` bit 0 | AMI's "DEL pressed" flag, set by the keyboard poll during the memory test |
| CMOS `0Eh` bit 3 | fixed disk failed POST init (see §7) |
| CMOS `10h-2Dh`, checksum `2Eh/2Fh`; CMOS `34h-6Eh` | AMI's two checksummed ranges |
| CMOS `7Fh` / `7Eh` | BUBios auto-detect switch (bit 0) / check byte (`7Fh xor A5h`), outside both checksums |
| BDA `40:F0-40:FF` | BUBios state between the POST hooks, cleared by `bu_final` |
| BDA `40:FE` bit 6 | a CF card answered IDENTIFY (set by `detect_disks`, which also draws the ` CF ` badge on that drive's row) |
| BDA `40:8F` bit 3 | a CF card is on the IDE bus (set by `bu_final` from `40:FE` bit 6, read by `cf_chgline`) |
| checkpoint `05h` | chipset setup; turns shadow RAM off again after a restart |
| checkpoint `91h` | AMI's hard-disk setup; waits about a minute for a configured drive that is missing |
| checkpoint `9Ah` | AMI's COM/LPT probe |
| checkpoints `40h`, `60h`, `95h` | AMI's copyright check `F3DB` (disabled) |
