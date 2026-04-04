# RivianPilot

Custom openpilot fork for 2023 Rivian R1S with comma four + harness upgrade, enabling full lateral and longitudinal control.

## Repository Structure

- **`openpilot/`** — Custom fork (`victorbl/openpilot`, branch: `master`). The main working codebase.
- **`sunnypilot/`** — Reference implementation (`sunnypilot/sunnypilot`). Used for comparing Rivian-specific patterns, not modified.

The openpilot fork uses a custom opendbc submodule (`victorbl/opendbc`, branch: `rivian-harness`) at `openpilot/opendbc_repo/`.

## Rivian-Specific Files

All Rivian car support lives in the opendbc submodule:

- `opendbc_repo/opendbc/car/rivian/carstate.py` — CAN signal parsing, button event generation, cruise state
- `opendbc_repo/opendbc/car/rivian/interface.py` — Car params, safety config, longitudinal setup
- `opendbc_repo/opendbc/car/rivian/carcontroller.py` — Steering and longitudinal CAN message output
- `opendbc_repo/opendbc/car/rivian/riviancan.py` — CAN message creation helpers
- `opendbc_repo/opendbc/car/rivian/values.py` — Constants, flags, CarControllerParams
- `opendbc_repo/opendbc/safety/modes/rivian.h` — Panda safety layer (TX whitelist, controls_allowed)

## Key Architecture Decisions

### Longitudinal Control (`pcmCruise = False`)

Rivian uses `pcmCruise=False` with `openpilotLongitudinalControl=True`, matching the standard openpilot paradigm (same as GM, Honda Bosch, VW, Ford). This means:

- **Engagement**: Left steering wheel button (`RightButton_LeftClick`) toggles openpilot via synthetic `decelCruise` button event → `update_button_enable` → `buttonEnable`. Uses `decelCruise` specifically to avoid `resumeBlocked` NO_ENTRY when vCruise is UNSET. Gated on: Drive gear, stock ACC not active (`ACM_FeatureStatus == 0`), debounce expired.
- **Disengagement**: Left button again emits `cancel` → `buttonCancel`. Also disengages on brake press.
- **Speed adjustment**: Scroll wheel rotation generates `accelCruise`/`decelCruise` events via 8-bit direction detection (wrap-around delta), processed by VCruiseHelper's `_update_v_cruise_non_pcm` (+/-1 MPH per scroll step). Gated on `_button_engaged` and `not left_rising` (no speed events on engagement frame).
- **Speed initialization**: VCruiseHelper sets initial speed to current vehicle speed on engage.
- **Personality cycling**: Right button (`RightButton_RightClick`) generates `gapAdjustCruise` events on falling edge.
- **Stock ACC coexistence**: When openpilot is disengaged, stock ACC via stalk works normally. Buttons are ignored by openpilot — ACM receives them directly via its own bus connection.

### Button Event Gating

All openpilot button events are gated on `self._button_engaged` (left button engaged state). Scroll speed events additionally require `not left_rising` to prevent VCruiseHelper resume-path activation on the engagement frame. The engagement toggle itself is gated on `acm_feature_status == 0` (stock ACC not active) and `gearShifter == drive`.

carstate.py mirrors the panda's 6-frame debounce (~300ms at 20Hz) to prevent panda/Python state mismatch. Brake reset runs BEFORE toggle logic; `cruiseState.enabled` set AFTER both.

### Panda Safety (Dynamic Relay)

Uses Tesla-style `disable_static_blocking` pattern for stock ACC coexistence:

- **0x160** (`ACM_longitudinalRequest`): `disable_static_blocking = true` in RIVIAN_LONG_TX_MSGS. Custom `rivian_fwd_hook` blocks ACM's 0x160 from bus 2→0 ONLY when `controls_allowed` (openpilot engaged). When disengaged, ACM's 0x160 passes through — stock ACC works.
- **0x162** (`VDM_AdasSts`): Normal static blocking — openpilot always re-creates it. When engaged: overrides `VDM_UserAdasRequest` to IDLE (suppresses stalk). When disengaged: forwards real stalk values.
- **0x160 from openpilot**: Only sent when `CC.enabled`. ACM is sole source otherwise. No dual 0x160 on bus 0.
- **controls_allowed**: Gated by left button toggle (rising edge of `RightButton_LeftClick == 2` on 0x131A). NOT gated by `ACM_FeatureStatus` (removed `pcm_cruise_check` in longitudinal mode). Guards: `rivian_in_drive`, `!rivian_acm_engaged`, 6-frame debounce.
- **Relay malfunction**: Forwarded messages are panda-transmitted on the destination bus → invisible to `stock_ecu_check`. Only real hardware relay failure triggers malfunction.
- **AEB** (`ACM_AebRequest`, 0x101): Separate CAN address from steering (0x120), NOT in any TX_MSGS list → never blocked by relay. AEB works regardless of openpilot/ACC state.

