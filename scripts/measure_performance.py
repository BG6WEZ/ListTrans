# -*- coding: utf-8 -*-
"""
Performance & Benchmark Measurement Script for V0.1.3 Data Flywheel.
"""
import os
import sys
import time
import json
import subprocess
import psutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def measure_targeted_subprocess():
    cmd = [
        sys.executable,
        os.path.join(PROJECT_ROOT, "tests", "eval_golden_set.py"),
        "--filter-keyword", "排风机",
        "--output-json", os.path.join(PROJECT_ROOT, "tests", "fixtures", "perf_test_out.json"),
        "--chunk-size", "50"
    ]
    
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = time.time() - t0
    
    # Measure memory of a fresh evaluation run
    proc_mem = subprocess.Popen(cmd, cwd=PROJECT_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p = psutil.Process(proc_mem.pid)
    peak_rss_mb = 0.0
    while proc_mem.poll() is None:
        try:
            mem = p.memory_info().rss / (1024 * 1024)
            if mem > peak_rss_mb:
                peak_rss_mb = mem
        except Exception:
            break
        time.sleep(0.2)
        
    print(f"Targeted eval (24 cases): Latency = {elapsed:.2f}s, Peak RSS = {peak_rss_mb:.2f} MB")
    return elapsed, peak_rss_mb

if __name__ == "__main__":
    measure_targeted_subprocess()
