# DF-ISE Reader Plugin for ParaView (5.13.3)

A Python plugin for ParaView 5.13.3 to read DF-ISE mesh files (`.grd`) from Synopsys Sentaurus TCAD.

## Overview

This plugin enables direct visualization of DF-ISE mesh files within ParaView, providing:

- **Direct File Reading**: Load `.grd` files directly through ParaView's file browser
- **3D Mesh Visualization**: Display tetrahedral volume meshes and surface boundaries
- **Material Region Selection**: Filter and color-code different material regions
- **Quality Control**: Built-in mesh validation and statistics
- **High Performance**: Optimized parsing for large TCAD mesh files

## Features

### Core Functionality
- Parse DF-ISE hierarchical mesh structure (elements → faces → edges → vertices)
- Convert to VTK unstructured grid format for ParaView visualization
- Support for multiple mesh representation modes
- Material region-based filtering and coloring

### User Interface
- **File Selection**: Standard ParaView file browser with `.grd` extension support
- **Material Selection**: Interactive checkbox list for material regions
- **Mesh Options**: Choose between volume, surface, or combined visualization
- **Quality Indicators**: Mesh statistics and validation feedback

### Performance Features
- Efficient memory usage with streaming capabilities
- Progressive loading for large files
- Smart caching to avoid re-parsing
- Optimized VTK data structure conversion

## Install and load (Python plugin, no XML)

ParaView 5.13.3 supports Python-decorator-based plugins. You only need the Python files; do not use the XML.

Files required (keep together in one folder):
- `DFISEReader.py`  — plugin entry point (registers the reader via decorators)
- `dfise_parser.py` — DF-ISE parser used by the reader

Load in ParaView 5.13.3:
1. Open ParaView 5.13.3 (GUI)
2. Tools → Manage Plugins → Load New → select `DFISEReader.py`
3. File → Open → choose your `.grd` file (the DF-ISE Reader should be auto-selected)
4. Click Apply

Notes
- The old XML-based proxy (`DFISEReader.xml`) is not needed and should not be loaded.
- The plugin uses the modern VTK 9.x Python API (vtkmodules.* and smproxy/smproperty decorators) that is available in ParaView 5.13.x.

## Usage

### Basic Usage

#### Using Direct Python Method:
1. **Follow installation steps above**
2. **Configure in script**: Change `FILE_PATH` variable
3. **Modify options** (optional):
   ```python
   INCLUDE_MATERIALS = ["Silicon", "Oxide"]  # Specific materials
   MESH_TYPE = "volume"                     # "volume", "surface", or "both"
   INCLUDE_BOUNDARIES = False               # Boundary surfaces
   ```
4. **Click Apply** in Properties panel

#### Using the Python plugin (recommended)
1. File → Open → select your `.grd`
2. In Properties:
   - MaterialRegions: enable/disable regions to display
   - MeshType: Volume, Surface, or Volume + Boundary
3. Click Apply
4. Use ParaView filters as usual (Clip, Slice, Threshold, etc.) and color by `Material` or `MaterialID` cell arrays

### Advanced Features

This reader focuses on simple, reliable visualization of DF‑ISE meshes in ParaView. The features below reflect the current implementation.

1) Region selection (Properties panel)
- Use MaterialRegions to enable/disable regions before loading the mesh.
- Selection affects only volume tetrahedra; boundaries are derived from the full connectivity and then labeled per region.

2) Mesh representation (MeshType)
- Volume Mesh: tetrahedra only.
- Surface Mesh: boundary triangles only (exterior + interfaces).
- Volume + Boundary: both tetrahedra and boundary triangles in one dataset.

3) Unified boundary coloring
- Color any boundary (exterior or shared interface) by BoundaryRegion or BoundaryRegionID for a single consistent color per region.
- Internally, interface triangles are duplicated so each adjacent region gets its own copy; this enables uniform coloring by region across all boundaries.

4) Interface-specific coloring (optional)
- If you need to distinguish the two sides of an interface, color by:
   - InterfacePair / InterfacePairID (e.g., "Oxide|Silicon"), or
   - MaterialA/MaterialB and MaterialAID/MaterialBID.

5) Quick filtering tips
- Use Threshold on CellKindID: 0=Volume, 1=Surface-Exterior, 2=Surface-Interface.
- To avoid z‑fighting from duplicated interface triangles, Threshold BoundaryRegion to one region at a time or use a Clip/Slice filter for visual separation.

6) Performance notes
- For very large meshes, start with Surface Mesh for fast inspection, then switch to Volume or Volume + Boundary as needed.
- Disable MaterialRegions you don’t need to reduce memory/visual load.

### Cell arrays produced by the reader

The reader adds useful per-cell arrays you can use for coloring and filtering:

- Material (string) and MaterialID (int):
   - For volume cells, the region/material name and a stable ID
   - For surface cells, set to "Unknown" and -1
