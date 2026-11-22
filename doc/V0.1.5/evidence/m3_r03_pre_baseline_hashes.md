# V015-M3 R03 改动前工作区哈希基线与保护备份

- 采集时间：2026-11-14（Asia/Shanghai）；记录者：开发AI。
- Git HEAD：`e964fb16848de9dece8e729e801b61c7d81dd341`（R02 复审时实测 HEAD；本轮无 git 写操作、未提交、未 push、无 tag）。
- 备份目录：`.local-archive/m3_r03_pre_backup/`（按相对路径保存 R03 改动前 9 个拟涉及文件的完整内容副本；原 `.local-archive/m3_r02_pre_backup/` 保持未动）。
- 外层业务快照 before：`doc/V0.1.5/evidence/m3_r03_outer_snapshot_before.json`（SHA256：`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`，日志见 `m3_r03_outer_snapshot_before.log`）。
- 固定 venv 隔离预检 before：`doc/V0.1.5/evidence/m3_r03_venv_verify_before.json`（`verdict: "PASS"`，`exit_code: 0`，`business_changes: []`，`hard_errors: []`，`pending_modules: []`，日志见 `m3_r03_venv_verify_before.log`）。
- 金标路径状态：`tests/fixtures/golden_last_run.json` 与 `data/golden_last_run.json` 均 `False`。

## R03 拟修改对象及已备份基线（9 个文件）

| 相对路径 | R02 交付（R03 前）SHA256 | 备份路径 |
|---|---|---|
| `app/binding_decisions.py` | `b012ef44cd871f67bdb7597a79a6ecb3347433c27faa916fe5498b628c7373a3` | `.local-archive/m3_r03_pre_backup/app/binding_decisions.py` |
| `app/server.py` | `b9c5668e3278db9eb3dfc96363456bae2b60261ea353d3dbed25a5caf5ea82f9` | `.local-archive/m3_r03_pre_backup/app/server.py` |
| `app/llm_rule_engine.py` | `6d2e18391516f0e5fb1a69303beb16ab28bfe955f27fabbef069fd2b2dca854b` | `.local-archive/m3_r03_pre_backup/app/llm_rule_engine.py` |
| `app/matcher.py` | `4dfc51660debc8d60c217600a1e08bdbff9f55bb63b83719b80bfb93ce234cdb` | `.local-archive/m3_r03_pre_backup/app/matcher.py` |
| `app/normalizer.py` | `711f221ff7933725e131ecd5f659e674b8dd3cef8c2e6897c437d17d167b46cf` | `.local-archive/m3_r03_pre_backup/app/normalizer.py` |
| `app/query_preprocessor.py` | `d0753c3a26da448060f9c2044c0fa4e7c0be250e2ca610943b6b57360fcffe25` | `.local-archive/m3_r03_pre_backup/app/query_preprocessor.py` |
| `app/domain_extractors.py` | `0620b5b46030a29972e2f98bc0db814d2f35fb0e80b6ef5e5bf3df63933bb20c` | `.local-archive/m3_r03_pre_backup/app/domain_extractors.py` |
| `tests/test_m3_hard_constraints.py` | `91b3ff0aafc6157ea97d2884986911e4d7b4ee1fa05838ce74da85ad32aad7dc` | `.local-archive/m3_r03_pre_backup/tests/test_m3_hard_constraints.py` |
| `tests/test_m3_hard_constraints_http.py` | `f5057f0400be13924873c2c290101d729a65dff729a98bfdb25fd7d3ab8d149b` | `.local-archive/m3_r03_pre_backup/tests/test_m3_hard_constraints_http.py` |

说明：若需从 R03 定向回退至 R02 交付态，可直接从 `.local-archive/m3_r03_pre_backup/` 对应文件写回并逐项校验 R02 SHA256，不使用 `git checkout` / `git reset`。
