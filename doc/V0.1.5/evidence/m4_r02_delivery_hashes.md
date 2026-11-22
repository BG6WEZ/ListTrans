# V015-M4 R02 开发交付 SHA256 核对清单

- 角色：**开发AI**（无 git commit/push/tag/checkout/reset）
- 基线：`HEAD` = `e964fb16848de9dece8e729e801b61c7d81dd341`
- 业务快照：`5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e`（前/后及相对 R01 下发前 8 项 `diffs=[]`）
- 本清单按磁盘实算写入，不含本清单自身 SHA256。

## 1. 本轮实际修改文件（14 项：11 份代码/配置/测试 + 3 份共享台账/索引）

| 文件 | 说明 | R02改动前 SHA256 | 当前交付 SHA256 | 大小(B) |
|---|---|---|---|---:|
| `app/auth_manager.py` | A-M4-01 Excel并发撤销 / A-M4-02 JWT验真 / A-M4-03 唯一临时口令 | `ff30e866ced18596f545eafe4cc7df0237560072b849f18a013d82f6e5d4611b` | `4d774c0016e3ad7f91345275a05dbcf61f0fa2e40664da5aba04dc65ee11b711` | 48396 |
| `app/server.py` | A-M4-02 移除save_auto_binding_rules无actor绕过 | `f91d9ab7ba73cb18a9f418b3ff829900be592d3d3f947ae45d6a350ae0eea94e` | `aaeba6ff6981d4f57b279fe78d70c277a130a11c1e3a8f76a2eb240ada25e891` | 140507 |
| `app/flywheel_sandbox.py` | A-M4-02 get_eval_task_status_for_user真认证 | `887e24957704b35c10912f5dc85b1deaa7d4749f617d86566644ddca716953d3` | `29f3152a2b0029a014e7b173743433a1e60321abf490305fcfc2a58b650ea843` | 27031 |
| `.dockerignore` | A-M4-04 敏感排除与16份config allowlist | `d37c2ec4512f4e016c8d665ced63761127e00346f710c34760994c0443617d74` | `4c40a6755581f7b0feb276d27dc25c8b9118304f55df7c47e2edc0f86dd243e1` | 1677 |
| `tests/test_m4_auth_lifecycle.py` | R02新增A-M4-01/03正式回归与重置断言 | `231dbccd9077d2fd9eff97f609d4ab6f521059ce77d9a05c3a12ace8f0a7f100` | `17583fbde0666263fcffd38b3f157b7ff88add535af42e918541192acfff710e` | 29643 |
| `tests/test_m4_permissions_and_tasks.py` | R02新增A-M4-02服务层鉴权与Docker允许清单 | `635791610a90cfed0d879d14edc5d2e35802b32c19720f9d5a12bb79a1d7e02c` | `5f258a8ac742f73830a94d93c40d2389157c6cd3947e91be6740eca6a4587abf` | 30001 |
| `tests/test_auto_binding.py` | 旧测夹具真认证适配(0删断言) | `0b32c687486edbe85bb0383c2435968526fa4db9fc594d97b60c53d611c8629e` | `d86af2d39f30aa91ab33d67f5da7749b2a3aa084385aaf39f3748099e4556b58` | 15091 |
| `tests/test_binding_decisions.py` | 旧测夹具真认证适配(0删断言) | `d8e4cc8904fb73de7896fe5952af3d1b711976a23fafee9293c1f74a03dbdd0a` | `9e9f18061ab8ce2840e378dcbc3660ff0fdeae2027f6ca069d2b2f6f3970026d` | 30474 |
| `tests/test_m3_hard_constraints.py` | 旧测夹具真认证适配(0删断言) | `24fc94f8e399f2f5f663a7869946ee93ccfaeb6db73ab5c4fe2fb5809deae19b` | `dac079439b10592e5713af4319b0063ddfb511aaca294a9d1196b26fba1546c4` | 81614 |
| `tests/test_m3_hard_constraints_http.py` | 旧测夹具真认证适配(0删断言) | `1adabbd3687b374d9b488be056b3d3c3a2ec45ec0d2f59e31a616bbadbb537d0` | `5af49d4c85f01901f5573a9aeb73c42197cc9873cf42ff83f667ce580e8ddc8d` | 83406 |
| `tests/test_v0_0_8_llm_rules.py` | 旧测夹具真认证适配(0删断言) | `18441fdcecd0c1d5ea5ef1355bd8f5e3464a99f84473d86a205fedb0ac17766e` | `558efc9a9876f0662b0d2fbef4819497aa226a77b0ab1dce217de0e837664a5a` | 5168 |
| `doc/V0.1.5/02_任务台账.md` | 台账追加R02开发交付状态与留痕 | `9205cfffd9e8a5db714d0c237102f7a6c0195a483c3de078e6a33e7c55339c47` | `71c82b1b0e806b6a3dae042d307146b4a49d9632c2104b7127cf70ec094f29fb` | 43609 |
| `doc/V0.1.5/rounds/README.md` | 轮次索引追加R02开发入口 | `659d2c4087d7592ac84f9f795aedc9249ef23d8f620b09b41876824a6588c9ec` | `54302304d8894b8a05df0ecbd104a770f3ae59875cbff38598734cb3f9664b8a` | 7725 |
| `doc/V0.1.5/evidence/README.md` | 证据索引追加R02实测清单 | `0f5f880b31769888ee46a71e3e9c56c713486e8ffccb25e146b6572f9a4985ff` | `04193864919ac0b4e5d040826e49f6341ff355f33748510b4724122542265bf5` | 25893 |