- CellKind (string) and CellKindID (int):
   - Volume = 0, Surface-Exterior = 1, Surface-Interface = 2
- For exterior surface triangles (CellKind == Surface-Exterior):
   - SurfaceRegion (string): region name on the exterior side
   - SurfaceMaterial (string) and SurfaceMaterialID (int): exterior material and its ID
- For interface surface triangles (CellKind == Surface-Interface):
   - RegionA, RegionB (string): regions adjacent to the face
   - MaterialA, MaterialB (string): materials for each side
   - MaterialAID, MaterialBID (int): stable IDs for each side
   - InterfacePair (string) and InterfacePairID (int): canonical pair label (e.g., "Oxide|Silicon") and a stable ID

Simplified, uniform boundary coloring:
- BoundaryRegion (string) and BoundaryRegionID (int): a single region label for both exterior and interface boundaries.
   - Exterior boundary triangles are labeled with their sole region.
   - Interface triangles are duplicated internally so each region gets its own copy labeled with that region.
   - Use this to color all boundaries of a given region the same, regardless of whether they are exterior or shared.

Tips:
- Use the Threshold filter on CellKindID to isolate Volume (0), Exterior (1), or Interface (2) cells.
- Easiest: color any boundary by BoundaryRegion or BoundaryRegionID. This gives each region a uniform color across exterior and shared interfaces.
- Alternatively: color exterior surfaces by SurfaceMaterial, and interfaces by InterfacePair or MaterialA/MaterialB.

Note: Because interface triangles are duplicated (one per adjacent region) for BoundaryRegion labeling, you may see z-fighting when viewing “all boundary regions” together. To avoid artifacts, Threshold to a specific BoundaryRegion or clip/slice to separate coincident triangles visually.

#### Quality Control
- Automatic mesh validation and topology checking
- Statistics display (vertex count, element count, Euler characteristic)
- Error detection for corrupted files

### Quick Setup Guide

**For Immediate Testing (Recommended)**:
1. Open ParaView
2. Use Direct Python method (see above)
3. Visualize your DF-ISE file in 5 minutes

**For Regular Use**:
1. Build the full plugin or use Python plugin
2. Get professional integration with file dialogs
3. Save as reusable tool for future projects

**For Development**:
1. Modify the Python scripts directly
2. Test changes immediately with Direct Python method
3. Package as plugin when ready for distribution

## File Format Support

### DF-ISE Format
The plugin supports standard DF-ISE grid files with:

- **Vertices**: 3D coordinate data
- **Edges**: Vertex connectivity (signed indices)
- **Faces**: Triangular surface elements (type 3)
- **Elements**: Tetrahedral volume elements (type 5)
- **Regions**: Material region definitions
- **Locations**: Boundary classification (interior, interface, exterior)

### Supported Features
- Hierarchical mesh structure reconstruction
- Material region parsing and mapping
- Coordinate system transformations
- Boundary surface identification
- Error recovery for corrupted files

## Environment options (Linux)

- Kitware binary: Download ParaView 5.13.3 for Linux. It bundles compatible Python and VTK 9.3. Launch `paraview` and load `DFISEReader.py`.
- Conda (GUI build):
   ```bash
   conda create -n pv513 -c conda-forge python=3.12 paraview=5.13.3=*_qt
   conda activate pv513
   paraview --version
   ```
- Conda (EGL/headless build):
   ```bash
   conda create -n pv513-egl -c conda-forge python=3.12 paraview=5.13.3=*_egl
   conda activate pv513-egl
   paraview --version
   ```

## Troubleshooting (5.13.3)

### Common Issues

**Plugin not appearing in Manage Plugins:**
1. Ensure you’re running ParaView 5.13.x (Help → About)
2. Use Tools → Manage Plugins → Load New → select `DFISEReader.py` (not the XML)
3. Keep `DFISEReader.py` and `dfise_parser.py` in the same folder
4. Restart ParaView if needed

**File reading errors:**
1. Check file permissions and path accessibility
2. Verify the file is a valid DF-ISE format
3. Look for corruption or incomplete files
4. Check for unusual coordinate values or scaling

**ImportError for numpy/vtk/paraview in your editor:**
- That’s normal when viewing the file outside ParaView. Inside ParaView 5.13.3 (or its pvpython), these modules are available.

**openPMD warning in the output:**
- Unrelated to DF-ISE; you can ignore it. To silence, install `openPMD-api` into the environment running ParaView.

## External debug workflow (outside ParaView)

Validate boundaries/interfaces and export surfaces without using the plugin:

1) Run with pvpython
```bash
pvpython /home/dabajabaza/Documents/dfise2nas/DFISEReaderPlugin/dfise_debug_surfaces.py \
   /path/to/input.grd \
   --out-prefix /path/to/out/mesh
```

