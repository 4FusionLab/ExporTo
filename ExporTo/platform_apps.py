"""Discover local applications that can open a model and launch them."""

from collections import namedtuple
import hashlib
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from xml.parsers.expat import ExpatError


App = namedtuple("App", "name path formats icon executable arguments suffix", defaults=(None, None, (), None))
EXTENSIONS = {
    "step": (".step", ".stp"),
    "stl": (".stl",),
    "3mf": (".3mf",),
    "dxf": (".dxf",),
    "f3d": (".f3d",),
    "iges": (".iges", ".igs"),
    "obj": (".obj",),
    "sat": (".sat",),
    "smt": (".smt",),
    "svg": (".svg",),
    "usdz": (".usdz",),
}
# Some applications import these formats without registering a system file handler.
KNOWN_APPS = (
    ("Bambu Studio", ("bambustudio",), ("step", "stl", "3mf", "obj")),
    ("OrcaSlicer", ("orcaslicer",), ("step", "stl", "3mf", "obj")),
    ("PrusaSlicer", ("prusaslicer",), ("step", "stl", "3mf", "obj")),
    ("Creality Print", ("crealityprint",), ("step", "stl")),
    ("QIDI Studio", ("qidistudio",), ("step", "stl")),
    ("UltiMaker Cura", ("ultimakercura", "cura"), ("stl",)),
    ("ideaMaker", ("ideamaker",), ("stl",)),
    ("Blender", ("blender",), ("stl",)),
    ("FreeCAD", ("freecad",), ("step", "stl", "iges", "dxf")),
    ("Shapr3D", ("shapr3d",), ("step", "stl", "iges", "dxf")),
    ("Rhino", ("rhinoceros", "rhino"), ("step", "stl", "iges", "dxf", "obj")),
    ("Autodesk Inventor", ("inventor",), ("step", "stl", "iges", "sat", "dxf")),
    ("SOLIDWORKS", ("sldworks", "solidworks"), ("step", "stl", "iges")),
    ("Inkscape", ("inkscape",), ("svg",)),
)
IGNORED_EXECUTABLES = {
    "cmd", "explorer", "rundll32", "powershell", "pwsh", "wscript", "cscript", "mshta",
    "fusion", "fusionlauncher", "autodeskfusion",
}
MAC_TYPES = {
    "step": {"public.step", "public.step-file", "public.stp", "com.apple.step", "model.step"},
    "stl": {"public.stl", "public.standard-tessellated-geometry-format", "com.apple.stl", "model.stl"},
    "3mf": {"public.3mf", "org.3mf.3mf"},
    "dxf": {"com.autodesk.dxf"},
    "iges": {"public.iges", "com.autodesk.iges"},
    "obj": {"public.wavefront-obj", "com.wavefront.obj"},
    "svg": {"public.svg-image"},
    "usdz": {"com.pixar.universal-scene-description-mobile"},
}
ICON_SIZES = {
    "16x16.png": 16,
    "32x32.png": 32,
    "16x16@2x.png": 32,
    "32x32@2x.png": 64,
}

BLENDER_IMPORT = """import bpy, sys
initial_objects = tuple(bpy.context.scene.objects)
path = sys.argv[-1]
if bpy.app.version >= (4, 0, 0):
    result = bpy.ops.wm.stl_import(filepath=path, global_scale=0.001, forward_axis='Y', up_axis='Z')
else:
    result = bpy.ops.import_mesh.stl(filepath=path, global_scale=0.001, axis_forward='Y', axis_up='Z')
if 'FINISHED' not in result:
    raise RuntimeError('STL import did not finish')
for obj in initial_objects:
    bpy.data.objects.remove(obj, do_unlink=True)
for area in bpy.context.screen.areas if bpy.context.screen and hasattr(bpy.context, 'temp_override') else ():
    if area.type == 'VIEW_3D':
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.view3d.view_selected(use_all_regions=False)
"""


def known_app(name):
    key = re.sub(r"[^a-z0-9]", "", name.casefold())
    for label, aliases, formats in KNOWN_APPS:
        if any(re.fullmatch(alias + r"(?:version)?\d*", key) for alias in aliases):
            return label, formats
    return None, ()