### CAN Bus Topology

- Bus 0 (pt): Primary CAN — ESP_Status, VDM_PropStatus, EPAS, brakes, VDM_AdasSts
- Bus 1 (adas): ADAS — Cluster, ACM_tsrCmd. Also used for harness upgrade detection (0x131a)
- Bus 2 (cam): ACM — ACM_Status, ACM_AebRequest
- Bus.alt (park assist): WheelButtons_Fwd, BSM — accessed via harness upgrade

### Tuning Panel

Custom "Tuning" settings tab (after Toggles in sidebar) with per-personality driving profiles. Each personality (Aggressive/Standard/Relaxed) has 3 configurable values:

- **Follow Distance**: 0.8–2.0s, step 0.05s. Direct T_FOLLOW override passed to MPC.
- **Steering Response**: 1–10 scale (5 = stock 1.0x). Piecewise multiplier applied to `output_lataccel` in torque controller.
- **Acceleration Response**: 1–10 scale (5 = stock standard, 8 = stock aggressive). Maps to MPC jerk_factor.

9 params total (`TFollow*`, `LatAggr*`, `AccelResp*`), registered in `params_keys.h` with PERSISTENT flag and defaults. Scroll wheel cycles profiles, switching all 3 values as a group. Backend reads cached in `longitudinal_planner.py` (follow + accel) and `latcontrol_torque.py` (steering), refreshed on personality change or every ~1 second.

Key files: `selfdrive/ui/layouts/settings/tuning.py`, `system/ui/widgets/list_view.py` (OptionAction widget).

## Rivian Stalk & Controls Mapping

### Physical Stalk (Right Side — shared gear/cruise)

The same stalk handles both gear selection and cruise control. Context (speed/gear) determines behavior:

| Direction | At standstill / low speed | While driving |
|---|---|---|
| Push down (toward driver) | Shift to Drive | ACC engage (single tap) / Driver+ (double tap) |
| Push up (away from driver) | Shift to Reverse | ACC cancel |
| Hold down 0.5s | — | Set speed to detected speed limit |

### CAN Signal: `VDM_UserAdasRequest` (in `VDM_AdasSts`, Bus 0)

Reports physical stalk position independent of ACM state:

| Value | DBC Label | Gesture |
|---|---|---|
| 0 | IDLE | No input |
| 1 | UP_1 | Single push up (cancel / reverse) |
| 2 | UP_2 | Double push up |
| 3 | DOWN_1 | Single push down (ACC engage / drive) |
| 4 | DOWN_2 | Double push down (Driver+ engage) |

### Steering Wheel Buttons (Bus 1 — `WheelButtons_Fwd` 0x131A)

7-byte message at 20Hz. Bytes 0-1 are checksum/counter.

| Byte | Idle | Right Press (+spd) | Left Press (-spd) | Scroll Wheel |
|------|------|--------------------|--------------------|--------------|
| [2]  | 0x56 | 0x56               | 0x56               | 0x56         |
| [3]  | 0x00 | 0x00               | 0x00               | 0x00         |
| [4]  | 0x56 | **0x66**           | **0x96**           | 0x56         |
| [5]  | 0x00 | 0x00               | 0x00               | **0-3**      |
| [6]  | 0x03 | 0x03               | 0x03               | 0x03         |

- **byte[4]**: Speed buttons. `0x56`=idle, `0x66`=right click (accelCruise), `0x96`=left click (decelCruise). Pulse ~200ms per press.
- **byte[5]**: Scroll wheel position for follow distance. Steps `0→1→2→3` (4 settings). Stock Rivian ACC uses this for follow gap. Openpilot maps to `gapAdjustCruise`.

Button events mapped in openpilot (longitudinal mode — button engagement):

| Signal | Event | Function |
|---|---|---|
| `RightButton_LeftClick` (byte[4]=0x96) | `decelCruise` / `cancel` | Toggle engage/disengage openpilot |
| `RightButton_Scroll` (byte[5], 8-bit) | `accelCruise` / `decelCruise` | Adjust set speed (direction via wrap-around delta) |
| `RightButton_RightClick` (byte[4]=0x66) | `gapAdjustCruise` | Cycle driving personality |

In stock ACC mode (stalk engaged, openpilot off), all buttons are ignored by openpilot — ACM receives them directly via its own bus for stock speed/follow control.

## CAN Signal Research (from drive 7, route 0406078302)

