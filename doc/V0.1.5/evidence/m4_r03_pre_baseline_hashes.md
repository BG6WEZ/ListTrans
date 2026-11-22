# V015-M4 R03 改动前基线哈希与本地备份清单

- 基线提交：`HEAD` = `e964fb16848de9dece8e729e801b61c7d81dd341`（无 commit/push/tag/checkout/reset）
- 备份目录：`.local-archive/m4_r03_pre_backup/`（完整保留 `.local-archive/m4_r01_pre_backup/`、`.local-archive/m4_r02_pre_backup/` 与 `.local-archive/m3_*`）

| 文件 | 改动前 SHA256 | 大小(B) | 备份位置 |
|---|---|---:|---|
| `.dockerignore` | `4c40a6755581f7b0feb276d27dc25c8b9118304f55df7c47e2edc0f86dd243e1` | 1677 | `.local-archive/m4_r03_pre_backup/.dockerignore` |
| `.env.example` | `2cf00504fd013eee4f357d7081625b1d151b59e4bddc621e83a99ebc3742342d` | 934 | `.local-archive/m4_r03_pre_backup/.env.example` |
| `Dockerfile` | `bc5e8d54fb8758fd4f0ebdbb7c586d3c6d68cf5386ff570b17b7c7154259413a` | 1534 | `.local-archive/m4_r03_pre_backup/Dockerfile` |
| `app/admin_api.py` | `ea4cd0998e6a812a18282004e9d45931b63b079bc081b96924ef3b6f7f2245bc` | 15058 | `.local-archive/m4_r03_pre_backup/app/admin_api.py` |
| `app/auth_manager.py` | `4d774c0016e3ad7f91345275a05dbcf61f0fa2e40664da5aba04dc65ee11b711` | 48396 | `.local-archive/m4_r03_pre_backup/app/auth_manager.py` |
| `app/auth_middleware.py` | `3c09aa6b339743581d5d6e6c38f2318b737f7caae6e6f18b0cd8b388c206eef3` | 3541 | `.local-archive/m4_r03_pre_backup/app/auth_middleware.py` |
| `app/flywheel_sandbox.py` | `29f3152a2b0029a014e7b173743433a1e60321abf490305fcfc2a58b650ea843` | 27031 | `.local-archive/m4_r03_pre_backup/app/flywheel_sandbox.py` |
| `app/llm_rule_engine.py` | `bcf17254b356d87d433536171b5e55c861f23c32aa1e6db4cccb62648398d813` | 81023 | `.local-archive/m4_r03_pre_backup/app/llm_rule_engine.py` |
| `app/rule_api.py` | `f83c22b3368e948267ceb0e7b4507457d5b872d4ddcbec6b4bf4259cd0e47c9c` | 6787 | `.local-archive/m4_r03_pre_backup/app/rule_api.py` |
| `app/server.py` | `aaeba6ff6981d4f57b279fe78d70c277a130a11c1e3a8f76a2eb240ada25e891` | 140507 | `.local-archive/m4_r03_pre_backup/app/server.py` |
| `deploy_guide.md` | `c35df24850a940bb13c4e100324495c4ec708b0e544073b5360cb736b335276b` | 6183 | `.local-archive/m4_r03_pre_backup/deploy_guide.md` |
| `docker-compose.yml` | `dc74a02b00e64e598ce675d0a1fe9214ef803d1022551e775d47e4eef557efc7` | 636 | `.local-archive/m4_r03_pre_backup/docker-compose.yml` |
| `scripts/_deploy_env.py` | `a9aa895e54885242d53fc1398a95bc91acd28c82062f55ad983eb8770d260f6d` | 3927 | `.local-archive/m4_r03_pre_backup/scripts/_deploy_env.py` |
| `scripts/check_cloud_status.py` | `78819b5b8e157b491521a383b9ac2051bfed6c9303b35d9c811980e6d1d5179c` | 1534 | `.local-archive/m4_r03_pre_backup/scripts/check_cloud_status.py` |
| `scripts/deploy_cloud.py` | `f1a7580878839eb84a84c49704fefe99b3d8eb5493c8caf29efe19f906f239fd` | 5307 | `.local-archive/m4_r03_pre_backup/scripts/deploy_cloud.py` |
| `scripts/deploy_to_cloud_safe.py` | `81b7d84d0ff05621da63f723facf1d9a39d17f742618b9a0acca584ceb4976b8` | 7274 | `.local-archive/m4_r03_pre_backup/scripts/deploy_to_cloud_safe.py` |
| `scripts/probe_servers.py` | `c1de9c53ffcc7aaf11382a9d16743ce67c67cfe9b51ea15a2fe2315fd12c6d54` | 1382 | `.local-archive/m4_r03_pre_backup/scripts/probe_servers.py` |
| `tests/test_admin_dashboard.py` | `64d599e0645c75c8660d160df8845344fa9399846328d815646cc7e8421ae872` | 5692 | `.local-archive/m4_r03_pre_backup/tests/test_admin_dashboard.py` |
| `tests/test_auth_security_fix.py` | `664a36b0a2a614bec22fdc7c18ce1fdb836cd0fee7e257c67b6de1bfcc150e6b` | 2651 | `.local-archive/m4_r03_pre_backup/tests/test_auth_security_fix.py` |
| `tests/test_auto_binding.py` | `d86af2d39f30aa91ab33d67f5da7749b2a3aa084385aaf39f3748099e4556b58` | 15091 | `.local-archive/m4_r03_pre_backup/tests/test_auto_binding.py` |
| `tests/test_binding_decisions.py` | `9e9f18061ab8ce2840e378dcbc3660ff0fdeae2027f6ca069d2b2f6f3970026d` | 30474 | `.local-archive/m4_r03_pre_backup/tests/test_binding_decisions.py` |
| `tests/test_flywheel_end2end.py` | `e14c024960314eb47ab633d9a9b2c91d57109e6e1e0f111a2422e7b2ae58a84b` | 9579 | `.local-archive/m4_r03_pre_backup/tests/test_flywheel_end2end.py` |
| `tests/test_m3_hard_constraints.py` | `dac079439b10592e5713af4319b0063ddfb511aaca294a9d1196b26fba1546c4` | 81614 | `.local-archive/m4_r03_pre_backup/tests/test_m3_hard_constraints.py` |
| `tests/test_m3_hard_constraints_http.py` | `5af49d4c85f01901f5573a9aeb73c42197cc9873cf42ff83f667ce580e8ddc8d` | 83406 | `.local-archive/m4_r03_pre_backup/tests/test_m3_hard_constraints_http.py` |
| `tests/test_m4_auth_lifecycle.py` | `17583fbde0666263fcffd38b3f157b7ff88add535af42e918541192acfff710e` | 29643 | `.local-archive/m4_r03_pre_backup/tests/test_m4_auth_lifecycle.py` |
| `tests/test_m4_permissions_and_tasks.py` | `5f258a8ac742f73830a94d93c40d2389157c6cd3947e91be6740eca6a4587abf` | 30001 | `.local-archive/m4_r03_pre_backup/tests/test_m4_permissions_and_tasks.py` |
| `tests/test_phase0_phase1.py` | `1cdf4e055183990d14990007f339098a8a718fb827e8e0ceccca41eba8af1d34` | 3926 | `.local-archive/m4_r03_pre_backup/tests/test_phase0_phase1.py` |
| `tests/test_v0_0_8_llm_rules.py` | `558efc9a9876f0662b0d2fbef4819497aa226a77b0ab1dce217de0e837664a5a` | 5168 | `.local-archive/m4_r03_pre_backup/tests/test_v0_0_8_llm_rules.py` |
| `web/admin.html` | `ed1a3eeb9c6ef09683c16924a0c436966fa91de7020767b7cb198dedacea0669` | 154682 | `.local-archive/m4_r03_pre_backup/web/admin.html` |
| `web/app.js` | `3b37776865e1328f85158c4d138ab799ada290c13675c7330e79ae650860f35c` | 179435 | `.local-archive/m4_r03_pre_backup/web/app.js` |
| `web/index.html` | `9794bf2c45aa4441ecf29510542e27ed50dd46703324f82629ea55a9a95f5cae` | 125949 | `.local-archive/m4_r03_pre_backup/web/index.html` |
| `web/login.html` | `f8fe49b0a1315ca5bdf86c79c85d7a78a4111af42880cec1cd3520c8a5af9cb5` | 7400 | `.local-archive/m4_r03_pre_backup/web/login.html` |