def mac_bundles():
    # Check Applications even when Spotlight indexing is disabled.
    paths = set()
    for root in (Path("/Applications"), Path.home() / "Applications", Path("/System/Applications")):
        for folder, directories, _ in os.walk(root):
            bundles = [name for name in directories if name.casefold().endswith(".app")]
            directories[:] = [name for name in directories if name not in bundles and not name.startswith(".")]
            paths.update(Path(folder) / name for name in bundles)
    try:
        indexed = subprocess.check_output(
            ["/usr/bin/mdfind", "-0", 'kMDItemContentType == "com.apple.application-bundle"'],
            timeout=2,
        )
        paths.update(Path(os.fsdecode(path)) for path in indexed.split(b"\0") if path)
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(paths, key=lambda path: (not path.is_relative_to("/Applications"), str(path).casefold()))


def mac_formats(info):
    types = {key: set(MAC_TYPES.get(key, ())) for key in EXTENSIONS}
    for declaration in (*info.get("UTImportedTypeDeclarations", ()), *info.get("UTExportedTypeDeclarations", ())):
        identifier = declaration.get("UTTypeIdentifier", "").casefold()
        tags = declaration.get("UTTypeTagSpecification", {}).get("public.filename-extension", ())
        extensions = {tag.casefold().lstrip(".") for tag in ([tags] if isinstance(tags, str) else tags)}
        for key, suffixes in EXTENSIONS.items():
            if extensions.intersection(ext[1:] for ext in suffixes):
                types[key].add(identifier)

    formats = set()
    for document in info.get("CFBundleDocumentTypes", ()):
        if document.get("CFBundleTypeRole") == "None" or document.get("LSHandlerRank") == "None":
            continue
        extensions = {ext.casefold().lstrip(".") for ext in document.get("CFBundleTypeExtensions", ())}
        identifiers = {uti.casefold() for uti in document.get("LSItemContentTypes", ())}
        for key, suffixes in EXTENSIONS.items():
            if identifiers.intersection(types[key]) or extensions.intersection(ext[1:] for ext in suffixes):
                formats.add(key)
    return formats


