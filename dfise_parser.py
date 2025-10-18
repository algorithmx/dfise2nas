#!/usr/bin/env python3
"""
DF-ISE Grid File Format Parser
==============================

A comprehensive parser for DF-ISE (.grd) files used by Synopsys Sentaurus TCAD.

This script provides:
1. File format analysis and validation
2. Mesh statistics and topology verification
3. Data extraction and inspection
4. Euler characteristic calculation
5. Material region analysis

Based on comprehensive analysis of test1.grd and test2.grd files.

Usage:
    python3 dfise_parser.py <grd_file>
    python3 dfise_parser.py test1.grd --verbose
    python3 dfise_parser.py test1.grd --export-stats stats.json
    python3 dfise_parser.py test1.grd --full-report
    python3 dfise_parser.py corrupted.grd --show-issues
    python3 dfise_parser.py test1.grd --strict
    
Options:
    --verbose        Show detailed progress and issues
    --full-report    Generate comprehensive detailed report
    --show-issues    Display parsing warnings and errors
    --strict         Fail on any parsing issues (no error recovery)
    --export-stats   Export statistics to JSON file
"""

import re
import sys
import json
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional, Union
from collections import defaultdict
import logging
from enum import Enum


class ParseError(Exception):
    """Base exception for parsing errors"""
    pass


class CorruptedFileError(ParseError):
    """Exception for corrupted or malformed files"""
    pass


class MissingRequiredSectionError(ParseError):
    """Exception for missing required sections"""
    pass


class DataInconsistencyError(ParseError):
    """Exception for data inconsistencies"""
    pass


class ParseWarning(UserWarning):
    """Warning for non-critical parsing issues"""
    pass


class ParseResult(Enum):
    """Result status for parsing operations"""
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CORRUPTED = "corrupted"


@dataclass
class InfoBlock:
    """Metadata from Info block"""
    version: str
    type: str
    dimension: int
    nb_vertices: int
    nb_edges: int
    nb_faces: int
    nb_elements: int
    nb_regions: int
    regions: List[str]
    materials: List[str]


@dataclass
class CoordSystem:
    """Coordinate system information"""
    translate: List[float]
    transform: List[List[float]]


@dataclass
class MeshStatistics:
    """Computed mesh statistics"""
    euler_characteristic: int
    edges_per_vertex: float
    faces_per_vertex: float
    elements_per_vertex: float
    faces_per_edge: float
    elements_per_face: float
    boundary_edges: int
    interior_faces: int
    interface_faces: int
    exterior_faces: int


@dataclass
class RegionInfo:
    """Material region information"""
    name: str
    material: str
    element_count: int
    fraction: float


