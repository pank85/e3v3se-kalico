# Assisted power-loss recovery workplan

Goal: a printer-specific assisted recovery workflow in the Kalico fork, without
changing the currently printing Eder. The operator confirms adhesion and accepts
that the interrupted layer can be skipped. No automatic boot-time heating or
motion, no Z home, and no bed purge.

- [x] Inspect the existing Eder layer markers, recovery helper, log evidence and Kalico fork.
- [x] Prepare an isolated, manual Orca layer-recovery generator.
- [x] Bind layer checkpoints to the active file and persist them atomically.
- [x] Provide explicit console commands to inspect evidence and prepare a recovery file.
- [x] Keep checkpoint collection opt-in; inactive configuration does not collect data.
- [x] Document setup, use, restrictions and rollback; keep real printer unchanged.
- [x] Review implementation manually; do not run tests at the operator's request.
- [ ] Printer deployment and verification: deliberately not performed while printing.
- [ ] Dedicated UI integration and general-purpose G-code support remain future work.

The checkpoint is evidence of a layer *start*, not proof of physical position or
layer completion. A power failure may interrupt a layer, cause Z to move, or
alter printer state. No automatic motion from checkpoint data. Generated G-code
requires operator confirmation of actual physical Z and clearance.
