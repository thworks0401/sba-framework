"""
Brain Hot-Swap Manager

Implements Brain save/load operations with atomic file operations and rollback support.
This is the CORE component for Brain management.

Operations:
- save: Atomic save of [active]/ Brain to brain_bank/ with version management
- load: Load a saved Brain from brain_bank/ to [active]/ with rollback on failure
- list: Enumerate all saved Brains with metadata
- get_active: Get the currently active Brain metadata
"""

import shutil
import json
import uuid
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any
import hashlib
import tempfile
import threading

from .brain_package import BrainPackage


class BrainManagerError(Exception):
    """Exception for Brain management operations"""
    pass


class BrainHotSwapManager:
    """
    Manages Brain hot-swap operations (save/load) with atomic operations
    and transaction-like guarantees.
    
    Hot-Swap semantics:
    - save: Backup active Brain to brain_bank with automatic versioning
    - load: Load a Brain from brain_bank to [active] with atomic swap
    - Both operations are atomic: succeed completely or fail without partial state
    """
    
    def __init__(self, brain_bank_path: Path | str, active_path: Path | str):
        """
        Initialize Brain Hot-Swap Manager.
        
        Args:
            brain_bank_path: Path to brain_bank/ directory (central repository)
            active_path: Path to [active]/ directory (working copy)
            
        Raises:
            BrainManagerError: If paths are invalid
        """
        self.brain_bank_path = Path(brain_bank_path)
        self.active_path = Path(active_path)
        self._lock = threading.Lock()  # For atomic operations
        
        self._validate_directories()
    
    def _validate_directories(self):
        """Validate both directories exist"""
        if not self.brain_bank_path.exists():
            raise BrainManagerError(f"brain_bank path not found: {self.brain_bank_path}")
        
        if not self.brain_bank_path.is_dir():
            raise BrainManagerError(f"brain_bank path is not directory: {self.brain_bank_path}")
        
        if not self.active_path.exists():
            raise BrainManagerError(f"active path not found: {self.active_path}")
        
        if not self.active_path.is_dir():
            raise BrainManagerError(f"active path is not directory: {self.active_path}")
    
    def save(
        self,
        brain_name: Optional[str] = None,
        description: str = ""
    ) -> Dict[str, Any]:
        """
        Atomically save the active Brain to brain_bank.
        
        This operation:
        1. Reads current [active] Brain metadata
        2. Copies entire [active]/  directory to brain_bank/<domain>_v<version>/
        3. Updates version number and last_saved_at timestamp
        4. Creates brain registry entry
        
        On failure, no partial state is left (atomic guarantee).
        
        Args:
            brain_name: Optional custom name (default: from metadata domain)
            description: Optional save description/notes
            
        Returns:
            Dict with save result and metadata
            
        Raises:
            BrainManagerError: If save operation fails
        """
        with self._lock:
            return self._save_impl(brain_name, description)
    
    def _save_impl(self, brain_name: Optional[str], description: str) -> Dict[str, Any]:
        """Actual save implementation (called within lock)"""
        
        # Step 1: Load active Brain metadata
        try:
            active_brain = BrainPackage.from_directory(self.active_path)
        except Exception as e:
            raise BrainManagerError(f"Failed to load active Brain: {e}")
        
        # Access metadata directly (it's a BrainMetadata instance stored on package)
        metadata_dict = active_brain.get_metadata_dict()
        current_version = metadata_dict.get('version', '1.0')
        domain = metadata_dict.get('domain', 'Unknown')
        brain_id = metadata_dict.get('brain_id', str(uuid.uuid4()))
        
        # Step 2: Create target directory name with versioning
        # Format: domain_vX.Y format
        target_dirname = f"{domain}_v{current_version}"
        target_path = self.brain_bank_path / target_dirname
        
        # If this exact version already exists, increment patch version
        if target_path.exists():
            current_version = self._increment_version(current_version)
            target_dirname = f"{domain}_v{current_version}"
            target_path = self.brain_bank_path / target_dirname
        
        # Step 3: Use temp directory for atomic copy
        # Copy to temp location first, then atomically move
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix='brain_save_', dir=self.brain_bank_path)
            temp_path = Path(temp_dir)
            
            # Copy all files from active to temp
            self._copy_brain_files(self.active_path, temp_path)
            
            # Update metadata with new version and timestamp
            self._update_saved_metadata(
                temp_path,
                current_version,
                brain_name,
                description,
                brain_id
            )
            
            # Step 4: Atomic move from temp to target
            # On Windows, need to remove target if it exists
            if target_path.exists():
                shutil.rmtree(target_path)
            
            shutil.move(str(temp_path), str(target_path))
            
            # Step 5: Create registry entry
            registry_entry = {
                'brain_id': brain_id,
                'domain': domain,
                'version': current_version,
                'name': brain_name or f"{domain} v{current_version}",
                'description': description,
                'saved_at': datetime.utcnow().isoformat() + 'Z',
                'saved_path': str(target_path),
                'size_bytes': self._calculate_dir_size(target_path),
            }
            
            return {
                'success': True,
                'message': f"Brain saved: {target_dirname}",
                'brain_id': brain_id,
                'domain': domain,
                'version': current_version,
                'saved_path': str(target_path),
                'registry': registry_entry,
            }
        
        except Exception as e:
            # Cleanup temp directory on failure
            if temp_dir and Path(temp_dir).exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise BrainManagerError(f"Error during save: {e}")
    
    def load(
        self,
        brain_name: str,
        rollback_on_error: bool = True
    ) -> Dict[str, Any]:
        """
        Atomically load a saved Brain from brain_bank to [active].
        
        This operation:
        1. Validates target Brain exists and is valid
        2. Backs up current [active] Brain to temp location
        3. Copies target Brain to [active]/
        4. On success: discards backup
        5. On failure: restores [active] from backup (rollback)
        
        Args:
            brain_name: Name of Brain to load (directory name without path)
            rollback_on_error: If True, restore [active] on any error
            
        Returns:
            Dict with load result and metadata
            
        Raises:
            BrainManagerError: If load fails (with rollback if enabled)
        """
        with self._lock:
            return self._load_impl(brain_name, rollback_on_error)
    
    def _load_impl(
        self,
        brain_name: str,
        rollback_on_error: bool
    ) -> Dict[str, Any]:
        """Actual load implementation (called within lock)"""
        
        # Step 1: Locate Brain in brain_bank
        target_brain_path = self.brain_bank_path / brain_name
        if not target_brain_path.exists():
            # Try fuzzy matching (case-insensitive prefix)
            matches = self._find_brain_fuzzy(brain_name)
            if not matches:
                raise BrainManagerError(
                    f"Brain not found: {brain_name}. Available: {self.list_brains_names()}"
                )
            if len(matches) > 1:
                raise BrainManagerError(
                    f"Ambiguous Brain name '{brain_name}'. Matches: {matches}"
                )
            target_brain_path = self.brain_bank_path / matches[0]
        
        # Step 2: Validate target Brain
        try:
            target_brain = BrainPackage.from_directory(target_brain_path)
            target_metadata = target_brain.get_metadata_dict()
        except Exception as e:
            raise BrainManagerError(f"Invalid target Brain: {e}")
        
        # Step 3: Backup current [active]
        backup_dir = None
        try:
            backup_dir = tempfile.mkdtemp(prefix='brain_load_backup_')
            backup_path = Path(backup_dir)
            
            # Backup current active
            self._copy_brain_files(self.active_path, backup_path)
            
            # Step 4: Copy target Brain to [active]
            self._clear_active_directory()
            self._copy_brain_files(target_brain_path, self.active_path)
            
            # Step 5: Update [active] metadata to reflect load
            self._update_loaded_metadata(self.active_path)
            
            # Verify the load was successful
            active_brain = BrainPackage.from_directory(self.active_path)
            
            # Success: discard backup
            shutil.rmtree(backup_path, ignore_errors=True)
            
            return {
                'success': True,
                'message': f"Brain loaded: {brain_name}",
                'brain_id': target_metadata.get('brain_id'),
                'domain': target_metadata.get('domain'),
                'version': target_metadata.get('version'),
                'loaded_at': datetime.utcnow().isoformat() + 'Z',
                'active_path': str(self.active_path),
            }
        
        except Exception as e:
            # Attempt rollback if enabled
            if rollback_on_error and backup_dir and Path(backup_dir).exists():
                try:
                    self._clear_active_directory()
                    self._copy_brain_files(Path(backup_dir), self.active_path)
                    # Cleanup backup
                    shutil.rmtree(Path(backup_dir), ignore_errors=True)
                    raise BrainManagerError(
                        f"Load failed and [active] was rolled back: {e}"
                    )
                except BrainManagerError:
                    raise
                except Exception as rollback_error:
                    raise BrainManagerError(
                        f"Load failed AND rollback failed: {e}. Rollback error: {rollback_error}"
                    )
            else:
                # Cleanup backup
                if backup_dir:
                    shutil.rmtree(Path(backup_dir), ignore_errors=True)
                raise BrainManagerError(f"Load failed: {e}")
    
    def list_brains(self) -> List[Dict[str, Any]]:
        """
        List all saved Brains in brain_bank.
        
        Returns:
            List of Brain metadata dicts with keys:
            - name: Directory name (e.g., "Python開発_v1.0")
            - domain: Domain from metadata.json
            - version: Version from metadata.json
            - brain_id: Unique Brain ID
            - saved_at: When it was saved
            - level: Current Brain level
            - size_bytes: Directory size
        """
        brains = []
        
        try:
            for brain_dir in sorted(self.brain_bank_path.iterdir()):
                if (
                    brain_dir.name.startswith('_')
                    or brain_dir.name.startswith('[')
                    or brain_dir.name == "blank_template"
                    or brain_dir.name.startswith("brain_save_")
                    or brain_dir.name.startswith("brain_load_backup_")
                ):
                    continue  # Skip template and active directories
                
                if not brain_dir.is_dir():
                    continue
                
                try:
                    brain = BrainPackage.from_directory(brain_dir)
                    metadata = brain.get_metadata_dict()
                    
                    brains.append({
                        'name': brain_dir.name,
                        'domain': metadata.get('domain'),
                        'version': metadata.get('version'),
                        'brain_id': metadata.get('brain_id'),
                        'saved_at': metadata.get('last_saved_at'),
                        'created_at': metadata.get('created_at'),
                        'level': metadata.get('level', 1),
                        'size_bytes': self._calculate_dir_size(brain_dir),
                    })
                except Exception:
                    # Skip invalid Brains, but don't fail the listing
                    pass
        
        except Exception as e:
            raise BrainManagerError(f"Failed to list Brains: {e}")
        
        return brains
    
    def list_brains_names(self) -> List[str]:
        """Get list of all saved Brain directory names"""
        return [b['name'] for b in self.list_brains()]
    
    def get_active_brain(self) -> Dict[str, Any]:
        """Get metadata of the currently active Brain"""
        try:
            active_brain = BrainPackage.from_directory(self.active_path)
            metadata = active_brain.get_metadata_dict()
            return {
                'name': "[active]",
                'domain': metadata.get('domain'),
                'version': metadata.get('version'),
                'brain_id': metadata.get('brain_id'),
                'level': metadata.get('level', 1),
                'size_bytes': self._calculate_dir_size(self.active_path),
            }
        except Exception as e:
            raise BrainManagerError(f"Failed to get active Brain metadata: {e}")
    
    # Private helper methods
    
    def _increment_version(self, version_str: str) -> str:
        """Increment version number (X.Y -> X.Y+1)"""
        try:
            parts = version_str.split('.')
            if len(parts) >= 2:
                parts[-1] = str(int(parts[-1]) + 1)
                return '.'.join(parts)
            return f"{version_str}.1"
        except:
            return "1.0"
    
    def _copy_brain_files(self, src: Path, dst: Path):
        """Copy Brain directory contents (not parent directory itself)"""
        dst.mkdir(parents=True, exist_ok=True)
        
        for item in src.iterdir():
            if item.name.startswith('.'):
                continue  # Skip hidden files
            
            if item.is_dir():
                shutil.copytree(
                    item,
                    dst / item.name,
                    dirs_exist_ok=True,
                    copy_function=shutil.copy2
                )
            else:
                shutil.copy2(item, dst / item.name)
            self._make_writable(dst / item.name)
    
    def _clear_active_directory(self):
        """Clear all contents of [active] directory"""
        for item in self.active_path.iterdir():
            if item.name.startswith('.'):
                continue  # Keep hidden files

            self._make_writable(item)
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    def _make_writable(self, path: Path):
        """Remove read-only bit from files/directories recursively."""
        if not path.exists():
            return
        try:
            os.chmod(path, 0o777)
        except OSError:
            pass
        if path.is_dir():
            for child in path.rglob('*'):
                try:
                    os.chmod(child, 0o777)
                except OSError:
                    pass
    
    def _update_saved_metadata(
        self,
        brain_path: Path,
        version: str,
        brain_name: Optional[str],
        description: str,
        brain_id: str
    ):
        """Update metadata for saved Brain"""
        metadata_path = brain_path / 'metadata.json'
        self._make_writable(metadata_path)
        
        with open(metadata_path, 'r', encoding='utf-8-sig') as f:
            metadata = json.load(f)
        
        metadata['version'] = version
        metadata['brain_id'] = brain_id
        metadata['last_saved_at'] = datetime.utcnow().isoformat() + 'Z'
        if brain_name:
            metadata['name'] = brain_name
        if description:
            metadata['save_description'] = description
        
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    def _update_loaded_metadata(self, brain_path: Path):
        """Update metadata for loaded Brain"""
        metadata_path = brain_path / 'metadata.json'
        self._make_writable(metadata_path)
        
        with open(metadata_path, 'r', encoding='utf-8-sig') as f:
            metadata = json.load(f)
        
        metadata['last_loaded_at'] = datetime.utcnow().isoformat() + 'Z'
        
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    def _find_brain_fuzzy(self, brain_name: str) -> List[str]:
        """Find Brains matching name (case-insensitive prefix search)"""
        matches = []
        search_name = brain_name.lower()
        
        for brain_dir in self.brain_bank_path.iterdir():
            if brain_dir.is_dir() and brain_dir.name.lower().startswith(search_name):
                matches.append(brain_dir.name)
        
        return sorted(matches)
    
    def _calculate_dir_size(self, path: Path) -> int:
        """Calculate total size of directory in bytes"""
        total = 0
        try:
            for item in path.rglob('*'):
                if item.is_file():
                    total += item.stat().st_size
        except:
            pass
        return total
    
    # ========== Formatted Output Methods (for CLI) ==========
    
    def format_brain_list_table(self) -> str:
        """
        Format saved Brains as a table for CLI display.
        
        Returns:
            Formatted table string (ASCII)
        """
        brains = self.list_brains()
        
        if not brains:
            return "No saved Brains found in brain_bank."
        
        # Build table
        headers = ['Name', 'Domain', 'Version', 'Level', 'Saved At', 'Size']
        rows = []
        
        for brain in brains:
            saved_at = brain.get('saved_at', brain.get('created_at', 'N/A'))
            if saved_at:
                # Truncate timestamp to date only
                saved_at = saved_at[:10] if isinstance(saved_at, str) else 'N/A'
            else:
                saved_at = 'N/A'
            
            size_mb = brain['size_bytes'] / (1024 * 1024)
            size_str = f"{size_mb:.1f}MB"
            
            rows.append([
                brain['name'],
                brain['domain']  or '(blank)',
                brain['version'] or 'N/A',
                str(brain['level']),
                saved_at,
                size_str,
            ])
        
        # Calculate column widths
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, col in enumerate(row):
                col_widths[i] = max(col_widths[i], len(col))
        
        # Build table string
        lines = []
        
        # Header
        header_row = ' | '.join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
        lines.append(header_row)
        lines.append('-' * len(header_row))
        
        # Data rows
        for row in rows:
            data_row = ' | '.join(col.ljust(col_widths[i]) for i, col in enumerate(row))
            lines.append(data_row)
        
        return '\n'.join(lines)
    
    def get_brain_stats(self) -> Dict[str, Any]:
        """
        Get statistics about saved Brains.
        
        Returns:
            Dict with stats: count, total_size, domains, versions
        """
        brains = self.list_brains()
        
        total_size = sum(b['size_bytes'] for b in brains)
        domains = set(b['domain'] for b in brains if b['domain'])
        versions = {}
        
        for brain in brains:
            domain = brain['domain'] or '(blank)'
            versions[domain] = versions.get(domain, 0) + 1
        
        return {
            'total_brains': len(brains),
            'total_size_bytes': total_size,
            'total_size_mb': total_size / (1024 * 1024),
            'unique_domains': len(domains),
            'domains': sorted(list(domains)),
            'brains_per_domain': versions,
        }
    
    def format_brain_stats(self) -> str:
        """Format Brain statistics as text"""
        stats = self.get_brain_stats()
        
        lines = [
            "=" * 50,
            "Brain Bank Statistics",
            "=" * 50,
            f"Total Brains: {stats['total_brains']}",
            f"Total Size: {stats['total_size_mb']:.1f} MB",
            f"Unique Domains: {stats['unique_domains']}",
            "",
            "Brains per Domain:",
        ]
        
        for domain, count in sorted(stats['brains_per_domain'].items()):
            lines.append(f"  {domain}: {count}")
        
        return '\n'.join(lines)
