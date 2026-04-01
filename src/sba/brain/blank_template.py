"""
Blank Template Management

Manages the read-only blank template used to generate new Brain instances.
Supports the Phase 1 canonical 7-component structure and auto-migrates the
legacy template layout when encountered.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class BlankTemplateError(Exception):
    """Exception for Blank Template operations."""


class BlankTemplate:
    """Manage the blank template used for creating new Brains."""

    REQUIRED_JSON_FILES = ("metadata.json", "self_eval.json", "subskill_manifest.json")
    REQUIRED_DIRS = ("knowledge_graph", "vector_index")
    REQUIRED_DBS = ("experiment_log.db", "learning_timeline.db")
    LEGACY_FILES = ("data.json", "brain.db")

    def __init__(self, template_path: Path | str):
        self.template_path = Path(template_path)
        self._validate_template()

    def _validate_template(self) -> None:
        if not self.template_path.exists():
            raise BlankTemplateError(f"Template directory not found: {self.template_path}")
        if not self.template_path.is_dir():
            raise BlankTemplateError(f"Template path is not a directory: {self.template_path}")

        self._migrate_legacy_template_if_needed()
        self._ensure_required_structure()
        self._validate_json_files()

    def _migrate_legacy_template_if_needed(self) -> None:
        metadata_path = self.template_path / "metadata.json"
        legacy_data = self.template_path / "data.json"
        legacy_db = self.template_path / "brain.db"

        if not metadata_path.exists():
            return

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise BlankTemplateError(f"Template 'metadata.json' contains invalid JSON: {exc}") from exc

        changed = False
        if metadata.get("version") == "1.0.0":
            metadata["version"] = "0.0"
            changed = True
        if "domain" not in metadata:
            metadata["domain"] = None
            changed = True
        if "level" not in metadata:
            metadata["level"] = 0
            changed = True
        metadata.setdefault("last_saved_at", None)
        metadata.setdefault("description", "Blank Brain Template")
        metadata.setdefault("tags", [])
        metadata.setdefault("brain_id", "uuid-blank-template")
        metadata.setdefault("source", "sba")
        metadata.setdefault("exported_at", None)
        changed = True

        if changed:
            metadata_path.write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        self_eval_path = self.template_path / "self_eval.json"
        if not self_eval_path.exists():
            self_eval = {
                "level": 0,
                "last_eval_at": datetime.utcnow().isoformat(),
                "next_eval_at": datetime.utcnow().isoformat(),
                "subskills": {},
            }
            self_eval_path.write_text(
                json.dumps(self_eval, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        manifest_path = self.template_path / "subskill_manifest.json"
        if not manifest_path.exists():
            domain = metadata.get("domain") or "unknown"
            if legacy_data.exists():
                try:
                    legacy_payload = json.loads(legacy_data.read_text(encoding="utf-8-sig"))
                    domain = legacy_payload.get("domain", domain)
                except json.JSONDecodeError:
                    pass
            manifest = {"domain": domain, "subskills": []}
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        for dirname in self.REQUIRED_DIRS:
            (self.template_path / dirname).mkdir(parents=True, exist_ok=True)

        for db_name in self.REQUIRED_DBS:
            db_path = self.template_path / db_name
            if not db_path.exists():
                sqlite3.connect(db_path).close()

        if legacy_db.exists() and not (self.template_path / "experiment_log.db").stat().st_size:
            shutil.copy2(legacy_db, self.template_path / "experiment_log.db")

    def _ensure_required_structure(self) -> None:
        missing = []
        for filename in self.REQUIRED_JSON_FILES + self.REQUIRED_DBS:
            if not (self.template_path / filename).exists():
                missing.append(filename)
        for dirname in self.REQUIRED_DIRS:
            if not (self.template_path / dirname).exists():
                missing.append(f"{dirname}/")
        if missing:
            raise BlankTemplateError(f"Template missing required components: {', '.join(missing)}")

    def _validate_json_files(self) -> None:
        for filename in self.REQUIRED_JSON_FILES:
            filepath = self.template_path / filename
            try:
                json.loads(filepath.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                raise BlankTemplateError(
                    f"Template '{filename}' contains invalid JSON: {exc}"
                ) from exc

    def get_metadata(self) -> Dict[str, Any]:
        try:
            return json.loads((self.template_path / "metadata.json").read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise BlankTemplateError(f"Failed to read template metadata: {exc}") from exc

    def get_data(self) -> Dict[str, Any]:
        manifest = json.loads((self.template_path / "subskill_manifest.json").read_text(encoding="utf-8-sig"))
        self_eval = json.loads((self.template_path / "self_eval.json").read_text(encoding="utf-8-sig"))
        return {"subskill_manifest": manifest, "self_eval": self_eval}

    def get_checksum(self) -> str:
        hasher = hashlib.sha256()
        for filepath in sorted(self.template_path.rglob("*")):
            if filepath.is_file():
                hasher.update(filepath.relative_to(self.template_path).as_posix().encode("utf-8"))
                hasher.update(filepath.read_bytes())
        return hasher.hexdigest()

    def clone_to(
        self,
        target_directory: Path | str,
        domain: str,
        version: str = "1.0",
        brain_name: Optional[str] = None,
    ) -> Path:
        target_path = Path(target_directory)
        if target_path.exists():
            raise BlankTemplateError(f"Target directory already exists: {target_path}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(self.template_path, target_path, copy_function=shutil.copy2)
            self._update_cloned_files(target_path, domain, version, brain_name)
            return target_path
        except Exception as exc:
            if target_path.exists():
                shutil.rmtree(target_path, ignore_errors=True)
            raise BlankTemplateError(f"Unexpected error during template cloning: {exc}") from exc

    def _update_cloned_files(
        self,
        brain_path: Path,
        domain: str,
        version: str,
        brain_name: Optional[str],
    ) -> None:
        now = datetime.utcnow().isoformat()
        metadata = json.loads((brain_path / "metadata.json").read_text(encoding="utf-8-sig"))
        metadata.update(
            {
                "brain_id": str(uuid.uuid4()),
                "domain": domain,
                "version": version,
                "created_at": now,
                "last_saved_at": None,
                "description": metadata.get("description") or f"{domain} Brain",
                "name": brain_name or f"{domain}_v{version}",
                "source": "sba",
                "exported_at": None,
            }
        )
        (brain_path / "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        manifest = json.loads((brain_path / "subskill_manifest.json").read_text(encoding="utf-8-sig"))
        manifest["domain"] = domain
        (brain_path / "subskill_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self_eval = json.loads((brain_path / "self_eval.json").read_text(encoding="utf-8-sig"))
        self_eval.setdefault("subskills", {})
        self_eval["level"] = 0
        self_eval["last_eval_at"] = now
        self_eval["next_eval_at"] = now
        (brain_path / "self_eval.json").write_text(
            json.dumps(self_eval, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def validate_clone(self, brain_path: Path | str) -> bool:
        brain_path = Path(brain_path)
        if not brain_path.exists():
            raise BlankTemplateError(f"Brain directory not found: {brain_path}")

        for filename in self.REQUIRED_JSON_FILES + self.REQUIRED_DBS:
            if not (brain_path / filename).exists():
                raise BlankTemplateError(f"Cloned Brain missing '{filename}': {brain_path / filename}")
        for dirname in self.REQUIRED_DIRS:
            if not (brain_path / dirname).exists():
                raise BlankTemplateError(f"Cloned Brain missing '{dirname}/': {brain_path / dirname}")

        for filename in self.REQUIRED_JSON_FILES:
            try:
                json.loads((brain_path / filename).read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as exc:
                raise BlankTemplateError(
                    f"Cloned Brain has invalid JSON in '{filename}': {exc}"
                ) from exc
        return True

    def get_info(self) -> Dict[str, Any]:
        return {
            "path": str(self.template_path),
            "checksum": self.get_checksum(),
            "exists": self.template_path.exists(),
            "required_files": list(self.REQUIRED_JSON_FILES + self.REQUIRED_DBS),
            "optional_dirs": list(self.REQUIRED_DIRS),
            "metadata_sample": self.get_metadata(),
            "validated_at": datetime.utcnow().isoformat(),
        }
