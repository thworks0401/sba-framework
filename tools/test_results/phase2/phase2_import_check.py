import importlib
import json

modules = [
    "sba.storage.vector_store",
    "sba.storage.graph_store",
    "sba.storage.knowledge_store",
    "sba.storage.experiment_db",
    "sba.storage.timeline_db",
    "sba.storage.api_usage_db",
    "sba.utils.embedder",
    "sba.utils.chunker",
]

result = {}
for name in modules:
    try:
        importlib.import_module(name)
        result[name] = {"status": "PASS"}
    except Exception as e:
        result[name] = {"status": "FAIL", "error": str(e)}

print(json.dumps(result, ensure_ascii=False))
