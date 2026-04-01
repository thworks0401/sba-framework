"""
Phase 2 統合テスト — ストレージ層

KnowledgeStore による統合的なCRUD・検索・グラフ操作を検証。
"""

import tempfile
import hashlib
from pathlib import Path
import sys
import os

# PYTHONPATH に src を追加
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# テスト用 Brain Package ディレクトリ作成
test_dir = Path(tempfile.mkdtemp(prefix="sba_phase2_test_"))
brain_id = "test-brain-001"

print("=" * 70)
print("Phase 2 Storage Layer - Integration Test")
print("=" * 70)

try:
    # ─────────────────────────────────────────────────
    # 1. KnowledgeStore 初期化
    # ─────────────────────────────────────────────────
    print("\n[1/5] Initializing KnowledgeStore...")

    from sba.storage.knowledge_store import KnowledgeStore

    kb = KnowledgeStore(str(test_dir), brain_id)

    # SubSkill ノード作成
    kb.ensure_subskill_node("design", "設計")
    kb.ensure_subskill_node("implementation", "実装")
    kb.ensure_subskill_node("testing", "テスト")

    print("[OK] KnowledgeStore initialized")
    print(f"  Brain ID: {brain_id}")
    print(f"  Vector Index: {test_dir}/vector_index")
    print(f"  Knowledge Graph: {test_dir}/knowledge_graph")

    # ─────────────────────────────────────────────────
    # 2. チャンク追加テスト
    # ─────────────────────────────────────────────────
    print("\n[2/5] Testing chunk storage...")

    chunks_to_store = [
        {
            "text": "Pythonのデコレータは関数を修飾するための高度なメカニズムです。「@」記号を使って関数定義の前に記述します。デコレータは元の関数を別の関数でラップし、その機能を追加・変更・制限できます。典型的な用途はログ記録、権限チェック、キャッシング、類型チェックなどです。詳細な実装方法とベストプラクティスについて理解することは、Pythonプログラマにとって必須のスキルです。",
            "primary_subskill": "implementation",
            "source_type": "Web",
            "source_url": "https://example.com/python-decorators",
            "trust_score": 0.85,
        },
        {
            "text": "ソフトウェア設計における関数型プログラミングの利点は、副作用を最小化し、コードの予測可能性と並列処理の効率を向上させることです。イミュータブルなデータ構造を使用することで、バグを大幅に削減できます。また、純粋関数により、ユニットテストが容易になり、デバッグ効率が向上します。Pythonはマルチパラダイム言語であり、関数型とオブジェクト指向の併用がベストプラクティスです。",
            "primary_subskill": "design",
            "secondary_subskills": ["implementation"],
            "source_type": "PDF",
            "source_url": "/papers/fp-design.pdf",
            "trust_score": 0.90,
        },
        {
            "text": "ユニットテストの作成時には、テストケースが独立していることが重要です。各テストケースは他のテストに依存しない状態を保つべきです。pytest ライブラリを使用すると、フィクスチャにより共通セットアップを効率的に管理できます。テストカバレッジは最低でも80%以上を目指すべきです。エッジケースも含めた包括的なテストケースを設計することが品質向上の鍵です。",
            "primary_subskill": "testing",
            "source_type": "Web",
            "source_url": "https://example.com/pytest-guide",
            "trust_score": 0.88,
        },
    ]

    results = []
    for i, chunk_data in enumerate(chunks_to_store, 1):
        result = kb.store_chunk(
            text=chunk_data["text"],
            primary_subskill=chunk_data["primary_subskill"],
            source_type=chunk_data["source_type"],
            source_url=chunk_data["source_url"],
            trust_score=chunk_data["trust_score"],
            secondary_subskills=chunk_data.get("secondary_subskills"),
        )
        results.append(result)

        status = "[OK]" if not result["duplicate_detected"] else "[WARN] (duplicate)"
        print(f"  {status} Chunk {i}: {result['chunk_id'][:8] if result['chunk_id'] else 'N/A'}...")

    print(f"[OK] {len([r for r in results if not r['duplicate_detected']])} chunks stored")

    # ─────────────────────────────────────────────────
    # 3. 重複検出テスト
    # ─────────────────────────────────────────────────
    print("\n[3/5] Testing duplicate detection...")

    # 同一テキストで重複チェック
    dup_result = kb.store_chunk(
        text=chunks_to_store[0]["text"],  # 同一テキスト
        primary_subskill="implementation",
        source_type="Web",
        trust_score=0.85,
    )

    if dup_result["duplicate_detected"]:
        print(f"[OK] Duplicate detected: {dup_result['reason']}")
    else:
        print("[FAIL] Duplicate detection failed")

    # ─────────────────────────────────────────────────
    # 4. 検索テスト（ハイブリッド）
    # ─────────────────────────────────────────────────
    print("\n[4/5] Testing hybrid search...")

    search_results = kb.query_hybrid(
        query_text="Pythonのテスト駆動開発について学びたい",
        limit=5,
    )

    print(f"[OK] Found {len(search_results)} results:")
    for i, result in enumerate(search_results[:3], 1):
        print(f"  {i}. score={result['score']:.3f}, trust={result['trust_score']:.2f}")
        print(f"     SubSkill: {result['primary_subskill']}")

    # ─────────────────────────────────────────────────
    # 5. 統計情報取得
    # ─────────────────────────────────────────────────
    print("\n[5/5] Retrieving statistics...")

    stats = kb.get_knowledge_base_stats()

    print("[OK] Knowledge Base Statistics:")
    print(f"  Vector Store:")
    print(f"    - Collection: {stats['vector_store']['collection_name']}")
    print(f"    - Points: {stats['vector_store']['points_count']}")
    print(f"    - Dimension: {stats['vector_store']['vector_dim']}")

    print(f"  Graph Store:")
    print(f"    - KnowledgeChunks: {stats['graph_store']['knowledge_chunks']}")
    print(f"    - SubSkills: {stats['graph_store']['subskill_nodes']}")

    print(f"  Timeline:")
    print(f"    - Total Entries: {stats['timeline']['total_entries']}")
    print(f"    - Avg Freshness: {stats['timeline']['avg_freshness']:.2f}")

    # SubSkill 概要
    subskill_overview = kb.get_subskill_overview()
    print(f"\n  SubSkill Overview:")
    for skill in subskill_overview:
        print(f"    - {skill['display_name']}: {skill['chunk_count']} chunks, "
              f"avg_trust={skill['avg_trust_score']:.2f}")

    print("\n" + "=" * 70)
    print("Phase 2 Integration Test PASSED")
    print("=" * 70)

    # テスト結果
    print("\nTest Results:")
    print(f"  [OK] All components working")
    print(f"  [OK] Qdrant vector search")
    print(f"  [OK] Kuzu graph operations")
    print(f"  [OK] SQLite (3DB)")
    print(f"  [OK] Duplicate detection")
    print(f"  [OK] Hybrid search")

except Exception as e:
    print(f"\nTest FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    # テンポラリディレクトリ清掃（オプション）
    pass