Data source: stock ACC drive with openpilot longitudinal disabled. 28 segments, 7 ACC engagement windows totaling 4.3 minutes. User performed deliberate test patterns: 30→35→30 mph speed taps in W1, 20→25 range in W2, follow distance cycling (1→2→3→4→3→2→1) in W3/W6/W7.

### Follow Distance (confirmed on CAN)

Stock Rivian ACC follow distance is controlled by the scroll wheel (`WheelButtons_Fwd` byte[5], 4 settings: 0→1→2→3). When the scroll wheel changes, the ACM echoes the new setting on several CAN messages within ~100ms:

| Bus | Address | Description |
|-----|---------|-------------|
| 0/130 | 0x38D | Follow distance response — byte[6] values {145, 153, 157} |
| 0/130 | 0x38E | Follow distance response — byte[2] values {20, 23, 24} |
| 0/130 | 0x38F | Follow distance response — byte[4] toggles {20, 24} |
| 0/130 | 0x550 | Follow distance response |
| 0/130 | 0x555 | Follow distance response |
| 0/130 | 0x600 | Follow distance response |
| 0/130 | 0x427 | Follow distance response |

These messages appear on both Bus 0 (PT) and Bus 130 (forwarded ACM). They respond to scroll events but are completely static during speed button presses — confirming they are follow-distance-only, not set-speed. Exact signal decoding (which byte maps to which of the 4 settings) needs further analysis.

### ACC Set Speed (not found on accessible CAN buses)

Exhaustive analysis of CAN recordings using multiple approaches:

**Methods applied:**
1. All 520 CAN IDs across buses 0, 1, 2, 128, 129, 130
2. Byte-aligned (8-bit), 16-bit BE/LE, and arbitrary bit-position extraction (6-12 bits, all offsets)
3. Exact button press timing from `WheelButtons_Fwd` byte[4] (right press at t=157.4, 160.5, 163.5, 166.4, 168.9; left press at t=172.1, 174.6, 177.0, 179.4, 181.6 — 5 up, 5 down)
4. Time-series Pearson correlation of every extractable signal vs known set speed staircase
5. 4-state differential analysis (30 mph before taps, 35 mph after up taps, 30 mph after down taps, 20 mph in W2)
6. Combined W1 set-speed correlation + W2 vehicle-speed anti-correlation filtering (high W1 corr + low W2 vehicle tracking = set speed)
7. ISO-TP/multi-frame scan — no genuine ISO-TP found; byte[0] upper nibble matches are false positives from checksum/counter rotation
8. ACM message analysis on Bus 2: 0x100 (status), 0x101 (static config), 0x110 (accel-related), 0x120 (AEB), 0x160 (longitudinal request) — none carry set speed

**Key finding:** Speed button presses cause NO CAN signal changes on any accessible bus (other than the button event itself and subsequent vehicle dynamics as the car accelerates/decelerates). In contrast, scroll wheel presses for follow distance immediately trigger changes on 6+ ACM messages. This asymmetry confirms the ACM publishes follow distance but not set speed on CAN.

**Result:** No CAN signal holds a stable set speed value during W2 stop-and-go traffic (vehicle at 10-18 mph, set speed at 20-25 mph). Every candidate with high W1 correlation tracks vehicle speed, not set speed. The ACM processes speed taps internally and outputs only acceleration commands (0x160/0x390).

**Community validation:** Sunnypilot (lukasloetkolben, who reverse-engineered the original Rivian DBC for openpilot) also has `# TODO: find cruise set speed on CAN` and manages set speed entirely in software from button events via `carstate_ext.py`.

**How other cars do it:** Most OEMs (Toyota, Honda, Hyundai, GM, VW, Ford, Subaru) publish ACC set speed as an 8-bit signal in kph on CAN, typically from the cruise control module to the cluster. The Rivian ACM does not follow this pattern on the buses accessible through the comma four harness.

### Why Set Speed May Not Be On Accessible CAN

**Rivian network architecture:**
- Zonal architecture with 7 ECUs connected via Ethernet backbone at 2 Gbps (TE MATEnet connectors)
- Replaced 1.6 miles of wiring with Ethernet; CAN retained for legacy/low-bandwidth domains
- OBD2 port has NO CAN lines — uses 4 Ethernet pins (DoIP / Diagnostics over IP)
- Diagnostic access requires RiDE tool ($5,500/year), proprietary authentication

**Comma four harness access:**
- Taps 3 CAN domains via ADAS camera connector at windshield: Bus 0 (PT), Bus 1 (ADAS), Bus 2 (CAM/ACM)
- xnor harness upgrade adds park assist bus (forwarded on Bus 1) via AXM module in passenger footwell
- Additional CAN domains (body, infotainment, cluster) may exist but are not accessible through the comma harness

