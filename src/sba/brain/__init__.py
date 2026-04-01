"""
Brain Package Management Module

This module provides Pydantic v2 data models and IO utilities for managing
SBA Brain Packages (knowledge containers with metadata, self-evaluation, and SubSkill definitions).

Key Classes:
- BrainMetadata: Metadata about a Brain (domain, version, level, timestamps)
- SubSkillDef: Definition of a single SubSkill
- SubSkillManifest: Collection of SubSkill definitions for a Brain
- SubSkillScore: Evaluation score for a single SubSkill
- SelfEval: Complete self-evaluation state with all SubSkill scores
- BrainPackage: Manager class for all Brain Package files and components
"""

from .brain_package import (
    BrainMetadata,
    SubSkillDef,
    SubSkillScore,
    SubSkillManifest,
    SelfEval,
    BrainPackage,
    create_blank_brain_package,
    load_brain_package,
)

__all__ = [
    "BrainMetadata",
    "SubSkillDef",
    "SubSkillScore",
    "SubSkillManifest",
    "SelfEval",
    "BrainPackage",
    "create_blank_brain_package",
    "load_brain_package",
]
