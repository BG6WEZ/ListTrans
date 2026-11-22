# V015-M4 R02 改动前基线哈希与本地备份清单

- 基线提交：`HEAD` = `e964fb16848de9dece8e729e801b61c7d81dd341`（无 commit/push/tag/checkout/reset）
- 备份目录：`.local-archive/m4_r02_pre_backup/`（完整保留 `.local-archive/m4_r01_pre_backup/` 与 `.local-archive/m3_*`）

| 文件 | 改动前 SHA256 | 大小(B) | 备份位置 |
|---|---|---:|---|
| `app/auth_manager.py` | `ff30e866ced18596f545eafe4cc7df0237560072b849f18a013d82f6e5d4611b` | 43396 | `.local-archive/m4_r02_pre_backup/app/auth_manager.py` |
| `app/auth_middleware.py` | `3c09aa6b339743581d5d6e6c38f2318b737f7caae6e6f18b0cd8b388c206eef3` | 3541 | `.local-archive/m4_r02_pre_backup/app/auth_middleware.py` |
| `app/admin_api.py` | `ea4cd0998e6a812a18282004e9d45931b63b079bc081b96924ef3b6f7f2245bc` | 15058 | `.local-archive/m4_r02_pre_backup/app/admin_api.py` |
| `app/rule_api.py` | `f83c22b3368e948267ceb0e7b4507457d5b872d4ddcbec6b4bf4259cd0e47c9c` | 6787 | `.local-archive/m4_r02_pre_backup/app/rule_api.py` |
| `app/llm_rule_engine.py` | `bcf17254b356d87d433536171b5e55c861f23c32aa1e6db4cccb62648398d813` | 81023 | `.local-archive/m4_r02_pre_backup/app/llm_rule_engine.py` |
| `app/flywheel_sandbox.py` | `887e24957704b35c10912f5dc85b1deaa7d4749f617d86566644ddca716953d3` | 26958 | `.local-archive/m4_r02_pre_backup/app/flywheel_sandbox.py` |
| `app/server.py` | `f91d9ab7ba73cb18a9f418b3ff829900be592d3d3f947ae45d6a350ae0eea94e` | 140499 | `.local-archive/m4_r02_pre_backup/app/server.py` |
| `web/login.html` | `f8fe49b0a1315ca5bdf86c79c85d7a78a4111af42880cec1cd3520c8a5af9cb5` | 7400 | `.local-archive/m4_r02_pre_backup/web/login.html` |
| `web/index.html` | `9794bf2c45aa4441ecf29510542e27ed50dd46703324f82629ea55a9a95f5cae` | 125949 | `.local-archive/m4_r02_pre_backup/web/index.html` |
| `web/app.js` | `3b37776865e1328f85158c4d138ab799ada290c13675c7330e79ae650860f35c` | 179435 | `.local-archive/m4_r02_pre_backup/web/app.js` |
| `web/admin.html` | `ed1a3eeb9c6ef09683c16924a0c436966fa91de7020767b7cb198dedacea0669` | 154682 | `.local-archive/m4_r02_pre_backup/web/admin.html` |
| `Dockerfile` | `bc5e8d54fb8758fd4f0ebdbb7c586d3c6d68cf5386ff570b17b7c7154259413a` | 1534 | `.local-archive/m4_r02_pre_backup/Dockerfile` |
| `.dockerignore` | `d37c2ec4512f4e016c8d665ced63761127e00346f710c34760994c0443617d74` | 810 | `.local-archive/m4_r02_pre_backup/.dockerignore` |
| `docker-compose.yml` | `dc74a02b00e64e598ce675d0a1fe9214ef803d1022551e775d47e4eef557efc7` | 636 | `.local-archive/m4_r02_pre_backup/docker-compose.yml` |
| `.env.example` | `2cf00504fd013eee4f357d7081625b1d151b59e4bddc621e83a99ebc3742342d` | 934 | `.local-archive/m4_r02_pre_backup/.env.example` |
| `deploy_guide.md` | `c35df24850a940bb13c4e100324495c4ec708b0e544073b5360cb736b335276b` | 6183 | `.local-archive/m4_r02_pre_backup/deploy_guide.md` |
| `scripts/deploy_cloud.py` | `f1a7580878839eb84a84c49704fefe99b3d8eb5493c8caf29efe19f906f239fd` | 5307 | `.local-archive/m4_r02_pre_backup/scripts/deploy_cloud.py` |
| `scripts/deploy_to_cloud_safe.py` | `81b7d84d0ff05621da63f723facf1d9a39d17f742618b9a0acca584ceb4976b8` | 7274 | `.local-archive/m4_r02_pre_backup/scripts/deploy_to_cloud_safe.py` |
| `scripts/check_cloud_status.py` | `78819b5b8e157b491521a383b9ac2051bfed6c9303b35d9c811980e6d1d5179c` | 1534 | `.local-archive/m4_r02_pre_backup/scripts/check_cloud_status.py` |
| `scripts/probe_servers.py` | `c1de9c53ffcc7aaf11382a9d16743ce67c67cfe9b51ea15a2fe2315fd12c6d54` | 1382 | `.local-archive/m4_r02_pre_backup/scripts/probe_servers.py` |
| `scripts/_deploy_env.py` | `a9aa895e54885242d53fc1398a95bc91acd28c82062f55ad983eb8770d260f6d` | 3927 | `.local-archive/m4_r02_pre_backup/scripts/_deploy_env.py` |
| `tests/test_auth_security_fix.py` | `664a36b0a2a614bec22fdc7c18ce1fdb836cd0fee7e257c67b6de1bfcc150e6b` | 2651 | `.local-archive/m4_r02_pre_backup/tests/test_auth_security_fix.py` |
| `tests/test_admin_dashboard.py` | `64d599e0645c75c8660d160df8845344fa9399846328d815646cc7e8421ae872` | 5692 | `.local-archive/m4_r02_pre_backup/tests/test_admin_dashboard.py` |
| `tests/test_v0_0_8_llm_rules.py` | `18441fdcecd0c1d5ea5ef1355bd8f5e3464a99f84473d86a205fedb0ac17766e` | 5039 | `.local-archive/m4_r02_pre_backup/tests/test_v0_0_8_llm_rules.py` |
| `tests/test_phase0_phase1.py` | `1cdf4e055183990d14990007f339098a8a718fb827e8e0ceccca41eba8af1d34` | 3926 | `.local-archive/m4_r02_pre_backup/tests/test_phase0_phase1.py` |
| `tests/test_flywheel_end2end.py` | `e14c024960314eb47ab633d9a9b2c91d57109e6e1e0f111a2422e7b2ae58a84b` | 9579 | `.local-archive/m4_r02_pre_backup/tests/test_flywheel_end2end.py` |
| `tests/test_binding_decisions.py` | `d8e4cc8904fb73de7896fe5952af3d1b711976a23fafee9293c1f74a03dbdd0a` | 30063 | `.local-archive/m4_r02_pre_backup/tests/test_binding_decisions.py` |
| `tests/test_m3_hard_constraints.py` | `24fc94f8e399f2f5f663a7869946ee93ccfaeb6db73ab5c4fe2fb5809deae19b` | 81553 | `.local-archive/m4_r02_pre_backup/tests/test_m3_hard_constraints.py` |
| `tests/test_m3_hard_constraints_http.py` | `1adabbd3687b374d9b488be056b3d3c3a2ec45ec0d2f59e31a616bbadbb537d0` | 83085 | `.local-archive/m4_r02_pre_backup/tests/test_m3_hard_constraints_http.py` |
| `tests/test_auto_binding.py` | `0b32c687486edbe85bb0383c2435968526fa4db9fc594d97b60c53d611c8629e` | 14432 | `.local-archive/m4_r02_pre_backup/tests/test_auto_binding.py` |
| `tests/test_m4_auth_lifecycle.py` | `231dbccd9077d2fd9eff97f609d4ab6f521059ce77d9a05c3a12ace8f0a7f100` | 19719 | `.local-archive/m4_r02_pre_backup/tests/test_m4_auth_lifecycle.py` |
| `tests/test_m4_permissions_and_tasks.py` | `635791610a90cfed0d879d14edc5d2e35802b32c19720f9d5a12bb79a1d7e02c` | 25593 | `.local-archive/m4_r02_pre_backup/tests/test_m4_permissions_and_tasks.py` |
