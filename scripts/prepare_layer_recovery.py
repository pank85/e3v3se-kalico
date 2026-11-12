#!/usr/bin/env python3
"""Prepare a manually supervised, layer-boundary OrcaSlicer recovery file.

This is an experimental offline file generator, not automatic power-loss
recovery. It never communicates with a printer or starts a print. The operator
must verify the input job, layer, physical Z, clearances and part adhesion.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys

LAYER = re.compile(r"^;LAYER_CHANGE\s*$")
Z_COMMENT = re.compile(r"^;Z:([0-9.]+)\s*$")
XY = re.compile(r"^G[01]\s+.*?\bX(-?[0-9.]+).*?\bY(-?[0-9.]+)")
NUMBER = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)$")


def fail(message):
    raise ValueError(message)


def finite(value, name, low, high):
    if not math.isfinite(value) or not low <= value <= high:
        fail("%s must be between %s and %s" % (name, low, high))
    return value


def prepare(lines, layer_started, physical_z, safe_z, bed, nozzle):
    starts = [i for i, line in enumerate(lines) if LAYER.match(line)]
    # layer_started is the slicer's zero-based BEFORE_LAYER_CHANGE value.
    # Skip that whole, potentially partially printed layer.
    next_index = layer_started + 1
    if layer_started < 0 or next_index >= len(starts):
        fail("there is no layer after the specified started layer")
    start = starts[next_index]
    stop = starts[next_index + 1] if next_index + 1 < len(starts) else len(lines)
    layer_lines = lines[start:stop]
    z_values = [Z_COMMENT.match(line) for line in layer_lines[:8]]
    z_values = [float(match.group(1)) for match in z_values if match]
    if len(z_values) != 1:
        fail("next layer must have exactly one ;Z: marker near its start")
    target_z = finite(z_values[0], "target layer Z", 0, 250)
    finite(physical_z, "assumed physical Z", 0, 250)
    finite(safe_z, "travel Z", 0, 250)
    finite(bed, "bed target", 0, 120)
    finite(nozzle, "nozzle target", 170, 300)
    if safe_z <= max(physical_z, target_z):
        fail("travel Z must exceed both the assumed physical Z and layer Z")
    if target_z < physical_z:
        fail("next layer is below the assumed physical Z")

    # Do not blindly execute arbitrary preambles, start scripts or tool changes.
    # This first implementation requires Orca's relative extrusion and wipe block.
    preamble = lines[:starts[0]]
    commands = [line.split(";", 1)[0].strip() for line in preamble]
    if "M83" not in commands or "M82" in commands[commands.index("M83") + 1 :]:
        fail("expected relative extrusion (M83) in the original preamble")
    body = lines[start:]
    try:
        wipe_start = body.index(";WIPE_START")
        wipe_end = body.index(";WIPE_END", wipe_start + 1)
    except ValueError:
        fail("expected an initial Orca wipe block; unsupported layer")
    if wipe_start > 8 or wipe_end > 25:
        fail("initial wipe block is not in the expected position")
    xy_moves = [XY.match(line) for line in body[wipe_start + 1 : wipe_end]]
    xy_moves = [match for match in xy_moves if match]
    if len(xy_moves) != 1:
        fail("initial wipe must contain exactly one XY move")
    x, y = (float(value) for value in xy_moves[0].groups())
    finite(x, "start X", 0, 230)
    finite(y, "start Y", 0, 225)
    if any(re.match(r"^(?:G28|G29|PRINT_START|LINE_PURGE|M82|T\d+)\b", line.strip(), re.I)
           for line in body[wipe_end + 1 :]):
        fail("remaining file contains unsupported homing, purge, absolute extrusion or tool change")

    fan = "M107"
    pressure = None
    for line in lines[:start]:
        cmd = line.split(";", 1)[0].strip()
        if re.fullmatch(r"M106\s+S\d+", cmd) or cmd == "M107":
            fan = cmd
        match = re.fullmatch(r"SET_PRESSURE_ADVANCE\s+ADVANCE=([^\s]+)", cmd)
        if match:
            if not NUMBER.fullmatch(match.group(1)):
                fail("unsupported pressure advance")
            finite(float(match.group(1)), "pressure advance", 0, 1)
            pressure = cmd
    definitions = [line for line in preamble if line.startswith("EXCLUDE_OBJECT_DEFINE ")]
    # The initial retract/wipe relies on the discarded layer's extrusion state.
    # Skip it rather than replaying it from an invented position.
    setup = [
        "; EXPERIMENTAL recovery: operator must verify actual Z and clearance",
        "; No automatic Z homing or bed purge; partial layer is skipped.",
        "G90", "M83", "SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=%g" % bed,
        "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=%g" % nozzle,
        "M190 S%g" % bed, "M109 S%g" % nozzle,
        "SET_KINEMATIC_POSITION Z=%.3f SET_HOMED=Z" % physical_z,
        "G1 Z%.3f F300" % safe_z,
        "G28 X Y", "BED_MESH_CLEAR", "G90",
        "G0 X%.3f Y%.3f F6000" % (x, y),
        "G1 Z%.3f F300" % target_z,
        "M83", "G92 E0", fan,
    ]
    if pressure:
        setup.append(pressure)
    marker = "BEFORE_LAYER_CHANGE LAYER=%d" % next_index
    if marker not in body[:wipe_start]:
        fail("expected matching BEFORE_LAYER_CHANGE marker")
    setup.append(marker)
    return definitions + setup + body[wipe_end + 1 :]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", help="original OrcaSlicer G-code file")
    p.add_argument("output", help="new recovery G-code file; must not exist")
    layer = p.add_mutually_exclusive_group(required=True)
    layer.add_argument("--started-layer", type=int, help="zero-based started layer, not last completed layer")
    layer.add_argument("--checkpoint", help="checkpoint JSON recorded by power_loss_checkpoint")
    p.add_argument("--physical-z", type=float, required=True, help="operator-verified current physical Z in mm")
    p.add_argument("--travel-z", type=float, required=True, help="operator-verified clear travel height in mm")
    p.add_argument("--bed", type=float, required=True)
    p.add_argument("--nozzle", type=float, required=True)
    args = p.parse_args()
    try:
        if os.path.realpath(args.source) == os.path.realpath(args.output):
            fail("source and output must be different")
        if args.checkpoint:
            with open(args.checkpoint, encoding="utf-8") as f:
                checkpoint = json.load(f)
            if checkpoint.get("schema") != 1 or checkpoint.get("kind") != "layer_started":
                fail("unsupported checkpoint")
            if os.path.realpath(args.source) != checkpoint.get("file"):
                fail("checkpoint belongs to a different file")
            hasher = hashlib.sha256()
            with open(args.source, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    hasher.update(chunk)
            if os.path.getsize(args.source) != checkpoint.get("file_size") or hasher.hexdigest() != checkpoint.get("file_sha256"):
                fail("source file no longer matches checkpoint")
            args.started_layer = checkpoint.get("layer")
            if type(args.started_layer) is not int:
                fail("checkpoint layer is invalid")
            print("Checkpoint layer %d; last commanded Z %s (not a physical measurement)" %
                  (args.started_layer, checkpoint.get("commanded_z")))
        with open(args.source, encoding="utf-8", errors="strict") as f:
            lines = f.read().splitlines()
        result = prepare(lines, args.started_layer, args.physical_z, args.travel_z, args.bed, args.nozzle)
        with open(args.output, "x", encoding="utf-8") as f:
            f.write("\n".join(result) + "\n")
    except (OSError, UnicodeError, ValueError) as exc:
        p.exit(2, "recovery refused: %s\n" % exc)
    print("Prepared %s; not started. Inspect before supervised use." % args.output)
    print("Requires enabled SET_KINEMATIC_POSITION, verified physical Z and clear XY homing paths.")


if __name__ == "__main__":
    main()
