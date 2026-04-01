"""
Brain Package Data Model (Pydantic v2)

Manages all file I/O and validation for a Brain Package:
- metadata.json: Domain, version, level, timestamps, etc.
- self_eval.json: Self-evaluation scores per SubSkill
- subskill_manifest.json: SubSkill definitions and structure
- knowledge_graph/ (Kuzu database directory)
- vector_index/ (Qdrant collection directory)
- experiment_log.db (SQLite)
- learning_timeline.db (SQLite)
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

from pydantic import BaseModel, Field, field_validator, ConfigDict


# ============================================================================
# Pydantic Models
# ============================================================================


class SubSkillDef(BaseModel):
    """SubSkill definition (used in subskill_manifest.json)"""
    
    model_config = ConfigDict(validate_assignment=True)
    
    id: str = Field(
        ...,
        description="SubSkill identifier (e.g., 'design', 'implementation', 'debug')"
    )
    display_name: str = Field(
        ...,
        description="Human-readable name (e.g., '設計', '実装', 'デバッグ')"
    )
    description: str = Field(
        ...,
        description="Detailed description of what this SubSkill covers"
    )
    category: str = Field(
        ...,
        description="Category type (e.g., 'development', 'business', 'domain_specific')"
    )
    priority: int = Field(
        default=1,
        ge=0,
        description="Priority ranking within the Brain"
    )
    aliases: List[str] = Field(
        default_factory=list,
        description="Alternative names for auto-classification"
    )
    related_subskills: List[str] = Field(
        default_factory=list,
        description="List of related SubSkill IDs"
    )


class SubSkillManifest(BaseModel):
    """SubSkill manifest that defines the structure of a Brain's SubSkills"""
    
    model_config = ConfigDict(validate_assignment=True)
    
    domain: str = Field(
        ...,
        description="Domain name (e.g., 'Python開発', '法人営業')"
    )
    subskills: List[SubSkillDef] = Field(
        default_factory=list,
        description="List of SubSkill definitions"
    )
    
    @field_validator('subskills')
    @classmethod
    def validate_unique_ids(cls, v: List[SubSkillDef]) -> List[SubSkillDef]:
        """Ensure all SubSkill IDs are unique"""
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("SubSkill IDs must be unique")
        return v
    
    def get_subskill_ids(self) -> List[str]:
        """Get all SubSkill IDs"""
        return [s.id for s in self.subskills]
    
    def get_subskill(self, skill_id: str) -> Optional[SubSkillDef]:
        """Get a SubSkill by ID"""
        for s in self.subskills:
            if s.id == skill_id:
                return s
        return None


class SubSkillScore(BaseModel):
    """Per-SubSkill evaluation record in self_eval.json"""
    
    model_config = ConfigDict(validate_assignment=True)
    
    density: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Knowledge density score (0.0-1.0)"
    )
    weak: bool = Field(
        default=False,
        description="Weak flag (True if density <= 0.6)"
    )
    priority: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Learning priority indicator"
    )
    
    def __init__(self, **data):
        """Auto-compute weak flag from density if not explicitly provided"""
        if 'weak' not in data and 'density' in data:
            data['weak'] = data['density'] <= 0.6
        super().__init__(**data)


class SelfEval(BaseModel):
    """Self-evaluation record (self_eval.json schema)"""
    
    model_config = ConfigDict(validate_assignment=True)
    
    level: int = Field(
        default=0,
        ge=0,
        le=3,
        description="Brain level (0=blank, 1-3=trained)"
    )
    last_eval_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Last evaluation timestamp (ISO 8601)"
    )
    next_eval_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Next scheduled evaluation (ISO 8601)"
    )
    subskills: Dict[str, SubSkillScore] = Field(
        default_factory=dict,
        description="Per-SubSkill evaluation scores"
    )
    
    @field_validator('next_eval_at')
    @classmethod
    def validate_next_eval(cls, v, info):
        """next_eval_at should be >= last_eval_at"""
        if info.data.get('last_eval_at') and v < info.data['last_eval_at']:
            raise ValueError("next_eval_at must be >= last_eval_at")
        return v
    
    def get_avg_density(self) -> float:
        """Calculate average knowledge density across all SubSkills"""
        if not self.subskills:
            return 0.0
        densities = [s.density for s in self.subskills.values()]
        return sum(densities) / len(densities)
    
    def get_weak_subskills(self) -> List[str]:
        """Get list of SubSkill IDs marked as weak (density <= 0.6)"""
        return [skill_id for skill_id, score in self.subskills.items() if score.weak]
    
    def update_subskill_score(self, skill_id: str, density: float, priority: float = None):
        """Update a SubSkill's score (convenience method)"""
        self.subskills[skill_id] = SubSkillScore(
            density=density,
            weak=density <= 0.6,
            priority=priority or 0.5
        )


