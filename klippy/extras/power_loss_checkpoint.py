# Opt-in evidence for assisted layer recovery; does not move the printer.
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
import hashlib
import importlib.util
import json
import logging
import os
import tempfile


class PowerLossCheckpoint:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.path = os.path.expanduser(config.get("path"))
        self.gcode = self.printer.lookup_object("gcode")
        self.sd = None
        self._identity = None
        self.printer.register_event_handler("klippy:connect", self._connect)
        for event in ("virtual_sdcard:reset_file", "virtual_sdcard:cancel_file",
                      "virtual_sdcard:complete_file"):
            self.printer.register_event_handler(event, self._invalidate)
        self.gcode.register_command(
            "POWER_LOSS_CHECKPOINT", self.cmd_POWER_LOSS_CHECKPOINT,
            desc="Record the active file and an explicitly marked layer start",
        )
        self.gcode.register_command(
            "POWER_LOSS_DISCARD", self.cmd_POWER_LOSS_DISCARD,
            desc="Discard saved power-loss checkpoint",
        )
        self.gcode.register_command(
            "POWER_LOSS_STATUS", self.cmd_POWER_LOSS_STATUS,
            desc="Show saved power-loss checkpoint evidence",
        )
        self.gcode.register_command(
            "POWER_LOSS_PREPARE", self.cmd_POWER_LOSS_PREPARE,
            desc="Prepare a recovery file from a verified checkpoint; does not print",
        )

    def _connect(self):
        self.sd = self.printer.lookup_object("virtual_sdcard")

    def _invalidate(self, *args):
        self._identity = None
        try:
            os.unlink(self.path)
            directory = os.path.dirname(os.path.abspath(self.path))
            fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logging.exception("Unable to invalidate power-loss checkpoint")
            raise self.gcode.error("Cannot invalidate power-loss checkpoint: %s" % exc)

    def cmd_POWER_LOSS_STATUS(self, gcmd):
        try:
            with open(self.path, encoding="utf-8") as f:
                record = json.load(f)
            if record.get("schema") != 1 or record.get("kind") != "layer_started":
                raise ValueError("unsupported checkpoint")
            gcmd.respond_info(
                "Power-loss evidence: file=%s started_layer=%s commanded_z=%s. "
                "Z is not a measured position; no recovery has started." %
                (record.get("file"), record.get("layer"), record.get("commanded_z"))
            )
        except FileNotFoundError:
            gcmd.respond_info("No saved power-loss checkpoint")
        except (OSError, ValueError, AttributeError) as exc:
            raise gcmd.error("Cannot read checkpoint: %s" % exc)

    def cmd_POWER_LOSS_PREPARE(self, gcmd):
        if self.sd is None or self.sd.is_active():
            raise gcmd.error("Recovery preparation requires an idle printer")
        physical_z = gcmd.get_float("PHYSICAL_Z", minval=0., maxval=250.)
        travel_z = gcmd.get_float("TRAVEL_Z", minval=0., maxval=250.)
        bed = gcmd.get_float("BED", minval=0., maxval=120.)
        nozzle = gcmd.get_float("NOZZLE", minval=170., maxval=300.)
        try:
            with open(self.path, encoding="utf-8") as f:
                record = json.load(f)
            if record.get("schema") != 1 or record.get("kind") != "layer_started":
                raise ValueError("unsupported checkpoint")
            source = record["file"]
            root = os.path.realpath(self.sd.sdcard_dirname)
            if os.path.commonpath((root, os.path.realpath(source))) != root:
                raise ValueError("checkpoint file is outside virtual SD directory")
            if type(record.get("layer")) is not int:
                raise ValueError("invalid checkpoint layer")
            hasher = hashlib.sha256()
            with open(source, "rb") as f:
                raw = f.read()
            hasher.update(raw)
            if len(raw) != record.get("file_size") or hasher.hexdigest() != record.get("file_sha256"):
                raise ValueError("source does not match saved checkpoint")
            generator = os.path.abspath(os.path.join(
                os.path.dirname(__file__), "..", "..", "scripts", "prepare_layer_recovery.py"))
            spec = importlib.util.spec_from_file_location("_layer_recovery_generator", generator)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result = module.prepare(raw.decode("utf-8").splitlines(), record["layer"],
                                    physical_z, travel_z, bed, nozzle)
            output = os.path.splitext(source)[0] + "_recovery.gcode"
            with open(output, "x", encoding="utf-8") as f:
                f.write("\n".join(result) + "\n")
        except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError, ImportError) as exc:
            logging.exception("Power-loss recovery preparation failed")
            raise gcmd.error("Recovery preparation refused: %s" % exc)
        gcmd.respond_info("Prepared %s. Inspect the file; print has NOT started." % output)

    def cmd_POWER_LOSS_DISCARD(self, gcmd):
        if self.sd is not None and self.sd.is_active():
            raise gcmd.error("Cannot discard a checkpoint while printing")
        self._invalidate()
        gcmd.respond_info("Power-loss checkpoint discarded")

    def cmd_POWER_LOSS_CHECKPOINT(self, gcmd):
        if self.sd is None or not self.sd.is_active() or not self.sd.cmd_from_sd:
            raise gcmd.error("POWER_LOSS_CHECKPOINT requires an active SD print")
        layer = gcmd.get_int("LAYER", minval=0)
        path = self.sd.file_path()
        if not path:
            raise gcmd.error("No active SD file")
        try:
            stat = os.stat(path)
            key = (os.path.realpath(path), stat.st_size, stat.st_mtime_ns,
                   stat.st_ino, stat.st_dev)
            if self._identity is None or self._identity[0] != key:
                hasher = hashlib.sha256()
                with open(path, "rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
                after = os.stat(path)
                if (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev) != key[1:]:
                    raise ValueError("file changed while hashing")
                self._identity = (key, digest)
            # Flush prior queued movement. This is not a physical position sensor,
            # and the new layer has not necessarily finished when power is lost.
            toolhead = self.printer.lookup_object("toolhead")
            toolhead.wait_moves()
            now = self.printer.get_reactor().monotonic()
            state = self.printer.lookup_object("gcode_move").get_status(now)
            position = state["gcode_position"]
            record = {
                "schema": 1, "kind": "layer_started", "file": key[0],
                "file_size": key[1], "file_sha256": self._identity[1],
                "layer": layer, "commanded_z": position[2],
            }
            self._write(record)
        except (OSError, ValueError, KeyError) as exc:
            logging.exception("Power-loss checkpoint failed")
            raise gcmd.error("Power-loss checkpoint failed: %s" % exc)

    def _write(self, record):
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(prefix=".power-loss-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def load_config(config):
    return PowerLossCheckpoint(config)
