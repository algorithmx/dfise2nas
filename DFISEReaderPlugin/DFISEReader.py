"""
DF-ISE Reader Plugin for ParaView
================================

A ParaView file reader plugin for DF-ISE mesh files (.grd extension) used by
Synopsys Sentaurus TCAD.

This plugin provides:
- Direct reading of DF-ISE (.grd) files in ParaView
- 3D tetrahedral mesh visualization
- Material region selection and coloring
- Boundary surface extraction
- Quality control and mesh statistics

Author: Yunlong Lian
Version: 1.0
"""

import os
import sys
import vtk  # required for vtk.vtkPoints, vtk constants, arrays
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Union

# Import the main parser class
# Ensure we can import the local parser when loaded as a Python plugin
_here = os.path.abspath(os.path.dirname(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from dfise_parser import DFISEParser, RegionInfo

from paraview.util.vtkAlgorithm import *
from vtkmodules.vtkCommonDataModel import vtkUnstructuredGrid, vtkCellArray, vtkTetra
from vtkmodules.vtkCommonDataModel import vtkTriangle
from vtkmodules.vtkCommonCore import vtkDataArraySelection
from vtkmodules.vtkCommonExecutionModel import vtkStreamingDemandDrivenPipeline
from vtkmodules.numpy_interface import dataset_adapter as dsa
from vtkmodules.vtkCommonCore import VTK_DOUBLE, VTK_INT, VTK_STRING
from vtkmodules.util.numpy_support import numpy_to_vtk


class DFISEParaViewModule:
    """
    ParaView-friendly wrapper for DF-ISE parsing functionality.

    This class adapts the original DFISEParser for use within ParaView,
    removing console output and providing VTK-compatible data structures.
    """

    def __init__(self, filepath: str):
        """Initialize parser with file path"""
        self.filepath = Path(filepath)
        self.parser = DFISEParser(filepath)

        # Cache parsed data
        self._vertices = None
        self._elements = None
        self._region_elements = None
        self._materials = None
        self._parsed = False

    def _ensure_file_accessible(self) -> bool:
        """Check if file is accessible without printing errors"""
        try:
            if not self.filepath.exists():
                raise FileNotFoundError(f"File does not exist: {self.filepath}")
            if not self.filepath.is_file():
                raise ValueError(f"Path is not a file: {self.filepath}")
            if self.filepath.stat().st_size == 0:
                raise ValueError(f"File is empty: {self.filepath}")

            # Try to read first few bytes
            with open(self.filepath, 'r', encoding='utf-8') as f:
                f.read(100)
            return True
        except Exception:
            return False

    def parse_all_data(self) -> bool:
        """
        Parse all required data for ParaView visualization.
        Returns True if successful, False otherwise.
        """
        if self._parsed:
            return True

        try:
            if not self._ensure_file_accessible():
                return False

            # Parse metadata
            self.parser.parse_info_block()

            # Parse regions
            self.parser.parse_regions()

            # Parse geometry data
            self._vertices = np.array(self.parser.parse_vertices(), dtype=np.float64)

            # Parse connectivity
            edges = self.parser.parse_edges_full()
            faces_as_edges = self.parser.parse_faces_full()
            elements_as_faces = self.parser.parse_elements_full()

            # Reconstruct face connectivity
            faces_as_vertices = []
            for face_edges in faces_as_edges:
                face_verts = self.parser._reconstruct_face_vertices(face_edges, edges)
                faces_as_vertices.append(face_verts)

            # Reconstruct element connectivity
            self._elements = []
            for elem_faces in elements_as_faces:
                elem_verts = self.parser._reconstruct_element_vertices(elem_faces, faces_as_vertices)
                self._elements.append(elem_verts)
            self._elements = np.array(self._elements, dtype=np.int32)

            # Parse region-element mapping
            self._region_elements = self.parser.parse_region_elements()

            # Build material mapping
            self._materials = {}
            for region_name, elem_indices in self._region_elements.items():
                for elem_idx in elem_indices:
                    self._materials[elem_idx] = region_name

            self._parsed = True
            return True

        except Exception as e:
            # In ParaView, we should not print to console but could log
            # For now, return False to indicate failure
            return False

    def get_vertices(self) -> Optional[np.ndarray]:
        """Get vertex coordinates as numpy array"""
        if not self._parsed:
            self.parse_all_data()
        return self._vertices

    def get_elements(self) -> Optional[np.ndarray]:
        """Get element connectivity as numpy array"""
        if not self._parsed:
            self.parse_all_data()
        return self._elements

    def get_materials(self) -> Dict[int, str]:
        """Get material mapping for each element"""
        if not self._parsed:
            self.parse_all_data()
        return self._materials.copy() if self._materials else {}

    def get_region_info(self) -> Dict[str, RegionInfo]:
        """Get region information"""
        if not self._parsed:
            self.parse_all_data()
        return self.parser.regions.copy() if self.parser.regions else {}

    def get_material_names(self) -> List[str]:
        """Get list of unique material names"""
        if not self._parsed:
            self.parse_all_data()
        return list(set(self._materials.values())) if self._materials else []

    def get_mesh_info(self) -> Dict[str, Union[int, str, List[str]]]:
        """Get basic mesh information for display"""
        if not self._parsed:
            if not self.parse_all_data():
                return {
                    'status': 'error',
                    'message': 'Failed to parse file'
                }

        return {
            'status': 'success',
            'num_vertices': len(self._vertices) if self._vertices is not None else 0,
            'num_elements': len(self._elements) if self._elements is not None else 0,
            'num_regions': len(self.parser.regions) if self.parser.regions else 0,
            'materials': self.get_material_names(),
            'dimension': self.parser.info.dimension if self.parser.info else 3,
            'file_size_mb': str(self.filepath.stat().st_size / (1024**2) if self.filepath.exists() else 0)
        }

    def validate_mesh(self) -> Dict[str, bool]:
        """Validate mesh consistency and return validation results"""
        if not self._parsed:
            if not self.parse_all_data():
                return {}

        try:
            # Basic validation checks
            results = {}

            # Check data consistency
            if self._vertices is not None and self._elements is not None:
                max_vertex_idx = np.max(self._elements) if len(self._elements) > 0 else -1
                results['valid_vertex_indices'] = max_vertex_idx < len(self._vertices)

            # Check region consistency
            if self.parser.info and self._region_elements:
                total_region_elements = sum(len(elem_indices) for elem_indices in self._region_elements.values())
                results['region_elements_match'] = total_region_elements == self.parser.info.nb_elements

            # Check material consistency
            results['all_elements_have_materials'] = len(self._materials) == len(self._elements) if self._elements is not None else False

            return results

        except Exception:
            return {}

    def get_boundary_faces(self) -> List[Tuple[int, int, int]]:
        """
        Extract boundary faces (exterior surfaces) from the mesh.
        Returns list of vertex triplets forming boundary triangles.
        """
        if not self._parsed:
            self.parse_all_data()

        try:
            # Parse locations to identify boundary faces
            locations = self.parser.parse_locations_full()

            # Parse faces and convert to vertices
            edges = self.parser.parse_edges_full()
            faces_as_edges = self.parser.parse_faces_full()

            boundary_faces = []
            for i, (loc, face_edges) in enumerate(zip(locations, faces_as_edges)):
                if loc == 'e':  # Exterior boundary
                    face_verts = self.parser._reconstruct_face_vertices(face_edges, edges)
                    boundary_faces.append(face_verts)

            return boundary_faces

        except Exception:
            return []


def create_paraview_parser(filepath: str) -> DFISEParaViewModule:
    """
    Factory function to create a ParaView-compatible DF-ISE parser.

    Args:
        filepath: Path to the DF-ISE (.grd) file

    Returns:
        DFISEParaViewModule instance ready for use in ParaView
    """
    return DFISEParaViewModule(filepath)


def createModifiedCallback(anobject):
    """Create a modified callback for property changes"""
    import weakref
    weakref_obj = weakref.ref(anobject)
    anobject = None
    def _markmodified(*args, **kwargs):
        o = weakref_obj()
        if o is not None:
            o.Modified()
    return _markmodified


@smproxy.reader(name="DFISEReader",
                label="DF-ISE Grid File Reader",
                extensions="grd",
                file_description="DF-ISE Grid Files")
class DFISEReader(VTKPythonAlgorithmBase):
    """
    ParaView reader for DF-ISE mesh files (.grd)

    This reader parses DF-ISE files and creates VTK unstructured grids
    suitable for visualization in ParaView. It supports material region
    selection, boundary extraction, and mesh quality control.
    """

    def __init__(self):
        VTKPythonAlgorithmBase.__init__(self,
            nInputPorts=0,
            nOutputPorts=1,
            outputType='vtkUnstructuredGrid')

        # File properties
        self._filename = None

        # Data storage
        self._parser = None
        self._parsed_successfully = False
        self._vertices = None
        self._elements = None
        self._regions = None
        self._material_arrays = None

        # Selection properties
        from vtkmodules.vtkCommonCore import vtkDataArraySelection
        self._material_selection = vtkDataArraySelection()
        self._material_selection.AddObserver("ModifiedEvent", createModifiedCallback(self))

        # Mesh representation
        self._mesh_type = 0  # 0=Volume, 1=Surface, 2=Both
        self._include_boundaries = False

        # Cached data for performance
        self._cached_output = None
        self._cache_mtime = 0

    def _ensure_parser(self):
        """Initialize parser if not already done"""
        if self._parser is None or not self._parsed_successfully:
            if not self._filename or not os.path.exists(self._filename):
                raise RuntimeError(f"File not found or not accessible: {self._filename}")

            self._parser = create_paraview_parser(self._filename)
            self._parsed_successfully = self._parser.parse_all_data()
            if not self._parsed_successfully:
                raise RuntimeError(f"Failed to parse DF-ISE file: {self._filename}")
            self._clear_cache()

    def _clear_cache(self):
        """Clear cached data"""
        self._vertices = None
        self._elements = None
        self._regions = None
        self._material_arrays = None
        self._cached_output = None
        self._cache_mtime = 0

    def _parse_geometry(self):
        """Parse geometry data from file"""
        if self._vertices is not None and self._elements is not None:
            return  # Already parsed

        self._ensure_parser()

        # Get parsed data from the module
        self._vertices = self._parser.get_vertices()
        self._elements = self._parser.get_elements()
        self._material_arrays = self._parser.get_materials()
        self._regions = self._parser.get_region_info()

    def _create_vtk_output(self):
        """Create VTK unstructured grid from parsed data"""
        if self._cached_output is not None:
            return self._cached_output

        self._parse_geometry()

        # Create output grid
        output = vtkUnstructuredGrid()

        # Add points
        points = vtk.vtkPoints()
        points.SetData(numpy_to_vtk(self._vertices.astype(np.float64), deep=1))
        output.SetPoints(points)

        # Prepare lists for cells and labels
        cell_kinds: List[str] = []  # 'Volume' | 'Surface-Exterior' | 'Surface-Interface'
        cell_materials: List[str] = []  # material name or 'Unknown'
        cell_material_ids: List[int] = []  # material id or -1 for surfaces
        # Surface annotations (per-cell, default for volume)
        surf_region: List[str] = []           # for exterior faces
        surf_material: List[str] = []
        surf_material_id: List[int] = []
        iface_region_a: List[str] = []        # for interface faces
        iface_region_b: List[str] = []
        iface_material_a: List[str] = []
        iface_material_b: List[str] = []
        iface_material_a_id: List[int] = []
        iface_material_b_id: List[int] = []
        iface_pair: List[str] = []
        iface_pair_id: List[int] = []
        # Unified boundary label for easy region coloring (exterior+interface)
        boundary_region: List[str] = []

        # 1) Volume tetrahedra (MeshType 0 or 2)
        add_volume = (self._mesh_type in [0, 2])
        selected_elements: List[Tuple[int, int, int, int]] = []
        selected_materials: List[str] = []

        if add_volume:
            # Filter elements based on material selection
            enabled_materials = set()
            for i in range(self._material_selection.GetNumberOfArrays()):
                if self._material_selection.ArrayIsEnabled(self._material_selection.GetArrayName(i)):
                    enabled_materials.add(self._material_selection.GetArrayName(i))

            for elem_idx, elem_verts in enumerate(self._elements):
                material_name = self._material_arrays.get(elem_idx, "Unknown")
                if enabled_materials and material_name not in enabled_materials:
                    continue
                selected_elements.append(tuple(int(v) for v in elem_verts))
                selected_materials.append(material_name)

            # Insert tetrahedra directly
            for idx, elem_verts in enumerate(selected_elements):
                tet = vtkTetra()
                for j, v_idx in enumerate(elem_verts):
                    tet.GetPointIds().SetId(j, int(v_idx))
                output.InsertNextCell(tet.GetCellType(), tet.GetPointIds())
                cell_kinds.append('Volume')
                cell_materials.append(selected_materials[idx])
                # material IDs mapping will be computed later
                cell_material_ids.append(0)  # placeholder
                # defaults for surface-specific arrays on volume cells
                surf_region.append("")
                surf_material.append("")
                surf_material_id.append(-1)
                iface_region_a.append("")
                iface_region_b.append("")
                iface_material_a.append("")
                iface_material_b.append("")
                iface_material_a_id.append(-1)
                iface_material_b_id.append(-1)
                iface_pair.append("")
                iface_pair_id.append(-1)
                boundary_region.append("")

        # 2) Surfaces (MeshType 1 or 2)
        add_surfaces = (self._mesh_type in [1, 2])
        if add_surfaces:
            try:
                # Reconstruct faces -> vertices and classify by Locations
                edges = self._parser.parser.parse_edges_full()
                faces_as_edges = self._parser.parser.parse_faces_full()
                locations = self._parser.parser.parse_locations_full()
                elements_as_faces = self._parser.parser.parse_elements_full()

                faces_as_vertices: List[Tuple[int, int, int]] = []
                for fe in faces_as_edges:
                    fv = self._parser.parser._reconstruct_face_vertices(fe, edges)
                    faces_as_vertices.append(fv)

                # Build face -> adjacent element indices map
                face_to_elems: Dict[int, List[int]] = {}
                for eidx, face_quad in enumerate(elements_as_faces):
                    for s in face_quad:
                        fidx = -s - 1 if s < 0 else s
                        face_to_elems.setdefault(fidx, []).append(eidx)

                # Helper maps for region/material
                elem_to_region = self._material_arrays  # elem_idx -> region name
                region_info = self._regions             # region name -> RegionInfo (has .material)
                # For material ID mapping across all cells
                material_names_seen = set([m for m in cell_materials if m and m != 'Unknown'])

                for idx, fv in enumerate(faces_as_vertices):
                    loc = locations[idx] if idx < len(locations) else 'i'
                    if loc not in ('e', 'f'):
                        continue
                    if loc == 'e':
                        tri = vtk.vtkTriangle()
                        tri.GetPointIds().SetId(0, int(fv[0]))
                        tri.GetPointIds().SetId(1, int(fv[1]))
                        tri.GetPointIds().SetId(2, int(fv[2]))
                        output.InsertNextCell(tri.GetCellType(), tri.GetPointIds())
                        cell_kinds.append('Surface-Exterior')
                        # annotate exterior with single-sided region/material
                        adj = face_to_elems.get(idx, [])
                        rname = elem_to_region.get(adj[0], "") if adj else ""
                        rin = region_info.get(rname)
                        mname = rin.material if rin else ""
                        surf_region.append(rname)
                        surf_material.append(mname)
                        surf_material_id.append(-1)  # fill later
                        material_names_seen.add(mname) if mname else None
                        # defaults for interface fields
                        iface_region_a.append("")
                        iface_region_b.append("")
                        iface_material_a.append("")
                        iface_material_b.append("")
                        iface_material_a_id.append(-1)
                        iface_material_b_id.append(-1)
                        iface_pair.append("")
                        iface_pair_id.append(-1)
                        # unified boundary label
                        boundary_region.append(rname)
                        # material fields for surfaces remain Unknown in per-cell Material array
                        cell_materials.append('Unknown')
                        cell_material_ids.append(-1)
                    else:
                        # annotate both sides; duplicate the triangle so each region gets its own label
                        adj = face_to_elems.get(idx, [])
                        rA = elem_to_region.get(adj[0], "") if len(adj) >= 1 else ""
                        rB = elem_to_region.get(adj[1], "") if len(adj) >= 2 else ""
                        rinA = region_info.get(rA)
                        rinB = region_info.get(rB)
                        mA = rinA.material if rinA else ""
                        mB = rinB.material if rinB else ""
                        # First copy for region A
                        triA = vtk.vtkTriangle()
                        triA.GetPointIds().SetId(0, int(fv[0]))
                        triA.GetPointIds().SetId(1, int(fv[1]))
                        triA.GetPointIds().SetId(2, int(fv[2]))
                        output.InsertNextCell(triA.GetCellType(), triA.GetPointIds())
                        cell_kinds.append('Surface-Interface')
                        iface_region_a.append(rA)
                        iface_region_b.append(rB)
                        iface_material_a.append(mA)
                        iface_material_b.append(mB)
                        iface_material_a_id.append(-1)  # fill later
                        iface_material_b_id.append(-1)
                        pair_label = "|".join(sorted([x for x in [mA, mB] if x]))
                        iface_pair.append(pair_label)
                        iface_pair_id.append(-1)
                        material_names_seen.update([mA, mB])
                        surf_region.append("")
                        surf_material.append("")
                        surf_material_id.append(-1)
                        boundary_region.append(rA)
                        cell_materials.append('Unknown')
                        cell_material_ids.append(-1)

                        # Second copy for region B (if present)
                        triB = vtk.vtkTriangle()
                        triB.GetPointIds().SetId(0, int(fv[0]))
                        triB.GetPointIds().SetId(1, int(fv[1]))
                        triB.GetPointIds().SetId(2, int(fv[2]))
                        output.InsertNextCell(triB.GetCellType(), triB.GetPointIds())
                        cell_kinds.append('Surface-Interface')
                        iface_region_a.append(rA)
                        iface_region_b.append(rB)
                        iface_material_a.append(mA)
                        iface_material_b.append(mB)
                        iface_material_a_id.append(-1)
                        iface_material_b_id.append(-1)
                        iface_pair.append(pair_label)
                        iface_pair_id.append(-1)
                        surf_region.append("")
                        surf_material.append("")
                        surf_material_id.append(-1)
                        boundary_region.append(rB)
                        cell_materials.append('Unknown')
                        cell_material_ids.append(-1)
            except Exception:
                # Fail silently to keep reader robust
                pass

        # Build Material arrays spanning all cells
        if cell_materials:
            # Map materials (excluding 'Unknown') to stable IDs starting at 0
            unique = {}
            next_id = 0
            for name in cell_materials:
                if name == 'Unknown':
                    continue
                if name not in unique:
                    unique[name] = next_id
                    next_id += 1

            mat_arr = vtk.vtkStringArray()
            mat_arr.SetName("Material")
            mat_arr.SetNumberOfComponents(1)
            mat_arr.SetNumberOfTuples(len(cell_materials))

            mat_id_arr = vtk.vtkIntArray()
            mat_id_arr.SetName("MaterialID")
            mat_id_arr.SetNumberOfComponents(1)
            mat_id_arr.SetNumberOfTuples(len(cell_materials))

            for i, name in enumerate(cell_materials):
                mat_arr.SetValue(i, name)
                if name == 'Unknown':
                    mat_id_arr.SetValue(i, -1)
                else:
                    mat_id_arr.SetValue(i, unique.get(name, -1))

            output.GetCellData().AddArray(mat_arr)
            output.GetCellData().AddArray(mat_id_arr)

        # Surface/exterior material IDs mapping (based on all seen names)
        # Build global material ID map for surface arrays
        all_surface_mats = set()
        all_surface_mats.update([m for m in surf_material if m])
        all_surface_mats.update([m for m in iface_material_a if m])
        all_surface_mats.update([m for m in iface_material_b if m])
        mat_map = {m: i for i, m in enumerate(sorted(all_surface_mats))}
        # Pair map for interfaces
        pair_set = set([p for p in iface_pair if p])
        pair_map = {p: i for i, p in enumerate(sorted(pair_set))}

        # Create and add arrays if any surfaces exist
        n_cells = len(cell_kinds)
        if n_cells > 0:
            def make_str_array(name, values):
                a = vtk.vtkStringArray()
                a.SetName(name)
                a.SetNumberOfComponents(1)
                a.SetNumberOfTuples(n_cells)
                for i, v in enumerate(values):
                    a.SetValue(i, v)
                output.GetCellData().AddArray(a)

            def make_int_array(name, values):
                a = vtk.vtkIntArray()
                a.SetName(name)
                a.SetNumberOfComponents(1)
                a.SetNumberOfTuples(n_cells)
                for i, v in enumerate(values):
                    a.SetValue(i, int(v))
                output.GetCellData().AddArray(a)

            # Fill numeric ids for surface materials/pairs
            surf_material_id = [mat_map.get(m, -1) if m else -1 for m in surf_material]
            iface_material_a_id = [mat_map.get(m, -1) if m else -1 for m in iface_material_a]
            iface_material_b_id = [mat_map.get(m, -1) if m else -1 for m in iface_material_b]
            iface_pair_id = [pair_map.get(p, -1) if p else -1 for p in iface_pair]

            # Add arrays
            make_str_array("SurfaceRegion", surf_region)
            make_str_array("SurfaceMaterial", surf_material)
            make_int_array("SurfaceMaterialID", surf_material_id)
            make_str_array("RegionA", iface_region_a)
            make_str_array("RegionB", iface_region_b)
            make_str_array("MaterialA", iface_material_a)
            make_str_array("MaterialB", iface_material_b)
            make_int_array("MaterialAID", iface_material_a_id)
            make_int_array("MaterialBID", iface_material_b_id)
            make_str_array("InterfacePair", iface_pair)
            make_int_array("InterfacePairID", iface_pair_id)

            # Boundary region arrays (uniform label for exterior+interface)
            make_str_array("BoundaryRegion", boundary_region)
            # Map region names to IDs
            unique_regions = sorted(set([r for r in boundary_region if r]))
            reg_id_map = {r: i for i, r in enumerate(unique_regions)}
            make_int_array("BoundaryRegionID", [reg_id_map.get(r, -1) if r else -1 for r in boundary_region])

        # Cell kind labeling to distinguish surfaces vs volume
        if cell_kinds:
            kind_arr = vtk.vtkStringArray()
            kind_arr.SetName("CellKind")
            kind_arr.SetNumberOfComponents(1)
            kind_arr.SetNumberOfTuples(len(cell_kinds))
            for i, k in enumerate(cell_kinds):
                kind_arr.SetValue(i, k)
            output.GetCellData().AddArray(kind_arr)

            # Also provide an integer variant for easy Threshold filtering
            kind_id = vtk.vtkIntArray()
            kind_id.SetName("CellKindID")
            kind_id.SetNumberOfComponents(1)
            kind_id.SetNumberOfTuples(len(cell_kinds))
            mapping = {'Volume': 0, 'Surface-Exterior': 1, 'Surface-Interface': 2}
            for i, k in enumerate(cell_kinds):
                kind_id.SetValue(i, mapping.get(k, -1))
            output.GetCellData().AddArray(kind_id)

        self._cached_output = output
        return output

    def _add_boundary_surfaces(self, output):
        """Add boundary surface triangles to the output"""
        try:
            boundary_faces = self._parser.get_boundary_faces()

            if boundary_faces:
                # For now, this is a placeholder for boundary surface functionality
                # Full implementation would require multi-block dataset output
                pass

        except Exception:
            pass  # Silent failure in ParaView environment

    def _get_available_materials(self):
        """Get list of available materials from the file"""
        try:
            self._ensure_parser()
            return self._parser.get_material_names()
        except Exception:
            return []

    # File selection property
    @smproperty.stringvector(name="FileName")
    @smdomain.filelist()
    @smhint.filechooser(extensions="grd", file_description="DF-ISE Grid Files")
    def SetFileName(self, filename):
        """Specify filename for the DF-ISE file to read."""
        if self._filename != filename:
            self._filename = filename
            self._clear_cache()
            self.Modified()

    # Material selection properties
    @smproperty.dataarrayselection(name="MaterialRegions")
    def GetMaterialRegionSelection(self):
        """Get the material region selection object"""
        # Update available materials
        materials = self._get_available_materials()
        for material in materials:
            if self._material_selection.GetArraySetting(material) == -1:
                self._material_selection.AddArray(material)
                # Enable all materials by default
                self._material_selection.EnableArray(material)

        return self._material_selection

    # Mesh type selection
    @smproperty.intvector(name="MeshType", default_values=["0"])
    @smdomain.xml("""
        <EnumerationDomain name="enum">
            <Entry value="0" text="Volume Mesh (Tetrahedra)"/>
            <Entry value="1" text="Surface Mesh (Boundaries)"/>
            <Entry value="2" text="Volume + Boundary"/>
        </EnumerationDomain>
    """)
    def SetMeshType(self, mesh_type):
        """Set the mesh representation type"""
        if self._mesh_type != mesh_type:
            self._mesh_type = mesh_type
            self._include_boundaries = (mesh_type in [1, 2])
            self._clear_cache()
            self.Modified()

    def RequestInformation(self, request, inInfoVec, outInfoVec):
        """Set information about the data"""
        executive = self.GetExecutive()
        outInfo = outInfoVec.GetInformationObject(0)

        # No time steps for static DF-ISE files
        outInfo.Remove(executive.TIME_STEPS())
        outInfo.Remove(executive.TIME_RANGE())

        return 1

    def RequestData(self, request, inInfoVec, outInfoVec):
        """Generate the output data"""
        output = dsa.WrapDataObject(vtkUnstructuredGrid.GetData(outInfoVec, 0))

        try:
            vtk_output = self._create_vtk_output()
            output.ShallowCopy(vtk_output)

        except Exception as e:
            print(f"Error reading DF-ISE file: {e}")
            # Create empty output
            empty_grid = vtkUnstructuredGrid()
            output.ShallowCopy(empty_grid)

        return 1
