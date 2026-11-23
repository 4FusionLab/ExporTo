"""Send Fusion models to local applications from the context menu."""

from errno import ENOTEMPTY, EEXIST
import hashlib
import importlib.util
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import traceback

import adsk.core
import adsk.fusion


PREFIX = "ExporTo_"
CACHE_PREFIX = "ExporTo-"
CACHE_DIRECTORY = re.compile(r"ExporTo-[a-z0-9_]{8}")
CACHE_COMMAND = PREFIX + "ClearCache"
APP_REFRESH_SECONDS = 20
HERE = Path(__file__).resolve().parent
RESOURCES = HERE / "resources"
# Fusion loads this file directly, so resolve the support module by its own path.
_spec = importlib.util.spec_from_file_location("exporto_platform_apps", HERE / "platform_apps.py")
platform_apps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(platform_apps)
_addin = None


def export_path(name, suffix):
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    filename = filename.encode("utf-8")[:200].decode("utf-8", errors="ignore").rstrip(" .")
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", filename.split(".", 1)[0]):
        filename = "_" + filename
    directory = Path(tempfile.mkdtemp(prefix=CACHE_PREFIX))
    return directory / f"{filename or 'Model'}{suffix}"


def cached_files():
    for directory in Path(tempfile.gettempdir()).glob(CACHE_PREFIX + "*"):
        if not CACHE_DIRECTORY.fullmatch(directory.name):
            continue
        try:
            if not stat.S_ISDIR(directory.lstat().st_mode):
                continue
            for path in directory.iterdir():
                if path.suffix.casefold() not in {".step", ".stp", ".stl"}:
                    continue
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(info.st_mode):
                    yield path, info.st_size
        except FileNotFoundError:
            continue


def format_size(size):
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{f'{value:.1f}'.rstrip('0').rstrip('.')} {unit}"


def export_model(design, entity, path):
    manager = design.exportManager
    filename = str(path)
    fmt = "stl" if path.suffix.casefold() == ".stl" else "step"
    if fmt == "stl":
        options = manager.createSTLExportOptions(entity, filename)
        if options:
            options.sendToPrintUtility = False
            options.unitType = adsk.fusion.DistanceUnits.MillimeterDistanceUnits
            options.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
    elif adsk.fusion.Occurrence.cast(entity):
        options = manager.createSTEPExportOptions(filename)
        if options:
            options.geometry = entity
    else:
        options = manager.createSTEPExportOptions(filename, entity)

    if not options or not manager.execute(options):
        raise RuntimeError(f"Fusion could not export the selected geometry as {fmt.upper()}.")
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"Fusion did not produce a {fmt.upper()} file.")


class MenuHandler(adsk.core.MarkingMenuEventHandler):
    def __init__(self, addin):
        super().__init__()
        self.addin = addin

    def notify(self, args):
        try:
            self.addin.populate_menu(adsk.core.MarkingMenuEventArgs.cast(args))
        except Exception:
            self.addin.app.log(traceback.format_exc())


class CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self, addin, destination=None, fmt=None):
        super().__init__()
        self.addin = addin
        self.destination = destination
        self.fmt = fmt

    def notify(self, args):
        target = self.addin.menu_target
        self.addin.menu_target = None
        if self.destination and not target:
            return
        command = args.command
        command.isExecutedWhenPreEmpted = False

        def execute(_args):
            if self.destination:
                self.addin.export(self.destination, self.fmt, *target)
            else:
                self.addin.clear_cache()

        def destroy(_args):
            self.addin.command_handlers.discard(handlers)

        handlers = (CommandHandler(execute), CommandHandler(destroy))
        self.addin.command_handlers.add(handlers)
        command.execute.add(handlers[0])
        command.destroy.add(handlers[1])


