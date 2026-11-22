"""Read-only audit probes. No HTTP server, model calls, or production writes.

Run from the repository root with Python 3.12 + the existing numpy/openpyxl.
The database module is loaded from its AST without the unused pandas import;
its connection factories are replaced with SQLite read-only connections.
Server functions are extracted from AST without importing FastAPI or startup.
These are targeted probes, not a production environment benchmark.
"""
import ast
import asyncio
import collections
import contextlib
import copy
import json
import logging
import pathlib
import re
import sqlite3
import sys
import types
import unittest
from typing import Any, Dict, List, Optional

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = pathlib.Path(__file__).with_name("audit_20261112_rerun_results.json")
results = {}


def record(key, value):
    results[key] = value
    print(key, json.dumps(value, ensure_ascii=False))


def ro_connection():
    conn = sqlite3.connect((ROOT / "app/materials.db").as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def extracted(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)


# Only replace the import needed for Excel imports, which these probes never use.
db_tree = ast.parse((ROOT / "app/db_manager.py").read_text(encoding="utf-8-sig"))
db_tree.body = [n for n in db_tree.body if not (isinstance(n, ast.Import) and any(a.name == "pandas" for a in n.names))]
db = types.ModuleType("app.db_manager")
db.__file__ = str(ROOT / "app/db_manager.py")
sys.modules[db.__name__] = db
exec(compile(db_tree, db.__file__, "exec"), db.__dict__)
db.get_thread_db_connection = ro_connection
db.get_db_connection = ro_connection

rule_tree = ast.parse((ROOT / "app/llm_rule_engine.py").read_text(encoding="utf-8-sig"))
# Suppress the module-level schema initializer before any code is executed.
rule_tree.body = [n for n in rule_tree.body if not (isinstance(n, ast.Try) and any(
    isinstance(x, ast.Expr) and isinstance(x.value, ast.Call) and isinstance(x.value.func, ast.Name)
    and x.value.func.id == "init_rule_engine_db" for x in n.body))]
rules = types.ModuleType("app.llm_rule_engine")
rules.__file__ = str(ROOT / "app/llm_rule_engine.py")
sys.modules[rules.__name__] = rules
exec(compile(rule_tree, rules.__file__, "exec"), rules.__dict__)
rules.get_db_conn = ro_connection
from app.query_preprocessor import extract_query_params
from app import matcher

suite = unittest.TestSuite(unittest.TestLoader().discover(str(ROOT / "tests"), pattern=p)
                           for p in ("test_query_preprocessor.py", "test_query_cleaning_pipeline.py"))
test_result = unittest.TextTestRunner(verbosity=1).run(suite)
record("existing_tests", {"run": test_result.testsRun, "failures": len(test_result.failures), "errors": len(test_result.errors)})

with ro_connection() as conn:
    materials = [dict(r) for r in conn.execute("SELECT * FROM materials")]
    codes = {r["code"] for r in materials}
    golden = json.loads((ROOT / "tests/fixtures/golden_set_v1.json").read_text(encoding="utf-8"))
    core = [x for x in golden["items"] if x["label_type"] == "1to1" and not x["ambiguous"] and not x.get("qc_suspect")]
    missing = [x for x in core if not any(c in codes for c in x["target_codes"])]
    record("data", {"materials": len(materials), "golden_snapshot_rows": golden["meta"]["material_db_snapshot"]["rows"],
                    "core": len(core), "missing_target": len(missing), "target_present": len(core)-len(missing),
                    "missing_by_professional": dict(collections.Counter(x["professional"] for x in missing)),
                    "duplicate_name_spec_groups": sum(v > 1 for v in collections.Counter((r["name"], r["specification"]) for r in materials).values()),
                    "flywheel_records": conn.execute("SELECT COUNT(*) FROM flywheel_records").fetchone()[0]})

record("negative_phrases", {spec: extract_query_params(name, spec, "暖通")["attrs"]
                           for name, spec in [("离心风机", "非防爆 风量1000m3/h"), ("风机盘管", "不带冷凝水提升泵")]})

ctx = matcher.ScoringContext(
    query_info={"name": "网络摄像机", "brand": "海康威视", "parsed_spec": {"attrs": {}, "model": ""}},
    candidate={"name": "网络摄像机", "brand": "", "parsed_params": json.dumps({"attrs": {"品牌": "海康威视"}}, ensure_ascii=False)})
blocked = matcher._check_initial_hard_conflicts(ctx)
record("same_brand_blocked", {"blocked": blocked, "candidate_brand": ctx.c_brand, "reasons": ctx.reasons})
record("unit_compatibility", {f"{a}/{b}": matcher.check_unit_compatibility(a, b) for a, b in [("m", "m2"), ("米", "平方米"), ("米", "台")]})
ctx = matcher.ScoringContext(query_info={"professional": "医用气体", "name": "氧气终端", "parsed_spec": {"attrs": {}}},
                            candidate={"name": "氧气终端", "category": "医用气体", "specification": "氧气终端"})
record("professional_alias_conflict", {"blocked": matcher._check_initial_hard_conflicts(ctx), "query_prof": ctx.q_prof, "candidate_prof": ctx.c_prof, "reasons": ctx.reasons})

saved_rules = rules.LLMRuleManager.get_active_rules
rules.LLMRuleManager.get_active_rules = classmethod(lambda cls: [{"scope_level": "discipline", "scope_target": "给排水", "parsed_dsl": {
    "extraction_rules": [{"key": "AUDIT_SENTINEL", "pattern": "(NEVER_MATCH)", "default_value": "EXPECTED"}]}}])
trace_errors = []
def trace(frame, event, arg):
    if event == "exception" and frame.f_code.co_name == "extract_query_params":
        trace_errors.append(str(arg[1]))
    return trace
sys.settrace(trace)
parsed = extract_query_params("阀门", "DN15", "给排水")
sys.settrace(None)
record("discipline_extraction", {"sentinel": parsed["attrs"].get("AUDIT_SENTINEL"), "swallowed_errors": trace_errors})

rules.LLMRuleManager.get_active_rules = classmethod(lambda cls: [{"scope_level": "universal", "parsed_dsl": {"matching_rules": {"score_boost_keywords": {"阀门": 100}}}}])
ctx = matcher.ScoringContext(query_info={}, candidate={}, base_score=0.95,
                            q_attrs={"管径": 15}, c_attrs={"管径": 25}, c_name="阀门",
                            domain_config={"hard_gates": {"conflict_caps": {"管径": 30}}})
matcher._apply_config_hard_gates(ctx)
cap = ctx.base_score
matcher._apply_dynamic_rules(ctx)
record("hard_cap_overridden", {"after_hard_gate": cap, "after_dynamic_boost": ctx.base_score, "reasons": ctx.reasons})
rules.LLMRuleManager.get_active_rules = saved_rules

# Exercise actual server batch function using deterministic candidate results.
server_ns = dict(List=List, Dict=Dict, Any=Any, Optional=Optional, logger=logging.getLogger("audit"),
                 extract_query_params=extract_query_params, extract_params=matcher.extract_params,
                 load_auto_binding_rules=lambda: [], ENABLE_SERIES_RERUN=False)
captured = []
def deterministic_match(query, vector=None, *args):
    captured.append(copy.deepcopy(query))
    return {"is_exact": False, "matched_code": None, "top_candidates": [{"code": "AUDIT-WRONG", "name": "错误候选",
            "specification": "DN25", "category": "阀门", "unit": "个", "brand": "", "score": 0.0}]}
server_ns["match_item"] = deterministic_match
extracted("app/server.py", ["batch_match_rows"], server_ns)
sample = {"row_idx": 1, "name": "普通阀门", "specification": "1.名称：卫生级球阀\n2.规格：DN15\n3.工作内容：安装、调试", "unit": "米", "brand": "", "qty": 2.0, "loss": 1.0, "category": "", "professional": "给排水"}
batch = server_ns["batch_match_rows"]([sample])[0]
record("production_input_and_zero_score_binding", {"query_sent_to_matcher": captured[-1], "binding": batch["bindings"], "is_exact": batch["is_exact"], "matched_code": batch["matched_code"]})
server_ns["load_auto_binding_rules"] = lambda: [{"original_name": "普通阀门", "bound_materials": [{"code": "DN15-CODE", "specification": "DN15", "qty_ratio": 1.0}]}]
sample["specification"] = "DN100"
rule_batch = server_ns["batch_match_rows"]([sample])[0]
record("name_only_auto_binding", {"query_spec": sample["specification"], "bindings": rule_batch["bindings"], "is_exact": rule_batch["is_exact"]})

# Actual DDL and miner SQL in an in-memory database.
mem = sqlite3.connect(":memory:")
mem.row_factory = sqlite3.Row
@contextlib.contextmanager
def memory_cursor(commit=True):
    yield mem.cursor()
    mem.commit()
schema_ns = {"get_db_cursor": memory_cursor}
extracted("app/flywheel_db.py", ["init_flywheel_tables"], schema_ns)
schema_ns["init_flywheel_tables"]()
for i in range(2):
    mem.execute("INSERT INTO flywheel_records (client_event_id,session_id,row_idx,raw_name,raw_name_norm,action_type,final_targets_json,user_id,extracted_params) VALUES (?,?,?,?,?,?,?,?,?)",
                (str(i), "session", i, "阀门", "阀门", "PARAM_ADJUST", "[]", "audit", "{}"))
# Reuse the exact supersession statement extracted from the HTTP handler.
server_tree = ast.parse((ROOT / "app/server.py").read_text(encoding="utf-8-sig"))
supersede_sql = next(n.value for n in ast.walk(server_tree) if isinstance(n, ast.Constant)
                     and isinstance(n.value, str) and "UPDATE flywheel_records SET superseded = 1" in n.value)
for event, user in [("cross-a", "user-a"), ("cross-b", "user-b")]:
    cur = mem.execute("INSERT INTO flywheel_records(client_event_id,session_id,row_idx,raw_name,raw_name_norm,action_type,final_targets_json,user_id) VALUES(?,?,?,?,?,?,?,?)",
                      (event, "default_session", 1, "阀门", "阀门", "PICK_CANDIDATE", "[]", user))
    mem.execute(supersede_sql, ("default_session", 1, cur.lastrowid))
record("cross_user_superseded", [dict(r) for r in mem.execute("SELECT user_id,superseded FROM flywheel_records WHERE session_id='default_session' ORDER BY id")])
from app.feedback_miner import FlywheelMiner
try:
    FlywheelMiner()._aggregate_parameter_adjustments(mem.cursor())
    record("miner_param_adjust", "unexpected success")
except sqlite3.OperationalError as exc:
    record("miner_param_adjust", str(exc))

# Direct reproduction of INSERT OR REPLACE semantics in FTS5 with no unique code constraint.
mem.execute("CREATE VIRTUAL TABLE audit_fts USING fts5(code UNINDEXED, name)")
for name in ("旧名称", "新名称"):
    mem.execute("INSERT OR REPLACE INTO audit_fts(code,name) VALUES (?,?)", ("SAME-CODE", name))
record("fts_append_replace", [list(r) for r in mem.execute("SELECT code,name FROM audit_fts")])

class NonClosingConnection(sqlite3.Connection):
    def close(self):
        pass
rule_mem = sqlite3.connect(":memory:", factory=NonClosingConnection)
rule_mem.row_factory = sqlite3.Row
rules.get_db_conn = lambda: rule_mem
rules.init_rule_engine_db()
rule_mem.execute("INSERT INTO llm_rules(rule_id,rule_name,status,rule_json,author_id,author_name) VALUES('AUDIT-RULE','audit','draft','{}','999','another_user')")
record("employee_can_approve_draft", rules.LLMRuleManager.toggle_rule_status("AUDIT-RULE", {"id": 123, "username": "audit_employee", "role": "employee"}))
rules.get_db_conn = ro_connection

record("db_schema_fill_stats", db.get_db_schema_stats())

# Compiler checks do not import modules or write pycache.
syntax_errors = []
files = list((ROOT / "app").glob("*.py")) + list((ROOT / "tests").glob("*.py"))
for path in files:
    try:
        compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
    except SyntaxError as exc:
        syntax_errors.append(str(exc))
record("syntax", {"files": len(files), "errors": syntax_errors})
OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
