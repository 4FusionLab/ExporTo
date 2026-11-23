# Release checks

The archive builder stamps the release version into the packaged manifest.

## Local verification

Run from the repository root.

    python3 scripts/check_project.py
    python3 -m unittest discover -s tests -v
    python3 scripts/build.py --version 1.0.0

Open the ZIP to confirm that ExporTo.py, ExporTo.manifest, platform_apps.py, and the PNG resources are under one ExporTo folder. CI also verifies the ZIP checksum and builds the package on Linux, macOS, and Windows.

## Version 1.0.0

- Loaded the extracted release ZIP in Fusion 2706.1.17 on macOS 27.0.1.
- The exported STEP opened in OrcaSlicer 2.5.2 as a 20 × 20 × 20 mm cube.
- Fusion exported individual solid bodies to STL in millimeters. The Clear cache command removed isolated test exports.
- The release archive includes the PolyForm Noncommercial License 1.0.0.

Publish through the [shared release workflow](../.github/RELEASE_POLICY.md). GitHub Release assets include the ZIP and its SHA-256 checksum.
