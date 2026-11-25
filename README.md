<h1 align="center">ExporTo for Autodesk Fusion</h1>
<p align="center"><strong>Send a model from Fusion to your slicer or CAD app.</strong><br>Choose an installed app from the context menu and ExporTo creates the STEP or STL file for you.</p>

<p align="center"><a href="https://github.com/4FusionLab/ExporTo/releases/latest">Download ExporTo</a> · <a href="README.ru.md">Русский</a> · <a href="https://github.com/4FusionLab/ExporTo/issues">Report a problem</a></p>

## Install

1. Download the release ZIP and extract the `ExporTo` folder to a permanent location.
2. In Fusion, open **Utilities → Scripts and Add-Ins**. Choose **+ → Script or add-in from device**, then select the extracted folder.
3. Select **Run** and enable **Run on Startup**.

## Export from the design browser

1. Select one or more bodies, components, or subassemblies, or select the design root. Right-click the selection.
2. For STEP, open **Export to**. For a mesh in millimeters, open **Export to → STL**.
3. Choose **File…** to save locally, or choose an application to open the export.

A multi-selection exports to one file with the bodies in their assembly positions.

The menu shows applications available on your computer. Common options include Bambu Studio, OrcaSlicer, PrusaSlicer, and FreeCAD. When Blender is installed, **Export to → STL → Blender** opens a new scene with the mesh. ExporTo uses installed application icons when available.

## Clear the export cache

**Export to → Clear cache** shows the total size of temporary STEP and STL files. Select it to remove those exports. Let the receiving app finish importing before clearing the cache.

## Compatibility

ExporTo is packaged for Autodesk Fusion on macOS and Windows.

ExporTo is an independent [4FusionLab](https://github.com/4FusionLab) project distributed under the [PolyForm Noncommercial License 1.0.0](LICENSE).
