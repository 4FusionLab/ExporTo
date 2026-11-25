"""Send Fusion models to local applications from the context menu."""

from collections import deque
from errno import ENOTEMPTY, EEXIST
import hashlib
import importlib.util
import math
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import traceback
from xml.etree import ElementTree

import adsk.core
import adsk.fusion


PREFIX = "ExporTo_"
CACHE_PREFIX = "ExporTo-"
CACHE_DIRECTORY = re.compile(r"ExporTo-[a-z0-9_]{8}")
CACHE_COMMAND = PREFIX + "ClearCache"
SELECTION_EXPORT_EVENT = PREFIX + "SelectionExport"
NATIVE_EXPORT_COMMAND = "ExportCommand"
APP_REFRESH_SECONDS = 20
HERE = Path(__file__).resolve().parent
RESOURCES = HERE / "resources"
# Fusion loads this file directly, so resolve the support module by its own path.
_spec = importlib.util.spec_from_file_location("exporto_platform_apps", HERE / "platform_apps.py")
platform_apps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(platform_apps)
EXTENSIONS = platform_apps.EXTENSIONS
CACHE_EXTENSIONS = {".mtl", *(suffix for suffixes in EXTENSIONS.values() for suffix in suffixes)}
FORMAT_NAMES = {"f3d": "Fusion Archive (F3D)"}
MESH_EXPORTERS = {
    "stl": "createSTLExportOptions",
    "3mf": "createC3MFExportOptions",
    "obj": "createOBJExportOptions",
}
COMPONENT_EXPORTERS = {
    "step": "createSTEPExportOptions",
    "f3d": "createFusionArchiveExportOptions",
    "iges": "createIGESExportOptions",
    "sat": "createSATExportOptions",
    "smt": "createSMTExportOptions",
    "usdz": "createUSDExportOptions",
}
OTHER_FORMATS = ("f3d", "iges", "obj", "sat", "smt", "usdz")
_addin = None


def export_filename(name, suffix):
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    filename = filename.encode("utf-8")[:200].decode("utf-8", errors="ignore").rstrip(" .")
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", filename.split(".", 1)[0]):
        filename = "_" + filename
    return f"{filename or 'Model'}{suffix}"


def export_path(name, suffix):
    directory = Path(tempfile.mkdtemp(prefix=CACHE_PREFIX))
    return directory / export_filename(name, suffix)


def cached_files():
    for directory in Path(tempfile.gettempdir()).glob(CACHE_PREFIX + "*"):
        if not CACHE_DIRECTORY.fullmatch(directory.name):
            continue
        try:
            if not stat.S_ISDIR(directory.lstat().st_mode):
                continue
            for path in directory.iterdir():
                if path.suffix.casefold() not in CACHE_EXTENSIONS:
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


def svg_point(point):
    # Fusion uses centimeters and SVG's vertical axis points down.
    return f"{point.x * 10:.9g} {-point.y * 10:.9g}"


def export_svg(sketch, path):
    curves = [curve for curve in sketch.sketchCurves if curve.isVisible and not curve.isConstruction]
    if not curves:
        raise RuntimeError("The sketch contains no visible curves to export as SVG.")
    if any(not curve.is2D for curve in curves):
        raise RuntimeError("SVG export requires curves on the sketch plane.")

    bounds = [curve.boundingBox for curve in curves]
    left = 10 * min(box.minPoint.x for box in bounds) - 0.1
    top = -10 * max(box.maxPoint.y for box in bounds) - 0.1
    width = 10 * max(box.maxPoint.x for box in bounds) - left + 0.1
    height = -10 * min(box.minPoint.y for box in bounds) - top + 0.1
    svg = ElementTree.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": f"{width:.9g}mm", "height": f"{height:.9g}mm",
        "viewBox": f"{left:.9g} {top:.9g} {width:.9g} {height:.9g}",
    })
    group = ElementTree.SubElement(svg, "g", fill="none", stroke="black", attrib={"stroke-width": "0.2"})
    for curve in curves:
        geometry = getattr(curve, "geometry", None)
        if arc := adsk.core.Arc3D.cast(geometry):
            radius = f"{10 * arc.radius:.9g}"
            large = int(abs(arc.endAngle - arc.startAngle) > math.pi)
            sweep = int(arc.normal.z < 0)
            data = f"M {svg_point(arc.startPoint)} A {radius} {radius} 0 {large} {sweep} {svg_point(arc.endPoint)}"
        elif circle := adsk.core.Circle3D.cast(geometry):
            cx, cy = svg_point(circle.center).split()
            ElementTree.SubElement(group, "circle", cx=cx, cy=cy, r=f"{circle.radius * 10:.9g}")
            continue
        elif line := adsk.core.Line3D.cast(geometry):
            data = f"M {svg_point(line.startPoint)} L {svg_point(line.endPoint)}"
        else:
            evaluator = geometry.evaluator if geometry else curve.evaluator
            valid, start, end = evaluator.getParameterExtents()
            if not valid:
                raise RuntimeError("Fusion could not read a sketch curve for SVG export.")
            # Chord tolerance in centimeters, equivalent to 0.01 mm.
            valid, points = evaluator.getStrokes(start, end, 0.001)
            if not valid or len(points) < 2:
                raise RuntimeError("Fusion could not convert a sketch curve to SVG.")
            data = "M " + svg_point(points[0]) + " L " + " ".join(svg_point(point) for point in points[1:])
        ElementTree.SubElement(group, "path", d=data)
    ElementTree.ElementTree(svg).write(path, encoding="utf-8", xml_declaration=True)