class CommandHandler(adsk.core.CommandEventHandler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def notify(self, args):
        self.callback(args)


class Addin:
    def __init__(self):
        self.app = adsk.core.Application.get()
        self.ui = self.app.userInterface
        self.definitions = {}
        self.created_handlers = {}
        self.command_handlers = set()
        self.menu_target = None
        self.menu_handler = MenuHandler(self)
        self.destinations = {"step": [], "stl": []}
        self.destination_icons = {}
        self.refresh_at = 0
        self.clear_definition = None

    def add_command(self, id, name, hint, icon, handler):
        definition = self.ui.commandDefinitions.addButtonDefinition(id, name, hint, str(icon))
        self.definitions[id] = definition
        self.created_handlers[id] = handler
        definition.commandCreated.add(handler)
        return definition

    def start(self):
        self.clear_definition = self.add_command(
            CACHE_COMMAND, "Clear cache", "Delete temporary STEP and STL files created by ExporTo.",
            RESOURCES / "clear", CreatedHandler(self),
        )
        self.refresh_apps()
        self.ui.markingMenuDisplaying.add(self.menu_handler)

    def stop(self):
        self.ui.markingMenuDisplaying.remove(self.menu_handler)
        for definition in self.definitions.values():
            if definition.isValid:
                definition.deleteMe()

    def destination_definition(self, destination, fmt):
        fingerprint = repr((destination.name, destination.path, destination.arguments, destination.suffix)).encode("utf-8")
        key = f"{PREFIX}{fmt}_{hashlib.sha256(fingerprint).hexdigest()[:20]}"
        if key not in self.definitions:
            if destination.path not in self.destination_icons:
                self.destination_icons[destination.path] = platform_apps.app_icon_folder(destination)
            icon = self.destination_icons[destination.path] or RESOURCES / "application"
            return self.add_command(
                key, destination.name, f"Open the selection as {fmt.upper()} in {destination.name}.",
                icon, CreatedHandler(self, destination, fmt),
            )
        return self.definitions[key]

    def refresh_apps(self):
        self.destinations = platform_apps.discover_apps()
        for fmt, destinations in self.destinations.items():
            for destination in destinations:
                self.destination_definition(destination, fmt)
        self.refresh_at = time.monotonic() + APP_REFRESH_SECONDS

    def populate_menu(self, args):
        self.menu_target = None
        entities = args.selectedEntities
        design = adsk.fusion.Design.cast(self.app.activeProduct)
        if not design or not entities or len(entities) != 1 or not entities[0]:
            return
        selected = entities[0]
        entity = adsk.fusion.Occurrence.cast(selected) or adsk.fusion.Component.cast(selected)
        body = None if entity else adsk.fusion.BRepBody.cast(selected)
        if not entity and not body:
            return

        if time.monotonic() >= self.refresh_at:
            self.refresh_apps()

        menu = args.linearMarkingMenu.controls.addDropDown("Export to", str(RESOURCES / "export"), PREFIX + "Menu")
        step_apps = self.destinations["step"] if entity else []
        for destination in step_apps:
            menu.controls.addCommand(self.destination_definition(destination, "step"))
        stl_apps = self.destinations["stl"]
        if stl_apps:
            stl = menu.controls.addDropDown("STL", str(RESOURCES / "stl"), PREFIX + "STLMenu")
            for destination in stl_apps:
                stl.controls.addCommand(self.destination_definition(destination, "stl"))
        if step_apps or stl_apps:
            menu.controls.addSeparator(PREFIX + "CacheSeparator")
        self.refresh_cache_label()
        menu.controls.addCommand(self.clear_definition)
        self.menu_target = (self.app.activeDocument, design, entity or body)

    def refresh_cache_label(self):
        try:
            size = format_size(sum(size for _, size in cached_files()))
        except OSError:
            self.app.log(traceback.format_exc())
            self.clear_definition.name = "Clear cache"
            return
        self.clear_definition.name = f"Clear cache ({size})"

    def clear_cache(self):
        deleted = 0
        failures = []
        try:
            for path, size in list(cached_files()):
                try:
                    path.unlink()
                    deleted += size
                    try:
                        path.parent.rmdir()
                    except OSError as error:
                        if error.errno not in {ENOTEMPTY, EEXIST} and not isinstance(error, FileNotFoundError):
                            raise
                except FileNotFoundError:
                    continue
                except OSError as error:
                    failures.append(str(error))
        except OSError as error:
            failures.append(str(error))
        self.refresh_cache_label()
        if failures:
            self.app.log("\n".join(failures))
            self.ui.messageBox(
                f"Removed {format_size(deleted)}. Some temporary files could not be cleared.",
                "Export to",
            )
        else:
            self.ui.statusMessage = f"ExporTo cache cleared ({format_size(deleted)})."

    def export(self, destination, fmt, document, design, entity):
        path = None
        try:
            if self.app.activeDocument != document or not entity.isValid:
                raise RuntimeError("The Fusion selection is no longer available.")
            name = document.name if entity == design.rootComponent else entity.name
            path = export_path(name, destination.suffix or f".{fmt}")
            export_model(design, entity, path)
            platform_apps.launch_app(destination, path)
        except Exception as error:
            if path:
                shutil.rmtree(path.parent, ignore_errors=True)
            self.app.log(traceback.format_exc())
            detail = (getattr(error, "stderr", None) or str(error)).strip()
            self.ui.messageBox(detail, "Export to")


def run(_context):
    global _addin
    if _addin is None:
        # Fusion can discover the same add-in through both its path and a symlink.
        ui = adsk.core.Application.get().userInterface
        if ui.commandDefinitions.itemById(CACHE_COMMAND):
            return
        _addin = Addin()
        try:
            _addin.start()
        except Exception:
            stop(_context)
            raise


def stop(_context):
    global _addin
    if _addin is not None:
        _addin.stop()
        _addin = None