2) Outputs
- /path/to/out/mesh_exterior.vtp   (Locations == 'e')
- /path/to/out/mesh_interface.vtp  (Locations == 'f')

3) Use
- Open the VTP files in ParaView to inspect surfaces.
- If interface VTP has triangles, the .grd has shared region boundaries; add surface generation to the plugin if desired.

**Performance issues:**
1. For large files (>100MB), consider using the mesh quality options
2. Disable material regions you don't need to visualize
3. Use surface-only representation for initial inspection
4. Check available system memory

**Material region problems:**
1. Ensure the file contains valid region definitions
2. Check for missing or incomplete material information
3. Verify region-element mapping consistency

### Debug Information

To enable debug output:
1. Open ParaView's Python Shell (Tools → Python Shell)
2. Access the reader's internal methods for diagnostics
3. Check the output console for parsing messages

## Development

### Plugin Structure (Python-only)
```
DFISEReaderPlugin/
├── DFISEReader.py      # Main reader implementation (load this)
├── dfise_parser.py     # Parser used by the reader
└── README.md           # This documentation
```

### Extending the Plugin

To add new features:

1. Modify `DFISEReader.py`: Add new properties/methods with smproperty/smdomain decorators
2. Extend the parser in `dfise_parser.py` as needed
3. Reload the Python plugin in ParaView to test

### Testing

Test the plugin with sample DF-ISE files:
1. Create a small test mesh with known properties
2. Verify material region detection
3. Check boundary surface extraction
4. Test with various file sizes and complexities

## License

 

## Support

For issues and questions:
1. Check the troubleshooting section above
2. Verify ParaView and plugin compatibility
3. Consult the DF-ISE file format documentation
4. Test with the provided sample files

## Changelog

### Version 1.0.0
- Initial release
- Basic DF-ISE file reading capability
- Material region selection
- Volume mesh visualization
- Quality control and validation
- ParaView 5.13.3 compatibility (Python plugin)
- Direct Python support (no XML/build required)

## File Format Support

### DF-ISE Format
The plugin supports standard DF-ISE grid files with:

- **Vertices**: 3D coordinate data
- **Edges**: Vertex connectivity (signed indices)
- **Faces**: Triangular surface elements (type 3)
- **Elements**: Tetrahedral volume elements (type 5)
- **Regions**: Material region definitions
- **Locations**: Boundary classification (interior, interface, exterior)

### Supported Features
- Hierarchical mesh structure reconstruction
- Material region parsing and mapping
- Coordinate system transformations
- Boundary surface identification
- Error recovery for corrupted files

## Troubleshooting

### Common Issues

**Plugin not appearing in ParaView:**
1. Verify ParaView version compatibility (5.12+ with Python 3)
2. Check that the plugin is installed correctly
3. Ensure all dependencies (NumPy, VTK) are available
4. Try restarting ParaView after installation

**File reading errors:**
1. Check file permissions and path accessibility
2. Verify the file is a valid DF-ISE format
3. Look for corruption or incomplete files
4. Check for unusual coordinate values or scaling

**Performance issues:**
1. For large files (>100MB), consider using the mesh quality options
2. Disable material regions you don't need to visualize
3. Use surface-only representation for initial inspection
4. Check available system memory

**Material region problems:**
1. Ensure the file contains valid region definitions
2. Check for missing or incomplete material information
3. Verify region-element mapping consistency

### Debug Information

To enable debug output:
1. Open ParaView's Python Shell (Tools → Python Shell)
2. Access the reader's internal methods for diagnostics
3. Check the output console for parsing messages

## Development

### Plugin Structure
```
DFISEReaderPlugin/
├── DFISEReader.py              # Main reader implementation
├── DFISEReader.xml             # ParaView plugin configuration
├── dfise_parser_module.py      # Adapted parser for ParaView
├── CMakeLists.txt              # Build configuration
└── README.md                   # This documentation
```

### Extending the Plugin

To add new features:

1. **Modify `DFISEReader.py`**: Add new properties and methods
2. **Update `DFISEReader.xml`**: Configure UI elements and properties
3. **Extend the parser**: Add new parsing capabilities in `dfise_parser_module.py`
4. **Rebuild**: Run CMake and make to install updated plugin

### Testing

Test the plugin with sample DF-ISE files:
1. Create a small test mesh with known properties
2. Verify material region detection
3. Check boundary surface extraction
4. Test with various file sizes and complexities

## Support

For issues and questions:
1. Check the troubleshooting section above
2. Verify ParaView and plugin compatibility
3. Consult the DF-ISE file format documentation
4. Test with the provided sample files

## Changelog

### Version 1.0.0
- Initial release
- Basic DF-ISE file reading capability
- Material region selection
- Volume mesh visualization
- Quality control and validation
- ParaView 5.13.3 compatibility