class BrainMetadata(BaseModel):
    """Brain metadata (metadata.json schema)"""
    
    model_config = ConfigDict(validate_assignment=True)
    
    domain: Optional[str] = Field(
        default=None,
        description="Domain name (None for blank template)"
    )
    version: str = Field(
        default="0.0",
        description="Version string (e.g., '1.4')"
    )
    level: int = Field(
        default=0,
        ge=0,
        le=3,
        description="Brain level (0=blank, 1-3=trained)"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Creation timestamp (ISO 8601)"
    )
    last_saved_at: Optional[datetime] = Field(
        default=None,
        description="Last save timestamp (ISO 8601, None if not saved yet)"
    )
    description: str = Field(
        default="",
        description="Optional description"
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Classification tags"
    )
    brain_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique Brain identifier (UUID)"
    )
    source: str = Field(
        default="sba",
        description="Source system (fixed to 'sba')"
    )
    exported_at: Optional[datetime] = Field(
        default=None,
        description="Export timestamp (None if not exported)"
    )
    
    @field_validator('version')
    @classmethod
    def validate_version(cls, v: str) -> str:
        """Validate version format (X.Y)"""
        parts = v.split('.')
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ValueError("Version must be in format 'X.Y' (e.g., '1.4')")
        return v
    
    @field_validator('source')
    @classmethod
    def validate_source(cls, v: str) -> str:
        """Source should always be 'sba'"""
        if v != "sba":
            raise ValueError("Source must be 'sba'")
        return v
    
    def is_blank_template(self) -> bool:
        """Check if this is a blank template"""
        return self.domain is None and self.level == 0


# ============================================================================
# BrainPackage Manager Class
# ============================================================================


