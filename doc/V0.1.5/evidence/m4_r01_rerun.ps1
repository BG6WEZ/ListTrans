$ErrorActionPreference = 'Continue'
Set-Location 'c:\Users\tlyer\project\ListTrans'
$py = '.\venv\Scripts\python.exe'
$ev = 'doc\V0.1.5\evidence'
$env:PYTHONIOENCODING = 'utf-8'
$env:LLM_ENABLED = 'false'
$anyFailed = $false

function Run($name, $log, [string[]]$cmdArgs) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $py @cmdArgs 2>&1 | Out-File -FilePath "$ev\$log" -Encoding utf8
    $code = $LASTEXITCODE
    $sw.Stop()
    "$name`_EXIT=$code WALL_SECONDS=$([math]::Round($sw.Elapsed.TotalSeconds,3))" | Out-File -FilePath "$ev\$log" -Append -Encoding utf8
    Write-Output "$name EXIT=$code $([math]::Round($sw.Elapsed.TotalSeconds,1))s"
    if ($code -ne 0) { $script:anyFailed = $true }
}

Write-Output "START $(Get-Date -Format o)"
Run 'SNAPSHOT_BEFORE' 'm4_r01_outer_snapshot_before.log' @('.local-archive/business_snapshot.py', "$ev\m4_r01_outer_snapshot_before.json")
Run 'VERIFY_BEFORE' 'm4_r01_venv_verify_before.log' @('tests/verify_isolation.py', '--out', "$ev\m4_r01_venv_verify_before.json")
Run 'PY_COMPILE' 'm4_r01_py_compile.log' @('-W', 'error', '-m', 'py_compile', 'app/auth_manager.py', 'app/auth_middleware.py', 'app/admin_api.py', 'app/llm_rule_engine.py', 'app/rule_api.py', 'app/flywheel_sandbox.py', 'app/server.py', 'scripts/_deploy_env.py', 'scripts/deploy_cloud.py', 'scripts/deploy_to_cloud_safe.py', 'scripts/check_cloud_status.py', 'scripts/probe_servers.py', 'tests/test_m4_auth_lifecycle.py', 'tests/test_m4_permissions_and_tasks.py')
Run 'M4_CASES' 'm4_r01_pytest_m4.txt' @('-m', 'pytest', 'tests/test_m4_auth_lifecycle.py', 'tests/test_m4_permissions_and_tasks.py', '-v', '--tb=short')
Run 'BROWSER_E2E' 'm4_r01_browser_e2e.log' @("$ev\m4_r01_browser_check.py")
Run 'M3_CASES' 'm4_r01_pytest_m3.txt' @('-m', 'pytest', 'tests/test_m3_hard_constraints.py', 'tests/test_m3_hard_constraints_http.py', '-v')
Run 'REQUIRED' 'm4_r01_pytest_m0_m2_regression.txt' @('-m', 'pytest', 'tests/test_m0_isolation.py', 'tests/test_m0_guardrail_regression.py', 'tests/test_query_contract.py', 'tests/test_auto_binding.py', 'tests/test_binding_decisions.py', '-q', '--tb=short')
Run 'BOUNDARIES' 'm4_r01_pytest_sandbox_30.txt' @('-m', 'pytest', 'tests/test_flywheel_sandbox.py', '-k', 'compare or nonzero', '-q', '--tb=short')
Run 'GATE3' 'm4_r01_3rule_gate.txt' @("$ev\m4_r01_capture_evidence.py")
Run 'FULL' 'm4_r01_pytest_full.txt' @('-m', 'pytest', 'tests/', '-q', '--tb=short')
Run 'VERIFY_AFTER' 'm4_r01_venv_verify_after.log' @('tests/verify_isolation.py', '--out', "$ev\m4_r01_venv_verify_after.json")
Run 'SNAPSHOT_AFTER' 'm4_r01_outer_snapshot_after.log' @('.local-archive/business_snapshot.py', "$ev\m4_r01_outer_snapshot_after.json")

Write-Output "golden_last_run(tests)=$(Test-Path tests/fixtures/golden_last_run.json) golden_last_run(data)=$(Test-Path data/golden_last_run.json)"
Write-Output "END $(Get-Date -Format o)"
if ($anyFailed) { exit 1 }
