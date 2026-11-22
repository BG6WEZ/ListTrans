$ErrorActionPreference = 'Continue'
Set-Location 'c:\Users\tlyer\project\ListTrans'
$py = '.\venv\Scripts\python.exe'
$ev = 'doc\V0.1.5\evidence'
$env:PYTHONIOENCODING = 'utf-8'
function Run($name, $log, [string[]]$cmdArgs) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $py @cmdArgs 2>&1 | Out-File -FilePath "$ev\$log" -Encoding utf8
    $code = $LASTEXITCODE
    $sw.Stop()
    "$name`_EXIT=$code WALL_SECONDS=$([math]::Round($sw.Elapsed.TotalSeconds,3))" | Out-File -FilePath "$ev\$log" -Append -Encoding utf8
    Write-Output "$name EXIT=$code $([math]::Round($sw.Elapsed.TotalSeconds,1))s"
}
Write-Output "START $(Get-Date -Format o)"
Run 'SNAPSHOT_BEFORE' 'm3_r01_rerun_outer_snapshot_before.log' @('.local-archive/business_snapshot.py', "$ev\m3_r01_rerun_outer_snapshot_before.json")
Run 'VERIFY_BEFORE' 'm3_r01_rerun_venv_verify_before.log' @('tests/verify_isolation.py', '--out', "$ev\m3_r01_rerun_venv_verify_before.json")
Run 'M3_CASES' 'm3_r01_rerun_m3_cases.log' @('-m', 'pytest', 'tests/test_m3_hard_constraints.py', 'tests/test_m3_hard_constraints_http.py', '-q', '--tb=short')
Run 'REQUIRED' 'm3_r01_rerun_required_regressions.log' @('-m', 'pytest', 'tests/test_m0_isolation.py', 'tests/test_m0_guardrail_regression.py', 'tests/test_query_contract.py', 'tests/test_auto_binding.py', 'tests/test_binding_decisions.py', '-q', '--tb=short')
Run 'BOUNDARIES' 'm3_r01_compare_boundaries.log' @('-m', 'pytest', 'tests/test_flywheel_sandbox.py', '-k', 'compare or nonzero', '-q', '--tb=short')
Run 'GATE3' 'm3_r01_gate_reports_capture.log' @('C:/Users/tlyer/AppData/Local/Temp/m3_r01_capture_evidence.py')
Run 'FULL' 'm3_r01_rerun_full_pytest.log' @('-m', 'pytest', 'tests/', '-q', '--tb=short')
Run 'VERIFY_AFTER' 'm3_r01_rerun_venv_verify_after.log' @('tests/verify_isolation.py', '--out', "$ev\m3_r01_rerun_venv_verify_after.json")
Run 'SNAPSHOT_AFTER' 'm3_r01_rerun_outer_snapshot_after.log' @('.local-archive/business_snapshot.py', "$ev\m3_r01_rerun_outer_snapshot_after.json")
Write-Output "golden_last_run(tests)=$(Test-Path tests/fixtures/golden_last_run.json) golden_last_run(data)=$(Test-Path data/golden_last_run.json)"
Write-Output "END $(Get-Date -Format o)"