class DFISEParser:
    """Main parser for DF-ISE grid files
    
    This parser handles Synopsys Sentaurus TCAD DF-ISE (.grd) files, providing:
    - Metadata parsing (Info block)
    - Topology analysis (vertices, edges, faces, elements)
    - Region and material information extraction
    - Statistics computation (Euler characteristic, connectivity, etc.)
    - Export to NASTRAN (NAS) format
    
    Usage - Basic parsing:
        >>> parser = DFISEParser('mesh.grd')
        >>> info, stats = parser.parse_all()
        >>> parser.print_concise_report()
    
    Usage - Export to NAS format:
        >>> parser = DFISEParser('mesh.grd')
        >>> parser.export_to_nas('output.nas')  # Volume elements only (default)
        >>> parser.export_to_nas('output_full.nas', include_surfaces=True)  # With boundary faces
    
    The NAS export performs full geometry parsing and converts the hierarchical
    DFISE connectivity (elements → faces → edges → vertices) to direct vertex
    connectivity required by NASTRAN format.
    """
    
    def __init__(self, filepath: str):
        """Initialize parser with file path"""
        self.filepath = Path(filepath)
        self.info: Optional[InfoBlock] = None
        self.regions: Dict[str, RegionInfo] = {}
        self.locations_chars: Dict[str, int] = {}
        self.element_types: Dict[int, int] = {}
        self.face_types: Dict[int, int] = {}
        self.coord_system: Optional[CoordSystem] = None
        self.parse_warnings: List[str] = []
        self.parse_errors: List[str] = []
        self.parse_status: ParseResult = ParseResult.SUCCESS
        self.strict_mode: bool = False
        
    def _add_warning(self, message: str, line_num: Optional[int] = None) -> None:
        """Add a warning to the warnings list"""
        warning_msg = f"Line {line_num}: {message}" if line_num else message
        self.parse_warnings.append(warning_msg)
        if self.parse_status == ParseResult.SUCCESS:
            self.parse_status = ParseResult.WARNING
        import warnings
        warnings.warn(warning_msg, ParseWarning)
    
    def _add_error(self, message: str, line_num: Optional[int] = None) -> None:
        """Add an error to the errors list"""
        error_msg = f"Line {line_num}: {message}" if line_num else message
        self.parse_errors.append(error_msg)
        self.parse_status = ParseResult.ERROR
    
    def _is_file_accessible(self) -> bool:
        """Check if file is accessible and readable"""
        try:
            if not self.filepath.exists():
                self._add_error(f"File does not exist: {self.filepath}")
                return False
            if not self.filepath.is_file():
                self._add_error(f"Path is not a file: {self.filepath}")
                return False
            if self.filepath.stat().st_size == 0:
                self._add_error(f"File is empty: {self.filepath}")
                return False
            # Try to read first few bytes
            with open(self.filepath, 'r') as f:
                f.read(100)
            return True
        except PermissionError:
            self._add_error(f"Permission denied: {self.filepath}")
            return False
        except UnicodeDecodeError:
            self._add_error(f"File contains invalid characters: {self.filepath}")
            return False
        except Exception as e:
            self._add_error(f"File access error: {e}")
            return False
    
    def _validate_numeric_value(self, value_str: str, field_name: str, line_num: Optional[int] = None) -> Optional[Union[int, float]]:
        """Safely parse numeric values with validation"""
        try:
            # Try integer first
            if '.' not in value_str and 'e' not in value_str.lower():
                result = int(value_str)
                if result < 0:
                    self._add_warning(f"{field_name} has negative value: {result}", line_num)
                return result
            else:
                result = float(value_str)
                if not (-1e10 < result < 1e10):  # Reasonable range check
                    self._add_warning(f"{field_name} has extreme value: {result}", line_num)
                return result
        except ValueError:
            self._add_error(f"Invalid numeric value for {field_name}: '{value_str}'", line_num)
            return None
    
    def parse_info_block(self) -> InfoBlock:
        """Parse Info block from file with robust error handling"""
        if not self._is_file_accessible():
            raise CorruptedFileError("File is not accessible")
        
        info_dict = {}
        info_found = False
        line_num = 0
        
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                in_info = False
                for line in f:
                    line_num += 1
                    line = line.strip()
                    
                    if line.startswith('Info {'):
                        in_info = True
                        info_found = True
                        continue
                    
                    if in_info and line == '}':
                        break
                    
                    if in_info and '=' in line:
                        try:
                            parts = line.split('=', 1)
                            if len(parts) != 2:
                                self._add_warning(f"Malformed assignment: {line}", line_num)
                                continue
                                
                            key = parts[0].strip()
                            value = parts[1].strip()
                            
                            if not key:
                                self._add_warning(f"Empty key in assignment: {line}", line_num)
                                continue
                            
                            # Parse regions and materials as lists
                            if key in ('regions', 'materials'):
                                match = re.search(r'\[(.*?)\]', value)
                                if match:
                                    items = match.group(1).strip().split()
                                    value = [item.strip('"') for item in items if item.strip('"')]
                                    if not value:
                                        self._add_warning(f"Empty list for {key}", line_num)
                                        value = []  # Set to empty list
                                else:
                                    self._add_error(f"Invalid list format for {key}: {value}", line_num)
                                    if not self.strict_mode:
                                        value = []  # Use empty list as fallback in non-strict mode
                                    else:
                                        continue
                            else:
                                # Try to parse as numeric value
                                if value.replace('.', '').replace('-', '').replace('+', '').replace('e', '').replace('E', '').isdigit():
                                    parsed_value = self._validate_numeric_value(value, key, line_num)
                                    if parsed_value is not None:
                                        value = parsed_value
                            
                            info_dict[key] = value
                            
                        except Exception as e:
                            self._add_error(f"Error parsing line: {line} - {e}", line_num)
                            continue
        
        except UnicodeDecodeError as e:
            self._add_error(f"File encoding error: {e}")
            raise CorruptedFileError(f"File has invalid encoding: {e}")
        except Exception as e:
            self._add_error(f"Unexpected error reading file: {e}")
            raise CorruptedFileError(f"File reading failed: {e}")
        
        if not info_found:
            raise MissingRequiredSectionError("No Info block found in file")
        
        # Validate required fields
        required_fields = ['version', 'type', 'dimension', 'nb_vertices', 'nb_edges', 'nb_faces', 'nb_elements', 'nb_regions']
        missing_fields = [field for field in required_fields if field not in info_dict]
        
        if missing_fields:
            if self.strict_mode:
                raise MissingRequiredSectionError(f"Missing required Info fields: {missing_fields}")
            else:
                self._add_warning(f"Missing Info fields (using defaults): {missing_fields}")
                # Provide reasonable defaults
                defaults = {
                    'version': '1.0',
                    'type': 'grid',
                    'dimension': 3,
                    'nb_vertices': 0,
                    'nb_edges': 0,
                    'nb_faces': 0,
                    'nb_elements': 0,
                    'nb_regions': 0,
                    'regions': [],
                    'materials': []
                }
                for field in missing_fields:
                    if field in defaults:
                        info_dict[field] = defaults[field]
        
        # Basic consistency checks
        if 'nb_vertices' in info_dict and info_dict['nb_vertices'] < 0:
            self._add_error("Negative vertex count")
        if 'dimension' in info_dict and info_dict['dimension'] not in [2, 3]:
            self._add_warning(f"Unusual dimension: {info_dict['dimension']}")
        
        try:
            self.info = InfoBlock(**info_dict)
        except TypeError as e:
            self._add_error(f"Failed to create InfoBlock: {e}")
            raise CorruptedFileError(f"Invalid Info block data: {e}")
        
        return self.info
    
    def parse_locations(self) -> Dict[str, int]:
        """Parse Locations section and count individual character frequency with error handling"""
        locations_chars = defaultdict(int)
        line_num = 0
        locations_found = False
        valid_chars = {'i', 'f', 'e'}  # Expected location characters
        
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                in_locations = False
                
                for line in f:
                    line_num += 1
                    line_stripped = line.strip()
                    
                    if 'Locations (' in line_stripped:
                        in_locations = True
                        locations_found = True
                        # Extract expected count from header
                        try:
                            count_match = re.search(r'Locations \((\d+)\)', line_stripped)
                            if count_match:
                                expected_count = int(count_match.group(1))
                                if expected_count <= 0:
                                    self._add_warning(f"Invalid locations count: {expected_count}", line_num)
                        except Exception as e:
                            self._add_warning(f"Could not parse locations count: {e}", line_num)
                        continue
                    
                    if in_locations and line_stripped == '}':
                        break
                    
                    if in_locations and line_stripped and not line_stripped.startswith('}'):
                        try:
                            # Each line has space-separated characters
                            chars = line_stripped.split()
                            for char in chars:
                                for c in char:
                                    if c not in valid_chars:
                                        self._add_warning(f"Unexpected location character: '{c}'", line_num)
                                    locations_chars[c] += 1
                        except Exception as e:
                            self._add_error(f"Error parsing locations line: {line_stripped} - {e}", line_num)
            
            if not locations_found:
                self._add_warning("No Locations section found")
            
            # Validate against expected face count if Info is available
            total_chars = sum(locations_chars.values())
            if self.info and total_chars != self.info.nb_faces:
                self._add_error(f"Locations count mismatch: found {total_chars}, expected {self.info.nb_faces}")
        
        except Exception as e:
            self._add_error(f"Error reading Locations section: {e}")
        
        self.locations_chars = dict(locations_chars)
        return self.locations_chars
    
    def parse_elements(self) -> Dict[int, int]:
        """Parse Elements section and categorize by type"""
        element_types = defaultdict(int)
        
        with open(self.filepath, 'r') as f:
            in_elements = False
            
            for line in f:
                line_stripped = line.strip()
                
                if 'Elements (' in line_stripped and 'Region' not in line_stripped:
                    in_elements = True
                    continue
                
                if in_elements and line_stripped == '}':
                    break
                
                if in_elements and line_stripped and not line_stripped.startswith('}') and line_stripped != '{':
                    parts = line_stripped.split()
                    if parts and parts[0].isdigit():
                        elem_type = int(parts[0])
                        element_types[elem_type] += 1
        
        self.element_types = dict(element_types)
        return self.element_types
    
    def parse_faces(self) -> Dict[int, int]:
        """Parse Faces section and categorize by type"""
        face_types = defaultdict(int)
        
        with open(self.filepath, 'r') as f:
            in_faces = False
            
            for line in f:
                line_stripped = line.strip()
                
                if 'Faces (' in line_stripped:
                    in_faces = True
                    continue
                
                if in_faces and line_stripped == '}':
                    break
                
                if in_faces and line_stripped and not line_stripped.startswith('}') and line_stripped != '{':
                    parts = line_stripped.split()
                    if parts and parts[0].isdigit():
                        face_type = int(parts[0])
                        face_types[face_type] += 1
        
        self.face_types = dict(face_types)
        return self.face_types
    
    def parse_regions(self) -> Dict[str, RegionInfo]:
        """Parse all Region sections"""
        regions = {}
        
        with open(self.filepath, 'r') as f:
            in_region = False
            current_region = None
            
            for line in f:
                line_stripped = line.strip()
                
                # Detect region header
                match = re.match(r'Region \( "([^"]+)" \)', line_stripped)
                if match:
                    current_region = {
                        'name': match.group(1),
                        'material': None,
                        'element_count': 0,
                    }
                    in_region = True
                    continue
                
                if in_region and 'material =' in line_stripped:
                    match = re.search(r'material\s*=\s*(\w+)', line_stripped)
                    if match:
                        current_region['material'] = match.group(1)
                    continue
                
                if in_region and 'Elements (' in line_stripped:
                    match = re.search(r'Elements \((\d+)\)', line_stripped)
                    if match:
                        current_region['element_count'] = int(match.group(1))
                    continue
                
                # End region
                if in_region and line_stripped == '}' and current_region and current_region['material']:
                    fraction = current_region['element_count'] / self.info.nb_elements
                    regions[current_region['name']] = RegionInfo(
                        name=current_region['name'],
                        material=current_region['material'],
                        element_count=current_region['element_count'],
                        fraction=fraction
                    )
                    current_region = None
                    in_region = False
        
        self.regions = regions
        return self.regions
    
    def parse_vertices(self) -> List[Tuple[float, float, float]]:
        """Parse Vertices section and return list of 3D coordinates"""
        vertices = []
        line_num = 0
        vertices_found = False
        
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                in_vertices = False
                
                for line in f:
                    line_num += 1
                    line_stripped = line.strip()
                    
                    if 'Vertices (' in line_stripped:
                        in_vertices = True
                        vertices_found = True
                        continue
                    
                    if in_vertices and line_stripped == '}':
                        break
                    
                    if in_vertices and line_stripped and not line_stripped.startswith('}'):
                        try:
                            # Check if line contains parentheses (indexed format)
                            if '(' in line_stripped and ')' in line_stripped:
                                # Format: index (x y z)
                                # Extract coordinates from parentheses
                                coords_str = line_stripped[line_stripped.find('(')+1:line_stripped.find(')')]
                                parts = coords_str.split()
                                if len(parts) >= 3:
                                    x = float(parts[0])
                                    y = float(parts[1])
                                    z = float(parts[2])
                                    vertices.append((x, y, z))
                            else:
                                # Format: x y z (raw coordinates)
                                parts = line_stripped.split()
                                if len(parts) >= 3:
                                    x = float(parts[0])
                                    y = float(parts[1])
                                    z = float(parts[2])
                                    vertices.append((x, y, z))
                        except (ValueError, IndexError) as e:
                            self._add_warning(f"Error parsing vertex at line {line_num}: {e}")
            
            if not vertices_found:
                self._add_error("No Vertices section found")
        
        except Exception as e:
            self._add_error(f"Error reading Vertices section: {e}")
        
        return vertices
    
    def parse_edges_full(self) -> List[Tuple[int, int]]:
        """Parse Edges section and return list of vertex index pairs (0-based)"""
        edges = []
        line_num = 0
        edges_found = False
        
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                in_edges = False
                
                for line in f:
                    line_num += 1
                    line_stripped = line.strip()
                    
                    if 'Edges (' in line_stripped:
                        in_edges = True
                        edges_found = True
                        continue
                    
                    if in_edges and line_stripped == '}':
                        break
                    
                    if in_edges and line_stripped and not line_stripped.startswith('}'):
                        try:
                            # Check if line contains parentheses (indexed format)
                            if '(' in line_stripped and ')' in line_stripped:
                                # Format: index (v1 v2)
                                coords_str = line_stripped[line_stripped.find('(')+1:line_stripped.find(')')]
                                parts = coords_str.split()
                            else:
                                # Format: v1 v2 (raw)
                                parts = line_stripped.split()
                            
                            if len(parts) >= 2:
                                v1 = int(parts[0])
                                v2 = int(parts[1])
                                edges.append((v1, v2))
                        except (ValueError, IndexError) as e:
                            self._add_warning(f"Error parsing edge at line {line_num}: {e}")
            
            if not edges_found:
                self._add_error("No Edges section found")
        
        except Exception as e:
            self._add_error(f"Error reading Edges section: {e}")
        
        return edges
    
    def parse_faces_full(self) -> List[Tuple[int, int, int]]:
        """Parse Faces section and return list of signed edge index triplets (0-based)"""
        faces = []
        line_num = 0
        faces_found = False
        
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                in_faces = False
                
                for line in f:
                    line_num += 1
                    line_stripped = line.strip()
                    
                    if 'Faces (' in line_stripped:
                        in_faces = True
                        faces_found = True
                        continue
                    
                    if in_faces and line_stripped == '}':
                        break
                    
                    if in_faces and line_stripped and not line_stripped.startswith('}'):
                        try:
                            # Check if line contains parentheses (indexed format)
                            if '(' in line_stripped and ')' in line_stripped:
                                # Format: index (type e1 e2 e3)
                                coords_str = line_stripped[line_stripped.find('(')+1:line_stripped.find(')')]
                                parts = coords_str.split()
                            else:
                                # Format: type e1 e2 e3 (raw)
                                parts = line_stripped.split()
                            
                            # First element is face type (3 = triangle), skip it
                            if len(parts) >= 4 and parts[0] == '3':
                                e1 = int(parts[1])
                                e2 = int(parts[2])
                                e3 = int(parts[3])
                                faces.append((e1, e2, e3))
                        except (ValueError, IndexError) as e:
                            self._add_warning(f"Error parsing face at line {line_num}: {e}")
            
            if not faces_found:
                self._add_error("No Faces section found")
        
        except Exception as e:
            self._add_error(f"Error reading Faces section: {e}")
        
        return faces
    
    def parse_elements_full(self) -> List[Tuple[int, int, int, int]]:
        """Parse Elements section and return list of signed face index quadruplets (0-based)"""
        elements = []
        line_num = 0
        elements_found = False
        
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                in_elements = False
                
                for line in f:
                    line_num += 1
                    line_stripped = line.strip()
                    
                    if 'Elements (' in line_stripped and 'Region' not in line_stripped:
                        in_elements = True
                        elements_found = True
                        continue
                    
                    if in_elements and line_stripped == '}':
                        break
                    
                    if in_elements and line_stripped and not line_stripped.startswith('}'):
                        try:
                            # Check if line contains parentheses (indexed format)
                            if '(' in line_stripped and ')' in line_stripped:
                                # Format: index (type f1 f2 f3 f4)
                                coords_str = line_stripped[line_stripped.find('(')+1:line_stripped.find(')')]
                                parts = coords_str.split()
                            else:
                                # Format: type f1 f2 f3 f4 (raw)
                                parts = line_stripped.split()
                            
                            # First element is element type (5 = tetrahedron), skip it
                            if len(parts) >= 5 and parts[0] == '5':
                                f1 = int(parts[1])
                                f2 = int(parts[2])
                                f3 = int(parts[3])
                                f4 = int(parts[4])
                                elements.append((f1, f2, f3, f4))
                        except (ValueError, IndexError) as e:
                            self._add_warning(f"Error parsing element at line {line_num}: {e}")
            
            if not elements_found:
                self._add_error("No Elements section found")
        
        except Exception as e:
            self._add_error(f"Error reading Elements section: {e}")
        
        return elements
    
    def parse_locations_full(self) -> List[str]:
        """Parse Locations section and return list of location characters for each face"""
        locations = []
        line_num = 0
        locations_found = False
        
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                in_locations = False
                
                for line in f:
                    line_num += 1
                    line_stripped = line.strip()
                    
                    if 'Locations (' in line_stripped:
                        in_locations = True
                        locations_found = True
                        continue
                    
                    if in_locations and line_stripped == '}':
                        break
                    
                    if in_locations and line_stripped and not line_stripped.startswith('}'):
                        try:
                            # Each line has space-separated characters
                            chars = line_stripped.split()
                            for char_group in chars:
                                for c in char_group:
                                    if c in ['i', 'f', 'e']:
                                        locations.append(c)
                        except Exception as e:
                            self._add_warning(f"Error parsing locations at line {line_num}: {e}")
            
            if not locations_found:
                self._add_error("No Locations section found")
        
        except Exception as e:
            self._add_error(f"Error reading Locations section: {e}")
        
        return locations
    
    def parse_region_elements(self) -> Dict[str, List[int]]:
        """Parse Region sections and return mapping of region name to element indices (0-based)"""
        region_elements = {}
        
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                in_region = False
                current_region_name = None
                in_region_elements = False
                
                for line in f:
                    line_stripped = line.strip()
                    
                    # Detect region header
                    match = re.match(r'Region \( "([^"]+)" \)', line_stripped)
                    if match:
                        current_region_name = match.group(1)
                        in_region = True
                        in_region_elements = False
                        continue
                    
                    # Detect Elements subsection within Region
                    if in_region and 'Elements (' in line_stripped:
                        in_region_elements = True
                        region_elements[current_region_name] = []
                        continue
                    
                    # Parse element indices
                    if in_region_elements and line_stripped and not line_stripped.startswith('}'):
                        try:
                            parts = line_stripped.split()
                            for part in parts:
                                if part.isdigit():
                                    elem_idx = int(part)
                                    region_elements[current_region_name].append(elem_idx)
                        except (ValueError, IndexError) as e:
                            self._add_warning(f"Error parsing region elements: {e}")
                    
                    # End of region elements section
                    if in_region_elements and line_stripped == '}':
                        in_region_elements = False
                    
                    # End of region
                    if in_region and not in_region_elements and line_stripped == '}':
                        in_region = False
                        current_region_name = None
        
        except Exception as e:
            self._add_error(f"Error reading Region elements: {e}")
        
        return region_elements
    
    def _reconstruct_face_vertices(self, face_edges: Tuple[int, int, int], 
                                   edges: List[Tuple[int, int]]) -> Tuple[int, int, int]:
        """Convert signed edge triplet to vertex triplet
        
        Args:
            face_edges: Tuple of 3 signed edge indices (0-based, negative means reversed)
            edges: List of all edges as (v1, v2) pairs
            
        Returns:
            Tuple of 3 vertex indices forming the face
        """
        vertices = []
        
        for edge_idx in face_edges:
            # Handle signed indices
            if edge_idx < 0:
                # Negative index means reversed edge
                actual_idx = -edge_idx - 1
                v1, v2 = edges[actual_idx]
                # Reversed: swap vertices
                edge_verts = (v2, v1)
            else:
                # Positive index
                actual_idx = edge_idx
                edge_verts = edges[actual_idx]
            
            vertices.extend(edge_verts)
        
        # Extract unique vertices in order (triangular face has 3 vertices from 3 edges)
        # Each edge shares vertices with adjacent edges
        # For a triangle, edges are arranged so they form a cycle
        unique_verts = []
        for v in vertices:
            if v not in unique_verts:
                unique_verts.append(v)
        
        # Should have exactly 3 unique vertices
        if len(unique_verts) != 3:
            # Fallback: take first 3 unique vertices
            unique_verts = unique_verts[:3]
        
        return tuple(unique_verts)
    
    def _reconstruct_element_vertices(self, element_faces: Tuple[int, int, int, int],
                                     faces_as_vertices: List[Tuple[int, int, int]]) -> Tuple[int, int, int, int]:
        """Convert signed face quadruplet to vertex quadruplet for tetrahedron
        
        Args:
            element_faces: Tuple of 4 signed face indices (0-based, negative means reversed)
            faces_as_vertices: List of all faces as vertex triplets
            
        Returns:
            Tuple of 4 vertex indices forming the tetrahedron
        """
        vertices = set()
        
        for face_idx in element_faces:
            # Handle signed indices
            if face_idx < 0:
                actual_idx = -face_idx - 1
            else:
                actual_idx = face_idx
            
            # Add all vertices from this face
            face_verts = faces_as_vertices[actual_idx]
            vertices.update(face_verts)
        
        # A tetrahedron should have exactly 4 unique vertices
        if len(vertices) != 4:
            # This shouldn't happen for valid tetrahedra
            # Take first 4 or pad with zeros
            vert_list = list(vertices)
            while len(vert_list) < 4:
                vert_list.append(0)
            return tuple(vert_list[:4])
        
        return tuple(sorted(vertices))  # Sort for consistent ordering
    
    def _format_grid_card(self, node_id: int, x: float, y: float, z: float) -> str:
        """Format a GRID card in free-field format
        
        Args:
            node_id: Node ID (1-based)
            x, y, z: Coordinates
            
        Returns:
            Formatted GRID card string
        """
        return f"GRID,{node_id},,{x:.10e},{y:.10e},{z:.10e}"
    
    def _format_mat1_card(self, mid: int, E: float, nu: float, rho: float) -> str:
        """Format a MAT1 material card
        
        Args:
            mid: Material ID
            E: Young's modulus
            nu: Poisson's ratio
            rho: Density
            
        Returns:
            Formatted MAT1 card string
        """
        return f"MAT1,{mid},{E:.6e},,{nu:.6f},{rho:.6e}"
    
    def _format_psolid_card(self, pid: int, mid: int, region_name: str) -> str:
        """Format a PSOLID property card for solid elements
        
        Args:
            pid: Property ID
            mid: Material ID
            region_name: Region name for comment
            
        Returns:
            Formatted PSOLID card string with comment
        """
        return f"PSOLID,{pid},{mid}  $ Region: {region_name}"
    
    def _format_ctetra_card(self, eid: int, pid: int, n1: int, n2: int, n3: int, n4: int) -> str:
        """Format a CTETRA tetrahedral element card
        
        Args:
            eid: Element ID
            pid: Property ID
            n1, n2, n3, n4: Node IDs (1-based)
            
        Returns:
            Formatted CTETRA card string
        """
        return f"CTETRA,{eid},{pid},{n1},{n2},{n3},{n4}"
    
    def _format_ctria3_card(self, eid: int, pid: int, n1: int, n2: int, n3: int) -> str:
        """Format a CTRIA3 triangular element card
        
        Args:
            eid: Element ID
            pid: Property ID
            n1, n2, n3: Node IDs (1-based)
            
        Returns:
            Formatted CTRIA3 card string
        """
        return f"CTRIA3,{eid},{pid},{n1},{n2},{n3}"
    
    def parse_coord_system(self) -> Optional[CoordSystem]:
        """Parse CoordSystem section if present"""
        coord_system = None
        
        with open(self.filepath, 'r') as f:
            in_coord_system = False
            translate = None
            transform = []
            
            for line in f:
                line_stripped = line.strip()
                
                if 'CoordSystem {' in line:
                    in_coord_system = True
                    continue
                
                if in_coord_system and line_stripped == '}':
                    if translate and len(transform) == 3:
                        coord_system = CoordSystem(translate=translate, transform=transform)
                    break
                
                if in_coord_system:
                    # Parse translate vector
                    if 'translate = ' in line_stripped:
                        # Extract values between parentheses
                        match = re.search(r'translate\s*=\s*\(([^)]+)\)', line_stripped)
                        if match:
                            values = match.group(1).strip().split()
                            translate = [float(val) for val in values]
                    
                    # Parse transform matrix rows
                    elif 'transform = ' in line_stripped or (in_coord_system and '(' in line_stripped and ')' in line_stripped and not 'translate' in line_stripped):
                        # Extract values between parentheses
                        match = re.search(r'\(([^)]+)\)', line_stripped)
                        if match:
                            values = match.group(1).strip().split()
                            if len(values) == 3:  # Should be a 3x3 matrix row
                                row = [float(val) for val in values]
                                transform.append(row)
        
        self.coord_system = coord_system
        return coord_system
    
    def compute_statistics(self) -> MeshStatistics:
        """Compute topological and mesh statistics"""
        if not self.info:
            raise ValueError("Info block must be parsed first")
        
        V = self.info.nb_vertices
        E = self.info.nb_edges
        F = self.info.nb_faces
        C = self.info.nb_elements
        
        # Euler characteristic
        chi = V - E + F - C
        
        # Topological ratios
        edges_per_vertex = 2 * E / V
        faces_per_vertex = F / V
        elements_per_vertex = 4 * C / V
        faces_per_edge = F / E
        elements_per_face = C / F
        
        # Boundary analysis
        face_edge_incidences = 3 * F
        edge_face_incidences = 2 * E
        boundary_edges = face_edge_incidences - edge_face_incidences
        
        # Location distribution
        interior = self.locations_chars.get('i', 0)
        interface = self.locations_chars.get('f', 0)
        exterior = self.locations_chars.get('e', 0)
        
        stats = MeshStatistics(
            euler_characteristic=chi,
            edges_per_vertex=edges_per_vertex,
            faces_per_vertex=faces_per_vertex,
            elements_per_vertex=elements_per_vertex,
            faces_per_edge=faces_per_edge,
            elements_per_face=elements_per_face,
            boundary_edges=boundary_edges,
            interior_faces=interior,
            interface_faces=interface,
            exterior_faces=exterior,
        )
        
        return stats
    
    def parse_all(self, strict_mode: bool = False) -> Tuple[Optional[InfoBlock], Optional[MeshStatistics]]:
        """Parse all sections and compute statistics with error handling"""
        self.strict_mode = strict_mode
        
        print(f"Analyzing {self.filepath.name}...", end=" ", flush=True)
        
        try:
            # Parse Info block first (required)
            self.parse_info_block()
            
            # Parse other sections (optional, with error recovery)
            sections = [
                ('Locations', self.parse_locations),
                ('Elements', self.parse_elements),
                ('Faces', self.parse_faces),
                ('Regions', self.parse_regions),
                ('CoordSystem', self.parse_coord_system)
            ]
            
            for section_name, parse_func in sections:
                try:
                    parse_func()
                except Exception as e:
                    if strict_mode:
                        raise
                    else:
                        self._add_error(f"Failed to parse {section_name}: {e}")
            
            # Try to compute statistics
            stats = None
            try:
                stats = self.compute_statistics()
            except Exception as e:
                if strict_mode:
                    raise
                else:
                    self._add_error(f"Failed to compute statistics: {e}")
            
            # Print status
            if self.parse_status == ParseResult.SUCCESS:
                print("✓")
            elif self.parse_status == ParseResult.WARNING:
                print(f"⚠ ({len(self.parse_warnings)} warnings)")
            else:
                print(f"✗ ({len(self.parse_errors)} errors)")
            
            return self.info, stats
            
        except (CorruptedFileError, MissingRequiredSectionError) as e:
            print(f"✗ FAILED")
            raise e
        except Exception as e:
            print(f"✗ UNEXPECTED ERROR")
            self._add_error(f"Unexpected error during parsing: {e}")
            if strict_mode:
                raise
            return None, None
    
    def validate_consistency(self) -> Dict[str, bool]:
        """Validate mesh consistency"""
        if not self.info:
            raise ValueError("Info block must be parsed first")
        
        results = {}
        
        # Check 1: Region elements sum
        total_region_elements = sum(r.element_count for r in self.regions.values())
        results['regions_sum_correct'] = total_region_elements == self.info.nb_elements
        
        # Check 2: All elements are type-5 (tetrahedra)
        results['all_elements_type_5'] = (
            len(self.element_types) == 1 and 5 in self.element_types and
            self.element_types[5] == self.info.nb_elements
        )
        
        # Check 3: All faces are type-3 (triangles)
        results['all_faces_type_3'] = (
            len(self.face_types) == 1 and 3 in self.face_types and
            self.face_types[3] == self.info.nb_faces
        )
        
        # Check 4: Locations count matches faces
        total_locations = sum(self.locations_chars.values())
        results['locations_count_correct'] = total_locations == self.info.nb_faces
        
        # Check 5: Number of regions
        results['regions_count_correct'] = len(self.regions) == self.info.nb_regions
        
        return results
    
    def print_concise_report(self) -> None:
        """Print a concise, focused report with main information"""
        if not self.info:
            raise ValueError("No data parsed yet")
        
        try:
            stats = self.compute_statistics()
        except Exception as e:
            stats = None
            self._add_error(f"Cannot compute statistics: {e}")
        
        try:
            file_size_mb = self.filepath.stat().st_size / (1024**2)
        except:
            file_size_mb = 0
        
        print("\n" + "="*75)
        print("DF-ISE MESH REPORT: " + self.filepath.name)
        print("="*75)
        
        # Main mesh properties
        print(f"\nMESH PROPERTIES:")
        try:
            print(f"  • Dimension:      {self.info.dimension}D")
        except:
            print(f"  • Dimension:      {self.info.dimension} (invalid)")
        
        try:
            print(f"  • Vertices:       {self.info.nb_vertices:>12,}")
        except:
            print(f"  • Vertices:       {self.info.nb_vertices} (invalid)")
        
        try:
            print(f"  • Edges:          {self.info.nb_edges:>12,}")
        except:
            print(f"  • Edges:          {self.info.nb_edges} (invalid)")
        
        try:
            print(f"  • Faces:          {self.info.nb_faces:>12,}  (all triangles)")
        except:
            print(f"  • Faces:          {self.info.nb_faces} (invalid)")
        
        try:
            print(f"  • Elements:       {self.info.nb_elements:>12,}  (all tetrahedra)")
        except:
            print(f"  • Elements:       {self.info.nb_elements} (invalid)")
        
        try:
            print(f"  • File Size:      {file_size_mb:>12.1f} MB")
        except:
            print(f"  • File Size:      {file_size_mb} (invalid)")
        
        # CoordSystem information
        if self.coord_system:
            print(f"\nCOORDINATE SYSTEM:")
            print(f"  • Translation:    [{self.coord_system.translate[0]:8.3f}, {self.coord_system.translate[1]:8.3f}, {self.coord_system.translate[2]:8.3f}]")
            print(f"  • Transform:      3x3 matrix (available)")
        
        # Topology
        if stats:
            print(f"\nTOPOLOGY:")
            print(f"  • Euler char:     {stats.euler_characteristic:>12}")
            print(f"  • Edges/vertex:   {stats.edges_per_vertex:>12.2f}")
            print(f"  • Faces/vertex:   {stats.faces_per_vertex:>12.2f}")
            print(f"  • Boundary edges: {stats.boundary_edges:>12,}")
            
            # Face distribution
            print(f"\nFACE TYPES:")
            total_locs = sum(self.locations_chars.values()) if self.locations_chars else 1
            interior_pct = 100 * stats.interior_faces / total_locs if total_locs > 0 else 0
            interface_pct = 100 * stats.interface_faces / total_locs if total_locs > 0 else 0
            exterior_pct = 100 * stats.exterior_faces / total_locs if total_locs > 0 else 0
            print(f"  • Interior:       {stats.interior_faces:>12,}  ({interior_pct:5.1f}%)")
            print(f"  • Interface:      {stats.interface_faces:>12,}  ({interface_pct:5.1f}%)")
            print(f"  • Exterior:       {stats.exterior_faces:>12,}  ({exterior_pct:5.1f}%)")
        else:
            print(f"\n[Statistics unavailable due to parsing errors]")
        
        # Materials
        print(f"\nMATERIALS ({self.info.nb_regions} regions):")
        for name in sorted(self.regions.keys()):
            region = self.regions[name]
            pct = region.fraction * 100
            print(f"  • {region.material:20s} ({region.name:20s}): {region.element_count:8,} ({pct:5.1f}%)")
        
        # Validation
        consistency = self.validate_consistency()
        all_pass = all(consistency.values())
        status_str = "✓ VALID" if all_pass else "✗ INVALID"
        print(f"\nVALIDATION: {status_str}")
        
        print("\n" + "="*75 + "\n")
    
    def print_parse_issues(self) -> None:
        """Print parsing warnings and errors"""
        if not self.parse_warnings and not self.parse_errors:
            return
        
        print("\n" + "="*60)
        print("PARSING ISSUES")
        print("="*60)
        
        if self.parse_warnings:
            print(f"\nWARNINGS ({len(self.parse_warnings)}):")
            for i, warning in enumerate(self.parse_warnings, 1):
                print(f"  {i:2d}. {warning}")
        
        if self.parse_errors:
            print(f"\nERRORS ({len(self.parse_errors)}):")
            for i, error in enumerate(self.parse_errors, 1):
                print(f"  {i:2d}. {error}")
        
        print("\n" + "="*60 + "\n")
    
    def get_parse_summary(self) -> Dict[str, any]:
        """Get summary of parsing results"""
        return {
            'status': self.parse_status.value,
            'warnings_count': len(self.parse_warnings),
            'errors_count': len(self.parse_errors),
            'warnings': self.parse_warnings,
            'errors': self.parse_errors,
            'file_parsed': self.info is not None,
            'file_size_mb': self.filepath.stat().st_size / (1024**2) if self.filepath.exists() else 0
        }
    
    def print_full_report(self) -> None:
        """Print comprehensive detailed report"""
        if not self.info:
            raise ValueError("No data parsed yet")
        
        print("\n" + "="*70)
        print("DF-ISE GRID FILE ANALYSIS SUMMARY")
        print("="*70)
        
        print(f"\nFile: {self.filepath.name}")
        print(f"Size: {self.filepath.stat().st_size / (1024**2):.1f} MB")
        
        print("\n--- INFO BLOCK ---")
        print(f"Version: {self.info.version}")
        print(f"Dimension: {self.info.dimension}D")
        print(f"Vertices: {self.info.nb_vertices:,}")
        print(f"Edges: {self.info.nb_edges:,}")
        print(f"Faces: {self.info.nb_faces:,}")
        print(f"Elements: {self.info.nb_elements:,}")
        print(f"Regions: {self.info.nb_regions}")
        
        # CoordSystem details if present
        if self.coord_system:
            print("\n--- COORDINATE SYSTEM ---")
            print(f"Translation: [{self.coord_system.translate[0]:10.6f}, {self.coord_system.translate[1]:10.6f}, {self.coord_system.translate[2]:10.6f}]")
            print("Transform matrix:")
            for i, row in enumerate(self.coord_system.transform):
                print(f"  Row {i}: [{row[0]:10.6f}, {row[1]:10.6f}, {row[2]:10.6f}]")
        
        print("\n--- ELEMENT TYPES ---")
        for elem_type in sorted(self.element_types.keys()):
            count = self.element_types[elem_type]
            pct = 100 * count / self.info.nb_elements
            print(f"Type {elem_type}: {count:,} ({pct:.1f}%)")
        
        print("\n--- FACE TYPES ---")
        for face_type in sorted(self.face_types.keys()):
            count = self.face_types[face_type]
            pct = 100 * count / self.info.nb_faces
            print(f"Type {face_type}: {count:,} ({pct:.1f}%)")
        
        print("\n--- LOCATIONS DISTRIBUTION ---")
        total_locs = sum(self.locations_chars.values())
        for char in sorted(self.locations_chars.keys()):
            count = self.locations_chars[char]
            pct = 100 * count / total_locs
            meanings = {'i': 'Interior', 'f': 'Face/Interface', 'e': 'Exterior'}
            print(f"'{char}' ({meanings.get(char, 'Unknown')}): {count:,} ({pct:.1f}%)")
        
        print("\n--- REGIONS ---")
        total_check = 0
        for name in sorted(self.regions.keys()):
            region = self.regions[name]
            total_check += region.element_count
            print(f"{name:25s} | {region.material:15s} | {region.element_count:7,} ({region.fraction*100:5.1f}%)")
        print(f"{'TOTAL':25s} | {'':15s} | {total_check:7,} (100.0%)")
        
        stats = self.compute_statistics()
        print("\n--- MESH STATISTICS ---")
        print(f"Euler characteristic (V-E+F-C): {stats.euler_characteristic}")
        print(f"Edges per vertex: {stats.edges_per_vertex:.2f}")
        print(f"Faces per vertex: {stats.faces_per_vertex:.2f}")
        print(f"Elements per vertex: {stats.elements_per_vertex:.2f}")
        print(f"Faces per edge: {stats.faces_per_edge:.2f}")
        print(f"Elements per face: {stats.elements_per_face:.2f}")
        print(f"Boundary edges: {stats.boundary_edges:,}")
        
        consistency = self.validate_consistency()
        print("\n--- VALIDATION ---")
        for check, result in consistency.items():
            status = "✓ PASS" if result else "✗ FAIL"
            print(f"{check:40s}: {status}")
        
        print("\n" + "="*70 + "\n")
    
    def export_to_nas(self, output_path: str, include_surfaces: bool = False, 
                      E: float = 2.1e11, nu: float = 0.3, rho: float = 0.0) -> str:
        """Export mesh structure from DFISE to NASTRAN (NAS) format
        
        This method performs full geometry parsing and converts the DFISE mesh
        structure to NAS format with GRID, MAT1, PSOLID, and CTETRA cards.
        
        Args:
            output_path: Path to output NAS file
            include_surfaces: If True, also export boundary faces as CTRIA3 elements (default: False)
            E: Young's modulus for material properties (default: 2.1e11 Pa)
            nu: Poisson's ratio (default: 0.3)
            rho: Density (default: 0.0 kg/m^3)
        
        Returns:
            Path to the created NAS file
            
        Raises:
            ValueError: If required data is missing or invalid
            RuntimeError: If export fails
        
        Example:
            >>> parser = DFISEParser('mesh.grd')
            >>> parser.parse_info_block()
            >>> parser.export_to_nas('mesh.nas')
            >>> parser.export_to_nas('mesh_with_surfaces.nas', include_surfaces=True)
        """
        print(f"\nExporting {self.filepath.name} to NAS format...")
        
        # Step 1: Parse metadata if not already done
        if not self.info:
            print("  Parsing Info block...", end=" ", flush=True)
            self.parse_info_block()
            print("✓")
        
        # Step 2: Parse geometry data
        print("  Parsing vertices...", end=" ", flush=True)
        vertices = self.parse_vertices()
        if not vertices:
            raise ValueError("No vertices found in file")
        print(f"✓ ({len(vertices):,})")
        
        print("  Parsing edges...", end=" ", flush=True)
        edges = self.parse_edges_full()
        if not edges:
            raise ValueError("No edges found in file")
        print(f"✓ ({len(edges):,})")
        
        print("  Parsing faces...", end=" ", flush=True)
        faces_as_edges = self.parse_faces_full()
        if not faces_as_edges:
            raise ValueError("No faces found in file")
        print(f"✓ ({len(faces_as_edges):,})")
        
        print("  Parsing elements...", end=" ", flush=True)
        elements_as_faces = self.parse_elements_full()
        if not elements_as_faces:
            raise ValueError("No elements found in file")
        print(f"✓ ({len(elements_as_faces):,})")
        
        print("  Parsing regions...", end=" ", flush=True)
        if not self.regions:
            self.parse_regions()
        region_elements = self.parse_region_elements()
        print(f"✓ ({len(region_elements)} regions)")
        
        # Step 3: Reconstruct connectivity
        print("  Reconstructing face connectivity...", end=" ", flush=True)
        faces_as_vertices = []
        for face_edges in faces_as_edges:
            try:
                face_verts = self._reconstruct_face_vertices(face_edges, edges)
                faces_as_vertices.append(face_verts)
            except Exception as e:
                self._add_warning(f"Error reconstructing face: {e}")
                faces_as_vertices.append((0, 0, 0))  # Placeholder
        print("✓")
        
        print("  Reconstructing element connectivity...", end=" ", flush=True)
        elements_as_vertices = []
        for elem_faces in elements_as_faces:
            try:
                elem_verts = self._reconstruct_element_vertices(elem_faces, faces_as_vertices)
                elements_as_vertices.append(elem_verts)
            except Exception as e:
                self._add_warning(f"Error reconstructing element: {e}")
                elements_as_vertices.append((0, 0, 0, 0))  # Placeholder
        print("✓")
        
        # Step 4: Build region and material mappings
        print("  Building region mappings...", end=" ", flush=True)
        
        # Create element-to-region mapping
        element_to_region = {}
        for region_name, elem_indices in region_elements.items():
            for elem_idx in elem_indices:
                element_to_region[elem_idx] = region_name
        
        # Get unique materials and assign MIDs
        material_to_mid = {}
        mid_counter = 1
        for region_name, region_info in self.regions.items():
            if region_info.material not in material_to_mid:
                material_to_mid[region_info.material] = mid_counter
                mid_counter += 1
        
        # Assign PIDs to regions
        region_to_pid = {}
        pid_counter = 1
        for region_name in sorted(region_elements.keys()):
            region_to_pid[region_name] = pid_counter
            pid_counter += 1
        
        print(f"✓ ({len(material_to_mid)} materials, {len(region_to_pid)} PIDs)")
        
        # Step 5: Parse locations for boundary extraction if needed
        boundary_faces = []
        if include_surfaces:
            print("  Identifying boundary faces...", end=" ", flush=True)
            locations = self.parse_locations_full()
            if len(locations) == len(faces_as_vertices):
                for i, loc in enumerate(locations):
                    if loc == 'e':  # Exterior faces
                        boundary_faces.append(i)
            print(f"✓ ({len(boundary_faces)} boundary faces)")
        
        # Step 6: Write NAS file
        print(f"  Writing NAS file to {output_path}...", end=" ", flush=True)
        
        try:
            with open(output_path, 'w') as f:
                # Header
                f.write("CEND\n")
                f.write("BEGIN BULK\n")
                f.write(f"$ Generated by dfise_parser.py from {self.filepath.name}\n")
                f.write(f"$ Vertices: {len(vertices):,}, Elements: {len(elements_as_vertices):,}, Regions: {len(region_elements)}\n")
                f.write("$\n")
                
                # GRID cards (vertices)
                f.write("$ GRID cards (vertices)\n")
                for i, (x, y, z) in enumerate(vertices):
                    node_id = i + 1  # Convert to 1-based indexing
                    f.write(self._format_grid_card(node_id, x, y, z) + "\n")
                f.write("$\n")
                
                # MAT1 cards (materials)
                f.write("$ MAT1 cards (material properties)\n")
                for material, mid in sorted(material_to_mid.items(), key=lambda x: x[1]):
                    f.write(f"$ Material: {material}\n")
                    f.write(self._format_mat1_card(mid, E, nu, rho) + "\n")
                f.write("$\n")
                
                # PSOLID cards (solid properties)
                f.write("$ PSOLID cards (solid element properties)\n")
                for region_name in sorted(region_to_pid.keys(), key=lambda r: region_to_pid[r]):
                    pid = region_to_pid[region_name]
                    region_info = self.regions.get(region_name)
                    if region_info:
                        mid = material_to_mid[region_info.material]
                        f.write(self._format_psolid_card(pid, mid, region_name) + "\n")
                f.write("$\n")
                
                # CTETRA cards (volume elements)
                f.write("$ CTETRA cards (tetrahedral elements)\n")
                eid_counter = 1
                for elem_idx, elem_verts in enumerate(elements_as_vertices):
                    # Get region for this element
                    region_name = element_to_region.get(elem_idx, None)
                    if region_name and region_name in region_to_pid:
                        pid = region_to_pid[region_name]
                        # Convert to 1-based indexing
                        n1, n2, n3, n4 = [v + 1 for v in elem_verts]
                        f.write(self._format_ctetra_card(eid_counter, pid, n1, n2, n3, n4) + "\n")
                        eid_counter += 1
                f.write("$\n")
                
                # CTRIA3 cards (boundary surfaces) - optional
                if include_surfaces and boundary_faces:
                    f.write("$ CTRIA3 cards (boundary surface elements)\n")
                    for face_idx in boundary_faces:
                        face_verts = faces_as_vertices[face_idx]
                        # Try to determine which region this face belongs to
                        # For now, use PID=1 as a default
                        pid = 1
                        # Convert to 1-based indexing
                        n1, n2, n3 = [v + 1 for v in face_verts]
                        f.write(self._format_ctria3_card(eid_counter, pid, n1, n2, n3) + "\n")
                        eid_counter += 1
                    f.write("$\n")
                
                # Footer
                f.write("ENDDATA\n")
            
            print("✓")
            
            # Summary
            print(f"\n  Export complete:")
            print(f"    Vertices (GRID):    {len(vertices):>10,}")
            print(f"    Materials (MAT1):   {len(material_to_mid):>10}")
            print(f"    Regions (PSOLID):   {len(region_to_pid):>10}")
            print(f"    Tetrahedra (CTETRA):{eid_counter - 1:>10,}")
            if include_surfaces:
                print(f"    Boundaries (CTRIA3):{len(boundary_faces):>10,}")
            print(f"\n  Output: {output_path}\n")
            
            return output_path
            
        except Exception as e:
            raise RuntimeError(f"Failed to write NAS file: {e}")
    
    def export_stats(self, filepath: str) -> None:
        """Export statistics to JSON file"""
        stats = self.compute_statistics()
        consistency = self.validate_consistency()
        
        export_data = {
            'info': asdict(self.info),
            'statistics': asdict(stats),
            'regions': {name: asdict(region) for name, region in self.regions.items()},
            'element_types': self.element_types,
            'face_types': self.face_types,
            'locations_distribution': self.locations_chars,
            'coord_system': asdict(self.coord_system) if self.coord_system else None,
            'validation': consistency,
        }
        
        with open(filepath, 'w') as f:
            json.dump(export_data, f, indent=2)
        
        print(f"Statistics exported to {filepath}")