class BrainPackage:
    """
    Manages the complete Brain Package with validation and I/O.
    
    A Brain Package consists of:
    - metadata.json: Metadata (domain, version, timestamps)
    - self_eval.json: Self-evaluation scores
    - subskill_manifest.json: SubSkill definitions
    - knowledge_graph/ (Kuzu directory)
    - vector_index/ (Qdrant directory)
    - experiment_log.db (SQLite)
    - learning_timeline.db (SQLite)
    
    This class handles loading, validation, and serialization of all components.
    """
    
    def __init__(
        self,
        package_dir: Path | str,
        metadata: Optional[BrainMetadata] = None,
        self_eval: Optional[SelfEval] = None,
        subskill_manifest: Optional[SubSkillManifest] = None
    ):
        """
        Initialize a BrainPackage.
        
        Args:
            package_dir: Path to the Brain Package directory
            metadata: BrainMetadata instance (loaded from metadata.json if None)
            self_eval: SelfEval instance (loaded from self_eval.json if None)
            subskill_manifest: SubSkillManifest instance (loaded if None)
        """
        self.package_dir = Path(package_dir)
        self.package_dir.mkdir(parents=True, exist_ok=True)
        
        # Load or initialize from provided instances
        self.metadata = metadata or self._load_metadata()
        self.self_eval = self_eval or self._load_self_eval()
        self.subskill_manifest = subskill_manifest or self._load_subskill_manifest()
    
    # ========== File Paths ==========
    
    def get_metadata_path(self) -> Path:
        return self.package_dir / "metadata.json"
    
    def get_self_eval_path(self) -> Path:
        return self.package_dir / "self_eval.json"
    
    def get_subskill_manifest_path(self) -> Path:
        return self.package_dir / "subskill_manifest.json"
    
    def get_knowledge_graph_path(self) -> Path:
        return self.package_dir / "knowledge_graph"
    
    def get_vector_index_path(self) -> Path:
        return self.package_dir / "vector_index"
    
    def get_experiment_log_path(self) -> Path:
        return self.package_dir / "experiment_log.db"
    
    def get_learning_timeline_path(self) -> Path:
        return self.package_dir / "learning_timeline.db"

    def ensure_structure(self) -> None:
        """Ensure all non-JSON storage artifacts exist on disk."""
        self.get_knowledge_graph_path().mkdir(parents=True, exist_ok=True)
        self.get_vector_index_path().mkdir(parents=True, exist_ok=True)
        self.get_experiment_log_path().touch(exist_ok=True)
        self.get_learning_timeline_path().touch(exist_ok=True)
    
    # ========== Metadata I/O ==========
    
    def _load_metadata(self) -> BrainMetadata:
        """Load metadata.json from disk, or return default if not found"""
        path = self.get_metadata_path()
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return BrainMetadata.model_validate(data)
            except Exception as e:
                raise ValueError(f"Failed to load metadata.json: {e}")
        # Return default blank metadata if file doesn't exist
        return BrainMetadata()
    
    def save_metadata(self) -> None:
        """Save metadata to metadata.json"""
        path = self.get_metadata_path()
        self.metadata.last_saved_at = datetime.utcnow()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.metadata.model_dump_json(indent=2))
    
    def get_metadata_dict(self) -> dict:
        """Get metadata as dictionary"""
        return self.metadata.model_dump()
    
    def get_metadata_json(self) -> str:
        """Get metadata as JSON string"""
        return self.metadata.model_dump_json(indent=2)
    
    # ========== Self-Evaluation I/O ==========
    
    def _load_self_eval(self) -> SelfEval:
        """Load self_eval.json from disk, or return default if not found"""
        path = self.get_self_eval_path()
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return SelfEval.model_validate(data)
            except Exception as e:
                raise ValueError(f"Failed to load self_eval.json: {e}")
        # Return default blank self-eval if file doesn't exist
        return SelfEval()
    
    def save_self_eval(self) -> None:
        """Save self_eval to self_eval.json"""
        path = self.get_self_eval_path()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.self_eval.model_dump_json(indent=2))
    
    def get_self_eval_dict(self) -> dict:
        """Get self_eval as dictionary"""
        return self.self_eval.model_dump()
    
    def get_self_eval_json(self) -> str:
        """Get self_eval as JSON string"""
        return self.self_eval.model_dump_json(indent=2)
    
    # ========== SubSkill Manifest I/O ==========
    
    def _load_subskill_manifest(self) -> SubSkillManifest:
        """Load subskill_manifest.json from disk, or return default if not found"""
        path = self.get_subskill_manifest_path()
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return SubSkillManifest.model_validate(data)
            except Exception as e:
                raise ValueError(f"Failed to load subskill_manifest.json: {e}")
        # Return default manifest if file doesn't exist
        return SubSkillManifest(domain=self.metadata.domain or "unknown")
    
    def save_subskill_manifest(self) -> None:
        """Save subskill_manifest to subskill_manifest.json"""
        path = self.get_subskill_manifest_path()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(self.subskill_manifest.model_dump_json(indent=2))
    
    def get_subskill_manifest_dict(self) -> dict:
        """Get subskill_manifest as dictionary"""
        return self.subskill_manifest.model_dump()
    
    def get_subskill_manifest_json(self) -> str:
        """Get subskill_manifest as JSON string"""
        return self.subskill_manifest.model_dump_json(indent=2)
    
    # ========== File Existence Checks ==========
    
    def has_metadata_file(self) -> bool:
        return self.get_metadata_path().exists()
    
    def has_self_eval_file(self) -> bool:
        return self.get_self_eval_path().exists()
    
    def has_subskill_manifest_file(self) -> bool:
        return self.get_subskill_manifest_path().exists()
    
    def has_knowledge_graph(self) -> bool:
        return self.get_knowledge_graph_path().exists()
    
    def has_vector_index(self) -> bool:
        return self.get_vector_index_path().exists()
    
    def has_experiment_log_db(self) -> bool:
        return self.get_experiment_log_path().exists()
    
    def has_learning_timeline_db(self) -> bool:
        return self.get_learning_timeline_path().exists()
    
    def is_complete(self) -> bool:
        """Check if all 7 required files/dirs exist"""
        return all([
            self.has_metadata_file(),
            self.has_self_eval_file(),
            self.has_subskill_manifest_file(),
            self.has_knowledge_graph(),
            self.has_vector_index(),
            self.has_experiment_log_db(),
            self.has_learning_timeline_db()
        ])
    
    def get_missing_components(self) -> List[str]:
        """Get list of missing required files/directories"""
        missing = []
        if not self.has_metadata_file():
            missing.append("metadata.json")
        if not self.has_self_eval_file():
            missing.append("self_eval.json")
        if not self.has_subskill_manifest_file():
            missing.append("subskill_manifest.json")
        if not self.has_knowledge_graph():
            missing.append("knowledge_graph/")
        if not self.has_vector_index():
            missing.append("vector_index/")
        if not self.has_experiment_log_db():
            missing.append("experiment_log.db")
        if not self.has_learning_timeline_db():
            missing.append("learning_timeline.db")
        return missing
    
    # ========== High-Level API ==========
    
    def save_all(self) -> None:
        """Save all JSON files (metadata, self_eval, subskill_manifest)"""
        self.ensure_structure()
        self.save_metadata()
        self.save_self_eval()
        self.save_subskill_manifest()
    
    def get_brain_info(self) -> dict:
        """Get comprehensive Brain information"""
        return {
            "package_dir": str(self.package_dir),
            "domain": self.metadata.domain,
            "version": self.metadata.version,
            "level": self.metadata.level,
            "brain_id": self.metadata.brain_id,
            "created_at": self.metadata.created_at.isoformat(),
            "last_saved_at": self.metadata.last_saved_at.isoformat() if self.metadata.last_saved_at else None,
            "description": self.metadata.description,
            "tags": self.metadata.tags,
            "is_complete": self.is_complete(),
            "missing_components": self.get_missing_components(),
            "avg_knowledge_density": self.self_eval.get_avg_density(),
            "level_from_eval": self.self_eval.level,
            "weak_subskills": self.self_eval.get_weak_subskills(),
            "subskill_count": len(self.subskill_manifest.subskills),
            "subskill_ids": self.subskill_manifest.get_subskill_ids()
        }
    
    def validate(self) -> tuple[bool, List[str]]:
        """
        Validate the Brain Package.
        
        Returns:
            (is_valid: bool, errors: List[str])
        """
        errors = []
        
        # Check file existence
        missing = self.get_missing_components()
        if missing:
            errors.append(f"Missing files/dirs: {', '.join(missing)}")
        
        # Validate metadata
        if not self.metadata.brain_id:
            errors.append("metadata.brain_id is empty")
        if not self.metadata.domain and not self.metadata.is_blank_template():
            errors.append("metadata.domain is required (unless blank template)")
        
        # Validate self_eval
        if self.self_eval.level < 0 or self.self_eval.level > 3:
            errors.append(f"self_eval.level out of range: {self.self_eval.level}")
        
        # Validate subskill_manifest consistency
        eval_skill_ids = set(self.self_eval.subskills.keys())
        manifest_skill_ids = set(self.subskill_manifest.get_subskill_ids())
        
        if eval_skill_ids and manifest_skill_ids:  # Both non-empty
            extra_in_eval = eval_skill_ids - manifest_skill_ids
            extra_in_manifest = manifest_skill_ids - eval_skill_ids
            
            if extra_in_eval:
                errors.append(f"self_eval has unknown SubSkills: {extra_in_eval}")
            if extra_in_manifest:
                errors.append(f"subskill_manifest has no eval scores: {extra_in_manifest}")
        
        return len(errors) == 0, errors
    
    # ========== Factory Methods ==========
    
    @classmethod
    def create_blank(cls, package_dir: Path | str) -> 'BrainPackage':
        """Create a new blank Brain Package"""
        metadata = BrainMetadata(
            domain=None,
            version="0.0",
            level=0,
            description="Blank Brain Template"
        )
        self_eval = SelfEval(level=0)
        manifest = SubSkillManifest(domain="unknown")
        
        brain = cls(
            package_dir,
            metadata=metadata,
            self_eval=self_eval,
            subskill_manifest=manifest
        )
        brain.ensure_structure()
        return brain
    
    @classmethod
    def from_directory(cls, package_dir: Path | str) -> 'BrainPackage':
        """Load a Brain Package from a directory"""
        return cls(package_dir)


# ============================================================================
# Utility Functions
# ============================================================================


def create_blank_brain_package(target_dir: Path | str) -> BrainPackage:
    """Convenience function to create a blank Brain Package"""
    return BrainPackage.create_blank(target_dir)


def load_brain_package(package_dir: Path | str) -> BrainPackage:
    """Convenience function to load a Brain Package from disk"""
    return BrainPackage.from_directory(package_dir)