## 2. R02 备份区内未变更文件（21 项）与范围外保护文件确认

| 文件 | 当前 SHA256 | 大小(B) | 核对状态 |
|---|---|---:|---|
| `.env.example` | `2cf00504fd013eee4f357d7081625b1d151b59e4bddc621e83a99ebc3742342d` | 934 | 与 R01 交付备件一致 |
| `Dockerfile` | `bc5e8d54fb8758fd4f0ebdbb7c586d3c6d68cf5386ff570b17b7c7154259413a` | 1534 | 与 R01 交付备件一致 |
| `app/admin_api.py` | `ea4cd0998e6a812a18282004e9d45931b63b079bc081b96924ef3b6f7f2245bc` | 15058 | 与 R01 交付备件一致 |
| `app/auth_middleware.py` | `3c09aa6b339743581d5d6e6c38f2318b737f7caae6e6f18b0cd8b388c206eef3` | 3541 | 与 R01 交付备件一致 |
| `app/llm_rule_engine.py` | `bcf17254b356d87d433536171b5e55c861f23c32aa1e6db4cccb62648398d813` | 81023 | 与 R01 交付备件一致 |
| `app/rule_api.py` | `f83c22b3368e948267ceb0e7b4507457d5b872d4ddcbec6b4bf4259cd0e47c9c` | 6787 | 与 R01 交付备件一致 |
| `deploy_guide.md` | `c35df24850a940bb13c4e100324495c4ec708b0e544073b5360cb736b335276b` | 6183 | 与 R01 交付备件一致 |
| `docker-compose.yml` | `dc74a02b00e64e598ce675d0a1fe9214ef803d1022551e775d47e4eef557efc7` | 636 | 与 R01 交付备件一致 |
| `scripts/_deploy_env.py` | `a9aa895e54885242d53fc1398a95bc91acd28c82062f55ad983eb8770d260f6d` | 3927 | 与 R01 交付备件一致 |
| `scripts/check_cloud_status.py` | `78819b5b8e157b491521a383b9ac2051bfed6c9303b35d9c811980e6d1d5179c` | 1534 | 与 R01 交付备件一致 |
| `scripts/deploy_cloud.py` | `f1a7580878839eb84a84c49704fefe99b3d8eb5493c8caf29efe19f906f239fd` | 5307 | 与 R01 交付备件一致 |
| `scripts/deploy_to_cloud_safe.py` | `81b7d84d0ff05621da63f723facf1d9a39d17f742618b9a0acca584ceb4976b8` | 7274 | 与 R01 交付备件一致 |
| `scripts/probe_servers.py` | `c1de9c53ffcc7aaf11382a9d16743ce67c67cfe9b51ea15a2fe2315fd12c6d54` | 1382 | 与 R01 交付备件一致 |
| `tests/test_admin_dashboard.py` | `64d599e0645c75c8660d160df8845344fa9399846328d815646cc7e8421ae872` | 5692 | 与 R01 交付备件一致 |
| `tests/test_auth_security_fix.py` | `664a36b0a2a614bec22fdc7c18ce1fdb836cd0fee7e257c67b6de1bfcc150e6b` | 2651 | 与 R01 交付备件一致 |
| `tests/test_flywheel_end2end.py` | `e14c024960314eb47ab633d9a9b2c91d57109e6e1e0f111a2422e7b2ae58a84b` | 9579 | 与 R01 交付备件一致 |
| `tests/test_phase0_phase1.py` | `1cdf4e055183990d14990007f339098a8a718fb827e8e0ceccca41eba8af1d34` | 3926 | 与 R01 交付备件一致 |
| `web/admin.html` | `ed1a3eeb9c6ef09683c16924a0c436966fa91de7020767b7cb198dedacea0669` | 154682 | 与 R01 交付备件一致 |
| `web/app.js` | `3b37776865e1328f85158c4d138ab799ada290c13675c7330e79ae650860f35c` | 179435 | 与 R01 交付备件一致 |
| `web/index.html` | `9794bf2c45aa4441ecf29510542e27ed50dd46703324f82629ea55a9a95f5cae` | 125949 | 与 R01 交付备件一致 |
| `web/login.html` | `f8fe49b0a1315ca5bdf86c79c85d7a78a4111af42880cec1cd3520c8a5af9cb5` | 7400 | 与 R01 交付备件一致 |

