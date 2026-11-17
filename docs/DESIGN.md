# vroid-mcp — Design notes

Status: **implemented** (v0.1.0). This file keeps the original Phase 0 analysis, followed by the measurements from each phase that confirmed or changed the plan. Some hypotheses in sections 1–3 were later settled by the probe results; where they differ, the results win.

Every claim below is tagged:

- **[measured]**: checked on the dev machine (Windows 11 26200, 2 monitors, 100 % DPI, Italian locale).
- **[mcp-vroid src]**: read in the mcp-vroid source code (commit of 2026-08-24).
- **[hypothesis]**: not verified yet. Each one comes with a default, a fallback and the probe that will decide it.

## Phase 1 probe results (`scripts/probe.py`, VRoid 2.14.0 standalone)

| Probe | Result |
|---|---|
| **P0 discovery** | **[measured]** Found at `%LOCALAPPDATA%\Programs\VRoidStudio\2.14.0\VRoidStudio.exe`, both from the standalone folder and from the HKCU uninstall key. The build is IL2CPP (`GameAssembly.dll`). The window is `UnityWndClass` with a 1024×768 client at 96 dpi. |
| **P1 UIA** | **[measured]** UIA exposes **only the title bar**: System menu, Restore, Maximize, Close. **No VRoid control is visible.** Vision is the only way to see the UI. UIA stays useful for the native save dialog only. |
| **P2 capture** | **[measured]** **An unfocused VRoid shows a plain white client area**, even when it is uncovered on a second monitor. Screen copy sees white too. One WGC capture got the real start screen, but later ones were white, so the UI is **only reliable while VRoid is focused**. PrintWindow is always blank, so it is dropped as a fallback. |
| **WGC timing** | **[measured]** Opening a session takes 1–1.8 s. WGC sends a frame **only when the content changes** (1 frame in 3 s on a static screen). Decision: keep one **persistent session** and cache the last frame. A warm capture takes ~20 ms, and a new frame arriving signals "the UI changed". |
| **Capture flow** | **[decision]** Focus VRoid → wait for a fresh WGC frame → capture → act → capture → restore the user's focus. `is_blank` also catches "white + a corner of another window". |
| **OCR on the real UI** | **[measured]** Start screen, 1024×768: all 12 labels are read correctly with boxes accurate to a few px. Engine start-up takes ~1.6 s once. The full window takes **48 ms at 1×** (11/12 lines) or **273 ms at 2×** (12/12). A 1024×60 strip takes 20 ms. Decision: warm the engine at server start, locate at 1×, read values at 2× on small crops. Now a test fixture. |
| **P2 capture, VRoid focused** | **[measured]** All three backends return the real UI: WGC 279 ms (cold), PrintWindow 17 ms, screen copy 50 ms. PrintWindow works **only** while focused, so it stays a fallback. |
| **P3 input** | **[measured]** Start-screen test that clicks the "Dress-up Feature" tab. **PostMessage: ignored** (pixel diff 0.0). **SendInput: works** (diff 19.1, tab switched), and clicking back restored the screen (diff 0.16). Cursor and focus were restored afterwards. Decision: **SendInput only**. PostMessage stays as an undocumented experiment and is not offered in the README. |
| P4 log, P5 launch args | Not run yet. |

---

## 1. mcp-vroid: how it works and where it is weak

### How it works [mcp-vroid src]

- **Language and transport:** Python with FastMCP over stdio, about 2.8k lines, plus a C helper for the Wayland virtual pointer.
- **Loop:** `grim` screenshot → `tesseract` OCR / OpenCV colour blobs → click via virtual pointer, keys via XTEST → screenshot again.
- **Flows:** `actions.py` holds the flows (new character, open tab, set slider, export VRM, save). Each step is a fixed `time.sleep` followed by an OCR lookup.
- **Geometry:** positions are fractions of a 2560×1440 fullscreen capture (Hyprland scale 1.25), for example `F_PARAM_VALUE_X = 0.9785`.
- **Tool names (we keep them):** `vroid_status`, `vroid_launch`, `vroid_screenshot`, `vroid_find_text`, `vroid_click`, `vroid_type`, `vroid_key`, `vroid_scroll`, `vroid_drag`, `vroid_open_tab`, `vroid_set_slider`, `vroid_set_color`, `vroid_export_vrm`, `vroid_save_project`, `vroid_current_screen`.