def mac_apps():
    for bundle in mac_bundles():
        if any(parent.suffix.casefold() == ".app" or parent.name == ".Trash" for parent in bundle.parents):
            continue
        try:
            with (bundle / "Contents" / "Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            identifier = info.get("CFBundleIdentifier", "").casefold()
            if identifier == "com.apple.archiveutility":
                continue
            if identifier.startswith(("com.autodesk.fusion", "com.autodesk.dls.streamer.scriptapp.autodesk-fusion")):
                continue
            label = info.get("CFBundleDisplayName") or info.get("CFBundleName") or bundle.stem
            known_name, known_formats = known_app(bundle.stem)
            if not known_name:
                known_name, known_formats = known_app(label)
            if known_name == "FreeCAD":
                # The macOS bundle does not reliably accept unregistered IGES or DXF open events.
                known_formats = ("step", "stl")
            formats = mac_formats(info).union(known_formats)
            executable = bundle / "Contents" / "MacOS" / info.get("CFBundleExecutable", "")
            if not formats or not executable.is_file():
                continue
            icon_file = info.get("CFBundleIconFile")
            icon = None
            if icon_file:
                icon_path = bundle / "Contents" / "Resources" / icon_file
                icon = icon_path if icon_path.suffix else icon_path.with_suffix(".icns")
                if not icon.is_file():
                    icon = None
            yield App(known_name or label, bundle, formats, icon, executable)
        except (OSError, ValueError, plistlib.InvalidFileException, ExpatError, AttributeError, TypeError):
            continue


def registry_value(root, key, name=""):
    import winreg
    try:
        with winreg.OpenKey(root, key) as entry:
            return winreg.QueryValueEx(entry, name)[0]
    except OSError:
        return None


def registry_names(root, key, *, values=False):
    import winreg
    try:
        with winreg.OpenKey(root, key) as entry:
            subkeys, value_count, _ = winreg.QueryInfoKey(entry)
            if values:
                return [winreg.EnumValue(entry, index)[0] for index in range(value_count)]
            return [winreg.EnumKey(entry, index) for index in range(subkeys)]
    except OSError:
        return []


def windows_argv(command_line):
    import ctypes
    from ctypes import wintypes
    split = ctypes.windll.shell32.CommandLineToArgvW
    split.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    split.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = ctypes.windll.kernel32.LocalFree
    free.argtypes = (wintypes.HLOCAL,)
    free.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    items = split(os.path.expandvars(command_line), ctypes.byref(count))
    if not items:
        return ()
    try:
        return tuple(items[index] for index in range(count.value))
    finally:
        free(items)


def windows_app_path(name):
    import winreg
    path = Path(name)
    if path.is_absolute():
        return path if path.is_file() else None
    if path.name != name:
        return None
    key = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        value = registry_value(hive, key)
        if isinstance(value, str):
            target = Path(os.path.expandvars(value.strip('"')))
            if target.is_absolute() and target.is_file():
                return target
    found = shutil.which(name)
    return Path(found).resolve() if found else None


def windows_registered_apps():
    import winreg
    classes = winreg.HKEY_CLASSES_ROOT
    user = winreg.HKEY_CURRENT_USER
    by_extension = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"
    declared_types = {
        name: set(registry_names(classes, rf"Applications\{name}\SupportedTypes", values=True))
        for name in registry_names(classes, "Applications")
    }
    for fmt, suffixes in EXTENSIONS.items():
        for suffix in suffixes:
            paths = set(registry_names(classes, suffix + r"\OpenWithProgids", values=True))
            paths.update(registry_names(user, rf"{by_extension}\{suffix}\OpenWithProgids", values=True))
            paths.update(filter(None, (
                registry_value(classes, suffix),
                registry_value(user, rf"{by_extension}\{suffix}\UserChoice", "ProgId"),
            )))
            names = set(registry_names(classes, suffix + r"\OpenWithList"))
            names.update(name for name, types in declared_types.items() if suffix in types)
            for key in registry_names(user, rf"{by_extension}\{suffix}\OpenWithList", values=True):
                value = registry_value(user, rf"{by_extension}\{suffix}\OpenWithList", key)
                if isinstance(value, str) and value.casefold().endswith(".exe"):
                    names.add(value)
            paths.update("Applications\\" + name for name in names)
            for key in sorted(paths):
                if not key:
                    continue
                command = registry_value(classes, key + r"\shell\open\command")
                argv = windows_argv(command) if isinstance(command, str) and command.strip() else ()
                if not argv and key.startswith("Applications\\"):
                    argv = (key.removeprefix("Applications\\"),)
                if not argv:
                    continue
                executable = windows_app_path(argv[0])
                if not executable or executable.suffix.casefold() != ".exe":
                    continue
                if executable.stem.casefold() in IGNORED_EXECUTABLES or "fusion360" in executable.stem.casefold():
                    continue
                label, _ = known_app(executable.stem)
                friendly = registry_value(classes, rf"Applications\{executable.name}", "FriendlyAppName")
                if not isinstance(friendly, str) or friendly.startswith("@"):
                    friendly = executable.stem
                yield App(label or friendly, executable, {fmt}, executable, executable, argv[1:], suffix)


def windows_known_apps():
    import winreg
    root_names = {"autodesk", "blenderfoundation", "prusa3d", "solidworkscorp", "rhinoceros", "ultimaker", "raise3d"}
    roots = [
        Path(os.environ[var]) for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
        if os.environ.get(var)
    ]
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "Programs")
    candidates = {
        path for _, aliases, _ in KNOWN_APPS for alias in aliases
        if (path := windows_app_path(alias + ".exe"))
    }
    installed_roots = set()
    uninstall_paths = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for uninstall in uninstall_paths:
            for item in registry_names(hive, uninstall):
                key = rf"{uninstall}\{item}"
                label = registry_value(hive, key, "DisplayName")
                if not isinstance(label, str) or not known_app(label)[0]:
                    continue
                location = registry_value(hive, key, "InstallLocation")
                if isinstance(location, str):
                    target = Path(os.path.expandvars(location.strip('"')))
                    if target.is_absolute() and target.parent != target:
                        installed_roots.add(target)
                icon = registry_value(hive, key, "DisplayIcon")
                if isinstance(icon, str):
                    exe = Path(os.path.expandvars(re.sub(r",\s*-?\d+\s*$", "", icon).strip('"')))
                    if exe.is_absolute() and exe.suffix.casefold() == ".exe" and exe.is_file():
                        candidates.add(exe)
    roots.extend(installed_roots)
    for root in dict.fromkeys(roots):
        for current, directories, files in os.walk(root):
            depth = len(Path(current).relative_to(root).parts)
            if depth == 0 and root not in installed_roots:
                directories[:] = [
                    name for name in directories
                    if known_app(name)[0] or re.sub(r"[^a-z0-9]", "", name.casefold()) in root_names
                ]
            elif depth >= 4:
                directories.clear()
            candidates.update(
                Path(current) / file for file in files
                if file.casefold().endswith(".exe") and known_app(Path(file).stem)[0]
            )
    for path in sorted(candidates, key=lambda item: str(item).casefold(), reverse=True):
        label, formats = known_app(path.stem)
        if label:
            yield App(label, path, set(formats), path, path)


