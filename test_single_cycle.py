#!/usr/bin/env python
"""1サイクルテスト実行スクリプト"""

import asyncio
from src.sba.scheduler.scheduler import build_learning_runtime

def run_test():
    runtime = build_learning_runtime()
    try:
        result = asyncio.run(runtime["learning_loop"].run_single_cycle())
    
        print("=" * 70)
        print("1-CYCLE TEST RESULTS")
        print("=" * 70)
        print(f"step2_resources =          {result.step2_resources}")
        print(f"step3_chunks_stored =      {result.step3_chunks_stored}")
        print(f"step4_result =             {result.step4_result}")
        print(f"step5_contradictions =     {result.step5_contradictions}")
        print(f"step6_score =              {result.step6_overall_score}")
        print(f"step6_level =              {result.step6_level}")
        print(f"error =                    {result.error}")
        print("=" * 70)
        
    finally:
        runtime["close"]()

if __name__ == "__main__":
    run_test()