def main():
    """Main entry point with robust error handling"""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    grd_file = sys.argv[1]
    
    # Parse arguments
    verbose = '--verbose' in sys.argv
    full_report = '--full-report' in sys.argv
    strict_mode = '--strict' in sys.argv
    show_issues = '--show-issues' in sys.argv or verbose
    export_stats_arg = None
    if '--export-stats' in sys.argv:
        idx = sys.argv.index('--export-stats')
        if idx + 1 < len(sys.argv):
            export_stats_arg = sys.argv[idx + 1]
    
    # Create parser
    parser = DFISEParser(grd_file)
    
    try:
        if verbose:
            print(f"Starting analysis of {grd_file}...\n")
        
        # Parse with error handling
        info, stats = parser.parse_all(strict_mode=strict_mode)
        
        # Show parse issues if requested or if there were errors
        if show_issues or parser.parse_status in [ParseResult.ERROR, ParseResult.CORRUPTED]:
            parser.print_parse_issues()
        
        # Print appropriate report if we have data
        if info:
            if full_report:
                parser.print_full_report()
            else:
                parser.print_concise_report()
        else:
            print("\n❌ Could not generate report - file too corrupted or critical errors encountered.")
        
        # Export if requested and we have data
        if export_stats_arg and info:
            try:
                parser.export_stats(export_stats_arg)
            except Exception as e:
                print(f"Warning: Could not export statistics: {e}")
        
        # Set appropriate exit code
        if parser.parse_status == ParseResult.ERROR or info is None:
            print(f"\n⚠️  Parsing completed with {len(parser.parse_errors)} errors.")
            sys.exit(2)  # Errors but some data recovered
        elif parser.parse_status == ParseResult.WARNING:
            print(f"\n⚠️  Parsing completed with {len(parser.parse_warnings)} warnings.")
            sys.exit(0)  # Success with warnings
        else:
            sys.exit(0)  # Success
        
    except CorruptedFileError as e:
        print(f"\n❌ Corrupted file: {e}")
        if show_issues:
            parser.print_parse_issues()
        sys.exit(3)  # Corrupted file
    
    except MissingRequiredSectionError as e:
        print(f"\n❌ Invalid file format: {e}")
        if show_issues:
            parser.print_parse_issues()
        sys.exit(4)  # Invalid format
    
    except FileNotFoundError:
        print(f"❌ Error: File not found: {grd_file}")
        sys.exit(1)  # File not found
    
    except KeyboardInterrupt:
        print(f"\n⏹️  Analysis interrupted by user")
        sys.exit(130)  # Interrupted
    
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(5)  # Unexpected error


if __name__ == '__main__':
    main()
