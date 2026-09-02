# -*- coding: utf-8 -*-
"""
Subprocess Sandbox Isolation Evaluation Service for Data Flywheel (app/flywheel_sandbox.py).
Ensures zero-pollution to production memory by executing evaluations in an isolated subprocess
with custom CONFIG_DIR environment injection in a background worker thread.
Implements dual-run baseline vs candidate regression gate checking with strict Top1 zero-degradation.
"""
import os
import sys
import json
import shutil
import tempfile
import uuid
import logging
import subprocess
import threading
from typing import Dict, Any, List, Optional

logger = logging.getLogger("listtrans_app")
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
CONFIG_PROD_DIR = os.path.join(PROJECT_ROOT, "config")

# Thread-safe mutex lock to serialize sandbox evaluations and protect system resources
_sandbox_eval_lock = threading.Lock()

# Task progress store with capacity limit
_eval_tasks: Dict[str, Dict[str, Any]] = {}
_tasks_lock = threading.Lock()
MAX_EVAL_TASKS_CACHE = 100

def get_eval_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    with _tasks_lock:
        task = _eval_tasks.get(task_id)
        return dict(task) if task else None

def _run_sandbox_worker(task_id: str, suggestion: Dict[str, Any]):
    """Background worker thread for subprocess sandbox regression evaluation."""
    with _sandbox_eval_lock:
        with _tasks_lock:
            _eval_tasks[task_id]["status"] = "running"
            _eval_tasks[task_id]["progress"] = 10
            _eval_tasks[task_id]["message"] = "正在准备基线与候选沙箱隔离环境..."
        
        temp_sandbox_root = tempfile.mkdtemp(prefix=f"sandbox_{task_id}_")
        baseline_config_dir = os.path.join(temp_sandbox_root, "config_baseline")
        candidate_config_dir = os.path.join(temp_sandbox_root, "config_candidate")
        baseline_output_json = os.path.join(temp_sandbox_root, "baseline_result.json")
        candidate_output_json = os.path.join(temp_sandbox_root, "candidate_result.json")
        
        try:
            # 1. Clone baseline and candidate directories
            shutil.copytree(CONFIG_PROD_DIR, baseline_config_dir)
            shutil.copytree(CONFIG_PROD_DIR, candidate_config_dir)
            
            with _tasks_lock:
                _eval_tasks[task_id]["progress"] = 25
                _eval_tasks[task_id]["message"] = "正在向候选沙箱注入提议规则..."
            
            sug_type = suggestion.get("suggestion_type", "ALIAS")
            source_pattern = suggestion.get("source_pattern", "")
            if isinstance(source_pattern, str):
                source_pattern = source_pattern.strip()
            target_value = suggestion.get("target_value", "")
            if isinstance(target_value, str):
                target_value = target_value.strip()
            
            # 2. Inject candidate rule into candidate config directory
            if sug_type == "ALIAS":
                alias_path = os.path.join(candidate_config_dir, "alias_dict.json")
                alias_data = {}
                if os.path.exists(alias_path):
                    with open(alias_path, "r", encoding="utf-8") as f:
                        alias_data = json.load(f)
                alias_data[source_pattern] = str(target_value)
                with open(alias_path, "w", encoding="utf-8") as f:
                    json.dump(alias_data, f, ensure_ascii=False, indent=4)
                    
            elif sug_type in ("BINDING", "BINDING_RULE"):
                binding_path = os.path.join(candidate_config_dir, "auto_binding_rules.json")
                binding_data = []
                if os.path.exists(binding_path):
                    with open(binding_path, "r", encoding="utf-8") as f:
                        binding_data = json.load(f)
                new_rule = {
                    "original_name": source_pattern,
                    "bound_materials": json.loads(target_value) if isinstance(target_value, str) else target_value
                }
                binding_data.append(new_rule)
                with open(binding_path, "w", encoding="utf-8") as f:
                    json.dump(binding_data, f, ensure_ascii=False, indent=4)

            # 3. Prepare child process environment
            base_env = os.environ.copy()
            base_env["CONFIG_DIR"] = baseline_config_dir
            base_env["PYTHONPATH"] = PROJECT_ROOT
            base_env["TOKENIZERS_PARALLELISM"] = "false"
            base_env["OMP_NUM_THREADS"] = "1"

            cand_env = os.environ.copy()
            cand_env["CONFIG_DIR"] = candidate_config_dir
            cand_env["PYTHONPATH"] = PROJECT_ROOT
            cand_env["TOKENIZERS_PARALLELISM"] = "false"
            cand_env["OMP_NUM_THREADS"] = "1"

            # 4. Run Baseline Evaluation Subprocess
            with _tasks_lock:
                _eval_tasks[task_id]["progress"] = 40
                _eval_tasks[task_id]["message"] = "正在子进程执行定向基线评测 (Baseline)..."

            cmd_baseline = [
                sys.executable,
                os.path.join(PROJECT_ROOT, "tests", "eval_golden_set.py"),
                "--filter-keyword", source_pattern,
                "--output-json", baseline_output_json,
                "--chunk-size", "50"
            ]

            proc_base = subprocess.run(
                cmd_baseline,
                cwd=PROJECT_ROOT,
                env=base_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False
            )

            # 5. Run Candidate Evaluation Subprocess
            with _tasks_lock:
                _eval_tasks[task_id]["progress"] = 70
                _eval_tasks[task_id]["message"] = "正在子进程执行候选规则注入后评测 (Candidate)..."

            cmd_candidate = [
                sys.executable,
                os.path.join(PROJECT_ROOT, "tests", "eval_golden_set.py"),
                "--filter-keyword", source_pattern,
                "--output-json", candidate_output_json,
                "--chunk-size", "50"
            ]

            proc_cand = subprocess.run(
                cmd_candidate,
                cwd=PROJECT_ROOT,
                env=cand_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False
            )
            
            with _tasks_lock:
                _eval_tasks[task_id]["progress"] = 90
                _eval_tasks[task_id]["message"] = "评测完成，正在对比基线与候选回归指标..."

            # 6. Parse and compare baseline vs candidate
            if os.path.exists(baseline_output_json) and os.path.exists(candidate_output_json):
                with open(baseline_output_json, "r", encoding="utf-8") as f:
                    base_data = json.load(f)
                with open(candidate_output_json, "r", encoding="utf-8") as f:
                    cand_data = json.load(f)
                
                base_rows = base_data.get("rows", [])
                cand_rows = cand_data.get("rows", [])
                targeted_count = len(cand_rows)
                
                if targeted_count == 0:
                    passed = False
                    reason = "定向子集为空：黄金测试集中未匹配到该关键词相关用例，无法进行沙箱回归验证"
                    report = {
                        "passed": False,
                        "targeted_count": 0,
                        "improved_count": 0,
                        "degraded_count": 0,
                        "degraded_from_top1": 0,
                        "details": [],
                        "reason": reason
                    }
                else:
                    base_map = {r["id"]: r for r in base_rows}
                    cand_map = {r["id"]: r for r in cand_rows}
                    
                    improved_count = 0
                    degraded_count = 0
                    degraded_from_top1 = 0
                    details = []
                    
                    for cid, c_row in cand_map.items():
                        b_row = base_map.get(cid, {})
                        b_rank_raw = b_row.get("rank", 0)
                        c_rank_raw = c_row.get("rank", 0)
                        
                        b_rank = b_rank_raw if b_rank_raw > 0 else 999
                        c_rank = c_rank_raw if c_rank_raw > 0 else 999
                        
                        if b_rank == 1 and c_rank != 1:
                            degraded_from_top1 += 1
                            details.append({
                                "id": cid,
                                "name": c_row.get("name"),
                                "change": f"Top1 掉出 (1 -> {c_rank})",
                                "type": "critical_degrade"
                            })
                        elif c_rank < b_rank:
                            improved_count += 1
                            details.append({
                                "id": cid,
                                "name": c_row.get("name"),
                                "change": f"排名提升 ({b_rank} -> {c_rank})",
                                "type": "improved"
                            })
                        elif c_rank > b_rank:
                            degraded_count += 1
                            details.append({
                                "id": cid,
                                "name": c_row.get("name"),
                                "change": f"排名下降 ({b_rank} -> {c_rank})",
                                "type": "degraded"
                            })
                            
                    net_gain = improved_count - degraded_count
                    passed = (degraded_from_top1 == 0 and net_gain >= 0)
                    
                    if degraded_from_top1 > 0:
                        reason = f"门禁拦截：检测到 {degraded_from_top1} 条原有 Top1 匹配用例发生排名恶化"
                    elif net_gain < 0:
                        reason = f"门禁拦截：恶化用例数 ({degraded_count}) 大于改善用例数 ({improved_count})，净收益为负"
                    elif improved_count == 0 and degraded_count == 0:
                        reason = "门禁通过：稳态无退化（改善: 0 条, 恶化: 0 条）"
                    else:
                        reason = f"定向子集验证通过：改善 {improved_count} 条，恶化 {degraded_count} 条，Top1 零恶化"
                        
                    report = {
                        "passed": passed,
                        "targeted_count": targeted_count,
                        "improved_count": improved_count,
                        "degraded_count": degraded_count,
                        "degraded_from_top1": degraded_from_top1,
                        "details": details[:10],
                        "reason": reason
                    }
                
                with _tasks_lock:
                    _eval_tasks[task_id]["status"] = "completed"
                    _eval_tasks[task_id]["progress"] = 100
                    _eval_tasks[task_id]["passed"] = passed
                    _eval_tasks[task_id]["message"] = report["reason"]
                    _eval_tasks[task_id]["report"] = report
            else:
                err_msg = proc_cand.stderr[:300] if proc_cand.stderr else proc_base.stderr[:300]
                with _tasks_lock:
                    _eval_tasks[task_id]["status"] = "failed"
                    _eval_tasks[task_id]["progress"] = 100
                    _eval_tasks[task_id]["passed"] = False
                    _eval_tasks[task_id]["message"] = f"评测子进程未生成输出结果: {err_msg}"
                
        except Exception as e:
            logger.error(f"Error in sandbox evaluation task {task_id}: {e}")
            with _tasks_lock:
                _eval_tasks[task_id]["status"] = "failed"
                _eval_tasks[task_id]["progress"] = 100
                _eval_tasks[task_id]["passed"] = False
                _eval_tasks[task_id]["message"] = str(e)
        finally:
            try:
                shutil.rmtree(temp_sandbox_root, ignore_errors=True)
            except Exception:
                pass

def start_sandbox_evaluation(suggestion: Dict[str, Any]) -> str:
    """Trigger async sandbox evaluation in background thread and return task_id."""
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    with _tasks_lock:
        # Evict oldest tasks if limit reached
        if len(_eval_tasks) >= MAX_EVAL_TASKS_CACHE:
            oldest_keys = list(_eval_tasks.keys())[:20]
            for k in oldest_keys:
                _eval_tasks.pop(k, None)
                
        _eval_tasks[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "progress": 0,
            "message": "已排队等待沙箱评测...",
            "passed": False,
            "report": None
        }
    t = threading.Thread(target=_run_sandbox_worker, args=(task_id, suggestion), daemon=True)
    t.start()
    return task_id
