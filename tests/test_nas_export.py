#!/usr/bin/env python3
"""
Test script for DFISE to NAS export functionality

This script tests the export_to_nas() method by:
1. Parsing a small DFISE test file
2. Exporting to NAS format
3. Validating the output file structure
4. Optionally testing on larger production files
"""

import sys
from pathlib import Path
from dfise_parser import DFISEParser


def test_export_basic(grd_file, nas_file, include_surfaces=False):
    """Test basic export functionality"""
    print(f"="*70)
    print(f"Testing DFISE to NAS export")
    print(f"="*70)
    print(f"Input:  {grd_file}")
    print(f"Output: {nas_file}")
    print(f"Include surfaces: {include_surfaces}")
    print()
    
    try:
        # Create parser and export
        parser = DFISEParser(grd_file)
        output_path = parser.export_to_nas(
            nas_file, 
            include_surfaces=include_surfaces,
            E=2.1e11,  # Steel Young's modulus (Pa)
            nu=0.3,    # Poisson's ratio
            rho=7850.0 # Steel density (kg/m^3)
        )
        
        # Validate output file exists
        if Path(output_path).exists():
            file_size = Path(output_path).stat().st_size
            print(f"\n✓ Export successful!")
            print(f"  File size: {file_size / (1024**2):.2f} MB")
            
            # Quick validation - count cards
            with open(output_path, 'r') as f:
                lines = f.readlines()
                grid_count = sum(1 for line in lines if line.startswith('GRID'))
                ctetra_count = sum(1 for line in lines if line.startswith('CTETRA'))
                ctria3_count = sum(1 for line in lines if line.startswith('CTRIA3'))
                mat1_count = sum(1 for line in lines if line.startswith('MAT1'))
                psolid_count = sum(1 for line in lines if line.startswith('PSOLID'))
            
            print(f"\n  Card counts:")
            print(f"    GRID:    {grid_count:>8,}")
            print(f"    MAT1:    {mat1_count:>8}")
            print(f"    PSOLID:  {psolid_count:>8}")
            print(f"    CTETRA:  {ctetra_count:>8,}")
            if ctria3_count > 0:
                print(f"    CTRIA3:  {ctria3_count:>8,}")
            
            # Show first few lines
            print(f"\n  First 20 lines of output:")
            print("  " + "-"*66)
            with open(output_path, 'r') as f:
                for i, line in enumerate(f):
                    if i >= 20:
                        break
                    print(f"  {line.rstrip()}")
            print("  " + "-"*66)
            
            return True
        else:
            print("\n✗ Export failed - output file not found")
            return False
            
    except Exception as e:
        print(f"\n✗ Export failed with error:")
        print(f"  {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main test function"""
    
    # Get test file path
    if len(sys.argv) > 1:
        grd_file = sys.argv[1]
    else:
        # Default to test_coordsystem.grd for quick testing
        grd_file = "test_coordsystem.grd"
        if not Path(grd_file).exists():
            print("Usage: python3 test_nas_export.py <grd_file>")
            print("\nAvailable test files:")
            print("  test_coordsystem.grd  - Minimal test file")
            print("  test1.grd             - Production file (49 MB, 75k vertices)")
            print("  test2.grd             - Production file (29 MB, 45k vertices)")
            sys.exit(1)
    
    # Test 1: Basic export (volume elements only)
    print("\n" + "="*70)
    print("TEST 1: Volume elements only (default)")
    print("="*70)
    nas_file = Path(grd_file).stem + "_output.nas"
    success1 = test_export_basic(grd_file, nas_file, include_surfaces=False)
    
    # Test 2: Export with surfaces
    print("\n\n" + "="*70)
    print("TEST 2: Volume elements + boundary surfaces")
    print("="*70)
    nas_file_surf = Path(grd_file).stem + "_output_surfaces.nas"
    success2 = test_export_basic(grd_file, nas_file_surf, include_surfaces=True)
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    print(f"Test 1 (volume only):     {'PASS' if success1 else 'FAIL'}")
    print(f"Test 2 (with surfaces):   {'PASS' if success2 else 'FAIL'}")
    print()
    
    if success1 and success2:
        print("✓ All tests passed!")
        sys.exit(0)
    else:
        print("✗ Some tests failed")
        sys.exit(1)


if __name__ == '__main__':
    main()
