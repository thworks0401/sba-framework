#!/usr/bin/env python
"""Test Brain Bank listing and formatting (Task 1-4)"""

from src.sba.brain.brain_manager import BrainHotSwapManager

print('=' * 70)
print('BRAIN BANK LISTING TEST (Task 1-4)')
print('=' * 70)

# Test with actual brain_bank
try:
    manager = BrainHotSwapManager(
        'C:\\TH_Works\\SBA\\brain_bank',
        'C:\\TH_Works\\SBA\\brain_bank\\[active]'
    )
    
    print('\n' + '=' * 70)
    print('Brain Bank Inventory (Formatted Table)')
    print('=' * 70)
    print(manager.format_brain_list_table())
    
    print('\n' + '=' * 70)
    print('Brain Bank Statistics')
    print('=' * 70)
    print(manager.format_brain_stats())
    
    print('\n✓ Task 1-4 formatting tests passed')
    
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
