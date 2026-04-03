import json
from sba.utils.embedder import Embedder

sample_texts = [
    "SBA Framework Phase 2 storage layer test.",
    "Python 3.11 + Qdrant + Kuzu + SQLite + bge-m3.",
]

result = {}
try:
    emb = Embedder.get_instance()
    vecs = emb.encode(sample_texts)
    rows = len(vecs)
    cols = len(vecs[0]) if rows > 0 else 0
    result["status"] = "PASS"
    result["shape"] = [rows, cols]
except Exception as e:
    result["status"] = "FAIL"
    result["error"] = str(e)

print(json.dumps(result, ensure_ascii=False))
