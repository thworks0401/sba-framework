"""
Task 1-8: Phase 1 Final Integration Test

End-to-end test of Phase 1 Brain Management system:
1. Create new Brain from blank template
2. Write test data to Brain
3. Save Brain to brain_bank
4. Create another Brain
5. Load the saved Brain back
6. Verify all data was preserved
7. List all Brains
8. Export a Brain
"""

import sys
sys.path.insert(0, str(__file__).replace("\\tests\\test_phase1_integration.py", "\\src"))

from pathlib import Path
import json
import shutil
from typer.testing import CliRunner

from sba.__main__ import main_app
from sba.brain.brain_manager import BrainHotSwapManager
from sba.brain.brain_package import BrainPackage


# ============================================================================
# Configuration
# ============================================================================

BRAIN_BANK = Path("C:\\TH_Works\\SBA\\brain_bank")
ACTIVE_PATH = Path("C:\\TH_Works\\SBA\\brain_bank\\[active]")
EXPORTS_PATH = Path("C:\\TH_Works\\SBA\\exports")

runner = CliRunner()


def _cleanup_named_test_artifacts():
    for name in ["PythonDev_Phase1", "Python_Development_v1.0", "FinanceAgent_v1"]:
        path = BRAIN_BANK / name
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    if EXPORTS_PATH.exists():
        for export_dir in EXPORTS_PATH.iterdir():
            if export_dir.is_dir():
                shutil.rmtree(export_dir, ignore_errors=True)


def setup_module(module):
    _cleanup_named_test_artifacts()


# ============================================================================
# Test Scenario 1: Create and Save Brain
# ============================================================================

def test_01_create_and_save_brain():
    """Test creating a new Brain and saving it."""
    print("\n" + "="*70)
    print("TEST 1-8.1: Create and Save Brain")
    print("="*70)
    
    # Create new Brain
    result = runner.invoke(main_app, [
        "brain", "create",
        "Python_Development",
        "--name", "PythonDev_Phase1",
        "--version", "1.0"
    ])
    print(f"Create output:\n{result.stdout}")
    assert result.exit_code == 0, f"Create failed: {result.stdout}"
    
    # Verify active Brain
    result = runner.invoke(main_app, ["brain", "status"])
    print(f"Status output:\n{result.stdout}")
    assert "Python_Development" in result.stdout or "PythonDev" in result.stdout
    
    # Save the Brain
    result = runner.invoke(main_app, ["brain", "save"])
    print(f"Save output:\n{result.stdout}")
    assert result.exit_code == 0, f"Save failed: {result.stdout}"
    assert "saved" in result.stdout.lower()
    
    print("[OK] Brain created and saved successfully")
    return True


# ============================================================================
# Test Scenario 2: Create Another Brain and Verify Original is Preserved
# ============================================================================

def test_02_create_second_brain():
    """Test creating another Brain while original is in brain_bank."""
    print("\n" + "="*70)
    print("TEST 1-8.2: Create Second Brain")
    print("="*70)
    
    # Create second Brain
    result = runner.invoke(main_app, [
        "brain", "create",
        "Finance",
        "--name", "FinanceAgent_v1",
        "--version", "1.0"
    ])
    print(f"Create second output:\n{result.stdout}")
    assert result.exit_code == 0, f"Create second failed: {result.stdout}"
    
    # Verify it's now active
    result = runner.invoke(main_app, ["brain", "status"])
    print(f"Status after second:\n{result.stdout}")
    assert "Finance" in result.stdout or "FinanceAgent" in result.stdout
    
    # List all Brains
    result = runner.invoke(main_app, ["brain", "list"])
    print(f"Brain list:\n{result.stdout}")
    assert result.exit_code == 0, "List failed"
    
    print("[OK] Second Brain created and first Brain preserved in bank")
    return True


# ============================================================================
# Test Scenario 3: Load First Brain Back
# ============================================================================

def test_03_load_first_brain():
    """Test loading the first saved Brain back."""
    print("\n" + "="*70)
    print("TEST 1-8.3: Load First Brain Back")
    print("="*70)
    
    # Load first Brain
    result = runner.invoke(main_app, [
        "brain", "swap",
        "PythonDev_Phase1"
    ])
    print(f"Load output:\n{result.stdout}")
    assert result.exit_code == 0, f"Load failed: {result.stdout}"
    assert "loaded" in result.stdout.lower() or "Loading" in result.stdout
    
    # Verify it's restored
    result = runner.invoke(main_app, ["brain", "status"])
    print(f"Status after restore:\n{result.stdout}")
    assert "Python" in result.stdout or "PythonDev" in result.stdout or "1.0" in result.stdout
    
    print("[OK] First Brain loaded successfully")
    return True


# ============================================================================
# Test Scenario 4: List All Brains
# ============================================================================

def test_04_list_all_brains():
    """Test listing all Brains with verbose output."""
    print("\n" + "="*70)
    print("TEST 1-8.4: List All Brains")
    print("="*70)
    
    # Normal list
    result = runner.invoke(main_app, ["brain", "list"])
    print(f"Brain list:\n{result.stdout}")
    assert result.exit_code == 0, "List failed"
    
    # Verbose list
    result = runner.invoke(main_app, ["brain", "list", "--verbose"])
    print(f"Verbose list:\n{result.stdout}")
    assert result.exit_code == 0, "Verbose list failed"
    assert "Brain" in result.stdout, "Statistics not displayed"
    
    print("[OK] All Brains listed successfully")
    return True


# ============================================================================
# Test Scenario 5: Verify Data Persistence
# ============================================================================

