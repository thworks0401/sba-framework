"""
Phase 5 Single Learning Cycle Integration Test
===============================================

This test validates that a complete single learning cycle (Steps 1-6) 
can execute from start to finish without errors or hangs.

Focus: Critical path validation only
- NOT testing detailed accuracy of each step
- ONLY testing that all components integrate correctly
- ONLY testing that no infinite loops or hangs occur
"""

import asyncio
import logging
import os
import sys
import json
from datetime import datetime
from pathlib import Path

# Configure Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.WARNING,
    format='[%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


async def test_single_learning_cycle_minimal():
    """
    Minimal Phase 5 integration: Execute one complete learning cycle
    with all 6 steps to verify end-to-end functionality.
    """
    print("\n" + "="*70)
    print("Phase 5: Single Learning Cycle Integration Test")
    print("="*70)

    # Test Setup
    test_brain_path = Path("brain_bank/Tech_v1.1")
    if not test_brain_path.exists():
        print(f"ERROR: Test brain not found at {test_brain_path}")
        return False

    try:
        # Import core components
        from src.sba.learning.learning_loop import LearningLoop
        from src.sba.brain.brain_package import BrainPackage

        print("\n[IMPORT] Core learning loop components loaded")

        # Load brain
        brain = BrainPackage(package_dir=test_brain_path)
        brain_id = brain.metadata.brain_id if hasattr(brain.metadata, 'brain_id') else str(test_brain_path.name)
        brain_name = brain.metadata.domain if hasattr(brain.metadata, 'domain') else "Test Brain"
        print(f"[BRAIN] Loaded {brain_id} ({brain_name}) from {test_brain_path}")

        # Initialize learning loop
        loop = LearningLoop(
            brain_id=brain_id,
            brain_name=brain_name,
            active_brain_path=test_brain_path
        )
        print(f"[LOOP] LearningLoop initialized for brain {brain_id}")

        # Verify component availability
        checks = {
            "gap_detector": loop.gap_detector is not None,
            "resource_finder": loop.resource_finder is not None,
            "experiment_engine": loop.experiment_engine is not None,
            "integrator": loop.integrator is not None,
            "evaluator": loop.evaluator is not None,
            "knowledge_store": loop.knowledge_store is not None,
        }

        print(f"\n[CHECK] Component Availability:")
        for component, available in checks.items():
            status = "✓" if available else "✗"
            print(f"  {status} {component}: {available}")

        if not all(checks.values()):
            print("ERROR: Some components are missing")
            return False

        # Execute single cycle with timeout
        print(f"\n[EXECUTE] Starting single learning cycle (timeout: 300s)...")
        start_time = datetime.now()

        try:
            result = await asyncio.wait_for(
                loop.run_single_cycle(),
                timeout=300
            )
            elapsed = (datetime.now() - start_time).total_seconds()

            print(f"\n[RESULT] Cycle completed in {elapsed:.1f}s")
            print(f"  Cycle ID: {result.cycle_id}")
            print(f"  Status: {'SUCCESS' if not result.error else 'FAILED'}")

            # Log step results
            if result.step1_gap:
                print(f"  Step1: Gap detected - {result.step1_gap.get('target_subskill', 'unknown')}")
            else:
                print(f"  Step1: No gap detected")

            print(f"  Step2: {result.step2_resources} resources found")
            print(f"  Step3: {result.step3_chunks_stored} chunks stored")
            print(f"  Step4: {result.step4_experiment_type or 'N/A'}")
            print(f"  Step5: {result.step5_contradictions} contradictions")
            print(f"  Step6: {result.step6_level} (score: {result.step6_overall_score:.2f})")

            if result.error:
                print(f"\n  ERROR: {result.error}")
                print(f"\n  Logs:")
                for log in result.logs:
                    print(f"    - {log}")
                return False

            print(f"\n[SUCCESS] Single learning cycle completed without errors!")
            return True

        except asyncio.TimeoutError:
            print(f"ERROR: Learning cycle timed out after 300 seconds")
            return False

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_scheduler_readiness():
    """Verify scheduler can be started and stopped without errors."""
    print("\n" + "="*70)
    print("Phase 5: Scheduler Readiness Check")
    print("="*70)

    try:
        from src.sba.scheduler.scheduler import SBAScheduler

        print("\n[INIT] Creating scheduler instance...")
        scheduler = SBAScheduler(brain_id="test-brain", brain_name="Test Python Dev")

        print("[START] Starting scheduler...")
        scheduler.start()

        print("[STATUS] Scheduler running")

        print("[STOP] Stopping scheduler...")
        scheduler.stop()

        print("\n[SUCCESS] Scheduler start/stop cycle successful!")
        return True

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_experiment_runner_readiness():
    """Verify all experiment runner types can be instantiated."""
    print("\n" + "="*70)
    print("Phase 5: Experiment Runner Readiness")
    print("="*70)

    try:
        from src.sba.experiment.experiment_runner import (
            ExperimentRunnerA,
            ExperimentRunnerB,
            ExperimentRunnerD,
        )
        from src.sba.inference.tier1 import Tier1Engine
        from src.sba.storage.experiment_db import ExperimentRepository
        from src.sba.experiment.experiment_engine import ExperimentPlan, ExperimentType

        # Create minimal mock dependencies
        tier1 = Tier1Engine()
        exp_repo = ExperimentRepository(db_path="data/test_experiment.db")

        runner_types = [
            ("ExperimentRunnerA", ExperimentRunnerA),
            ("ExperimentRunnerB", ExperimentRunnerB),
            ("ExperimentRunnerD", ExperimentRunnerD),
        ]

        for name, runner_class in runner_types:
            try:
                runner = runner_class(brain_id="test", tier1=tier1, exp_repo=exp_repo)
                print(f"  ✓ {name} instantiated successfully")
            except Exception as e:
                print(f"  ✗ {name} failed: {e}")
                return False

        print("\n[SUCCESS] All experiment runners ready!")
        return True

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all Phase 5 integration tests."""
    results = {}

    # Test 1: Single learning cycle
    print("\n\n" + "█"*70)
    print("█ TEST 1: Single Learning Cycle")
    print("█"*70)
    results["single_cycle"] = await test_single_learning_cycle_minimal()

    # Test 2: Scheduler readiness
    print("\n\n" + "█"*70)
    print("█ TEST 2: Scheduler Readiness")
    print("█"*70)
    results["scheduler"] = await test_scheduler_readiness()

    # Test 3: Experiment runner readiness
    print("\n\n" + "█"*70)
    print("█ TEST 3: Experiment Runner Readiness")
    print("█"*70)
    results["experiment_runners"] = await test_experiment_runner_readiness()

    # Summary
    print("\n\n" + "═"*70)
    print("PHASE 5 INTEGRATION TEST SUMMARY")
    print("═"*70)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} passed")

    if passed == total:
        print("\n🎉 ALL PHASE 5 INTEGRATION TESTS PASSED!")
        return 0
    else:
        print(f"\n❌ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
