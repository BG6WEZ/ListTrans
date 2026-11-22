# V015-M3 R02 改动前工作区哈希基线与保护备份

- 采集时间：2026-11-14（Asia/Shanghai）；记录者：开发AI。
- Git HEAD：`4cbbbe5bd6e1bee21ad908ed1f631a919920ea8f`（未提交、未 push、无 tag）。
- 备份目录：`.local-archive/m3_r02_pre_backup/`（按相对路径保存 R02 改动前文件完整内容副本，支持无 git 写操作定向还原到 R01 交付基线，并在 R02 期间已逐文件核验备份 SHA256 与工作区完全一致）。
- 外层业务快照 before：`doc/V0.1.5/evidence/m3_r02_outer_snapshot_before.json`（SHA256：`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`，日志见 `m3_r02_outer_snapshot_before.log`）。
- 固定 venv 隔离预检 before：`doc/V0.1.5/evidence/m3_r02_venv_verify_before.json`（`verdict: "PASS"`，`exit_code: 0`，`business_changes: []`，`hard_errors: []`，`pending_modules: []`，日志见 `m3_r02_venv_verify_before.log`）。
- 金标路径状态：`tests/fixtures/golden_last_run.json` 与 `data/golden_last_run.json` 均不存在。

## R02 拟修改对象及已备份基线（8 个文件）

| 相对路径 | M2 基线（R01 前）SHA256 | R01 交付（R02 前）SHA256 | 备份路径 |
|---|---|---|---|
| `app/matcher.py` | `48780dfe4bd799535472b92116139da35a3ac01a87a5ab6803100390e5f1e796` | `fe08ca6a07a3431f75b740837edd573a8514a395da53619652b90b04be92a946` | `.local-archive/m3_r02_pre_backup/app/matcher.py` |
| `app/binding_decisions.py` | `8b5bc83db8cd0ae90dc565c28f7ccd6c47235a04cd4688f86f6f1006f23c49db` | `3288cccd00c022b6094570621baa6d23eb9fc73d4c36e3e795879b17a9b18f60` | `.local-archive/m3_r02_pre_backup/app/binding_decisions.py` |
| `app/server.py` | `a621a00cd31bfb4cff30003d33dcb1a152b548634b7bde0c7afd9386abcca1b3` | `abcc88fe7ae289df76e99086031ca4f0919bccb7f90fc2fd47e027d7637a755a` | `.local-archive/m3_r02_pre_backup/app/server.py` |
| `app/query_preprocessor.py` | `8ec776bf186851248fad6e7468edab85d3870b0fde2318f77f2eb53974ffda2e` | `d0753c3a26da448060f9c2044c0fa4e7c0be250e2ca610943b6b57360fcffe25` | `.local-archive/m3_r02_pre_backup/app/query_preprocessor.py` |
| `app/domain_extractors.py` | `08e66817d065293afe862a96560a35337737aa8a06ffb433ba3b52c6e1965e76` | `27d5f63bc78bc6771e2e56ac885d1cf4916eadbfae32a67f664b5f83237eb080` | `.local-archive/m3_r02_pre_backup/app/domain_extractors.py` |
| `app/llm_rule_engine.py` | `f223af4e06c819ca66f3b1342df9187d9b81ac0e005f55d6c30ad476f6e2645f` | `f223af4e06c819ca66f3b1342df9187d9b81ac0e005f55d6c30ad476f6e2645f` | `.local-archive/m3_r02_pre_backup/app/llm_rule_engine.py` |
| `tests/test_m3_hard_constraints.py` | 新增（R01 前无） | `ee927e1c94a29d807266459b1f6b0bbe8eaeac9381aa1b656e7ea9ee15157139` | `.local-archive/m3_r02_pre_backup/tests/test_m3_hard_constraints.py` |
| `tests/test_m3_hard_constraints_http.py` | 新增（R01 前无） | `bef0487653fd6966760a5dea36df999e5567d08bbf8bd07908691696adbeae38` | `.local-archive/m3_r02_pre_backup/tests/test_m3_hard_constraints_http.py` |

说明：若任何 R02 整改需定向回退至 R01 交付态，可直接从 `.local-archive/m3_r02_pre_backup/` 对应文件写回并逐项校验 R01 SHA256，不使用 `git checkout` / `git reset`。
