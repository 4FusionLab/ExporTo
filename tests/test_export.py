"""Regression tests for export routing, menus, and local save behavior."""

import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ADDIN = Path(__file__).resolve().parents[1] / "ExporTo" / "ExporTo.py"


class Entity:
    @classmethod
    def cast(cls, value):
        return value if isinstance(value, cls) else None


def load_addin():
    adsk = ModuleType("adsk")
    adsk.core = ModuleType("adsk.core")
    adsk.fusion = ModuleType("adsk.fusion")
    for name in ("MarkingMenuEventHandler", "CommandCreatedEventHandler",
                 "CommandEventHandler", "CustomEventHandler"):
        setattr(adsk.core, name, type(name, (), {}))
    for name in ("Design", "BRepBody", "BRepFace", "Component", "Occurrence", "Sketch", "FlatPattern"):
        setattr(adsk.fusion, name, type(name, (Entity,), {}))
    adsk.core.DialogResults = SimpleNamespace(DialogOK=1, DialogYes=2)
    adsk.core.MessageBoxButtonTypes = SimpleNamespace(YesNoButtonType=1)
    spec = importlib.util.spec_from_file_location("exporto_test", ADDIN)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"adsk": adsk, "adsk.core": adsk.core, "adsk.fusion": adsk.fusion}):
        spec.loader.exec_module(module)
    return module


exporto = load_addin()


class Menu:
    def __init__(self):
        self.controls = self
        self.items = {}

    def addDropDown(self, name, icon, control_id):
        submenu = Menu()
        self.items[name] = submenu
        return submenu

    def addCommand(self, definition):
        self.items[definition.name] = definition

    def addSeparator(self, control_id):
        pass


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.addin = exporto.Addin.__new__(exporto.Addin)
        self.design = exporto.adsk.fusion.Design()
        self.document = object()
        self.addin.app = Mock(activeProduct=self.design, activeDocument=self.document)
        self.addin.ui = Mock()
        self.addin.destinations = {fmt: [] for fmt in exporto.EXTENSIONS}
        self.addin.file_definitions = {}
        self.addin.export_handler = Mock()
        self.addin.menu_handler = Mock()
        self.addin.add_command = Mock(side_effect=lambda _, name, *_args: SimpleNamespace(name=name))
        self.addin.refresh_apps = Mock()
        self.addin.refresh_at = float("inf")
        self.addin.refresh_cache_label = Mock()
        self.addin.start()

    def menu_for(self, *entities):
        menu = Menu()
        self.addin.populate_menu(SimpleNamespace(selectedEntities=entities, linearMarkingMenu=menu))
        return menu.items["Export to"].items

    def test_model_and_sketch_menu_levels(self):
        model = self.menu_for(exporto.adsk.fusion.Component())
        self.assertEqual(list(model), ["STEP File…", "STL", "3MF", "Other", "Clear cache"])
        self.assertIn("STL File…", model["STL"].items)
        self.assertIn("3MF File…", model["3MF"].items)
        for fmt in exporto.OTHER_FORMATS:
            with self.subTest(fmt=fmt):
                submenu = exporto.FORMAT_NAMES.get(fmt, fmt.upper())
                self.assertIn(f"{fmt.upper()} File…", model["Other"].items[submenu].items)
        self.assertIn("Export entire design in Fusion…", model["Other"].items)
        sketch = self.menu_for(exporto.adsk.fusion.Sketch())
        self.assertEqual(list(sketch), ["DXF File…", "SVG", "Clear cache"])
        self.assertIn("SVG File…", sketch["SVG"].items)

    def test_iges_uses_document_for_instance_and_body(self):
        for entity_type in (exporto.adsk.fusion.Occurrence, exporto.adsk.fusion.BRepBody):
            self.assertTrue(exporto.needs_export_document((entity_type(),), "iges"))
        self.assertFalse(exporto.needs_export_document((exporto.adsk.fusion.Component(),), "iges"))

    def test_face_selection_uses_model_formats_and_a_temporary_document(self):
        face = exporto.adsk.fusion.BRepFace()
        model = self.menu_for(face)
        self.assertIn("STEP File…", model)
        self.assertIn("3MF File…", model["3MF"].items)
        for fmt in (*exporto.COMPONENT_EXPORTERS, *exporto.MESH_EXPORTERS):
            with self.subTest(fmt=fmt):
                self.assertTrue(exporto.needs_export_document((face,), fmt))

    def test_mixed_selection_keeps_faces_only_when_their_bodies_are_absent(self):
        body = exporto.adsk.fusion.BRepBody()
        covered_face = exporto.adsk.fusion.BRepFace()
        covered_face.body = body
        standalone_face = exporto.adsk.fusion.BRepFace()
        standalone_face.body = exporto.adsk.fusion.BRepBody()
        self.assertEqual(
            exporto.selection_geometry((covered_face, standalone_face, body, standalone_face)),
            [body, standalone_face],
        )

    def test_iges_uses_requested_extension_when_translator_changes_it(self):
        def write_preferred_extension(_options):
            path.with_suffix(".iges").write_bytes(b"export")
            return True

        with TemporaryDirectory() as directory:
            path = Path(directory) / "model.igs"
            manager = Mock()
            manager.execute.side_effect = write_preferred_extension
            exporto.export_model(SimpleNamespace(exportManager=manager), object(), "iges", path)
            self.assertEqual(path.read_bytes(), b"export")
            self.assertFalse(path.with_suffix(".iges").exists())

    def test_save_cancel_does_not_write(self):
        dialog = self.addin.ui.createFileDialog.return_value
        dialog.showSave.return_value = 0
        with patch.object(exporto, "export_selection") as export:
            self.addin.save_file("dxf", self.document, self.design, (), "Sketch")
        export.assert_not_called()
        self.assertEqual(dialog.initialFilename, "Sketch.dxf")

    def test_obj_save_keeps_material_file(self):
        dialog = self.addin.ui.createFileDialog.return_value
        dialog.showSave.return_value = exporto.adsk.core.DialogResults.DialogOK

        def write_obj(_design, _entities, _fmt, path):
            path.write_text("mtllib model.mtl\n", encoding="utf-8")
            path.with_suffix(".mtl").write_text("newmtl material\n", encoding="utf-8")

        with TemporaryDirectory() as directory:
            path = Path(directory) / "model.obj"
            dialog.filename = str(path)
            with patch.object(exporto, "export_selection", side_effect=write_obj):
                self.addin.save_file("obj", self.document, self.design, (), "model")
            self.assertEqual(path.read_text(encoding="utf-8"), "mtllib model.mtl\n")
            self.assertTrue(path.with_suffix(".mtl").is_file())


if __name__ == "__main__":
    unittest.main()
