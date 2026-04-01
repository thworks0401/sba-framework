"""
Kuzu グラフストア実装

設計根拠（補足設計書 §1.2.2）:
  - Graph DB: Kuzu（組み込み型グラフDB）
  - ディレクトリベース保存（Hot-Swap に最適）
  - Cypher クエリ対応
  - ノード・エッジの CRUD
"""

from __future__ import annotations

import uuid
from pathlib import Path
from datetime import datetime
import json

import kuzu


class GraphStoreError(Exception):
    """GraphStore 操作に関する例外"""


class KuzuGraphStore:
    """
    Kuzu グラフストア。

    ノード: KnowledgeChunk, SubSkillNode, ConceptNode
    エッジ: BELONGS_TO_PRIMARY, RELATED_TO_SECONDARY, CONTRADICTS, UPDATES, DERIVED_FROM, SUBSKILL_RELATED
    """

    def __init__(self, knowledge_graph_path: str, brain_id: str) -> None:
        """
        Initialize Kuzu database.

        Args:
            knowledge_graph_path: Kuzu DB ディレクトリパス（例: /path/to/knowledge_graph）
            brain_id: Brain UUID

        Note:
            Kuzu v0.6+ のAPI に従い、ディレクトリ内に kuzu.db ファイルを作成。
            Hot-Swap 時はディレクトリ全体をコピーして移植性を確保。
        """
        self.knowledge_graph_path = Path(knowledge_graph_path)
        self.knowledge_graph_path.mkdir(parents=True, exist_ok=True)

        self.brain_id = brain_id

        # Kuzu DB ファイルパス（ディレクトリ内の kuzu.db）
        db_file_path = self.knowledge_graph_path / "kuzu.db"

        # Kuzu DB + Connection 接続
        self.db = kuzu.Database(str(db_file_path))
        self.conn = kuzu.Connection(self.db)

        # スキーマ初期化
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """スキーマが存在しなければ作成"""
        try:
            # ノードテーブル確認
            self.conn.execute("MATCH (n:KnowledgeChunk) RETURN COUNT(*) LIMIT 1")
        except Exception:
            # テーブル非存在 → 新規作成
            self._create_schema()

    def _create_schema(self) -> None:
        """Kuzu スキーマを初回起動時に自動生成（補足設計書 §2.2）"""
        # KnowledgeChunk ノード
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS KnowledgeChunk (
                id STRING PRIMARY KEY,
                text STRING,
                summary STRING,
                trust_score DOUBLE,
                source_url STRING,
                source_type STRING,
                primary_subskill STRING,
                acquired_at STRING,
                is_deprecated BOOLEAN DEFAULT false,
                is_contradicted BOOLEAN DEFAULT false,
                requires_human_review BOOLEAN DEFAULT false,
                qdrant_id STRING
            )
        """)

        # SubSkillNode
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS SubSkillNode (
                id STRING PRIMARY KEY,
                display_name STRING,
                density_score DOUBLE DEFAULT 0.0
            )
        """)

        # ConceptNode
        self.conn.execute("""
            CREATE NODE TABLE IF NOT EXISTS ConceptNode (
                id STRING PRIMARY KEY,
                name STRING,
                definition STRING
            )
        """)

        # エッジテーブル
        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS BELONGS_TO_PRIMARY (
                FROM KnowledgeChunk TO SubSkillNode
            )
        """)

        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS RELATED_TO_SECONDARY (
                FROM KnowledgeChunk TO SubSkillNode,
                relevance DOUBLE DEFAULT 0.5
            )
        """)

        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS CONTRADICTS (
                FROM KnowledgeChunk TO KnowledgeChunk,
                detected_at STRING
            )
        """)

        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS UPDATES (
                FROM KnowledgeChunk TO KnowledgeChunk
            )
        """)

        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS DERIVED_FROM (
                FROM KnowledgeChunk TO KnowledgeChunk
            )
        """)

        self.conn.execute("""
            CREATE REL TABLE IF NOT EXISTS SUBSKILL_RELATED (
                FROM SubSkillNode TO SubSkillNode,
                relation_type STRING
            )
        """)

    # ======================================================================
    # KnowledgeChunk CRUD
    # ======================================================================

    def add_knowledge_chunk(
        self,
        text: str,
        trust_score: float,
        primary_subskill: str,
        source_type: str,
        source_url: str = "",
        summary: str = "",
        qdrant_id: str = "",
    ) -> str:
        """
        新規 KnowledgeChunk を追加。

        Returns:
            chunk_id (UUID)
        """
        chunk_id = str(uuid.uuid4())
        acquired_at = datetime.utcnow().isoformat() + "Z"

        query = """
            CREATE (n:KnowledgeChunk {
                id: $chunk_id,
                text: $text,
                summary: $summary,
                trust_score: $trust_score,
                source_url: $source_url,
                source_type: $source_type,
                primary_subskill: $primary_subskill,
                acquired_at: $acquired_at,
                is_deprecated: false,
                is_contradicted: false,
                requires_human_review: false,
                qdrant_id: $qdrant_id
            })
        """

        self.conn.execute(
            query,
            {
                "chunk_id": chunk_id,
                "text": text,
                "summary": summary,
                "trust_score": trust_score,
                "source_url": source_url,
                "source_type": source_type,
                "primary_subskill": primary_subskill,
                "acquired_at": acquired_at,
                "qdrant_id": qdrant_id,
            },
        )

        return chunk_id

    def get_knowledge_chunk(self, chunk_id: str) -> dict | None:
        """KnowledgeChunk 取得"""
        result = self.conn.execute(
            "MATCH (n:KnowledgeChunk) WHERE n.id = $id RETURN n",
            {"id": chunk_id},
        )

        rows = result.fetch_all()
        if not rows:
            return None

        node = rows[0][0]
        return {
            "id": node["id"],
            "text": node["text"],
            "summary": node["summary"],
            "trust_score": node["trust_score"],
            "source_url": node["source_url"],
            "source_type": node["source_type"],
            "primary_subskill": node["primary_subskill"],
            "acquired_at": node["acquired_at"],
            "is_deprecated": node["is_deprecated"],
            "is_contradicted": node["is_contradicted"],
            "requires_human_review": node["requires_human_review"],
            "qdrant_id": node["qdrant_id"],
        }

    def update_knowledge_chunk(self, chunk_id: str, **kwargs) -> None:
        """KnowledgeChunk 更新"""
        updates = []
        params = {"id": chunk_id}

        for key, value in kwargs.items():
            if key in ["text", "summary", "trust_score", "source_url", "source_type",
                       "is_deprecated", "is_contradicted", "requires_human_review"]:
                updates.append(f"SET n.{key} = ${key}")
                params[key] = value

        if not updates:
            return

        query = f"MATCH (n:KnowledgeChunk) WHERE n.id = $id {' '.join(updates)}"
        self.conn.execute(query, params)

    def delete_knowledge_chunk(self, chunk_id: str) -> None:
        """KnowledgeChunk 削除"""
        self.conn.execute(
            "MATCH (n:KnowledgeChunk) WHERE n.id = $id DELETE n",
            {"id": chunk_id},
        )

    def get_chunks_by_subskill(self, subskill_id: str, limit: int = 100) -> list[dict]:
        """
        SubSkill 別にチャンク一覧取得。

        Returns:
            KnowledgeChunk リスト
        """
        result = self.conn.execute(
            """
            MATCH (chunk:KnowledgeChunk) WHERE chunk.primary_subskill = $subskill_id
            RETURN chunk LIMIT $limit
            """,
            {"subskill_id": subskill_id, "limit": limit},
        )

        chunks = []
        for row in result.fetch_all():
            node = row[0]
            chunks.append({
                "id": node["id"],
                "text": node["text"],
                "trust_score": node["trust_score"],
                "source_type": node["source_type"],
                "source_url": node["source_url"],
                "is_deprecated": node["is_deprecated"],
            })

        return chunks

    # ======================================================================
    # SubSkillNode CRUD
    # ======================================================================

    def add_subskill_node(self, subskill_id: str, display_name: str) -> None:
        """SubSkillNode 追加"""
        query = """
            MERGE (n:SubSkillNode {id: $subskill_id})
            SET n.display_name = $display_name
        """
        self.conn.execute(query, {"subskill_id": subskill_id, "display_name": display_name})

    def update_subskill_density(self, subskill_id: str, density_score: float) -> None:
        """SubSkill density_score 更新"""
        self.conn.execute(
            "MATCH (n:SubSkillNode) WHERE n.id = $id SET n.density_score = $score",
            {"id": subskill_id, "score": density_score},
        )

    # ======================================================================
    # エッジ操作
    # ======================================================================

    def add_belongs_to_primary(self, chunk_id: str, subskill_id: str) -> None:
        """KnowledgeChunk → SubSkill: BELONGS_TO_PRIMARY"""
        query = """
            MATCH (chunk:KnowledgeChunk {id: $chunk_id})
            MATCH (skill:SubSkillNode {id: $subskill_id})
            CREATE (chunk)-[r:BELONGS_TO_PRIMARY]->(skill)
        """
        self.conn.execute(query, {"chunk_id": chunk_id, "subskill_id": subskill_id})

    def add_related_to_secondary(
        self,
        chunk_id: str,
        subskill_id: str,
        relevance: float = 0.5,
    ) -> None:
        """KnowledgeChunk → SubSkill: RELATED_TO_SECONDARY"""
        query = """
            MATCH (chunk:KnowledgeChunk {id: $chunk_id})
            MATCH (skill:SubSkillNode {id: $subskill_id})
            CREATE (chunk)-[r:RELATED_TO_SECONDARY {relevance: $relevance}]->(skill)
        """
        self.conn.execute(
            query,
            {"chunk_id": chunk_id, "subskill_id": subskill_id, "relevance": relevance},
        )

    def add_contradicts(
        self,
        chunk_id_a: str,
        chunk_id_b: str,
    ) -> None:
        """KnowledgeChunk ↔ KnowledgeChunk: 矛盾関係（双方向）"""
        now = datetime.utcnow().isoformat() + "Z"
        query = """
            MATCH (a:KnowledgeChunk {id: $chunk_a})
            MATCH (b:KnowledgeChunk {id: $chunk_b})
            CREATE (a)-[r1:CONTRADICTS {detected_at: $now}]->(b)
            CREATE (b)-[r2:CONTRADICTS {detected_at: $now}]->(a)
        """
        self.conn.execute(query, {"chunk_a": chunk_id_a, "chunk_b": chunk_id_b, "now": now})

    def add_updates(self, old_chunk_id: str, new_chunk_id: str) -> None:
        """KnowledgeChunk: 旧→新 UPDATES エッジ"""
        query = """
            MATCH (old:KnowledgeChunk {id: $old_id})
            MATCH (new:KnowledgeChunk {id: $new_id})
            CREATE (new)-[r:UPDATES]->(old)
        """
        self.conn.execute(query, {"old_id": old_chunk_id, "new_id": new_chunk_id})

    def get_related_chunks(self, chunk_id: str) -> dict:
        """チャンクの関連チャンク・SubSkill を取得"""
        result = self.conn.execute(
            """
            MATCH (chunk:KnowledgeChunk {id: $id})
            OPTIONAL MATCH (chunk)-[:BELONGS_TO_PRIMARY]->(primary:SubSkillNode)
            OPTIONAL MATCH (chunk)-[:RELATED_TO_SECONDARY]->(secondary:SubSkillNode)
            RETURN chunk, primary, collect(secondary) as secondaries
            """,
            {"id": chunk_id},
        )

        rows = result.fetch_all()
        if not rows:
            return {}

        row = rows[0]
        chunk, primary, secondaries = row[0], row[1], row[2]

        return {
            "chunk_id": chunk["id"],
            "primary_subskill": primary["id"] if primary else None,
            "secondary_subskills": [s["id"] for s in secondaries if s],
        }

    def mark_deprecated(self, chunk_id: str) -> None:
        """チャンクを deprecated マーク"""
        self.conn.execute(
            "MATCH (n:KnowledgeChunk {id: $id}) SET n.is_deprecated = true",
            {"id": chunk_id},
        )

    def mark_requires_review(self, chunk_id: str) -> None:
        """チャンクを requires_human_review マーク"""
        self.conn.execute(
            "MATCH (n:KnowledgeChunk {id: $id}) SET n.requires_human_review = true",
            {"id": chunk_id},
        )

    def get_all_subskill_nodes(self) -> list[dict]:
        """全 SubSkillNode 取得"""
        result = self.conn.execute("MATCH (n:SubSkillNode) RETURN n")

        subskills = []
        for row in result.fetch_all():
            node = row[0]
            subskills.append({
                "id": node["id"],
                "display_name": node["display_name"],
                "density_score": node["density_score"],
            })

        return subskills

    def get_graph_stats(self) -> dict:
        """グラフ統計情報"""
        chunk_count = self.conn.execute(
            "MATCH (n:KnowledgeChunk) RETURN COUNT(*) as cnt"
        ).fetch_all()[0][0]

        subskill_count = self.conn.execute(
            "MATCH (n:SubSkillNode) RETURN COUNT(*) as cnt"
        ).fetch_all()[0][0]

        return {
            "knowledge_chunks": chunk_count,
            "subskill_nodes": subskill_count,
        }
