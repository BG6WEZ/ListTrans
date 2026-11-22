# V015-M4 R01 改动前基线哈希与本地备份清单

- 基线提交：`HEAD` = `e964fb16848de9dece8e729e801b61c7d81dd341`（无 commit/push/tag/checkout/reset）
- 备份目录：`.local-archive/m4_r01_pre_backup/`（保留 `.local-archive/m3_*` 既有备份）

| 文件 | 改动前 SHA256 | 大小(B) | 备份位置 |
|---|---|---:|---|
| `app/auth_manager.py` | `8b5caeba98abc398c32f22e60adef2e31761ce9b4da9bc9a3d32f72aefa7dd78` | 17914 | `.local-archive/m4_r01_pre_backup/app/auth_manager.py` |
| `app/auth_middleware.py` | `5aba3cc84f0dc2203581f29e92b4bd14fc71dfcb2f90732ab1f5c8b8edd449db` | 3383 | `.local-archive/m4_r01_pre_backup/app/auth_middleware.py` |
| `app/admin_api.py` | `46faf848c04c70e2f1537195957115f07e6febe750baee8f7bd51b38f9060fa9` | 15688 | `.local-archive/m4_r01_pre_backup/app/admin_api.py` |
| `app/rule_api.py` | `afecd2cf7942ea75e74ee99ceb1c4d14a549d32df941d88ebc3f81acb272ed5a` | 5148 | `.local-archive/m4_r01_pre_backup/app/rule_api.py` |
| `app/llm_rule_engine.py` | `fce79ce5204ff00266b6974c7492225bb5488486c190b8270bfc72edb20a9f86` | 46617 | `.local-archive/m4_r01_pre_backup/app/llm_rule_engine.py` |
| `app/flywheel_sandbox.py` | `c4bc5e0ff9b6875208ead853ad1cf291717be128ab719fdfb13a4278057ca0d4` | 25743 | `.local-archive/m4_r01_pre_backup/app/flywheel_sandbox.py` |
| `app/server.py` | `9f5bf79e9fa8523c20424da3aac284a0ab1726b2b9ad81363c50ca2ec766b6b6` | 137020 | `.local-archive/m4_r01_pre_backup/app/server.py` |
| `web/login.html` | `878febc185535aac4ca21d2dced2c3df13c0b71e660ce007debd90a1e5124c3f` | 7328 | `.local-archive/m4_r01_pre_backup/web/login.html` |
| `web/index.html` | `f9883bd95c107586c1aec9542c071c066bbbcdf69499895ddb65081b5f08d435` | 125395 | `.local-archive/m4_r01_pre_backup/web/index.html` |
| `web/app.js` | `4a8eb6204c5e3f27cf7c81081e93dfbd71f3040ba1dea0dfdc86720e3caadc09` | 173783 | `.local-archive/m4_r01_pre_backup/web/app.js` |
| `web/admin.html` | `4350c8d5f7cea47d7515f0de475bcbdf01e0ea85437b2aabeb8031ce07b80e84` | 153474 | `.local-archive/m4_r01_pre_backup/web/admin.html` |
| `Dockerfile` | `34ae0eb6553f89997e080d0869b84da31287506a24087b9024039175738ad57a` | 1142 | `.local-archive/m4_r01_pre_backup/Dockerfile` |
| `.dockerignore` | `304cf9c894bd46f62bff7ae59f42da33f4dd672d21d0867283b7e75b179f9bac` | 317 | `.local-archive/m4_r01_pre_backup/.dockerignore` |
| `docker-compose.yml` | `dc74a02b00e64e598ce675d0a1fe9214ef803d1022551e775d47e4eef557efc7` | 636 | `.local-archive/m4_r01_pre_backup/docker-compose.yml` |
| `.env.example` | `645e5dbef395171918aa042572cdf1d99e3fe6903e9239131184185c15fd7f1d` | 534 | `.local-archive/m4_r01_pre_backup/.env.example` |
| `deploy_guide.md` | `f9d0f5dd621410d940362cb0e45d075f8e1a9e5302942530bb5b7e2cfbe1701a` | 4699 | `.local-archive/m4_r01_pre_backup/deploy_guide.md` |
| `scripts/deploy_cloud.py` | `65281c5937df0188bf18e4f327dc5d23aad7f8211ac05d44560f024991bce25a` | 5176 | `.local-archive/m4_r01_pre_backup/scripts/deploy_cloud.py` |
| `scripts/deploy_to_cloud_safe.py` | `b10cfc90eea587138cd1360937dcd72b55375a3d937858dd7ade519eaab333a4` | 7096 | `.local-archive/m4_r01_pre_backup/scripts/deploy_to_cloud_safe.py` |
| `scripts/check_cloud_status.py` | `19c7e51be22876e35e34291dba978f0da806549cae7633ad0616556566937455` | 1222 | `.local-archive/m4_r01_pre_backup/scripts/check_cloud_status.py` |
| `scripts/probe_servers.py` | `34e23bc898e72be82b46bf973a730789c845ec05e0b6b9b0a836805f553e33ab` | 1281 | `.local-archive/m4_r01_pre_backup/scripts/probe_servers.py` |
| `tests/test_auth_security_fix.py` | `4071b1d88343c19f65bc01b9889d6dc7a1275de8e284f6b4ccc68bb828d56055` | 2547 | `.local-archive/m4_r01_pre_backup/tests/test_auth_security_fix.py` |
| `tests/test_admin_dashboard.py` | `21a414db9f939b197b523a283c5ba69d9a2043e8c1dcfe99cc05b3adef8e8752` | 5718 | `.local-archive/m4_r01_pre_backup/tests/test_admin_dashboard.py` |
| `tests/test_v0_0_8_llm_rules.py` | `9765eb7ac61882e2fe8334d3103e6352216ec471d8f491c1cb29e45f12493a6c` | 3409 | `.local-archive/m4_r01_pre_backup/tests/test_v0_0_8_llm_rules.py` |
| `tests/test_phase0_phase1.py` | `891dbe21f0f79dcd65e18e40aa57219862ef1818e27a52063fa72eea19009e3e` | 3849 | `.local-archive/m4_r01_pre_backup/tests/test_phase0_phase1.py` |
| `tests/test_flywheel_end2end.py` | `d47af7426ad2828d02725ac4589fc3325bbc30d8276e0b2699c074efb48c18c6` | 9430 | `.local-archive/m4_r01_pre_backup/tests/test_flywheel_end2end.py` |
| `tests/test_binding_decisions.py` | `adb13a41e2a9cd0ed152164e86eb3e8debb43567299b10880a2ac5dda6f05878` | 30046 | `.local-archive/m4_r01_pre_backup/tests/test_binding_decisions.py` |
| `tests/test_m3_hard_constraints.py` | `81da3a23ccf2695d7987ad8912f96aaeff8267886745c2a258e97a619a920cea` | 79123 | `.local-archive/m4_r01_pre_backup/tests/test_m3_hard_constraints.py` |
| `tests/test_m3_hard_constraints_http.py` | `ee1cb20cbbcaab86f009da3f3bbbaa2ca4552b9d047d04c56320b7a566ac2e1c` | 82617 | `.local-archive/m4_r01_pre_backup/tests/test_m3_hard_constraints_http.py` |