### Weak points

| # | Problem | Evidence |
|---|---|---|
| W1 | **Platform lock-in.** Only Hyprland, Wayland and Proton are supported. The Wine save dialog is handled with OCR, and paths go through `Z:\`. | src |
| W2 | **Slow and noisy search.** `find_text` runs up to 4 tesseract passes (normal/inverted × psm 11/6) on a 2× upscaled image for every lookup. | src `locate.py` |
| W3 | **Word-level matching.** A hit is `want in word or word in want` (more than 2 chars, spaces stripped). A multi-word label like "Head Size" can match the bare word "Size" of another row. The highest-confidence hit wins, not the correct one. | src `locate.py:95` |
| W4 | **No post-action verification.** `set_param` types, presses Return and returns a screenshot. `read_param` exists but is never used to confirm. Export metadata checks only the first 8 characters. | src `actions.py` |
| W5 | **Fixed sleeps everywhere** (0.3–4 s). They are too slow when the app is idle and too fast when it is busy. | src |
| W6 | **Geometry tied to one setup:** 2560×1440, scale 1.25, fullscreen. Fractions assume panels scale proportionally. Unity UIs usually keep fixed-pixel side panels, so this assumption is risky. | src + hypothesis |
| W7 | **Destructive without asking.** `export_vrm` calls `out_path.unlink()` on an existing file. `vroid_launch(restart=true)` kills VRoid and loses unsaved work. | src `actions.py:246`, `server.py:200` |
| W8 | **Steals the user's mouse and keyboard.** Input is real global input, and the window must be focused. | src |
| W9 | **Modal blind spots.** A modal outside the OCR region silently swallows clicks (the author's own note). | README |

---

## 2. Making "look and click" reliable and fast on Windows

| Topic | Finding | Decision |
|---|---|---|
| **Locator strategy** | OCR is cheap now (see the next row). Most of the time is spent in screenshots and waits, not in recognition. Cached positions alone go stale when panels scroll. | Use a **layered locator**. (1) A **position cache** keyed by `(profile, screen, label)`, checked by comparing the cached crop with normalised cross-correlation (≥ 0.9, about 1 ms). (2) Fall back to **OCR on a region**. (3) Use **template matching** for icons with no text (tab rail, toolbar, ✕). |
| **Windows OCR vs Tesseract** | **[measured]** `Windows.Media.Ocr` through `winrt-*` packages takes **13–18 ms** on a 900×140 UI strip. It reads white-on-blue "Export" correctly, which is mcp-vroid's known failure case. At 1× it misread "Helght", "Sizex" and "0,350" (decimal comma). At 2× upscale everything was correct. The **en-US OCR pack is missing** on this PC; only **it-IT** is installed, and it still reads English UI fine. The `winocr` wrapper is broken with current winrt (`a coroutine was expected`), so we call winrt directly. Tesseract is not installed. | Use **Windows OCR as default**: 2× upscale, `try_create_from_user_profile_languages()`, and normalisation that maps `,`→`.` in numbers, ignores spaces and case, and handles i/l/1 confusions. Match against **lines**, not single words (fixes W3). Score matches with a fuzzy ratio and reject ambiguous ones. Tesseract is an optional fallback, never required. |
| **Verify results from files** | **VRM**: it is glTF binary (GLB), so its JSON chunk and images can be parsed with no extra dependencies. **.vroid**: **[measured]** the sibling format `.vroidcustomitem` is a ZIP (`meta.json`, `*/transferables/*.transferable`, `thumbnails/thumbnail.png`). The `.transferable` payload looks binary or proprietary **[hypothesis]**. | Add a **`vroid_inspect_vrm`** tool (meta, humanoid bones, expressions/blendshapes, texture sizes, VRM 0.x and 1.0). Add a **read-only** `.vroid` inspector (zip listing, `meta.json`, thumbnail). **Never write** `.vroid` files. |
| **Capture of a covered window** | VRoid uses Unity 6000.0.62f1 on **D3D11** **[measured, Player.log]**. `PrintWindow(PW_RENDERFULLCONTENT)` is reported to work on some DX windows and to return black on others. | **Default:** Windows Graphics Capture (works while the window is occluded, not while minimised). **Fallback 1:** `PrintWindow` with full-content flag. **Fallback 2:** BitBlt the screen rectangle after raising the window. Auto-select with a black/uniform-frame check. **Probe P2 decides.** |
| **DPI and multi-monitor** | **[measured]** DISPLAY1 is 1920×1080 at (0,0). DISPLAY2 is 1680×1050 at **x = −1680**, so coordinates can be negative. Desktop DPI is 96. | The process calls `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` at start-up. All internal coordinates are **physical client pixels** of the VRoid window. Conversion to screen coordinates uses `ClientToScreen`. SendInput uses `MOUSEEVENTF_ABSOLUTE \| VIRTUALDESK`, normalised over the virtual desktop. These are pure functions with unit tests. |
| **SendInput vs PostMessage** | **[measured, Player.log]** VRoid uses the **new Unity Input System** with custom processors (`VRoidMouseButtonRejectionProcessor`, palm rejection). The Input System commonly reads raw input or the real cursor, so posted `WM_LBUTTONDOWN` messages will likely be ignored **[hypothesis]**. | **Default: SendInput.** Before acting, check that VRoid is foreground. Afterwards, restore the cursor position and the previously focused window, so the user loses the mouse for less than 1 s per action. **PostMessage** is behind `VROID_MCP_INPUT=postmessage` as an experiment. **Probe P3 decides.** |
| **Verify after every action** | The 3D viewport changes every frame (dithering, also noted by mcp-vroid). | Each acting tool returns `verified: true/false` plus evidence. **Slider:** OCR the numeric box after Return and compare within tolerance; report clamping. **Tab:** the panel title or highlight changed. **Click:** the UI region changed, excluding the viewport mask. **Export:** the file exists, its size is stable, it parses as VRM, and the meta name matches. Replace fixed sleeps with **wait-until-stable** (poll a UI region until two frames match, with a timeout). |
| **Record mode / calibration** | A new VRoid version or window size will move things. | `uv run vroid-mcp calibrate` is a guided CLI. It asks "hover over *Export icon* and press F8", records the client-relative position and a template crop, and writes a profile YAML. `vroid_calibrate_check` reports which anchors in the active profile still resolve. |
| **Config files you can edit** | **[measured]** `%USERPROFILE%\AppData\LocalLow\pixiv\VRoid Studio\preferences\editoroption.xml` has `LanguageMode=En`, `UIScaling=100` and `ThemeMode=Light`. Window size and mode are stored in Unity PlayerPrefs under `HKCU\Software\pixiv\VRoid Studio`; the last run was 1024×768 windowed. | `vroid_status` **reads** these and warns about mismatches (non-English UI, scaling other than 100, dark theme without a profile). Writing them is opt-in (`vroid_prepare_config(confirm=true)`), **only while VRoid is closed**, and always with a `.bak` backup. Window size is set at launch through Unity's `-screen-width/-screen-height -screen-fullscreen 0` arguments **[hypothesis; probe P5]**, with `SetWindowPos` after launch as the fallback. |
| **Player.log as a signal** | **[measured]** The log contains `[HistoryManager] Execute: …Command` lines for hair strokes and loads. There is **no evidence yet** that slider edits are logged. | **[hypothesis]** Tail the log as a cheap secondary confirmation and for error detection, never as the only check. **Probe P4 decides.** |
| **Native dialogs** | The save dialog on Windows is very likely the standard common file dialog **[hypothesis]**. | Drive it with **UIA (pywinauto)**: set the File-name edit, verify it, press Save. No OCR (fixes the "Save in:" trap). This is the one place where UIA should certainly work. |

---

## 3. Proposed architecture

```
src/vroid_mcp/
  server.py            FastMCP tools (thin: validate → call flow → format result/error)
  config.py            env vars, exe discovery (standalone → Steam), profile selection
  win/
    dpi.py             DPI awareness + pure coordinate transforms (client↔screen↔virtual-desk↔image)
    window.py          find VRoid hwnd (process + UnityWndClass), client rect, focus/restore
    capture.py         WGC / PrintWindow / BitBlt backends, auto-select, blank-frame detection
    input.py           SendInput (default) / PostMessage (experimental), cursor+focus restore
    dialogs.py         common file dialog via UIA
  vision/
    ocr.py             Windows.Media.Ocr backend (+ optional tesseract), normalisation, line fuzzy match
    match.py           template matching, colour blobs, region diff with viewport mask
    locator.py         cache → OCR → template, ambiguity rejection
  profiles/vroid-2.14.0-en/
    profile.yaml       panels/regions in client px anchored to window edges, labels, icon templates
    icons/*.png
  flows/               launch, tabs, sliders, colours, export, save (each returns evidence)
  verify.py            wait_until_stable, OCR read-back, file checks, optional log tail
  formats/vrm.py       GLB parser (VRM 0.x / 1.0)
  formats/vroid.py     read-only zip inspector
  calibrate.py         record mode CLI
scripts/probe.py       Phase-1 environment probe
scripts/e2e_manual.py  launch → change one slider → export → inspect VRM
tests/                 OCR on fixture PNGs, VRM parsing (synthetic GLB), coordinates/DPI, locator logic
```

### Key choices

- **Geometry is anchored to window edges, not fractions.** For example, the right panel is "the last 310 px". The profile is keyed by `(VRoid version, language, UIScaling, client-size bucket)`. We recommend one **canonical window size** (for example a 1600×900 client) that `vroid_launch` sets, so a single profile fits most users.
- **Two tool layers.** High-level flows (`vroid_set_slider`, `vroid_export_vrm`) for speed and reliability, and raw tools (`vroid_click`, `vroid_find_text`, `vroid_screenshot`) so the model can recover when a flow fails.
- **Screenshots.** The default long edge is 1280 px in JPEG (configurable with `VROID_MCP_MAX_IMAGE_PX`), with an optional `region` crop. The tool returns the `scale` factor, and every tool accepts `space="image"|"client"`.
- **Errors.** Failures raise `VroidUIError(what, region, nearest_candidates, hint)`. The tool returns that text **plus** an annotated screenshot with the search region boxed and the closest OCR strings listed.
- **Safety.**
  - `vroid_export_vrm(overwrite=False)` refuses to replace an existing file.
  - `vroid_launch(restart=True)`, `vroid_close` and "return to model selection" require `confirm=True`.
  - When an "unsaved changes" modal is detected, we **stop and report** it, and never click "Don't save".
  - `.vroid` files are never overwritten unless `overwrite=True`.
- **Discovery.**
  1. `VROID_EXE` environment variable.
  2. `%LOCALAPPDATA%\Programs\VRoidStudio\<ver>\VRoidStudio.exe`, which is the default install path **[measured via Player.log]**.
  3. Uninstall registry keys.
  4. Steam: `HKCU\Software\Valve\Steam\SteamPath` → `libraryfolders.vdf` → `steamapps\common\VRoid Studio`, launched with `steam://rungameid/1486350`.
- **Dependencies:** `mcp`, `pillow`, `numpy`, `opencv-python-headless`, `winrt-Windows.Media.Ocr` (and its sibling packages), `pywinauto` (dialogs only), `pyyaml`, and `windows-capture` (for WGC; pending probe P2). Python ≥ 3.11 with `uv`.

### Pros and cons

| | |
|---|---|
| ➕ | Fast. OCR is about 15 ms and cache hits about 1 ms, and waits are event-like instead of fixed. |
| ➕ | Verified. Every action reports evidence, and VRM checks are file-based. |
| ➕ | Keeps the user's mouse and focus as much as possible, and refuses destructive actions by default. |
| ➕ | Pure layers (coordinates, OCR normalisation, VRM, locator) are unit-testable in CI without VRoid. |
| ➖ | Still fragile on UI changes. The profile and calibration make that fixable, not impossible. |
| ➖ | Windows only. Steam support is untested by us. |
| ➖ | SendInput still needs VRoid in the foreground briefly during each action. |
| ➖ | More moving parts than mcp-vroid (profiles, capture backends). |

**Rejected:** injecting a Unity mod (BepInEx) or reading process memory. It would be more reliable, but it is invasive, likely against the ToS, and breaks on every update.

---

## Phase 2 results: vision layer

| Piece | What it does | Measured on the real start screen |
|---|---|---|
| `vision/text_match.py` | Scores word *spans* per line, rejects labels that are only part of a longer text, and **raises on ambiguity**. Lines are split at large gaps (OCR merged the "New" and "Open" buttons into one line). Treats i/l/1 and o/0 as look-alikes ("Helght"). | Unit tests: "Head Size" vs "Size", "Eye Size X" vs "Eye Size Y", value suffixes such as "161.2 cm". |
| `vision/locator.py` | Tries the cache first (NCC ≥ 0.92 on the cached crop), then OCR at 1×, then OCR at 2×, then template matching. Errors carry an annotated screenshot and the closest texts. | First lookup **36–42 ms** for the full window and **3 ms** in a region. A cache hit takes **0.2–0.3 ms**. When the widget moves (Create New card, +248 px), the stale cache is detected and OCR runs again. |
| `vision/imaging.py` | Region specs anchored to window edges, JPEG model images (1024×768 → <80 KB at 512 px), masked diffs, template matching and colour blobs. | The "+" template is found on both screens (score > 0.95), and the blue pill is found by colour. |
| `verify.py` | `wait_until_stable` (can require a change from a reference frame and ignores masked areas), `read_number` at 3× OCR, and `values_match` at display precision. | Unit tests with a fake clock. |
| `profiles/vroid-2.14.0-en` | YAML profile with screens, regions and icons, plus screen identification. | `start` and `start_dressup` are identified correctly. |

## Phase 3 results: flows and MCP tools (measured live on 2.14.0)

| Finding | Consequence |
|---|---|
| **VRoid parses typed numbers with the Windows locale.** On an Italian system, typing `0.5` sets **5,000**, with no error and no clamp. | `vroid_set_slider` types with `GetLocaleInfoEx(LOCALE_SDECIMAL)` and always reads the field back. Any other approach silently produces 10× values on comma locales. |
| **Text fields insert Tab as a character.** The avatar name was exported as `"Flow Test\t"`, and the post-export VRM check caught it. | Never press Tab or Enter in metadata fields; the next click commits the value. |
| **Player.log records every parameter edit (P4: yes)**, e.g. `SetBoneTransformParameterCommand (… (Level2Eye_EyeSizeX, 0,25))`. Entries can lag by one command. | The log is returned as secondary evidence in `vroid_set_slider`. It is never the only check. |
| **Fixed-width side panels (P5).** At 1024×768 and at 1280×800 the right panel has identical offsets from the right edge. `SetWindowPos` resizes the window live. | Profile regions are anchored to edges. `vroid_launch(width, height)` can set a known size. Unity launch arguments are not needed. |
| On the Body tab **only the lower "Parameters" list scrolls**, and only with the mouse over it. | The wheel is placed 110 px above the panel bottom. Row search scrolls to the top, then pages down. If a label is missing, the error lists every label seen in the tab. |
| **The active tab has a `#0096FA` underline** at y = 43–47. | `vroid_open_tab` verifies the underline, not just "something changed". |
| Modals dim the page, but **OCR still reads the text behind them**. The VRM Settings dialog shows two "Export" texts. | Screens have priorities (modals first). Lookups inside modals are restricted to the modal region. The full-window search correctly reports "ambiguous". |
| **The save dialog is a native Windows dialog**, fully visible to UIA. | It is driven through automation IDs (`1001` file name, `1` Save, `2` Cancel), which work in any Windows language. No OCR and no mouse. |
| The title becomes `VRoid Studio 2.14.0 - Untitled*`. | `vroid_status` reports `unsaved_changes`. Close and restart refuse without `confirm=true`. |
| **Timings:** tab 0.5–0.8 s; visible slider ~1 s; slider that needs scrolling 2–3.5 s; colour 0.7 s; full VRM export with metadata, dialog and verification 12–18 s (~12 MB file). | |

The MCP server uses the `mcp` 2.x SDK (`MCPServer`) over stdio and exposes 15 tools. All VRoid work runs on one worker thread, so actions never overlap and COM/UIA stays on a single thread. Errors return `is_error` with an annotated screenshot.

## 4. Phases (done)

1. **Phase 1, skeleton and probe:** Windows layer, `scripts/probe.py` (P1–P5).
2. **Phase 2, vision:** OCR, label matching, locator, verification, profile.
3. **Phase 3, flows and tools:** 15 MCP tools, VRM reader, e2e and smoke scripts.
4. **Phase 5, release:** README with GIF, MIT licence, CI (`windows-latest`, Python 3.11–3.13), calibration CLI.

The calibration mode was planned with an F8 hotkey. It ended up as a console prompt (`vroid-calibrate record <icon>`: hover, then press Enter), which needs no global keyboard hook.
