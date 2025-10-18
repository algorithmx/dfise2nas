#!/usr/bin/env python3
"""
DFISE to NASTRAN (NAS) Export Utility

Converts Synopsys Sentaurus TCAD DF-ISE mesh files to NASTRAN bulk data format.

Usage:
    python3 dfise_to_nas.py input.grd output.nas
    python3 dfise_to_nas.py input.grd output.nas --surfaces
    
Options:
    --surfaces    Include boundary faces as CTRIA3 elements (default: volume only)
    
Examples:
    # Export volume elements only
    python3 dfise_to_nas.py device.grd device.nas
    
    # Export volume + boundary surfaces
    python3 dfise_to_nas.py device.grd device_full.nas --surfaces
"""

import sys
from pathlib import Path
from dfise_parser import DFISEParser


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    include_surfaces = '--surfaces' in sys.argv
    
    # Check input file exists
    if not Path(input_file).exists():
        print(f"Error: Input file not found: {input_file}")
        sys.exit(1)
    
    print(f"DFISE to NAS Export")
    print(f"="*70)
    print(f"Input:  {input_file}")
    print(f"Output: {output_file}")
    print(f"Mode:   {'Volume + Surfaces' if include_surfaces else 'Volume only'}")
    print()
    
    try:
        # Create parser instance
        parser = DFISEParser(input_file)
        
        # Export to NAS format
        # Default material properties for Silicon (typical TCAD material)
        parser.export_to_nas(
            output_file,
            include_surfaces=include_surfaces,
            E=170e9,    # Silicon Young's modulus (Pa)
            nu=0.28,    # Silicon Poisson's ratio
            rho=2329.0  # Silicon density (kg/m^3)
        )
        
        print("Export complete!")
        
    except Exception as e:
        print(f"\nError during export: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
