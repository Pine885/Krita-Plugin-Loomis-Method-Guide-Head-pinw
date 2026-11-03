# Loomis Method Head Guide for Krita (pinw fork)

A Krita docker that draws an adjustable Loomis-method head construction guide as SVG vectors on a dedicated vector layer.

Based on the original [Loomis Head Generator by lm0ss](https://github.com/lm0ss/loomis-head-krita). Forked by Nedeljko Umjetnik, then forked by pinw(me).

## Features

- Proper Krita docker, registered in the plugin's `setup()` and available from **Settings -> Dockers**.
- Compact, scrollable, resizable UI that can shrink to a narrow panel.
- Multiple heads per document: create, duplicate, and delete heads, each on its own vector layer, including layers nested inside groups.
- The docker starts empty; **New** creates the first head.
- A head is active only while its layer is the document's active layer. Selecting another layer clears the docker instance and disables editing and presets.
- Renaming or deleting a head layer in Krita updates or removes its docker instance.
- Loomis head guides generated as vector shapes in the active document.
- Trackball rotation, side cut, face length, face width, and stroke widths.
- Size and position are set on the canvas with Krita's Transform or Move tools; the docker absorbs both changes.
- Toggle silhouette, side rims, side cross, front arrow, and face guides.
- Separate front/back stroke widths and a stroke color picker; the direction arrow uses the back-stroke style.
- Moving or transforming the guide layer with Krita's tools is mirrored back into the head model.
- Selecting a head layer in Krita activates that head in the docker.
- Save and load visual presets outside the `.kra` file. Only the **Front** built-in preset is included.
- Each head's position, direction, and size are saved inside the document and restored when the document is reopened.
- **Reset to defaults** restores the head's assigned preset (or the global defaults when it has none) without changing position, rotation, or size.

## Requirements

- Krita 5.x with Python support
- PyQt5 (bundled with Krita 5.x)
- Python >= 3.10

Krita 6 / PyQt6 is not supported.

## Installation

- Download `loomis-head.zip`.
- In Krita: Tools -> Scripts -> Import Python Plugin from File.
- Enable the plugin under Settings -> Dockers, then restart Krita.

## Usage

Enable **Settings -> Dockers -> Loomis Method Head Guide**.

1. Select a head layer in Krita's Layers docker to make it active. The docker labels the active head; **New** adds a head, **Copy** duplicates it with an offset, and **Delete** removes it.
2. Adjust the active head with the trackball and sliders. The document updates live.
3. Move or scale the layer with Krita's Move or Transform tools. The docker mirrors the placement, then normalizes the guide after the gesture settles.

Each head is a separate vector layer named `_HeadGuide_1`, `_HeadGuide_2`,
and so on. Existing head layers are rediscovered when the document changes.
Legacy `Loomis Method Head Guide` layers are still recognized.

> **Warning:** layers named `_HeadGuide_N` or `Loomis Method Head Guide [N]`
> are managed by the plugin. Anything drawn directly on one of these layers is
> erased the next time the guide is redrawn. Keep painting on a separate layer.

## Presets

User presets store visual settings only. They do not store position, direction, or size, so one preset can be reused at different places, angles, and sizes. Only the **Front** built-in preset remains; it sets the head's direction to the front view.

Each head remembers the preset assigned to it, whether it has unsaved edits, and its full visual configuration inside the document. The current position, direction, and size are saved per layer as well, so reopening the `.kra` restores the head exactly. An edited preset is marked with ` *` in the preset list until it is saved or reloaded.

**Reset to defaults** restores the assigned preset and clears the dirty marker. If the head has no preset, or its preset no longer exists, it falls back to the global defaults and clears the stale assignment. Position, rotation, and size are never reset.

- Select a preset and click **Load**.
- Click **Save** to store the current settings under a new name.
- **Delete** removes the selected user preset.
- Built-in presets cannot be overwritten or deleted.
- If `presets.json` cannot be parsed, the plugin starts with the built-ins and keeps the damaged file as `presets.json.bak` before the next save.

The plugin stores data in two places:

- Per-document placement, direction, scale, preset assignment, and dirty flag
  live in the document annotation `loomis_head_local_state` inside the `.kra`
  file.
- User presets are stored outside the project file at
  `<AppDataLocation>/loomis_method_head_guide/presets.json`.

On Linux the preset file is usually
`~/.local/share/krita/loomis_method_head_guide/presets.json`.

The plugin uses the library name `loomis_head`, so it cannot be installed alongside another plugin that uses the same library name.


### Performance logging

Live redraws call Krita synchronously, so the useful timings are inside the
plugin, not in the Python geometry alone. Launch Krita from a terminal with
timing enabled:

```sh
LOOMIS_TIMING=1 krita
```

Each live redraw prints one line to stderr with SVG build, shape removal,
`addShapesFromSvg`, shape-state read, and bounds read times. To save the log to
a file instead:

```sh
LOOMIS_TIMING=1 LOOMIS_TIMING_LOG=/tmp/loomis-timing.log krita
```

Logging is off by default.

Two diagnostic overrides are also available for A/B tests:

```sh
LOOMIS_LIVE_SAMPLES=64 LOOMIS_REDRAW_MS=80 LOOMIS_TIMING=1 krita
```

`LOOMIS_LIVE_SAMPLES` changes live curve resolution (16–1024, default 128).
`LOOMIS_REDRAW_MS` changes the live redraw interval (0–1000 ms, default 40).
Restart Krita after changing them.

Tested with: Krita 5.3.4.1, Python 3.12.3

License: GPL-3.0 (see LICENSE)