说明：R01 审核交付 `m4_r01_audit_delivery_hashes.md` 中除 3 份共享台账/索引外的 **44 项** 审核/统筹文档及证据 SHA256 100% 保持不变；统筹基线 `m4_r01_coordinator_baseline.json` 中除 R01 改动的 29 项、R02 适配的 `tests/test_auto_binding.py` 与 3 份共享台账/索引外的 **35 项** 保护文件 100% 保持不变（`docker-compose.yml` 为 `dc74a02b00e64e598ce675d0a1fe9214ef803d1022551e775d47e4eef557efc7`）。

## 3. 本轮新增开发小结、脚本与证据产物（不含本清单）

| 文件 | 说明 | 当前交付 SHA256 | 大小(B) |
|---|---|---|---:|
| `doc/V0.1.5/rounds/V015-M4_R02_开发小结.md` | R02开发小结 | `fb9684b20ae760b95c5c3d6866a5f992332d4431252dabdee5c8f839b3f6072c` | 15593 |
| `doc/V0.1.5/evidence/m4_r02_01_forced_pwd_change.png` | R02验证脚本/证据/截图/日志 | `99e85bdc51b7749094c7204a78563673c8c042448327978e436e42ace82df6af` | 100044 |
| `doc/V0.1.5/evidence/m4_r02_02_employee_forbidden.png` | R02验证脚本/证据/截图/日志 | `0f4e6b2f88203f8483ae53d20ec12b355e7257594790568b43c4f36c58429e67` | 95315 |
| `doc/V0.1.5/evidence/m4_r02_03_dual_approval_and_isolation.png` | R02验证脚本/证据/截图/日志 | `c9b2f8722cd70c2e4c100d78770215bfe8e303666b3ec4ebf6d7f24c394004f0` | 160637 |
| `doc/V0.1.5/evidence/m4_r02_3rule_check.json` | R02验证脚本/证据/截图/日志 | `8f7054b812d461b43ec73815f2bf90237b81f9647da693d960176103e48e6340` | 233 |
| `doc/V0.1.5/evidence/m4_r02_3rule_gate.log` | R02验证脚本/证据/截图/日志 | `0f74bacca5982979b5269c20aca12d3286606c5d6b24273935f432bebf010dda` | 484 |
| `doc/V0.1.5/evidence/m4_r02_assert_check.log` | R02验证脚本/证据/截图/日志 | `13b76bc56e26ba0b76b2fac3e96267c4fb926c07a02157c4364ba90a7e6b9dce` | 436 |
| `doc/V0.1.5/evidence/m4_r02_assert_check.py` | R02验证脚本/证据/截图/日志 | `0a03414fbb60d88a967800678fd9cdeb08a9c7d6921cd1d9ba68b3060404e493` | 2192 |
| `doc/V0.1.5/evidence/m4_r02_browser_check.py` | R02验证脚本/证据/截图/日志 | `552724de159709cc6bcf65f93e1871f37b5bf1f04591942f9af8c9d9e96e5b04` | 21192 |
| `doc/V0.1.5/evidence/m4_r02_browser_e2e.json` | R02验证脚本/证据/截图/日志 | `25ee4cd4f4f0a83fd3fe818f115e194bda7f84878619cc5411983df87ff706f7` | 334 |
| `doc/V0.1.5/evidence/m4_r02_browser_e2e.log` | R02验证脚本/证据/截图/日志 | `99d2ce7c325cc1f4750ddea096723566bd29dcf52f74e6cf183630533d1bd796` | 1069 |
| `doc/V0.1.5/evidence/m4_r02_capture_evidence.py` | R02验证脚本/证据/截图/日志 | `65ea3657c333c647949a89d2011d464449793f996015b2960d71d46da6c618ce` | 6657 |
| `doc/V0.1.5/evidence/m4_r02_docker_check.log` | R02验证脚本/证据/截图/日志 | `719baf0a5512ae358007739201c6a18331d011419ff0d7965fdd53f144778f53` | 988 |
| `doc/V0.1.5/evidence/m4_r02_docker_probe.py` | R02验证脚本/证据/截图/日志 | `9a0a596a058a6d15da4998f65dd00b9fe0ce52fce29211ce228b3ec283d61ef8` | 19045 |
| `doc/V0.1.5/evidence/m4_r02_docker_probe_stdout.log` | R02验证脚本/证据/截图/日志 | `ddbc48b126244f10c1014e5cf4291d727563db67e7c672d05d47e1c5a8e17b45` | 1059 |
| `doc/V0.1.5/evidence/m4_r02_docker_probes.json` | R02验证脚本/证据/截图/日志 | `9c21e85d8efe5b3827f0dfc5c53fbc3f53ef38b6a8b41f8c53dc4383b51883a9` | 6820 |
| `doc/V0.1.5/evidence/m4_r02_f11_rotation_runbook.md` | R02验证脚本/证据/截图/日志 | `293be2d02db5c431067c44697fe262892ff9c8b47822b76f27328722399068cf` | 4235 |
| `doc/V0.1.5/evidence/m4_r02_full_pytest.log` | R02验证脚本/证据/截图/日志 | `eb4c49b1428f6defb0437599094dddef65dc710f9084ddf9857f46d904b723e0` | 2725 |
| `doc/V0.1.5/evidence/m4_r02_gate_reports.json` | R02验证脚本/证据/截图/日志 | `02fb19543f74f53f3a30309f40b4a2876805aa1f0f10e2ed17f490a9963d7fe4` | 51096 |
| `doc/V0.1.5/evidence/m4_r02_isolation_verify.json` | R02验证脚本/证据/截图/日志 | `9b3c223fee8b72870fb1fdd9ca2dc28e9f808d876014c2ca7b9f8ba3c4b1e240` | 12188 |
| `doc/V0.1.5/evidence/m4_r02_isolation_verify.log` | R02验证脚本/证据/截图/日志 | `335517bd54570375d2492f610c30ee2e3df6cd30474803a62f076f8721656832` | 381 |
| `doc/V0.1.5/evidence/m4_r02_isolation_verify_before.json` | R02验证脚本/证据/截图/日志 | `93f1067f933dac9a7364401c2101ee060ebc83ec3366002914e46e7bcea023a2` | 12188 |
| `doc/V0.1.5/evidence/m4_r02_isolation_verify_before.log` | R02验证脚本/证据/截图/日志 | `d9fceb8c8b6990856e57780d4002fead2b46aad9dcc97ec84b41b814da62a7b3` | 389 |
| `doc/V0.1.5/evidence/m4_r02_m0_m2_regression.log` | R02验证脚本/证据/截图/日志 | `cb57a712fe2778231b2decfbf37c1573ca263f04bfc3d0825415ea9c4088e731` | 1622 |
| `doc/V0.1.5/evidence/m4_r02_m3_regression.log` | R02验证脚本/证据/截图/日志 | `d7e82f315048d7e1bcde2c584feaebf24a92489f2eb5a3dc0a694c141e9d5568` | 1511 |
| `doc/V0.1.5/evidence/m4_r02_pre_baseline_hashes.md` | R02验证脚本/证据/截图/日志 | `6a5320346889558c2e849b2538934c69bfa112f5690545be4d956726b9c39e43` | 5749 |
| `doc/V0.1.5/evidence/m4_r02_probes.json` | R02验证脚本/证据/截图/日志 | `943f77daf6980667fa5390bc933734e6a93f1de06d13d8f2c98037a6dbb11c1b` | 2687 |
| `doc/V0.1.5/evidence/m4_r02_probes.log` | R02验证脚本/证据/截图/日志 | `30576fe033a2e129f5d670bd68e51615057228f2225c41dbae80550a63323361` | 1489 |
| `doc/V0.1.5/evidence/m4_r02_probes.py` | R02验证脚本/证据/截图/日志 | `c95f77c899c683bf1abecc268cabe13a85d619440469ed8092d92bd994f5c85d` | 16626 |
| `doc/V0.1.5/evidence/m4_r02_py_compile.log` | R02验证脚本/证据/截图/日志 | `b32d879a6fa23e0c0e624722f4735b2ec063da62e190baef0f865b5539ac5967` | 40 |
| `doc/V0.1.5/evidence/m4_r02_pytest.log` | R02验证脚本/证据/截图/日志 | `63e7c832932119cbee463a53a66f00afc3d2affc777d0760cf423a75676ea447` | 2789 |
| `doc/V0.1.5/evidence/m4_r02_rerun.ps1` | R02验证脚本/证据/截图/日志 | `2508a7e5cb83d18e238eb997e8ec8c70d30504b7c332321f00a4748f2af80da4` | 3286 |
| `doc/V0.1.5/evidence/m4_r02_sandbox_regression.log` | R02验证脚本/证据/截图/日志 | `a305271eb3fa20cd58fcb5827d3480b4e26b3b78978d97c284c274caf3119854` | 1556 |
| `doc/V0.1.5/evidence/m4_r02_snapshot_after.json` | R02验证脚本/证据/截图/日志 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | 3496 |
| `doc/V0.1.5/evidence/m4_r02_snapshot_after.log` | R02验证脚本/证据/截图/日志 | `4645df937b9e3c2f4eca15e71b6d049d71bb8daa74eb3d38bad145eacd079448` | 118 |
| `doc/V0.1.5/evidence/m4_r02_snapshot_before.json` | R02验证脚本/证据/截图/日志 | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` | 3496 |
| `doc/V0.1.5/evidence/m4_r02_snapshot_before.log` | R02验证脚本/证据/截图/日志 | `c2b67cbb14ed30dd8cf3c06813a6969470f3419a0d9df4759cf8c226e374b8b5` | 120 |
| `doc/V0.1.5/evidence/m4_r02_snapshot_diff.txt` | R02验证脚本/证据/截图/日志 | `0b0135e3fb19f92b01a52fe3da9e407c9cf7dfb5006b968a902d5177bf4c5755` | 318 |
| `doc/V0.1.5/evidence/m4_r02_test_assertions_check.json` | R02验证脚本/证据/截图/日志 | `8eb23410f381aecc9c9ad3e8dc2494029add787d56362324a744232ed0efe718` | 1150 |
