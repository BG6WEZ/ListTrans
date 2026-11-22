# V015-M4 R01 开发交付 SHA256 核对清单

- 角色：**开发AI**（无 git commit/tag/push/checkout/reset）
- 基线：`HEAD` = `e964fb16848de9dece8e729e801b61c7d81dd341`
- 业务快照：复验前/后及相对统筹下发快照恒为 `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`
- 备份核验：`.local-archive/m4_r01_pre_backup/` 内 28 份改动前备件与 `m4_r01_pre_baseline_hashes.md` 100% 匹配（其中 `docker-compose.yml` 保持未修改）

## 1. 本轮实际修改的已有文件（28 项）

| 文件 | 分类 | 改动前 SHA256 | 当前交付 SHA256 | 大小(B) | 备份/基线来源 |
|---|---|---|---|---:|---|
| `app/auth_manager.py` | 后端认证与凭据生命周期 | `8b5caeba98abc398c32f22e60adef2e31761ce9b4da9bc9a3d32f72aefa7dd78` | `ff30e866ced18596f545eafe4cc7df0237560072b849f18a013d82f6e5d4611b` | 43396 | `.local-archive/m4_r01_pre_backup/app/auth_manager.py` |
| `app/auth_middleware.py` | 后端认证与凭据生命周期 | `5aba3cc84f0dc2203581f29e92b4bd14fc71dfcb2f90732ab1f5c8b8edd449db` | `3c09aa6b339743581d5d6e6c38f2318b737f7caae6e6f18b0cd8b388c206eef3` | 3541 | `.local-archive/m4_r01_pre_backup/app/auth_middleware.py` |
| `app/admin_api.py` | 后端管理接口 | `46faf848c04c70e2f1537195957115f07e6febe750baee8f7bd51b38f9060fa9` | `ea4cd0998e6a812a18282004e9d45931b63b079bc081b96924ef3b6f7f2245bc` | 15058 | `.local-archive/m4_r01_pre_backup/app/admin_api.py` |
| `app/rule_api.py` | 后端规则审批接口 | `afecd2cf7942ea75e74ee99ceb1c4d14a549d32df941d88ebc3f81acb272ed5a` | `f83c22b3368e948267ceb0e7b4507457d5b872d4ddcbec6b4bf4259cd0e47c9c` | 6787 | `.local-archive/m4_r01_pre_backup/app/rule_api.py` |
| `app/llm_rule_engine.py` | 后端规则状态机与迁移 | `fce79ce5204ff00266b6974c7492225bb5488486c190b8270bfc72edb20a9f86` | `bcf17254b356d87d433536171b5e55c861f23c32aa1e6db4cccb62648398d813` | 81023 | `.local-archive/m4_r01_pre_backup/app/llm_rule_engine.py` |
| `app/flywheel_sandbox.py` | 后端飞轮沙盒所有权 | `c4bc5e0ff9b6875208ead853ad1cf291717be128ab719fdfb13a4278057ca0d4` | `887e24957704b35c10912f5dc85b1deaa7d4749f617d86566644ddca716953d3` | 26958 | `.local-archive/m4_r01_pre_backup/app/flywheel_sandbox.py` |
| `app/server.py` | 后端主服务与上传/绑定边界 | `9f5bf79e9fa8523c20424da3aac284a0ab1726b2b9ad81363c50ca2ec766b6b6` | `f91d9ab7ba73cb18a9f418b3ff829900be592d3d3f947ae45d6a350ae0eea94e` | 140499 | `.local-archive/m4_r01_pre_backup/app/server.py` |
| `web/login.html` | 前端登录页去弱口令提示 | `878febc185535aac4ca21d2dced2c3df13c0b71e660ce007debd90a1e5124c3f` | `f8fe49b0a1315ca5bdf86c79c85d7a78a4111af42880cec1cd3520c8a5af9cb5` | 7400 | `.local-archive/m4_r01_pre_backup/web/login.html` |
| `web/index.html` | 前端强制改密弹窗与规则UI | `f9883bd95c107586c1aec9542c071c066bbbcdf69499895ddb65081b5f08d435` | `9794bf2c45aa4441ecf29510542e27ed50dd46703324f82629ea55a9a95f5cae` | 125949 | `.local-archive/m4_r01_pre_backup/web/index.html` |
| `web/app.js` | 前端认证/改密/权限处理 | `4a8eb6204c5e3f27cf7c81081e93dfbd71f3040ba1dea0dfdc86720e3caadc09` | `3b37776865e1328f85158c4d138ab799ada290c13675c7330e79ae650860f35c` | 179435 | `.local-archive/m4_r01_pre_backup/web/app.js` |
| `web/admin.html` | 前端管理后台兼容 | `4350c8d5f7cea47d7515f0de475bcbdf01e0ea85437b2aabeb8031ce07b80e84` | `ed1a3eeb9c6ef09683c16924a0c436966fa91de7020767b7cb198dedacea0669` | 154682 | `.local-archive/m4_r01_pre_backup/web/admin.html` |
| `Dockerfile` | 容器构建边界与单worker | `34ae0eb6553f89997e080d0869b84da31287506a24087b9024039175738ad57a` | `bc5e8d54fb8758fd4f0ebdbb7c586d3c6d68cf5386ff570b17b7c7154259413a` | 1534 | `.local-archive/m4_r01_pre_backup/Dockerfile` |
| `.dockerignore` | 构建上下文敏感文件隔离 | `304cf9c894bd46f62bff7ae59f42da33f4dd672d21d0867283b7e75b179f9bac` | `d37c2ec4512f4e016c8d665ced63761127e00346f710c34760994c0443617d74` | 810 | `.local-archive/m4_r01_pre_backup/.dockerignore` |
| `.env.example` | 环境变量模板脱敏 | `645e5dbef395171918aa042572cdf1d99e3fe6903e9239131184185c15fd7f1d` | `2cf00504fd013eee4f357d7081625b1d151b59e4bddc621e83a99ebc3742342d` | 934 | `.local-archive/m4_r01_pre_backup/.env.example` |
| `deploy_guide.md` | 部署指南脱敏与外部注入说明 | `f9d0f5dd621410d940362cb0e45d075f8e1a9e5302942530bb5b7e2cfbe1701a` | `c35df24850a940bb13c4e100324495c4ec708b0e544073b5360cb736b335276b` | 6183 | `.local-archive/m4_r01_pre_backup/deploy_guide.md` |
| `scripts/deploy_cloud.py` | 部署脚本去凭据fail-closed | `65281c5937df0188bf18e4f327dc5d23aad7f8211ac05d44560f024991bce25a` | `f1a7580878839eb84a84c49704fefe99b3d8eb5493c8caf29efe19f906f239fd` | 5307 | `.local-archive/m4_r01_pre_backup/scripts/deploy_cloud.py` |
| `scripts/deploy_to_cloud_safe.py` | 部署脚本去凭据fail-closed | `b10cfc90eea587138cd1360937dcd72b55375a3d937858dd7ade519eaab333a4` | `81b7d84d0ff05621da63f723facf1d9a39d17f742618b9a0acca584ceb4976b8` | 7274 | `.local-archive/m4_r01_pre_backup/scripts/deploy_to_cloud_safe.py` |
| `scripts/check_cloud_status.py` | 状态脚本去凭据fail-closed | `19c7e51be22876e35e34291dba978f0da806549cae7633ad0616556566937455` | `78819b5b8e157b491521a383b9ac2051bfed6c9303b35d9c811980e6d1d5179c` | 1534 | `.local-archive/m4_r01_pre_backup/scripts/check_cloud_status.py` |
| `scripts/probe_servers.py` | 探测脚本去凭据fail-closed | `34e23bc898e72be82b46bf973a730789c845ec05e0b6b9b0a836805f553e33ab` | `c1de9c53ffcc7aaf11382a9d16743ce67c67cfe9b51ea15a2fe2315fd12c6d54` | 1382 | `.local-archive/m4_r01_pre_backup/scripts/probe_servers.py` |
| `tests/test_auth_security_fix.py` | 旧测夹具最小兼容（保留原断言） | `4071b1d88343c19f65bc01b9889d6dc7a1275de8e284f6b4ccc68bb828d56055` | `664a36b0a2a614bec22fdc7c18ce1fdb836cd0fee7e257c67b6de1bfcc150e6b` | 2651 | `.local-archive/m4_r01_pre_backup/tests/test_auth_security_fix.py` |
| `tests/test_admin_dashboard.py` | 旧测夹具最小兼容（保留原断言） | `21a414db9f939b197b523a283c5ba69d9a2043e8c1dcfe99cc05b3adef8e8752` | `64d599e0645c75c8660d160df8845344fa9399846328d815646cc7e8421ae872` | 5692 | `.local-archive/m4_r01_pre_backup/tests/test_admin_dashboard.py` |
| `tests/test_v0_0_8_llm_rules.py` | 旧测夹具最小兼容（保留原断言） | `9765eb7ac61882e2fe8334d3103e6352216ec471d8f491c1cb29e45f12493a6c` | `18441fdcecd0c1d5ea5ef1355bd8f5e3464a99f84473d86a205fedb0ac17766e` | 5039 | `.local-archive/m4_r01_pre_backup/tests/test_v0_0_8_llm_rules.py` |
| `tests/test_phase0_phase1.py` | 旧测夹具最小兼容（保留原断言） | `891dbe21f0f79dcd65e18e40aa57219862ef1818e27a52063fa72eea19009e3e` | `1cdf4e055183990d14990007f339098a8a718fb827e8e0ceccca41eba8af1d34` | 3926 | `.local-archive/m4_r01_pre_backup/tests/test_phase0_phase1.py` |
| `tests/test_flywheel_end2end.py` | 旧测夹具最小兼容（保留原断言） | `d47af7426ad2828d02725ac4589fc3325bbc30d8276e0b2699c074efb48c18c6` | `e14c024960314eb47ab633d9a9b2c91d57109e6e1e0f111a2422e7b2ae58a84b` | 9579 | `.local-archive/m4_r01_pre_backup/tests/test_flywheel_end2end.py` |
| `tests/test_binding_decisions.py` | 旧测夹具最小兼容（保留原断言） | `adb13a41e2a9cd0ed152164e86eb3e8debb43567299b10880a2ac5dda6f05878` | `d8e4cc8904fb73de7896fe5952af3d1b711976a23fafee9293c1f74a03dbdd0a` | 30063 | `.local-archive/m4_r01_pre_backup/tests/test_binding_decisions.py` |
| `tests/test_rule_deadlock_fix.py` | 旧测夹具最小兼容（保留原断言） | `561bb448bae14f19744bbe0d09d60bc0ffad30e464b27b7b6871e257d29a4ce6` | `9409992021da53b2a6d596a6e713307bff7797d9e4a9271e843724f39987d49e` | 2180 | `HEAD` / `m4_r01_coordinator_baseline.json` |
| `tests/test_m3_hard_constraints.py` | 旧测夹具最小兼容（保留原断言） | `81da3a23ccf2695d7987ad8912f96aaeff8267886745c2a258e97a619a920cea` | `24fc94f8e399f2f5f663a7869946ee93ccfaeb6db73ab5c4fe2fb5809deae19b` | 81553 | `.local-archive/m4_r01_pre_backup/tests/test_m3_hard_constraints.py` |
| `tests/test_m3_hard_constraints_http.py` | 旧测夹具最小兼容（保留原断言） | `ee1cb20cbbcaab86f009da3f3bbbaa2ca4552b9d047d04c56320b7a566ac2e1c` | `1adabbd3687b374d9b488be056b3d3c3a2ec45ec0d2f59e31a616bbadbb537d0` | 83085 | `.local-archive/m4_r01_pre_backup/tests/test_m3_hard_constraints_http.py` |