**Most likely explanations (not yet confirmed):**
1. Set speed transmitted to cluster via vehicle Ethernet (not CAN)
2. Set speed on a CAN bus we don't tap (body/infotainment domain feeding the cluster)
3. ACM manages set speed purely internally, sends only acceleration commands on CAN

**Our approach:** Software-managed via VCruiseHelper (same as sunnypilot). Button presses generate `accelCruise`/`decelCruise` events which VCruiseHelper processes with `_update_v_cruise_non_pcm`.

**Next steps to find set speed:**
- Probe cluster connector physically with USB CAN adapter to identify what bus feeds it
- UDS `ReadDataByIdentifier` (0x22) request to ACM for set speed DID while ACC is active
- Contact lukasloetkolben to ask if he investigated and ruled out other buses
- New recording with maximum divergence: set 60 mph behind stopped car, so set speed stays 60 while vehicle goes to 0

### Vehicle Speed Signals

| Bus | Address | Signal | Notes |
|-----|---------|--------|-------|
| 0 | 0x208 (ESP_Status) | `ESP_Vehicle_Speed` | 16-bit at bits 55\|16@0+ (Motorola), 0.01 kph scaling. Primary speed source used in carstate.py. |
| 0 | 0x38B | byte[2] | Tracks vehicle speed closely. During clear-road ACC (W1), shows clean staircase matching set speed taps — but this is the vehicle FOLLOWING set speed, not the set speed itself. Confirmed by W2: bounces with traffic (10-21) while set speed should be stable at 20-25. |
| 1 | 0x4F1 (Cluster) | `Cluster_VehicleSpeed` | 8-bit, 1 mph or kph depending on `Cluster_Unit`. Display speed used for `vEgoCluster`. |

### Analysis Tools

CAN analysis scripts in `tools/`:
- `can_signal_finder.py` — General stepping signal finder
- `can_bidir_finder.py` — Bidirectional stepping pattern finder
- `can_setspeed_finder.py` — Targeted set speed search with plateau detection
- `can_setspeed_v2.py` — 1Hz downsampled stability analysis
- `can_acc_windows.py` — ACC engagement window detection + per-window signal analysis
- `can_follow_dist.py` — Follow distance + set speed combined search
- `can_window_dump.py` — Multi-signal raw dump per ACC window

## Known Limitations

### 20 mph Minimum Engage Speed (stock ACC only)

The Rivian ACM will not report `ACM_FeatureStatus=1` below ~20 mph. This limits stock ACC engagement to 20+ mph. Openpilot's button engagement bypasses this entirely — left button can engage at any speed in Drive.

### Button Engagement Architecture (implemented — Tesla-style dynamic relay)

Two mutually exclusive operating modes with stock ACC coexistence:

| Mode | 0x160 (accel) | 0x162 (ADAS status) | Buttons | Engagement |
|------|--------------|---------------------|---------|------------|
| **Stock ACC** (stalk, OP off) | ACM only (fwd hook allows) | Forwards real stalk values | ACM receives directly via own bus | Left button BLOCKED |
| **Openpilot** (left button) | OP only (fwd hook blocks ACM) | Suppresses stalk (IDLE) | carstate.py reads panda copy | Stalk suppressed |

Switching: stock ACC → OP requires stalk-up (cancel) then left button. OP → stock ACC requires left button (disengage) then stalk-down.

**Stalk & button behavior:**

| Scenario | What happens |
|----------|-------------|
| OP off, stalk pull | Stock ACC engages. ACM controls car. Buttons control stock ACC speed/follow. |
| OP off, stalk cancel | Stock ACC disengages. Left button now available. |
| OP off, stock ACC active, left button | **Ignored** — engagement blocked while ACM active. |
| OP off, no ACC, left button | **Engage** openpilot. Scroll=speed, right=personality. |
| OP engaged, stalk pull | **Suppressed** — ACM never sees request via 0x162 IDLE override. |
| OP engaged, left button | **Disengage** openpilot. ACM's 0x160 unblocked. Stalk resumes. |
| OP engaged, brake | **Disengage** both panda and carstate. |

**Crash recovery:** If openpilot's Python process crashes in longitudinal mode, 0x162 stops being sent to the ACM (static blocking prevents stock 0x162 from reaching it). Stock ACC becomes unavailable until panda heartbeat timeout recovers the relay. AEB is unaffected (separate address 0x101, never blocked).

