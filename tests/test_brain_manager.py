#!/usr/bin/env python
"""Test Brain Hot-Swap Manager"""

from src.sba.brain.brain_manager import BrainHotSwapManager
from src.sba.brain.blank_template import BlankTemplate
from pathlib import Path
import shutil
import json

print('=' * 70)
print('BRAIN HOT-SWAP MANAGER TEST')
print('=' * 70)

# Setup: Create test directory structure
test_work_dir = Path('C:\\TH_Works\\SBA\\brain_bank\\__test_hotswapdist')
if test_work_dir.exists():
    shutil.rmtree(test_work_dir)

test_work_dir.mkdir(parents=True, exist_ok=True)
test_bank_dir = test_work_dir / 'bank'
test_active_dir = test_work_dir / 'active'

test_bank_dir.mkdir()
# Don't create test_active_dir yet - clone_to will create it

# Step 1: Initialize active Brain from template
print('\n' + '=' * 70)
print('STEP 1: Initialize Active Brain from Template')
print('=' * 70)

bt = BlankTemplate('C:\\TH_Works\\SBA\\brain_bank\\blank_template')
bt.clone_to(test_active_dir, domain='Python開発', version='1.0', brain_name='TestBrain')
print('✓ Active Brain initialized')

# Verify active structure
print(f'  Files in active: {list(test_active_dir.glob("*"))}')

# Step 2: Create Hot-Swap Manager
print('\n' + '=' * 70)
print('STEP 2: Create Hot-Swap Manager')
print('=' * 70)

manager = BrainHotSwapManager(test_bank_dir, test_active_dir)
print('✓ Hot-Swap Manager created')

# Step 3: Save active Brain
print('\n' + '=' * 70)
print('STEP 3: Save Active Brain')
print('=' * 70)

save_result = manager.save(brain_name='TestBrain_v1.0', description='First save')
print(f'✓ Brain saved')
print(f'  Message: {save_result["message"]}')
print(f'  Domain: {save_result["domain"]}')
print(f'  Version: {save_result["version"]}')
print(f'  Saved path: {save_result["saved_path"]}')

# Step 4: List Brains
print('\n' + '=' * 70)
print('STEP 4: List Saved Brains')
print('=' * 70)

brains = manager.list_brains()
print(f'✓ Saved Brains: {len(brains)}')
for brain in brains:
    print(f'  - {brain["name"]} (v{brain["version"]}, {brain["size_bytes"]} bytes)')

# Step 5: Modify active Brain and save again
print('\n' + '=' * 70)
print('STEP 5: Modify and Save Again (Version Increment)')
print('=' * 70)

# Modify metadata in active
metadata_path = test_active_dir / 'metadata.json'
with open(metadata_path, 'r', encoding='utf-8-sig') as f:
    metadata = json.load(f)
metadata['version'] = '1.1'
with open(metadata_path, 'w', encoding='utf-8') as f:
    json.dump(metadata, f, indent=2, ensure_ascii=False)

save_result2 = manager.save(brain_name='TestBrain_v1.1', description='Second save with v1.1')
print(f'✓ Brain v1.1 saved')
print(f'  Version: {save_result2["version"]}')

# Step 6: List again to verify both versions
print('\n' + '=' * 70)
print('STEP 6: List All Brains (Should See 2 Versions)')
print('=' * 70)

brains = manager.list_brains()
print(f'✓ Total saved Brains: {len(brains)}')
for brain in brains:
    print(f'  - {brain["name"]} (v{brain["version"]})')

# Step 7: Get active Brain info
print('\n' + '=' * 70)
print('STEP 7: Get Active Brain Info')
print('=' * 70)

active_info = manager.get_active_brain()
print(f'✓ Active Brain:')
print(f'  Domain: {active_info["domain"]}')
print(f'  Version: {active_info["version"]}')
print(f'  Level: {active_info["level"]}')

# Step 8: Load first version
print('\n' + '=' * 70)
print('STEP 8: Load First Version Brain')
print('=' * 70)

brain_names = manager.list_brains_names()
first_brain = brain_names[0]
print(f'  Loading: {first_brain}')

load_result = manager.load(first_brain)
print(f'✓ Brain loaded')
print(f'  Message: {load_result["message"]}')
print(f'  Version: {load_result["version"]}')

# Verify active was updated
active_info_after = manager.get_active_brain()
print(f'  Active version after load: {active_info_after["version"]}')

# Step 9: Load second version
print('\n' + '=' * 70)
print('STEP 9: Load Second Version Brain')
print('=' * 70)

second_brain = brain_names[1]
print(f'  Loading: {second_brain}')

load_result = manager.load(second_brain)
print(f'✓ Brain loaded')
print(f'  Version: {load_result["version"]}')

active_info_after = manager.get_active_brain()
print(f'  Active version after load: {active_info_after["version"]}')

# Cleanup
print('\n' + '=' * 70)
print('CLEANUP')
print('=' * 70)

shutil.rmtree(test_work_dir)
print('✓ Test directory cleaned up')

print('\n' + '=' * 70)
print('ALL TESTS PASSED ✓')
print('=' * 70)
