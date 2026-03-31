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

- **Engagement**: ACC stalk pull triggers a synthetic `decelCruise` button event (carstate.py) which flows through `update_button_enable` -> `buttonEnable` -> openpilot engages. Uses `decelCruise` specifically to avoid `resumeBlocked` NO_ENTRY when vCruise is UNSET.
- **Disengagement**: ACC stalk cancel generates a `cancel` button event -> `buttonCancel`.
- **Speed adjustment**: Right/left steering wheel buttons generate `accelCruise`/`decelCruise` events, processed by VCruiseHelper's `_update_v_cruise_non_pcm` (short press +/-1 MPH, long press +/-5 MPH steps).
- **Speed initialization**: VCruiseHelper sets initial speed to current vehicle speed on engage.
- **Personality cycling**: Scroll wheel generates `gapAdjustCruise` events.

### Button Event Gating

Speed button events (`accelCruise`/`decelCruise`) are only generated when `cruiseState.enabled=True` (stalk pulled). This prevents unintentional `buttonEnable` when buttons are pressed without the stalk, which would cause a panda `controls_allowed` mismatch.

Button state tracking (prev values) is always updated regardless of the gate, so no stale events occur when cruise later engages.

### Panda Safety

The relay blocks `ACM_longitudinalRequest` (0x160) from the stock ACM when in LONG_CONTROL mode. Openpilot sends its own version. The panda's `controls_allowed` is gated by `ACM_FeatureStatus` from the stock ACM — it must report engaged for openpilot to send longitudinal commands.

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

### Steering Wheel Buttons (Bus.alt — park assist)

Read from `WheelButtons_Fwd` (0x131A) via harness upgrade:

| Signal | Value=2 | Function |
|---|---|---|
| `RightButton_RightClick` | `accelCruise` | Increase set speed |
| `RightButton_LeftClick` | `decelCruise` | Decrease set speed |
| `RightButton_Scroll` | `gapAdjustCruise` | Cycle driving personality |

## Known Limitations

### 20 mph Minimum Engage Speed

The Rivian ACM will not report `ACM_FeatureStatus=1` below ~20 mph. Since openpilot engagement depends on `cruiseState.enabled` (which reads `ACM_FeatureStatus`), and the panda gates `controls_allowed` on this signal, openpilot cannot engage below 20 mph.

### Future Design: Scroll Wheel Engagement (Tesla-style)

Decouple openpilot from the ACM entirely. Scroll-only engagement, no stock ACC coexistence.

**Control scheme**:

| Control | Function |
|---|---|
| Scroll click | Toggle engage/disengage openpilot |
| Scroll wheel rotate | Adjust set speed |
| Left/right buttons | Cycle personality |
| Brake / gas pedal | Disengage openpilot |
| ACC stalk | Ignored by openpilot (stalk-initiated ACC is auto-cancelled) |

**Benefits**: Works at any speed (no 20 mph gate). Tesla-proven UX. Self-contained on right wheel. Eliminates ACM dependency.

**CAN signals**: `RightButton_ScrollClick` (2-bit, value 2 = pressed) on `WheelButtons_Fwd` (0x131A, Bus.alt). Currently unused while driving.

**Files to change (2 files)**:

**`rivian.h` (panda safety)**:
- Add `WheelButtons_Fwd` (0x131A) on bus 1 to `rivian_rx_checks`
- Replace `pcm_cruise_check(feature_status == 1)` with scroll click toggle on rising edge
- Guard: only allow engage when `VDM_Prndl_Status` is Drive (parse from 0x150 on bus 0)
- Add ~300ms debounce timer to prevent rapid toggling
- Reset `scroll_engaged` whenever `controls_allowed` is cleared by brake/safety
- Determine if `WheelButtons_Fwd` is periodic or event-driven — affects rx_checks viability

**`carstate.py`**:
- Read `RightButton_ScrollClick`, toggle engage (emit `decelCruise pressed=False`) / disengage (emit `cancel pressed=True`)
- Remap scroll rotate from `gapAdjustCruise` → `accelCruise`/`decelCruise` with direction detection (signed delta with wrap-around: `if delta > 127: delta -= 256`)
- Remap left/right buttons from `accelCruise`/`decelCruise` → `gapAdjustCruise`
- Gate speed events on `_scroll_click_engaged` instead of `cruiseState.enabled`
- Reset `_scroll_click_engaged` on brake/gas disengage
- Remove all stalk rising/falling edge engagement logic

**Stalk hazard mitigation (Option 1 — auto-cancel)**:
The relay statically blocks ACM's `ACM_longitudinalRequest` (0x160) in LONG_CONTROL mode. If the user pulls the stalk, the ACM engages but its commands are blocked → VDM System Fault. Fix: when openpilot detects `ACM_FeatureStatus >= 1` (stock ACC/Driver+ trying to engage), immediately send cancel via `VDM_AdasSts` (0x162) to force the ACM to disengage before the VDM faults. The carcontroller already has 0x162 in its TX list.

**Known edge cases to handle during implementation**:
- Brake press clears panda `controls_allowed` — must also reset `scroll_engaged` in panda and `_scroll_click_engaged` in carstate
- Scroll direction detection needs wrap-around handling for 0-254 range (255 = idle)
- Scroll generates many intermediate values per rotation — may need throttling for speed adjustment
- VCruiseHelper initialization works as-is (decelCruise triggers initialize_v_cruise to current speed)
- No Rivian cruise UI when openpilot engaged — comma screen is sole source of truth

**Scope**: 2 files (rivian.h + carstate.py) plus stalk auto-cancel in carcontroller. Not implementing now — documenting for future reference.

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