**Comparison with Tesla:** We borrowed the `disable_static_blocking` + custom fwd hook mechanism, but use it for mode coexistence (user chooses stock ACC vs openpilot) rather than safety system priority (Tesla yields to AEB/LKAS/Autopark automatically). Tesla's engagement is tied to the stock stalk; ours is fully independent.

## Settings

- `AlphaLongitudinalEnabled=True` — Required for longitudinal control
- `ExperimentalMode=OFF` — NOT needed for longitudinal. Causes confusing behavior: initial speed floors at 65 mph, set speed becomes a ceiling instead of a target

## Local Development (macOS)

### UI Testing
```bash
cd openpilot
source .venv/bin/activate
BIG=1 python3 selfdrive/ui/ui.py    # comma 3X layout (large screen)
python3 selfdrive/ui/ui.py           # comma 4 layout (small screen / mici)
```

### Device/UI Mapping
- **comma 3X** (tizi) = big UI = `BIG=1` = `MainLayout` in `selfdrive/ui/layouts/main.py`
- **comma 4** (tici) = small UI = default = `MiciMainLayout` in `selfdrive/ui/mici/layouts/main.py`
- Note: env var is `BIG`, NOT `BIG_UI`

### First-time setup
```bash
tools/op.sh setup        # install dependencies
source .venv/bin/activate
scons -u -j8             # build C extensions (required for params_keys.h changes)
```

### Safety Tests (panda)
```bash
cd openpilot
source .venv/bin/activate
cd opendbc_repo
python -m unittest opendbc.safety.tests.test_rivian -v    # Rivian only (80 tests)
cd opendbc/safety/tests && ./test.sh                       # All cars + coverage
```

Safety tests use CFFI to compile rivian.h at import time — no separate build step. Tests cover: button toggle engagement, gear/ACM guards, debounce, brake disengage, dynamic fwd hook (0x160 blocking), static blocking (0x162), rx_check timeout for WheelButtons_Fwd.

### Replay (testing with recorded drive logs)
```bash
cd openpilot
source .venv/bin/activate

# Replay a route from local rlog files
tools/replay/replay --data-dir /path/to/realdata "route_id"

# Replay from comma connect (requires login)
tools/replay/replay "route_id"
```

While replay is running, launch the UI in a separate terminal to see the driving screen:
```bash
source .venv/bin/activate
python3 selfdrive/ui/ui.py           # or BIG=1 for comma 3X layout
```

Drive recordings are stored at: `/Users/victorblanco/Library/Mobile Documents/com~apple~CloudDocs/Storage/Projects/Rivian/Comma Video/`

Route naming: `BOOTNUMBER--ROUTEID--SEGMENT` (e.g., `00000007--0406078302--0`)

### CAN Analysis
```bash
source .venv/bin/activate

# Signal finder (general stepping patterns)
python3 tools/can_signal_finder.py "/path/to/Comma Video" route_filter

# ACC-windowed analysis (detects ACC engagement, analyzes within windows)
python3 tools/can_acc_windows.py "/path/to/Comma Video" 0406078302

# Follow distance + set speed combined search
python3 tools/can_follow_dist.py "/path/to/Comma Video" 0406078302

# Raw signal dump per ACC window
python3 tools/can_window_dump.py "/path/to/Comma Video" 0406078302
```

### Full simulator (MetaDrive)
```bash
# Terminal 1:
./tools/sim/launch_openpilot.sh    # default fingerprint: HONDA_CIVIC_2022
# Terminal 2:
./tools/sim/run_bridge.py
```
Note: Full simulator has macOS fork() issues with CoreFoundation. Standalone UI works fine.

## Installation

Custom fork installer URL for comma devices: `installer.comma.ai/victorbl/master`

## Git Workflow

- opendbc submodule changes are committed in `opendbc_repo/` first, pushed to `victorbl/opendbc rivian-harness`
- Parent repo commits the submodule ref update, pushed to `victorbl/openpilot master`
- LFS: comma's LFS server is read-only for forks. Custom assets (e.g., spinner_rivian.png) are excluded from LFS via `.gitattributes`
- Always use `GIT_LFS_SKIP_PUSH=1` when pushing to avoid LFS upload failures

## Sunnypilot Reference Patterns

When investigating Rivian behavior, sunnypilot's extension files are useful references:
- `sunnypilot/opendbc_repo/opendbc/sunnypilot/car/rivian/carstate_ext.py` — Button handling, direct speed management
- `sunnypilot/selfdrive/car/cruise.py` — VCruiseHelper with `pcmCruiseSpeed` param
- Sunnypilot manages speed directly in carstate (not via VCruiseHelper). Our fork uses the standard openpilot VCruiseHelper path instead.
