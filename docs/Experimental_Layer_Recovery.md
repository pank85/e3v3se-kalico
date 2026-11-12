# Experimental manual layer recovery (not production-ready)

`scripts/prepare_layer_recovery.py` is a first, offline-only prototype based on
the assisted recovery of an OrcaSlicer print. It generates a separate file; it
does not connect to the printer, change its configuration or start a print.
It has not been tested. Do not deploy or print its output as a general recovery
feature without review and supervised printer verification.

## Optional checkpoint evidence

The opt-in `[power_loss_checkpoint]` extra and
`config/sample-power-loss-checkpoint.cfg` record a layer-start marker for the
active virtual SD print. Integrate the sample command into the printer's existing
`BEFORE_LAYER_CHANGE` macro; do not duplicate that macro. A checkpoint contains
an absolute file path, file size and SHA-256, the zero-based started layer and
last commanded Z. The generator checks file identity when `--checkpoint` is
used. The stored Z is only a command coordinate, not a physical measurement.
Writes are atomic and synced, but require persistent storage and wait for queued
moves at each marked layer. This may affect performance and storage endurance.
`POWER_LOSS_STATUS` shows the evidence; `POWER_LOSS_DISCARD` removes it when
not printing. With an idle printer, `POWER_LOSS_PREPARE PHYSICAL_Z=7.48
TRAVEL_Z=25 BED=60 NOZZLE=210` prepares a new `_recovery.gcode` file beside the
original after checking the source hash. The values are examples only and must
be verified for the actual machine and print. The command can block while it
reads and generates a large file; it does not heat, move, or start printing. It
refuses to overwrite an existing recovery file. Selecting/resetting a file,
cancelling or completing a print invalidates the checkpoint. No boot-time
heating, motion, or automatic restart occurs.
A sudden outage during a write, multiple interrupted jobs, or a failure during
recovery may leave missing or ambiguous evidence; inspect rather than guessing.

The operator must supply the original file, the zero-based layer that had
started, the current *physical* nozzle Z, a clear travel height and the bed and
nozzle targets. The started layer is skipped; it may have been partially printed.
The generator accepts only a narrow OrcaSlicer layout with relative extrusion
and an initial wipe block. It removes that initial wipe, avoids a bed purge and
Z homing, sets the assumed Z, lifts, homes X/Y, then travels to the next layer.
It refuses some unsupported G-code but cannot identify every unsafe macro or
modal state. It clears the bed mesh; calibration, offsets, pressure, fans,
extrusion state and object-specific clearance may differ from the interrupted
job. G-code and macros must be inspected before any use.

A saved layer or logged coordinate is **not proof of physical position** after
power loss. `SET_KINEMATIC_POSITION` requires `force_move` to be enabled and
does not move or measure the nozzle. An incorrect assumption can cause a
collision. Homing, travel and the selected height must be checked against the
actual machine and part. Never restart or reconfigure an active or paused print.
The generated file is not a verified or complete solution for multi-material,
arbitrary slicer profiles, repeated power loss, automatic checkpoint selection,
job identity, or a printer UI. It cannot guarantee adhesion or print quality.

Illustrative syntax only; values must be determined for the specific job:

```
python3 scripts/prepare_layer_recovery.py original.gcode recovery.gcode \
  --checkpoint ~/printer_data/config/power_loss_checkpoint.json \
  --physical-z 7.48 --travel-z 25 --bed 60 --nozzle 210
```

Alternatively use `--started-layer 39` instead of `--checkpoint` for manual
evidence. Checkpoint paths refer to files on the Klipper host; run the generator
there or carefully preserve the file paths and contents. The output is created
exclusively and will not overwrite an existing file. Select it manually in the
printer UI only after independent inspection and the above checks; no dedicated
UI button or automatic continuation is implemented. The console commands can
be used from a printer UI that provides a G-code console.
Deploy only after code review, offline validation, and printer verification
when idle, with the operator present. Back up the existing Klipper checkout and
printer configuration before deployment. To roll back when idle, remove the
added `POWER_LOSS_CHECKPOINT` line and opt-in config include, restore the prior
Klipper checkout and restart the service. Preserve printer-specific macros and
calibration. Remove the checkpoint file only after deciding it is not needed.
Do not merge to main until deployed and verified on the printer, per the project
workflow.
