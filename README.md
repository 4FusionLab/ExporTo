<h1 align="center">ExporTo for Autodesk Fusion</h1>
<p align="center"><strong>Send models and sketches from Fusion to other apps.</strong><br>Save a local file or open your export in an installed application from the context menu.</p>

<p align="center"><a href="https://github.com/4FusionLab/ExporTo/releases/latest">Download ExporTo</a> · <a href="README.ru.md">Русский</a> · <a href="https://github.com/4FusionLab/ExporTo/issues">Report a problem</a></p>

## Install

1. Download the release ZIP and extract the `ExporTo` folder to a permanent location.
2. In Fusion, open **Utilities → Scripts and Add-Ins**. Choose **+ → Script or add-in from device**, then select the extracted folder.
3. Select **Run** and enable **Run on Startup**.

## Export from the design browser

1. Select one or more bodies (solid or surface), components, or subassemblies, or select the design root. You can also select individual faces in the canvas. Right-click the selection.
2. For STEP, open **Export to**. For a mesh, choose **STL** or **3MF** from the same menu. **Other** contains **Fusion Archive (F3D)**, **IGES (.iges or .igs)**, **OBJ**, **SAT**, **SMT**, and **USDZ**.
3. Choose the file command for the format, such as **STEP File…** or **3MF File…**, to save locally. Choose an application to open the export.

Exporting multiple bodies or components produces one model with the bodies in their assembly positions. STL and OBJ use millimeters. Keep the material file alongside the OBJ when Fusion creates one.

Selected faces export as surface bodies in their assembly positions. If a whole body is selected, its selected faces are included only once. Mesh formats tessellate surfaces and retain their open boundaries.

For F3Z (a Fusion archive with external references), Inventor, DWG, model DXF, FBX, SketchUp, STEP AP242, or another format offered by your version of Fusion, open **Export to → Other → Export entire design in Fusion…** and choose the format in Fusion's export dialog. The dialog exports the entire design and lists the formats available to your license. IGES and SAT require an eligible Fusion license.

The menu shows installed applications for each format. For example, use OrcaSlicer for 3MF or an installed CAD application such as Shapr3D for IGES. When Blender is installed, **Export to → STL → Blender** opens a new scene with the mesh. ExporTo uses installed application icons when available.

## Export a sketch

Right-click one sketch in the design browser. **Export to → DXF File…** saves the sketch, and the applications in the same menu open it as DXF. **Export to → SVG** offers **SVG File…** and application entries. SVG uses millimeters for visible planar curves, skips construction geometry, and approximates splines and other curves with a chord tolerance of 0.01 mm. Lines, circles, and circular arcs retain their geometry.

Select a sheet metal flat pattern to access DXF export for the pattern.

## Clear the export cache

**Export to → Clear cache** shows the total size of temporary exports created when opening an application. Select it to remove those files. Let the receiving app finish importing before clearing the cache.

## Compatibility

ExporTo is packaged for Autodesk Fusion on macOS and Windows.

ExporTo is an independent [4FusionLab](https://github.com/4FusionLab) project distributed under the [PolyForm Noncommercial License 1.0.0](LICENSE).
