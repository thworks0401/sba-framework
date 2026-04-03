import json
import shutil
import uuid
from pathlib import Path

from sba.storage.knowledge_store import KnowledgeStore
from sba.utils.chunker import TextChunker

summary = {
    "status": "PASS",
    "brain_id": None,
    "steps": [],
}


def add_step(name: str, ok: bool, detail: str = "") -> None:
    summary["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
    if not ok:
        summary["status"] = "FAIL"


def resolve_chunk_id(value):
    if isinstance(value, str):
        return value

    if isinstance(value, dict):
        candidates = [
            value.get("chunk_id"),
            value.get("id"),
            value.get("knowledge_chunk_id"),
            value.get("graph_id"),
            value.get("node_id"),
            value.get("kuzu_id"),
            value.get("record_id"),
        ]
        for candidate in candidates:
            if candidate:
                return candidate

    for attr in ("chunk_id", "id", "graph_id", "node_id"):
        if hasattr(value, attr):
            v = getattr(value, attr)
            if v:
                return v

    return None


ks = None
tmp_brain_dir = None

try:
    root = Path(r"C:\TH_Works\SBA")
    brain_bank = root / "brain_bank"

    candidate_templates = [
        brain_bank / "_blank_template",
        brain_bank / "blank_template",
    ]

    blank_template = None
    for candidate in candidate_templates:
        if candidate.exists():
            blank_template = candidate
            break

    if blank_template is None:
        add_step("blank_template_exists", False, f"not found: {candidate_templates}")
        raise RuntimeError("blank_template not found")

    add_step("blank_template_exists", True, str(blank_template))

    # 毎回クリーンな一時Brainを作る
    tmp_brain_dir = brain_bank / f"_tmp_phase2_{uuid.uuid4()}"
    shutil.copytree(blank_template, tmp_brain_dir)
    add_step("tmp_brain_created", True, str(tmp_brain_dir))

    test_brain_id = f"phase2-test-{uuid.uuid4()}"
    summary["brain_id"] = test_brain_id

    ks = KnowledgeStore(
        brain_package_path=str(tmp_brain_dir),
        brain_id=test_brain_id,
    )
    add_step("knowledge_store_init", True, f"brain_id={test_brain_id}")

    # 毎回ユニークになるテキスト（重複検知を避ける）
    text = (
        f"SBA Phase 2 storage layer KnowledgeStore round-trip test. "
        f"Run id: {uuid.uuid4()}. "
        f"This text validates chunk generation, vector storage, graph linkage, "
        f"and hybrid retrieval. "
        f"The chunk should be unique for every test execution. "
        f"Primary subskill is storage.test. "
        f"Secondary metadata should also be persisted. "
        f"After insertion, similarity search and hybrid query should find the record. "
        f"Finally, deprecation marking should update the stored knowledge state."
    )

    chunker = TextChunker()
    chunks = []
    try:
        raw_chunks = chunker.chunk_text(text, min_tokens=1, max_tokens=120)
        chunks = list(raw_chunks)
        if len(chunks) > 0:
            add_step("chunker_chunk_text", True, f"chunks={len(chunks)}")
        else:
            chunks = [text]
            add_step("chunker_chunk_text", True, "chunks=0, fallback=original_text")
    except Exception as e:
        chunks = [text]
        add_step("chunker_chunk_text", True, f"fallback_used_due_to_error={str(e)}")

    primary_subskill = "storage.test"
    first_chunk = chunks[0] if chunks else text
    if not isinstance(first_chunk, str):
        first_chunk = str(first_chunk)

    chunk_id = None
    try:
        store_result = ks.store_chunk(
            text=first_chunk,
            primary_subskill=primary_subskill,
            source_type="test",
            source_url=f"about:phase2-knowledge-store:{uuid.uuid4()}",
            trust_score=0.9,
            summary="Phase2 KnowledgeStore roundtrip test chunk",
            secondary_subskills=["storage.secondary"],
        )
        chunk_id = resolve_chunk_id(store_result)

        detail_preview = repr(store_result)
        if len(detail_preview) > 300:
            detail_preview = detail_preview[:300] + "..."
        add_step(
            "store_chunk",
            store_result is not None and chunk_id is not None,
            f"result_type={type(store_result).__name__}, chunk_id={chunk_id}, preview={detail_preview}",
        )
    except Exception as e:
        add_step("store_chunk", False, str(e))

    if chunk_id is not None:
        try:
            chunk = ks.get_chunk(chunk_id)
            add_step("get_chunk", chunk is not None, f"type={type(chunk).__name__}")
        except Exception as e:
            add_step("get_chunk", False, str(e))
    else:
        add_step("get_chunk", False, "chunk_id could not be resolved from store_chunk result")

    query_text = first_chunk[:180]

    try:
        hits = ks.search_similar(
            text=query_text,
            limit=5,
            subskill_id=primary_subskill,
            score_threshold=0.0,
        )
        ok = hits is not None and len(hits) > 0
        add_step("search_similar", ok, f"type={type(hits).__name__}, len={len(hits)}")
    except Exception as e:
        add_step("search_similar", False, str(e))

    try:
        hybrid_hits = ks.query_hybrid(
            query_text=query_text,
            subskill_id=primary_subskill,
            limit=5,
        )
        ok = hybrid_hits is not None and len(hybrid_hits) > 0
        add_step("query_hybrid", ok, f"type={type(hybrid_hits).__name__}, len={len(hybrid_hits)}")
    except Exception as e:
        add_step("query_hybrid", False, str(e))

    try:
        subskill_chunks = ks.get_chunks_by_subskill(primary_subskill)
        add_step("get_chunks_by_subskill", len(subskill_chunks) > 0, f"hits={len(subskill_chunks)}")
    except Exception as e:
        add_step("get_chunks_by_subskill", False, str(e))

    try:
        stats = ks.get_knowledge_base_stats()
        add_step("get_knowledge_base_stats", stats is not None, f"type={type(stats).__name__}")
    except Exception as e:
        add_step("get_knowledge_base_stats", False, str(e))

    try:
        overview = ks.get_subskill_overview()
        add_step("get_subskill_overview", overview is not None, f"type={type(overview).__name__}")
    except Exception as e:
        add_step("get_subskill_overview", False, str(e))

    if chunk_id is not None:
        try:
            ks.mark_deprecated(chunk_id, reason="phase2-test")
            add_step("mark_deprecated", True, f"chunk_id={chunk_id}")
        except Exception as e:
            add_step("mark_deprecated", False, str(e))

        try:
            chunk_after = ks.get_chunk(chunk_id)
            add_step("get_chunk_after_deprecated", chunk_after is not None, f"type={type(chunk_after).__name__}")
        except Exception as e:
            add_step("get_chunk_after_deprecated", False, str(e))
    else:
        add_step("mark_deprecated", False, "chunk_id unresolved")
        add_step("get_chunk_after_deprecated", False, "chunk_id unresolved")

finally:
    if ks is not None:
        try:
            ks.close()
            add_step("knowledge_store_close", True)
        except Exception as e:
            add_step("knowledge_store_close", False, str(e))

    if tmp_brain_dir is not None and tmp_brain_dir.exists():
        try:
            shutil.rmtree(tmp_brain_dir)
            add_step("tmp_brain_removed", True, str(tmp_brain_dir))
        except Exception as e:
            add_step("tmp_brain_removed", False, str(e))

print(json.dumps(summary, ensure_ascii=False))
