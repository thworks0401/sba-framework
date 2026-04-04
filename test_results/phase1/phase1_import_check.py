import importlib
import json
modules = [
    "sba",
    "sba.brain.brain_package",
    "sba.brain.blank_template",
    "sba.brain.brain_manager",
    "sba.cli.brain_cmds",
]
result = {}
for name in modules:
    try:
        importlib.import_module(name)
        result[name] = {"status": "PASS"}
    except Exception as e:
        result[name] = {"status": "FAIL", "error": str(e)}
print(json.dumps(result, ensure_ascii=False))
