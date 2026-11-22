"""Structural proof of unchanged scoring, supplementing actual six-baseline runs."""
import ast
import copy
import hashlib
import json
from pathlib import Path


def dump(tree):
    return ast.dump(tree, include_attributes=False)


class RemoveRecallMetadata(ast.NodeTransformer):
    def visit_Dict(self, node):
        keep = [i for i, key in enumerate(node.keys) if not (isinstance(key, ast.Constant) and key.value in ("recall_source", "recall_sources"))]
        node.keys = [node.keys[i] for i in keep]
        node.values = [node.values[i] for i in keep]
        return self.generic_visit(node)


class RemoveSourceRecording(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        return None if node.name == "record_source" else self.generic_visit(node)

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "record_source":
            return None
        return self.generic_visit(node)


def main():
    project = Path(__file__).resolve().parents[3]
    evidence = Path(__file__).resolve().parent
    old_path = project / ".local-archive/v016_m4_r01_pre_backup/app/matcher.py"
    new_path = project / "app/matcher.py"
    old = ast.parse(old_path.read_text(encoding="utf-8")); new = ast.parse(new_path.read_text(encoding="utf-8"))
    get = lambda tree, name: next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    untouched_old = [n for n in old.body if not isinstance(n, ast.FunctionDef) or n.name not in ("hybrid_recall", "match_item")]
    untouched_new = [n for n in new.body if not isinstance(n, ast.FunctionDef) or n.name not in ("hybrid_recall", "match_item")]
    assert dump(ast.Module(body=untouched_old, type_ignores=[])) == dump(ast.Module(body=untouched_new, type_ignores=[]))
    recall = RemoveSourceRecording().visit(copy.deepcopy(get(new, "hybrid_recall")))
    assert dump(recall) == dump(get(old, "hybrid_recall"))
    match = copy.deepcopy(get(new, "match_item"))
    assert match.args.kwonlyargs[0].arg == "diagnostic_targets" and len(match.args.kwonlyargs) == 1
    match.args.kwonlyargs = []; match.args.kw_defaults = []
    assert isinstance(match.body[-3], ast.Assign) and match.body[-3].targets[0].id == "result"
    assert isinstance(match.body[-2], ast.If) and isinstance(match.body[-1], ast.Return)
    match.body[-3:] = [ast.Return(value=match.body[-3].value)]
    match = RemoveRecallMetadata().visit(match)
    assert dump(match) == dump(get(old, "match_item"))
    report = {"method": "AST comparison after removing only declared additive diagnostic statements/fields",
              "all_other_matcher_statements_identical": True, "recall_selection_and_order_identical": True,
              "scoring_sort_tie_adjustment_threshold_and_existing_return_semantics_identical": True,
              "score_candidate_two_value_wrapper_identical": dump(get(old, "score_candidate")) == dump(get(new, "score_candidate")),
              "before_sha256": hashlib.sha256(old_path.read_bytes()).hexdigest(), "after_sha256": hashlib.sha256(new_path.read_bytes()).hexdigest(),
              "limitation": "Structural audit supplements, and does not replace, runtime six-baseline regression and service tests"}
    with (evidence / "m4_r01_score_contract.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