## 2. 范围外保护文件未变更确认（36 项）

统筹基线 `m4_r01_coordinator_baseline.json` 中除上述 21 项 M4 范围内的文件外，其余 **36 项** 保护文件（包含 `docker-compose.yml`、`app/matcher.py`、`app/binding_decisions.py`、`app/domain_extractors.py`、`app/domain_router.py`、`app/db_manager.py`、`app/flywheel_db.py`、`app/vector_index.py`、`tests/conftest.py`、`tests/verify_isolation.py`、M0～M2 回归测试及 M3 R04 审核/裁决文档等）当前 SHA256 与基线 100% 一致，`docker-compose.yml` 保持 `dc74a02b00e64e598ce675d0a1fe9214ef803d1022551e775d47e4eef557efc7`。

## 3. 本轮新增代码、测试、证据与交付文档（28 项）

| 文件 | 说明 | 当前交付 SHA256 | 大小(B) |
|---|---|---|---:|
| `scripts/_deploy_env.py` | 新增部署环境变量fail-closed加载模块 | `a9aa895e54885242d53fc1398a95bc91acd28c82062f55ad983eb8770d260f6d` | 3927 |
| `tests/test_m4_auth_lifecycle.py` | 新增M4认证与凭据生命周期测试(5项) | `231dbccd9077d2fd9eff97f609d4ab6f521059ce77d9a05c3a12ace8f0a7f100` | 19719 |
| `tests/test_m4_permissions_and_tasks.py` | 新增M4规则审批/任务隔离/F11测试(5项) | `635791610a90cfed0d879d14edc5d2e35802b32c19720f9d5a12bb79a1d7e02c` | 25593 |
| `doc/V0.1.5/evidence/m4_r01_browser_check.py` | 真实Chrome CDP端到端检查脚本 | `9696b2ac03219616634212b9bd7722665b5b73093b42e48d49139fb4aa9cecc2` | 18592 |
| `doc/V0.1.5/evidence/m4_r01_capture_evidence.py` | 离线双子进程三规则门禁采集脚本 | `f6481789046d4e6861e8f633724d59a3d637c464b8e57161be9d8c3d93587b1d` | 6333 |
| `doc/V0.1.5/evidence/m4_r01_rerun.ps1` | M4 R01一键复验脚本 | `1c75723e8ab077140e35f6be7566d2eac5b98bafa55e6041ca1385adb24f578e` | 2891 |
| `doc/V0.1.5/evidence/m4_r01_f11_rotation_runbook.md` | F11外部凭据轮换与镜像验证手册(待验证) | `8ed39503fa7eff369ac1801ab427b4664fa7c7dfd1912181f5033565ab59ffd2` | 3647 |
| `doc/V0.1.5/evidence/m4_r01_browser_e2e.json` | 真实Chrome E2E结果JSON | `59ed0b6773e42a3f6d4117a3987ad7d986fc4ec7ad3f9ce9c9abc193d531f4c0` | 286 |
| `doc/V0.1.5/evidence/m4_r01_browser_e2e.log` | 真实Chrome E2E控制台日志 | `e5f49d3dc63a9dd6071fc43fca987bed5a756ff69e61d9b2a16c54e36d827c30` | 1026 |
| `doc/V0.1.5/evidence/m4_r01_browser_e2e.png` | 真实Chrome E2E最终态截图 | `bf1ac5698dc15fc1c5655ea3f2d0de1bef4a46c23cc0d5eb1bb2d9c75a69fb06` | 95324 |
| `doc/V0.1.5/evidence/m4_r01_docker_check.log` | 本机Docker离线状态诚实日志(待验证) | `b12eacc51814f25e0bf463fb02662998afcc8e786111af01d6acea3e3533e8ac` | 2855 |
| `doc/V0.1.5/evidence/m4_r01_gate_reports.json` | 离线双子进程三规则门禁报告 | `36b47de462b0f565874d9c8f319f5eb5084c24bbe83d32b762e5f2c30cc15374` | 51096 |
| `doc/V0.1.5/evidence/m4_r01_m3_r04_gate_compare.json` | 相对M3 R04门禁得分对比diff=[] | `e32093d2ba0dbbbd5ae21b36f8d16d57ffc5989c620c3745c3187128c4cebc79` | 148 |
| `doc/V0.1.5/evidence/m4_r01_3rule_gate.txt` | 三规则门禁摘要日志 | `e2265d14e34af46fb8016b178f1bbaceda52a03c2a487556bc12c3911cedad12` | 484 |
| `doc/V0.1.5/evidence/m4_r01_outer_snapshot_before.json` | 复验前外层业务资产快照 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | 3496 |
| `doc/V0.1.5/evidence/m4_r01_outer_snapshot_after.json` | 复验后外层业务资产快照(与前一致) | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | 3496 |
| `doc/V0.1.5/evidence/m4_r01_venv_verify_before.json` | 复验前verify_isolation报告 | `dd474062775d64f9a7f364873855b0eed3e182e18da421af00ee7f034ec3fb41` | 12188 |
| `doc/V0.1.5/evidence/m4_r01_venv_verify_after.json` | 复验后verify_isolation报告 | `71eb7834b2281441a4a6a747e6d7a592738dcc71edf9aa0091cb20e38088c0f2` | 12188 |
| `doc/V0.1.5/evidence/m4_r01_py_compile.log` | py_compile -W error日志 | `fade8ab2324915a555181c0059b7df2b721876cec2c7b8cba69f08f1b4bfb547` | 41 |
| `doc/V0.1.5/evidence/m4_r01_pytest_m4.txt` | M4专项10 passed日志 | `d5287558a5528db79b297cd9be9a60d6fb91e74df2860d930ea6814f9467b0ed` | 2419 |
| `doc/V0.1.5/evidence/m4_r01_pytest_m3.txt` | M3回归99 passed日志 | `0b0d1c499521168d0c1b307ec4f6191cee1121298740b1c0f9a0132aa77fdf4e` | 14570 |
| `doc/V0.1.5/evidence/m4_r01_pytest_m0_m2_regression.txt` | M0-M2回归58 passed日志 | `ece0bae1e9feacd59d02f0c88b352033c8c9d5040ad84f260b9b3c23d5afe7e8` | 1623 |
| `doc/V0.1.5/evidence/m4_r01_pytest_sandbox_30.txt` | 沙盒边界30 passed日志 | `4901d27e9c1ec6639bbda350da8b645c08007f04bdf923098a01ce01aa0a980d` | 1554 |
| `doc/V0.1.5/evidence/m4_r01_pytest_full.txt` | 全量测试255 passed日志 | `b03de6c14809f20d8942756afe01b9242f15642fe25ab983b6b7d6468aaa396e` | 2725 |
| `doc/V0.1.5/rounds/V015-M4_R01_开发小结.md` | M4 R01开发小结 | `1ed0c128e4d7229e97ce6c7821df5a32ae758acf49dc5c3b156dc0c8f298dd97` | 20874 |
| `doc/V0.1.5/02_任务台账.md` | 台账更新开发小结与状态 | `a1835c299a4c374176f78ba85c2c9f9498fa0bd70ffa7d5bcf92ba21429bd55e` | 39019 |
| `doc/V0.1.5/rounds/README.md` | 轮次索引更新M4 R01开发入口 | `f0245a76f6f70a218da8115c34dfba05ab802e43ed640a833cf4c24e7e2aa862` | 6986 |
| `doc/V0.1.5/evidence/README.md` | 证据索引更新M4 R01自验清单 | `ddb81d61ecccea0490b096566834d39214b97cee0783c2316b2cd6eac6671026` | 22941 |