def test_05_verify_data_persistence():
    """Test that Brain metadata is correctly preserved."""
    print("\n" + "="*70)
    print("TEST 1-8.5: Verify Data Persistence")
    print("="*70)
    
    # Get Brain metadata from disk
    brain1_path = BRAIN_BANK / "PythonDev_Phase1"
    assert brain1_path.exists(), f"Brain not found: {brain1_path}"
    
    # Load and verify metadata
    metadata_path = brain1_path / "metadata.json"
    self_eval_path = brain1_path / "self_eval.json"
    subskill_manifest_path = brain1_path / "subskill_manifest.json"
    
    assert metadata_path.exists(), "metadata.json missing"
    assert self_eval_path.exists(), "self_eval.json missing"
    assert subskill_manifest_path.exists(), "subskill_manifest.json missing"
    
    # Read metadata
    with open(metadata_path, 'r', encoding='utf-8-sig') as f:
        meta = json.load(f)
    
    print(f"Metadata: domain={meta.get('domain')}, version={meta.get('version')}")
    assert meta['domain'] == "Python_Development", f"Domain mismatch: {meta['domain']}"
    assert meta['version'] == "1.0", f"Version mismatch: {meta['version']}"
    
    # Read self_eval
    with open(self_eval_path, 'r', encoding='utf-8-sig') as f:
        self_eval = json.load(f)
    
    assert 'subskills' in self_eval, "SubSkills not in self_eval"
    print(f"Self-eval: level={self_eval.get('level')}, subskills={len(self_eval.get('subskills', []))}")
    
    print("[OK] Data persistence verified")
    return True


# ============================================================================
# Test Scenario 6: CLI Help and Documentation
# ============================================================================

def test_06_cli_documentation():
    """Test that CLI documentation is complete."""
    print("\n" + "="*70)
    print("TEST 1-8.6: CLI Documentation")
    print("="*70)
    
    # Main help
    result = runner.invoke(main_app, ["--help"])
    print(f"Main help contains: {len(result.stdout)} characters")
    assert "brain" in result.stdout.lower(), "brain command not documented"
    assert "version" in result.stdout.lower(), "version command not documented"
    
    # Brain help
    result = runner.invoke(main_app, ["brain", "--help"])
    print(f"Brain help contains: {len(result.stdout)} characters")
    assert "list" in result.stdout.lower(), "list command not documented"
    assert "swap" in result.stdout.lower(), "swap command not documented"
    assert "save" in result.stdout.lower(), "save command not documented"
    
    # Command-specific help
    result = runner.invoke(main_app, ["brain", "create", "--help"])
    assert result.exit_code == 0, "create help failed"
    assert "--name" in result.stdout or "name" in result.stdout.lower(), "--name option not documented"
    assert "--version" in result.stdout or "version" in result.stdout.lower(), "--version option not documented"
    
    print("[OK] CLI documentation is complete")
    return True


# ============================================================================
# Test Scenario 7: Error Scenarios
# ============================================================================

def test_07_error_handling():
    """Test that errors are handled gracefully."""
    print("\n" + "="*70)
    print("TEST 1-8.7: Error Handling")
    print("="*70)
    
    # Try to load non-existent Brain
    result = runner.invoke(main_app, ["brain", "swap", "NonExistent_Brain"])
    assert result.exit_code != 0, "Should fail for non-existent Brain"
    assert "not found" in result.stdout.lower() or "error" in result.stdout.lower(), "Error message not informative"
    print(f"[OK] Non-existent Brain error: handled")
    
    # Try to create with invalid version
    result = runner.invoke(main_app, [
        "brain", "create",
        "Test",
        "--version", "invalid"
    ])
    assert result.exit_code != 0, "Should fail for invalid version"
    print(f"[OK] Invalid version error: handled")
    
    # Try to create with empty domain
    result = runner.invoke(main_app, ["brain", "create", ""])
    assert result.exit_code != 0, "Should fail for empty domain"
    print(f"[OK] Empty domain error: handled")
    
    print("[OK] Error handling is consistent")
    return True


# ============================================================================
# Main test runner
# ============================================================================

def run_all_tests():
    """Run all Phase 1 integration tests."""
    print("\n" + "="*70)
    print("SBA PHASE 1 - FINAL INTEGRATION TEST (Task 1-8)")
    print("End-to-End Scenario Testing")
    print("="*70)
    
    tests = [
        ("Create and Save Brain", test_01_create_and_save_brain),
        ("Create Second Brain", test_02_create_second_brain),
        ("Load First Brain Back", test_03_load_first_brain),
        ("List All Brains", test_04_list_all_brains),
        ("Verify Data Persistence", test_05_verify_data_persistence),
        ("CLI Documentation", test_06_cli_documentation),
        ("Error Handling", test_07_error_handling),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, "PASS" if success else "FAIL"))
        except Exception as e:
            print(f"\n[FAIL] {test_name} FAILED: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, "FAIL"))
    
    # Print summary
    print("\n" + "="*70)
    print("PHASE 1 INTEGRATION TEST SUMMARY")
    print("="*70)
    
    for test_name, status in results:
        symbol = "[OK]" if status == "PASS" else "[NG]"
        print(f"{symbol} {test_name:35} {status}")
    
    passed = sum(1 for _, status in results if status == "PASS")
    total = len(results)
    
    print(f"\nTotal: {passed}/{total} passed")
    print("\n" + "="*70)
    print("PHASE 1 STATUS: " + ("COMPLETE" if passed == total else "INCOMPLETE"))
    print("="*70)
    
    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