def export_model(design, entity, fmt, path):
    manager = design.exportManager
    filename = str(path)
    if fmt == "svg":
        export_svg(entity, path)
        success = True
    elif fmt == "dxf" and (sketch := adsk.fusion.Sketch.cast(entity)):
        # The replacement ExportManager method is still a preview.
        success = sketch.saveAsDXF(filename)
    elif fmt == "dxf":
        options = manager.createDXFFlatPatternExportOptions(filename, entity)
        success = options and manager.execute(options)
    elif fmt in MESH_EXPORTERS:
        options = getattr(manager, MESH_EXPORTERS[fmt])(entity, filename)
        if options:
            options.sendToPrintUtility = False
            if fmt != "3mf":
                options.unitType = adsk.fusion.DistanceUnits.MillimeterDistanceUnits
            if fmt != "stl":
                options.isOneFilePerBody = False
            options.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
        success = options and manager.execute(options)
    else:
        create_options = getattr(manager, COMPONENT_EXPORTERS[fmt])
        if fmt == "step" and adsk.fusion.Occurrence.cast(entity):
            options = create_options(filename)
            if options:
                options.geometry = entity
        else:
            options = create_options(filename, entity)
        success = options and manager.execute(options)

    if not success:
        raise RuntimeError(f"Fusion could not export the selected geometry as {fmt.upper()}.")
    if not path.is_file() and len(EXTENSIONS[fmt]) > 1:
        # A translator can use its preferred spelling of the file extension.
        for suffix in EXTENSIONS[fmt]:
            for candidate in (path.with_suffix(suffix), path.with_name(path.name + suffix)):
                if candidate.is_file():
                    candidate.replace(path)
                    break
            if path.is_file():
                break
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"Fusion did not produce a {fmt.upper()} file.")


def selection_bodies(entity):
    if body := adsk.fusion.BRepBody.cast(entity):
        yield body
        return
    yield from (body for body in entity.bRepBodies if body.isLightBulbOn)
    occurrence = adsk.fusion.Occurrence.cast(entity)
    children = occurrence.childOccurrences if occurrence else entity.occurrences
    for child in children:
        if child.isLightBulbOn:
            yield from selection_bodies(child)


def selection_geometry(entities):
    bodies = []
    faces = []
    for entity in entities:
        if face := adsk.fusion.BRepFace.cast(entity):
            if face not in faces:
                faces.append(face)
            continue
        for body in selection_bodies(entity):
            if body not in bodies:
                bodies.append(body)
    # A whole selected body already contains its selected faces.
    return bodies + [face for face in faces if face.body not in bodies]


def geometry_name(entity):
    if face := adsk.fusion.BRepFace.cast(entity):
        return f"{face.body.name} - Face"
    return entity.name


def export_selection(design, entities, fmt, path):
    if not needs_export_document(entities, fmt):
        export_model(design, entities[0], fmt, path)
        return

    geometry = selection_geometry(entities)
    if not geometry:
        raise RuntimeError("The selection contains no bodies or faces to export.")

    # Assembly-context copies retain their placement. A face copy is a sheet body.
    manager = adsk.fusion.TemporaryBRepManager.get()
    copies = [(geometry_name(entity), manager.copy(entity)) for entity in geometry]
    app = adsk.core.Application.get()
    document = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    try:
        design = adsk.fusion.Design.cast(document.products.itemByProductType("DesignProductType"))
        design.designType = adsk.fusion.DesignTypes.DirectDesignType
        for name, copy in copies:
            exported_body = design.rootComponent.bRepBodies.add(copy) if copy else None
            if not exported_body:
                raise RuntimeError(f"Fusion could not prepare {name} for export.")
            exported_body.name = name
        export_model(design, design.rootComponent, fmt, path)
    finally:
        document.close(False)


