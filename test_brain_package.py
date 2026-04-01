#!/usr/bin/env python
"""Test script for brain_package.py implementation"""

import json
import tempfile
from pathlib import Path
from datetime import datetime

from src.sba.brain.brain_package import (
    BrainPackage, BrainMetadata, SelfEval, SubSkillManifest, SubSkillDef, SubSkillScore
)


def test_metadata_validation():
    """Test BrainMetadata validation"""
    print("\n=== Test 1: BrainMetadata Validation ===")
    
    # Test valid metadata
    meta = BrainMetadata(
        domain="Python開発",
        version="1.4",
        level=2,
        description="Python development Brain"
    )
    print(f"✓ Valid metadata created: domain={meta.domain}, version={meta.version}, level={meta.level}")
    
    # Test blank template metadata
    blank_meta = BrainMetadata(domain=None, version="0.0", level=0)
    print(f"✓ Blank template metadata: is_blank={blank_meta.is_blank_template()}")
    
    # Test invalid version format
    try:
        bad_meta = BrainMetadata(version="1.4.0")
        print("✗ Should have rejected invalid version format")
    except Exception as e:
        print(f"✓ Correctly rejected invalid version: {type(e).__name__}")


def test_subskill_definitions():
    """Test SubSkill definitions"""
    print("\n=== Test 2: SubSkill Definitions ===")
    
    manifest = SubSkillManifest(
        domain="Python開発",
        subskills=[
            SubSkillDef(
                id="design",
                display_name="設計",
                description="アーキテクチャ・要件定義・設計パターン",
                category="dev",
                priority=1,
                aliases=["アーキテクチャ設計", "要件設計"],
                related_subskills=["implementation", "test"]
            ),
            SubSkillDef(
                id="implementation",
                display_name="実装",
                description="コーディング・ライブラリ活用",
                category="dev"
            ),
            SubSkillDef(
                id="test",
                display_name="テスト",
                description="ユニットテスト・結合テスト",
                category="qa"
            )
        ]
    )
    
    print(f"✓ Created manifest with {len(manifest.subskills)} SubSkills")
    print(f"✓ SubSkill IDs: {manifest.get_subskill_ids()}")
    
    # Test unique ID validation
    try:
        bad_manifest = SubSkillManifest(
            domain="test",
            subskills=[
                SubSkillDef(id="dup", display_name="Dup1", description="", category="test"),
                SubSkillDef(id="dup", display_name="Dup2", description="", category="test")
            ]
        )
        print("✗ Should have rejected duplicate SubSkill IDs")
    except Exception as e:
        print(f"✓ Correctly rejected duplicate IDs: {type(e).__name__}")


def test_self_eval_scores():
    """Test SelfEval and SubSkillScore"""
    print("\n=== Test 3: SelfEval Scores ===")
    
    self_eval = SelfEval(
        level=2,
        subskills={
            "design": SubSkillScore(density=0.85, priority=0.3),
            "implementation": SubSkillScore(density=0.72, priority=0.5),
            "debug": SubSkillScore(density=0.45, priority=0.95),  # Weak skill
        }
    )
    
    print(f"✓ Created SelfEval with level={self_eval.level}")
    print(f"✓ Average density: {self_eval.get_avg_density():.2f}")
    print(f"✓ Weak SubSkills: {self_eval.get_weak_subskills()}")
    
    # Test weak flag inference
    for skill_id, score in self_eval.subskills.items():
        print(f"  - {skill_id}: density={score.density:.2f}, weak={score.weak}")


def test_brain_package_io():
    """Test BrainPackage file I/O"""
    print("\n=== Test 4: BrainPackage File I/O ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        package_dir = Path(tmpdir)
        
        # Create a new blank Brain Package
        brain = BrainPackage.create_blank(package_dir)
        print(f"✓ Created blank Brain Package at {package_dir}")
        
        # Update metadata
        brain.metadata.domain = "Python開発"
        brain.metadata.version = "1.0"
        brain.metadata.level = 1
        brain.metadata.description = "Test Brain"
        brain.metadata.tags = ["Tech", "Python"]
        
        # Add SubSkills
        brain.subskill_manifest.subskills = [
            SubSkillDef(
                id="coding",
                display_name="コーディング",
                description="Python coding",
                category="dev"
            )
        ]
        
        # Add eval scores
        brain.self_eval.level = 1
        brain.self_eval.update_subskill_score("coding", 0.75)
        
        # Save all
        brain.save_all()
        print(f"✓ Saved all JSON files")
        
        # Verify files exist
        print(f"✓ metadata.json exists: {brain.get_metadata_path().exists()}")
        print(f"✓ self_eval.json exists: {brain.get_self_eval_path().exists()}")
        print(f"✓ subskill_manifest.json exists: {brain.get_subskill_manifest_path().exists()}")
        
        # Load from disk
        brain2 = BrainPackage.from_directory(package_dir)
        print(f"✓ Loaded Brain from directory")
        print(f"  Domain: {brain2.metadata.domain}")
        print(f"  Version: {brain2.metadata.version}")
        print(f"  Level: {brain2.metadata.level}")
        print(f"  Brain ID: {brain2.metadata.brain_id}")


def test_brain_info():
    """Test comprehensive brain info"""
    print("\n=== Test 5: Brain Info ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        brain = BrainPackage.create_blank(tmpdir)
        brain.metadata.domain = "Law"
        brain.metadata.version = "0.5"
        brain.metadata.level = 0
        
        brain.subskill_manifest.subskills = [
            SubSkillDef(id="contracts", display_name="契約", description="Contract law", category="law"),
            SubSkillDef(id="tax", display_name="税務", description="Tax law", category="law")
        ]
        
        brain.self_eval.update_subskill_score("contracts", 0.6)
        brain.self_eval.update_subskill_score("tax", 0.5)
        
        info = brain.get_brain_info()
        print(f"✓ Brain info retrieved:")
        print(f"  Domain: {info['domain']}")
        print(f"  SubSkill count: {info['subskill_count']}")
        print(f"  Avg density: {info['avg_knowledge_density']:.2f}")
        print(f"  Weak SubSkills: {info['weak_subskills']}")
        print(f"  Is complete: {info['is_complete']}")
        if not info['is_complete']:
            print(f"  Missing: {info['missing_components']}")


def test_validation():
    """Test Brain Package validation"""
    print("\n=== Test 6: Validation ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        brain = BrainPackage.create_blank(tmpdir)
        brain.metadata.domain = "Test"
        
        is_valid, errors = brain.validate()
        print(f"✓ Validation result: valid={is_valid}")
        if errors:
            for error in errors:
                print(f"  - {error}")


def test_json_serialization():
    """Test JSON serialization/deserialization"""
    print("\n=== Test 7: JSON Serialization ===")
    
    metadata = BrainMetadata(
        domain="Python開発",
        version="1.4",
        level=2,
        description="Test Brain"
    )
    
    # Serialize to JSON string
    json_str = metadata.model_dump_json(indent=2)
    print(f"✓ Serialized metadata to JSON:")
    print(json_str[:200] + "...")
    
    # Deserialize from JSON
    data = json.loads(json_str)
    restored = BrainMetadata.model_validate(data)
    print(f"✓ Deserialized metadata: domain={restored.domain}, version={restored.version}")


if __name__ == "__main__":
    print("=" * 60)
    print("Brain Package Test Suite")
    print("=" * 60)
    
    try:
        test_metadata_validation()
        test_subskill_definitions()
        test_self_eval_scores()
        test_brain_package_io()
        test_brain_info()
        test_validation()
        test_json_serialization()
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
