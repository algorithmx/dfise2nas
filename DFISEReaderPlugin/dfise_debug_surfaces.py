#!/usr/bin/env python3
"""
DF-ISE Surface Debug/Export Tool
================================

Inspect a DF-ISE (.grd) file and export surface meshes as VTP:
- Exterior boundary faces (Locations == 'e')
- Interface faces between regions (Locations == 'f')

Usage (recommended with pvpython from ParaView 5.13.3):
  pvpython dfise_debug_surfaces.py input.grd --out-prefix out/mesh

Outputs:
  - out/mesh_exterior.vtp   (triangular surface of exterior boundary)
  - out/mesh_interface.vtp  (triangular surface of shared region boundaries)

Also prints counts and basic stats to stdout to help debugging outside ParaView.
"""

import os
import sys
import argparse


def _ensure_local_parser():
    here = os.path.abspath(os.path.dirname(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


def parse_dfise(filepath):
    _ensure_local_parser()
    from dfise_parser import DFISEParser

    p = DFISEParser(filepath)
    info = p.parse_info_block()
    regions = p.parse_regions()  # name -> RegionInfo(material, ...)
    vertices = p.parse_vertices()
    edges = p.parse_edges_full()
    faces_as_edges = p.parse_faces_full()
    elements_as_faces = p.parse_elements_full()
    locations = p.parse_locations_full()

    # reconstruct faces -> vertices
    fav = []
    for fe in faces_as_edges:
        fav.append(p._reconstruct_face_vertices(fe, edges))

    # Build region-to-elements and invert to element->region
    region_elements = p.parse_region_elements()  # region -> [elem indices]
    elem_to_region = {}
    for rname, elems in region_elements.items():
        for ei in elems:
            elem_to_region[ei] = rname

    # Build face->adjacent elements map using signed face references
    face_to_elems = {}
    for e_idx, face_quad in enumerate(elements_as_faces):
        for s in face_quad:
            if s < 0:
                f_idx = -s - 1
            else:
                f_idx = s
            face_to_elems.setdefault(f_idx, []).append(e_idx)

    return {
        'info': info,
        'regions': regions,
        'region_elements': region_elements,
        'elem_to_region': elem_to_region,
        'vertices': vertices,
        'faces_vertices': fav,
        'elements_faces': elements_as_faces,
        'locations': locations,
        'face_to_elems': face_to_elems,
    }


def export_vtp(triangles, vertices, out_path, cell_arrays=None):
    try:
        import vtk
    except Exception:
        sys.stderr.write("ERROR: vtk not available. Run with pvpython or install vtk for Python.\n")
        raise

    pts = vtk.vtkPoints()
    pts.SetNumberOfPoints(len(vertices))
    for i, (x, y, z) in enumerate(vertices):
        pts.SetPoint(i, float(x), float(y), float(z))

    poly = vtk.vtkPolyData()
    poly.SetPoints(pts)

    cells = vtk.vtkCellArray()
    for tri in triangles:
        if len(tri) != 3:
            continue
        cells.InsertNextCell(3)
        cells.InsertCellPoint(int(tri[0]))
        cells.InsertCellPoint(int(tri[1]))
        cells.InsertCellPoint(int(tri[2]))
    poly.SetPolys(cells)

    # Attach optional cell data arrays
    if cell_arrays:
        for name, (vtk_array, values) in cell_arrays.items():
            # values already set in vtk_array
            poly.GetCellData().AddArray(vtk_array)

    w = vtk.vtkXMLPolyDataWriter()
    w.SetFileName(out_path)
    if hasattr(w, 'SetCompressorTypeToZLib'):
        w.SetCompressorTypeToZLib()
    if hasattr(w, 'SetInputData'):
        w.SetInputData(poly)
    else:
        w.SetInput(poly)
    if w.Write() == 0:
        raise RuntimeError("Failed to write VTP: %s" % out_path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Inspect/export DF-ISE boundary and interface surfaces")
    ap.add_argument('input', help='Path to input .grd')
    ap.add_argument('--out-prefix', default=None, help='Output prefix (default: alongside input)')
    args = ap.parse_args(argv)

    infile = os.path.abspath(args.input)
    if not os.path.isfile(infile):
        ap.error('Input file not found: %s' % infile)

    meta = parse_dfise(infile)

    V = len(meta['vertices'])
    F = len(meta['faces_vertices'])
    L = meta['locations']
    loc_counts = {'i': 0, 'f': 0, 'e': 0}
    for c in L:
        if c in loc_counts:
            loc_counts[c] += 1

    print("[dfise-debug] vertices:", V)
    print("[dfise-debug] faces:", F)
    print("[dfise-debug] locations count:", loc_counts)
    if meta['info']:
        print("[dfise-debug] elements:", meta['info'].nb_elements)
        print("[dfise-debug] regions:", len(meta['regions']))

    # Collect triangles by location and build per-triangle annotation
    faces = meta['faces_vertices']
    exterior_tris = []
    interface_tris = []
    exterior_face_indices = []
    interface_face_indices = []
    for idx, tri in enumerate(faces):
        loc = L[idx] if idx < len(L) else 'i'
        if loc == 'e':
            exterior_tris.append(tri)
            exterior_face_indices.append(idx)
        elif loc == 'f':
            interface_tris.append(tri)
            interface_face_indices.append(idx)

    # Determine output prefix
    if args.out_prefix:
        out_prefix = os.path.abspath(args.out_prefix)
        out_dir = os.path.dirname(out_prefix)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
    else:
        base, _ = os.path.splitext(infile)
        out_prefix = base

    ext_path = out_prefix + '_exterior.vtp'
    int_path = out_prefix + '_interface.vtp'

    # Prepare material/region arrays for exterior
    def make_string_array(name, n):
        import vtk
        arr = vtk.vtkStringArray()
        arr.SetName(name)
        arr.SetNumberOfComponents(1)
        arr.SetNumberOfTuples(n)
        return arr

    def make_int_array(name, n):
        import vtk
        arr = vtk.vtkIntArray()
        arr.SetName(name)
        arr.SetNumberOfComponents(1)
        arr.SetNumberOfTuples(n)
        return arr

    # Build mappings for material IDs
    region_info = meta['regions']  # rname -> RegionInfo(material, ...)
    elem_to_region = meta['elem_to_region']
    face_to_elems = meta['face_to_elems']

    # Collect all material names encountered to build stable IDs
    material_names = set()
    # Exterior annotations
    exterior_region = make_string_array("Region", len(exterior_tris))
    exterior_material = make_string_array("Material", len(exterior_tris))
    exterior_material_id = make_int_array("MaterialID", len(exterior_tris))

    for i, f_idx in enumerate(exterior_face_indices):
        elems = face_to_elems.get(f_idx, [])
        # Pick first adjacent element if present
        rname = ''
        mname = ''
        if elems:
            e0 = elems[0]
            rname = elem_to_region.get(e0, '')
            rin = region_info.get(rname)
            mname = rin.material if rin else ''
        exterior_region.SetValue(i, rname)
        exterior_material.SetValue(i, mname)
        material_names.add(mname) if mname else None
        exterior_material_id.SetValue(i, -1)  # temp; fill after ID map

    # Interface annotations (two sides)
    iface_region_a = make_string_array("RegionA", len(interface_tris))
    iface_region_b = make_string_array("RegionB", len(interface_tris))
    iface_material_a = make_string_array("MaterialA", len(interface_tris))
    iface_material_b = make_string_array("MaterialB", len(interface_tris))
    iface_material_a_id = make_int_array("MaterialAID", len(interface_tris))
    iface_material_b_id = make_int_array("MaterialBID", len(interface_tris))
    iface_pair = make_string_array("InterfacePair", len(interface_tris))
    iface_pair_id = make_int_array("InterfacePairID", len(interface_tris))

    pair_names = set()
    for i, f_idx in enumerate(interface_face_indices):
        elems = face_to_elems.get(f_idx, [])
        rA = rB = ''
        mA = mB = ''
        if len(elems) >= 1:
            rA = elem_to_region.get(elems[0], '')
            rin = region_info.get(rA)
            mA = rin.material if rin else ''
        if len(elems) >= 2:
            rB = elem_to_region.get(elems[1], '')
            rin = region_info.get(rB)
            mB = rin.material if rin else ''
        iface_region_a.SetValue(i, rA)
        iface_region_b.SetValue(i, rB)
        iface_material_a.SetValue(i, mA)
        iface_material_b.SetValue(i, mB)
        material_names.update([mA, mB])
        label = "|".join(sorted([m for m in [mA, mB] if m]))
        iface_pair.SetValue(i, label)
        pair_names.add(label)
        iface_material_a_id.SetValue(i, -1)
        iface_material_b_id.SetValue(i, -1)
        iface_pair_id.SetValue(i, -1)

    # Build material ID map
    mat_list = sorted([m for m in material_names if m])
    mat_id = {m: i for i, m in enumerate(mat_list)}
    pair_list = sorted([p for p in pair_names if p])
    pair_id = {p: i for i, p in enumerate(pair_list)}

    # Fill IDs
    for i in range(exterior_material.GetNumberOfValues()):
        m = exterior_material.GetValue(i)
        exterior_material_id.SetValue(i, mat_id.get(m, -1))
    for i in range(len(interface_tris)):
        mA = iface_material_a.GetValue(i)
        mB = iface_material_b.GetValue(i)
        iface_material_a_id.SetValue(i, mat_id.get(mA, -1))
        iface_material_b_id.SetValue(i, mat_id.get(mB, -1))
        p = iface_pair.GetValue(i)
        iface_pair_id.SetValue(i, pair_id.get(p, -1))

    print("[dfise-debug] writing:", ext_path, "(triangles:", len(exterior_tris), ")")
    export_vtp(
        exterior_tris,
        meta['vertices'],
        ext_path,
        cell_arrays={
            'Region': (exterior_region, None),
            'Material': (exterior_material, None),
            'MaterialID': (exterior_material_id, None),
        },
    )

    print("[dfise-debug] writing:", int_path, "(triangles:", len(interface_tris), ")")
    export_vtp(
        interface_tris,
        meta['vertices'],
        int_path,
        cell_arrays={
            'RegionA': (iface_region_a, None),
            'RegionB': (iface_region_b, None),
            'MaterialA': (iface_material_a, None),
            'MaterialB': (iface_material_b, None),
            'MaterialAID': (iface_material_a_id, None),
            'MaterialBID': (iface_material_b_id, None),
            'InterfacePair': (iface_pair, None),
            'InterfacePairID': (iface_pair_id, None),
        },
    )

    print("[dfise-debug] done.")


if __name__ == '__main__':
    sys.exit(main())
