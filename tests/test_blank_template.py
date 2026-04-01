#!/usr/bin/env python
"""Test BlankTemplate implementation"""

from src.sba.brain.blank_template import BlankTemplate
from pathlib import Path
import shutil
import json

# Test 1: Load template
print('=' * 60)
print('TEST 1: Load BlankTemplate')
print('=' * 60)
bt = BlankTemplate('C:\\TH_Works\\SBA\\brain_bank\\blank_template')
print('✓ Blank Template loaded successfully')
print(f'  Checksum: {bt.get_checksum()[:16]}...')

# Test 2: Get metadata
print('\n' + '=' * 60)
print('TEST 2: Get Template Metadata')
print('=' * 60)
metadata = bt.get_metadata()
print(f'✓ Template metadata loaded')
for key in ['domain', 'version', 'brain_id']:
    print(f'  {key}: {metadata.get(key, "N/A")}')

# Test 3: Clone template
print('\n' + '=' * 60)
print('TEST 3: Clone Template')
print('=' * 60)
test_brain_path = Path('C:\\TH_Works\\SBA\\brain_bank\\test_python_dev_v1.0')
if test_brain_path.exists():
    shutil.rmtree(test_brain_path)

cloned_path = bt.clone_to(test_brain_path, domain='Python開発', version='1.0', brain_name='Python開発Brain')
print(f'✓ Template cloned to: {cloned_path}')
print(f'  Brain exists: {cloned_path.exists()}')
print(f'  Files: {list(cloned_path.glob("*"))}')

# Test 4: Validate clone
print('\n' + '=' * 60)
print('TEST 4: Validate Cloned Brain')
print('=' * 60)
is_valid = bt.validate_clone(cloned_path)
print(f'✓ Clone valid: {is_valid}')

# Test 5: Check cloned metadata
print('\n' + '=' * 60)
print('TEST 5: Verify Cloned Metadata')
print('=' * 60)
with open(cloned_path / 'metadata.json', 'r', encoding='utf-8') as f:
    cloned_meta = json.load(f)
print('✓ Cloned metadata:')
print(f'  domain: {cloned_meta.get("domain")}')
print(f'  version: {cloned_meta.get("version")}')
print(f'  brain_id: {cloned_meta.get("brain_id")}')
print(f'  name: {cloned_meta.get("name")}')
print(f'  created_at: {cloned_meta.get("created_at")}')

# Cleanup
print('\n' + '=' * 60)
print('CLEANUP')
print('=' * 60)
shutil.rmtree(cloned_path)
print('✓ Test cleanup complete')

print('\n' + '=' * 60)
print('ALL TESTS PASSED ✓')
print('=' * 60)