def needs_export_document(entities, fmt):
    if len(entities) > 1:
        return True
    entity = entities[0]
    if adsk.fusion.BRepFace.cast(entity):
        return True
    if fmt not in COMPONENT_EXPORTERS:
        return False
    return bool(adsk.fusion.BRepBody.cast(entity)) or (
        fmt != "step" and bool(adsk.fusion.Occurrence.cast(entity))
    )


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
        if self.fmt and not target:
            return
        command = args.command
        command.isExecutedWhenPreEmpted = False

        def execute(_args):
            if not self.fmt:
                self.addin.clear_cache()
            elif self.fmt == "native" or needs_export_document(target[2], self.fmt):
                self.addin.queue_export(self.destination, self.fmt, target)
            else:
                self.addin.export(self.destination, self.fmt, *target)

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


class SelectionExportHandler(adsk.core.CustomEventHandler):
    def __init__(self, addin):
        super().__init__()
        self.addin = addin

    def notify(self, _args):
        if self.addin.pending_exports:
            destination, fmt, target = self.addin.pending_exports.popleft()
            self.addin.export(destination, fmt, *target)


class Addin:
    def __init__(self):
        self.app = adsk.core.Application.get()
        self.ui = self.app.userInterface
        self.definitions = {}
        self.created_handlers = {}
        self.command_handlers = set()
        self.menu_target = None
        self.menu_handler = MenuHandler(self)
        self.destinations = {fmt: [] for fmt in EXTENSIONS}
        self.destination_icons = {}
        self.refresh_at = 0
        self.clear_definition = None
        self.native_definition = None
        self.file_definitions = {}
        self.export_event = None
        self.export_handler = SelectionExportHandler(self)
        self.pending_exports = deque()

    def add_command(self, id, name, hint, icon, handler):
        definition = self.ui.commandDefinitions.addButtonDefinition(id, name, hint, str(icon))
        self.definitions[id] = definition
        self.created_handlers[id] = handler
        definition.commandCreated.add(handler)
        return definition

    def start(self):
        self.export_event = self.app.registerCustomEvent(SELECTION_EXPORT_EVENT)
        if not self.export_event or not self.export_event.add(self.export_handler):
            raise RuntimeError("Fusion could not register the selection export event.")
        for fmt in self.destinations:
            self.file_definitions[fmt] = self.add_command(
                PREFIX + fmt + "_File", f"{fmt.upper()} File…",
                f"Save the selection as a {fmt.upper()} file.",
                RESOURCES / "export", CreatedHandler(self, fmt=fmt),
            )
        self.clear_definition = self.add_command(
            CACHE_COMMAND, "Clear cache", "Delete temporary exports created by ExporTo.",
            RESOURCES / "clear", CreatedHandler(self),
        )
        self.native_definition = self.add_command(
            PREFIX + "NativeExport", "Export entire design in Fusion…",
            "Choose F3Z, Inventor, DWG, DXF, FBX, SketchUp, STEP AP242, or another available format "
            "in Fusion's export dialog.",
            RESOURCES / "export", CreatedHandler(self, fmt="native"),
        )
        self.refresh_apps()
        self.ui.markingMenuDisplaying.add(self.menu_handler)

    def stop(self):
        self.ui.markingMenuDisplaying.remove(self.menu_handler)
        if self.export_event:
            self.app.unregisterCustomEvent(SELECTION_EXPORT_EVENT)
        self.pending_exports.clear()
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

    def add_format_menu(self, controls, fmt):
        name = FORMAT_NAMES.get(fmt, fmt.upper())
        icon = RESOURCES / ("stl" if fmt in MESH_EXPORTERS else "export")
        submenu = controls.addDropDown(name, str(icon), PREFIX + fmt.upper() + "Menu")
        self.add_destinations(submenu.controls, fmt)

    def add_destinations(self, controls, fmt):
        controls.addCommand(self.file_definitions[fmt])
        for destination in self.destinations[fmt]:
            controls.addCommand(self.destination_definition(destination, fmt))

    def populate_menu(self, args):
        self.menu_target = None
        entities = args.selectedEntities
        design = adsk.fusion.Design.cast(self.app.activeProduct)
        if not design or not entities:
            return
        dxf_entity = None
        if len(entities) == 1:
            dxf_entity = adsk.fusion.Sketch.cast(entities[0]) or adsk.fusion.FlatPattern.cast(entities[0])
        if dxf_entity:
            selection = (dxf_entity,)
        else:
            selection = tuple(
                adsk.fusion.Occurrence.cast(entity) or adsk.fusion.Component.cast(entity)
                or adsk.fusion.BRepBody.cast(entity) or adsk.fusion.BRepFace.cast(entity)
                for entity in entities
            )
            if not all(selection):
                return

        if time.monotonic() >= self.refresh_at:
            self.refresh_apps()

        menu = args.linearMarkingMenu.controls.addDropDown("Export to", str(RESOURCES / "export"), PREFIX + "Menu")
        if dxf_entity:
            self.add_destinations(menu.controls, "dxf")
            if adsk.fusion.Sketch.cast(dxf_entity):
                self.add_format_menu(menu.controls, "svg")
        else:
            self.add_destinations(menu.controls, "step")
            self.add_format_menu(menu.controls, "stl")
            self.add_format_menu(menu.controls, "3mf")
            other = menu.controls.addDropDown("Other", str(RESOURCES / "export"), PREFIX + "OtherMenu")
            for fmt in OTHER_FORMATS:
                self.add_format_menu(other.controls, fmt)
            if self.ui.commandDefinitions.itemById(NATIVE_EXPORT_COMMAND):
                other.controls.addSeparator(PREFIX + "NativeExportSeparator")
                other.controls.addCommand(self.native_definition)
        menu.controls.addSeparator(PREFIX + "CacheSeparator")
        self.refresh_cache_label()
        menu.controls.addCommand(self.clear_definition)
        self.menu_target = (self.app.activeDocument, design, selection)

    def queue_export(self, destination, fmt, target):
        # Fusion can only open the temporary document outside a command transaction.
        self.pending_exports.append((destination, fmt, target))
        # Fusion can report False even when it delivers the event.
        self.app.fireCustomEvent(SELECTION_EXPORT_EVENT)

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

    def save_file(self, fmt, document, design, entities, name):
        dialog = self.ui.createFileDialog()
        dialog.title = f"Save {fmt.upper()} file"
        suffixes = EXTENSIONS[fmt]
        patterns = ";".join(f"*{suffix}" for suffix in suffixes)
        dialog.filter = f"{fmt.upper()} files ({patterns})"
        dialog.initialFilename = export_filename(name, suffixes[0])
        if dialog.showSave() != adsk.core.DialogResults.DialogOK:
            return
        path = Path(dialog.filename)
        if path.suffix.casefold() not in suffixes:
            path = path.with_name(path.name + suffixes[0])
            if path.exists() and self.ui.messageBox(
                f"Replace {path.name}?", dialog.title, adsk.core.MessageBoxButtonTypes.YesNoButtonType
            ) != adsk.core.DialogResults.DialogYes:
                return
        self.check_selection(document, entities)
        with tempfile.TemporaryDirectory(dir=path.parent) as directory:
            staged = Path(directory) / path.name
            export_selection(design, entities, fmt, staged)
            if fmt == "obj":
                assets = [asset for asset in staged.parent.iterdir() if asset != staged]
                conflicts = [asset.name for asset in assets if (path.parent / asset.name).exists()]
                if conflicts and self.ui.messageBox(
                    "Replace accompanying OBJ files?\n" + "\n".join(conflicts),
                    dialog.title, adsk.core.MessageBoxButtonTypes.YesNoButtonType,
                ) != adsk.core.DialogResults.DialogYes:
                    return
                for asset in assets:
                    asset.replace(path.parent / asset.name)
            staged.replace(path)
        self.ui.statusMessage = f"Saved {path.name}."

    def check_selection(self, document, entities):
        if self.app.activeDocument != document or any(not entity.isValid for entity in entities):
            raise RuntimeError("The Fusion selection is no longer available.")

    def export(self, destination, fmt, document, design, entities):
        path = None
        try:
            self.check_selection(document, entities)
            if fmt == "native":
                command = self.ui.commandDefinitions.itemById(NATIVE_EXPORT_COMMAND)
                if not command or not command.execute():
                    raise RuntimeError("Fusion could not open its export dialog.")
                return
            name = document.name
            if len(entities) > 1:
                name += " - Selection"
            elif entities[0] != design.rootComponent:
                name = geometry_name(entities[0])
            if destination is None:
                self.save_file(fmt, document, design, entities, name)
                return
            path = export_path(name, destination.suffix or EXTENSIONS[fmt][0])
            export_selection(design, entities, fmt, path)
            platform_apps.launch_app(destination, path)
        except Exception as error:
            if path:
                shutil.rmtree(path.parent, ignore_errors=True)
            self.app.log(traceback.format_exc())
            detail = (getattr(error, "stderr", None) or str(error)).strip()
            if "user is restricted" in detail.casefold():
                detail = f"Your Fusion license does not allow {fmt.upper()} export."
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