def discover_apps():
    if sys.platform == "darwin":
        apps = mac_apps()
    elif sys.platform == "win32":
        def windows_apps():
            yield from windows_registered_apps()
            yield from windows_known_apps()
        apps = windows_apps()
    else:
        return {fmt: [] for fmt in EXTENSIONS}

    by_format = {fmt: {} for fmt in EXTENSIONS}
    for app in apps:
        for fmt in app.formats:
            existing = by_format[fmt]
            existing.setdefault(app.name.casefold(), app)
    return {fmt: sorted(found.values(), key=lambda app: app.name.casefold()) for fmt, found in by_format.items()}


def app_icon_folder(app):
    if not app.icon:
        return None
    try:
        signature = f"{app.icon}:{app.icon.stat().st_mtime_ns}"
        directory = Path(tempfile.gettempdir()) / "ExporTo-icons" / hashlib.sha256(signature.encode()).hexdigest()[:24]
        directory.mkdir(parents=True, exist_ok=True)
        if all((directory / name).is_file() for name in ICON_SIZES):
            return directory

        if sys.platform == "darwin":
            for name, size in ICON_SIZES.items():
                subprocess.run(
                    ["/usr/bin/sips", "-s", "format", "png", "-z", str(size), str(size),
                     str(app.icon), "--out", str(directory / name)],
                    check=True, capture_output=True, timeout=3,
                )
        elif sys.platform == "win32":
            extract_windows_icons(app.icon, directory)
        if all((directory / name).is_file() for name in ICON_SIZES):
            return directory
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def extract_windows_icons(source, directory):
    # Extract the installed executable's own shell icon, without starting it.
    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$icon = [System.Drawing.Icon]::ExtractAssociatedIcon($env:EXPORTO_ICON_SOURCE)
if (-not $icon) { throw 'No application icon' }
try {
    $files = @{ '16x16.png' = 16; '32x32.png' = 32; '16x16@2x.png' = 32; '32x32@2x.png' = 64 }
    foreach ($file in $files.Keys) {
        $size = $files[$file]
        $bitmap = [System.Drawing.Bitmap]::new($size, $size)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.DrawIcon($icon, [System.Drawing.Rectangle]::new(0, 0, $size, $size))
            $bitmap.Save((Join-Path $env:EXPORTO_ICON_DIR $file), [System.Drawing.Imaging.ImageFormat]::Png)
        } finally {
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    }
} finally { $icon.Dispose() }
"""
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "EXPORTO_ICON_SOURCE": str(source), "EXPORTO_ICON_DIR": str(directory)},
        check=True, capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def windows_file_arguments(template, path):
    filename = re.compile(r"%(?:1|l|L)")
    if any(filename.search(argument) for argument in template):
        return [filename.sub(lambda _: str(path), argument) for argument in template if argument != "%*"]
    if "%*" in template:
        return [str(path) if argument == "%*" else argument for argument in template]
    return [*template, str(path)]


def launch_app(app, path):
    if app.name == "Blender" and path.suffix.casefold() == ".stl":
        subprocess.Popen(
            [str(app.executable), "--factory-startup", "--python-expr", BLENDER_IMPORT, "--", str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=app.executable.parent,
        )
        return

    if sys.platform == "darwin":
        subprocess.run(
            ["/usr/bin/open", "-a", str(app.path), str(path)],
            check=True, capture_output=True, text=True, timeout=10,
        )
    elif sys.platform == "win32":
        arguments = windows_file_arguments(app.arguments, path)
        subprocess.Popen([str(app.executable), *arguments], cwd=app.executable.parent)
