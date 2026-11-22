# V015-M4 R01 独立审核与统筹裁决交付SHA256

结论：退回整改；仅下发同包R02，不放行M4/M5或后续版本。

HEAD: `e964fb16848de9dece8e729e801b61c7d81dd341`。本轮未改实现/开发测试，未执行Git写操作。

命令：`.\venv\Scripts\python.exe doc/V0.1.5/evidence/m4_r01_audit_close.py`。

清单不含自身；Docker tar仅含合成canary，不含真实账号/秘密。

| 文件 | SHA256 |
|---|---|
| `doc/00_项目管理/版本路线图.md` | `534def8a73c7e9b65b3929d2ddd8d7dd9aeba1b1a390f520e99f91fd3f66ddee` |
| `doc/00_项目管理/进度总表.md` | `67a3edaf187f2eabff6acbe663bdc23313871eeb44baf80ea23caa20efc73ed2` |
| `doc/00_项目管理/问题与版本归属总台账.md` | `54e1ff303f8d0839f1669c9a599932e11b92bb4b4c4f37904a23fe33cb814d64` |
| `doc/README.md` | `5c391a25f26f864f32a3eb14d2e3f7ac912753f03dc1a7987e2063fc3e6d2fcc` |
| `doc/V0.1.5/01_实施方案与任务包.md` | `e07103bead5a011f04ce9e4550115afb60f668bbf51a8326a6e44bb760a6ebae` |
| `doc/V0.1.5/02_任务台账.md` | `9205cfffd9e8a5db714d0c237102f7a6c0195a483c3de078e6a33e7c55339c47` |
| `doc/V0.1.5/03_终审与版本准入.md` | `ea4ac1010cb91ce5c2344f2a892f6c7bc6009fc495646b308eee053d8d1ef42f` |
| `doc/V0.1.5/evidence/README.md` | `0f5f880b31769888ee46a71e3e9c56c713486e8ffccb25e146b6572f9a4985ff` |
| `doc/V0.1.5/evidence/m4_r01_audit_baseline.json` | `e4111c3367daab076168ab49edd7715bb0d8d800496549d0ab35ffaa7c86aeed` |
| `doc/V0.1.5/evidence/m4_r01_audit_boundaries.log` | `889c83c4898305b5aa619f28b168f5551b23264fd0848edf1f19e397fc3c41d0` |
| `doc/V0.1.5/evidence/m4_r01_audit_browser.log` | `a3af0b74ae040e6303ddae5cee18786e7d1931acf28bb7b9a939f7504dea1c00` |
| `doc/V0.1.5/evidence/m4_r01_audit_browser/m4_r01_browser_e2e.json` | `256c6049972c831a8ba49cdec5aaff3f22e8da5adca3d5337b793af88589282e` |
| `doc/V0.1.5/evidence/m4_r01_audit_browser/m4_r01_browser_e2e.png` | `0f4e6b2f88203f8483ae53d20ec12b355e7257594790568b43c4f36c58429e67` |
| `doc/V0.1.5/evidence/m4_r01_audit_close.py` | `d7112bcbd43a9ebabaca1a32de984155dcacde74e2a910358b53eb45a33539f5` |
| `doc/V0.1.5/evidence/m4_r01_audit_closure.json` | `91445de22717ffb44beffcd86a1946320b5d3a1e7d3af98eac97abd8a8a5d596` |
| `doc/V0.1.5/evidence/m4_r01_audit_compile.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `doc/V0.1.5/evidence/m4_r01_audit_docker.log` | `1939f944a9284da9750da0da9a2a9e672bd6168dc9853331b1315376dc71d429` |
| `doc/V0.1.5/evidence/m4_r01_audit_docker_context.log` | `4a4647c7a7844e4e56d4832369982b3c21b9e6ce351fcec78c95c19e338391b8` |
| `doc/V0.1.5/evidence/m4_r01_audit_docker_context.tar` | `270961213993010a5974e2eee5f3d68535ff2b971570430f3ba92366f4dd2cbe` |
| `doc/V0.1.5/evidence/m4_r01_audit_docker_copy_patterns.log` | `3b0c21b1237baf77732d4b6b697335c6d8af2aa731dc3b9aa79642d5df4f88e8` |
| `doc/V0.1.5/evidence/m4_r01_audit_docker_copy_patterns.tar` | `b2d0df62f33c81526972c9339f6ad898c4c77ddf6f7552ebffcd6b837af057b0` |
| `doc/V0.1.5/evidence/m4_r01_audit_docker_probe.py` | `311c9bf31dbe1294abc15c269112259dee90c4dce6c90991f61e755f737152fa` |
| `doc/V0.1.5/evidence/m4_r01_audit_docker_probes.json` | `c155a732e16fd42eb2540ef80b96a48ab3370e3d8f8b0e5efde759b57de11cd1` |
| `doc/V0.1.5/evidence/m4_r01_audit_full.log` | `632b64d2dbb6320cec08db927f1d82ecd4b98723c4d9425b2c93a651a1436f02` |
| `doc/V0.1.5/evidence/m4_r01_audit_gate.log` | `abb736c5d13fe02c017b5cc680c165dd8f011e8b4ae52956e7137831fc1b5e1e` |
| `doc/V0.1.5/evidence/m4_r01_audit_gate_compare.json` | `e32093d2ba0dbbbd5ae21b36f8d16d57ffc5989c620c3745c3187128c4cebc79` |
| `doc/V0.1.5/evidence/m4_r01_audit_gate_reports.json` | `195e31c55d28134c78d999e4863239fc49e917b974d6f8358cbe20da5f11f068` |
| `doc/V0.1.5/evidence/m4_r01_audit_integration.py` | `7789471bd3680ea804d83be9623167e802a02ae00eaa5c0cd8ecf25ce235b843` |
| `doc/V0.1.5/evidence/m4_r01_audit_integration_summary.json` | `50719b30ad93e3b2ac19b4c9bed4ebbd1df88c50a7e5625d615dd67dcbfb2951` |
| `doc/V0.1.5/evidence/m4_r01_audit_m3.log` | `30109c14e74f22c935dd9a7e34c9ffab5ae71069fc3b7736d835df2fcd9e65b8` |
| `doc/V0.1.5/evidence/m4_r01_audit_m4.log` | `5e6d78357f825d4797d9465039620fec8de116a0ed8bfd213124d5da8cda2c09` |
| `doc/V0.1.5/evidence/m4_r01_audit_probes.json` | `b3e28242bf2eb5c9843a8ca2a3e8cf38bcf0c9483b6a8f168a54fd5d216af689` |
| `doc/V0.1.5/evidence/m4_r01_audit_probes.log` | `4ad692258b057b2f17f9c819ab4a46d13b6a0cf5f79582be43a6e1138e6ecbea` |
| `doc/V0.1.5/evidence/m4_r01_audit_probes.py` | `9cc6d3435f1c858bb8c1e0ae89f8b19a4dc88c170b2f06e6f2009db0166a03fd` |
| `doc/V0.1.5/evidence/m4_r01_audit_required.log` | `63a129e16317d2a2a512837119a226259436516690020c89b81f7ef33c7993aa` |
| `doc/V0.1.5/evidence/m4_r01_audit_rerun.py` | `7244de89336c7b57ef3109ed9f9a2b775b652dd60a58e609c459ac64a6211bdc` |
| `doc/V0.1.5/evidence/m4_r01_audit_snapshot_after.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| `doc/V0.1.5/evidence/m4_r01_audit_snapshot_before.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| `doc/V0.1.5/evidence/m4_r01_audit_summary.json` | `923addffd3f0a4288f8227aa3de4ab20588a1bf7793716b2b61fd9f169800519` |
| `doc/V0.1.5/evidence/m4_r01_audit_test_assertions.json` | `5516638fd7310c3e04adac1c18d9f89486703882933c447f22174bd90a1d62f6` |
| `doc/V0.1.5/evidence/m4_r01_audit_verify_after.json` | `df0ba7867b61a4c2f210ac01d7dee037ca05ca84c1c5cae2bfb7b5b7baf783fd` |
| `doc/V0.1.5/evidence/m4_r01_audit_verify_after.log` | `f4c6182fbc9b80557c134792fa02a02922bafeca84b7e7f1324871c2224ba07a` |
| `doc/V0.1.5/evidence/m4_r01_audit_verify_before.json` | `074d3b2c9b238f857f0c369f6b6142c526c4e9757b57b26833f291c486582adb` |
| `doc/V0.1.5/evidence/m4_r01_audit_verify_before.log` | `abff3c0e962ef613d7337d93e22ee0153f142625172dffd5fa987cc43d29f3ae` |
| `doc/V0.1.5/rounds/README.md` | `659d2c4087d7592ac84f9f795aedc9249ef23d8f620b09b41876824a6588c9ec` |
| `doc/V0.1.5/rounds/V015-M4_R01_审核小结.md` | `101ee6740be610f8954e2c34303da0a20cfe1e4148114dd5c14bbfa2077fc653` |
| `doc/V0.1.5/rounds/V015-M4_R01_统筹裁决.md` | `568ec42ab2ea91ebc500f7218ca744dc684d5ee75abc176c07478e3be628a40a` |
