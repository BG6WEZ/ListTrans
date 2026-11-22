# V015-M4 R03 独立审核与统筹裁决交付SHA256

结论：待验证；A-M4-05关闭、实现冻结，M4不放行，M5及后续版本锁定。

HEAD: `e964fb16848de9dece8e729e801b61c7d81dd341`。本轮未改实现/开发测试，未执行Git写操作。

命令：`.\venv\Scripts\python.exe doc/V0.1.5/evidence/m4_r03_audit_close.py`。

清单不含自身；Docker tar仅含合成canary，不含真实账号/秘密。

| 文件 | SHA256 |
|---|---|
| `doc/00_项目管理/版本路线图.md` | `07f69885a8ca42b850c3cd16cc3d28edeb3390e796a77aa4753bacfc5e0e38fc` |
| `doc/00_项目管理/进度总表.md` | `2b40253da808e13fd9319b6402febb612fc6b9c7c348c494ac1faaff18379abf` |
| `doc/00_项目管理/问题与版本归属总台账.md` | `e433aaadef1e754b89564f86647b2b47f8546ee082ec596b2cb6e7361464662f` |
| `doc/README.md` | `53df5f5bef2079be6c45b5ebc39d97797879db9815477d13b07d5ea0261c44b0` |
| `doc/V0.1.5/01_实施方案与任务包.md` | `18e096b40b91ad518341b510c1ec196371d62aa4a0f15e6a388d54233973b8e5` |
| `doc/V0.1.5/02_任务台账.md` | `f0270e73bd87da7b98438f7749ff242c323f43c8c075d73919f4d5561ed0da7e` |
| `doc/V0.1.5/03_终审与版本准入.md` | `0288564d01d30081485cafa4990067e4e863e46c9823495a399fb0b00a1941fa` |
| `doc/V0.1.5/evidence/README.md` | `9c8a1e040e589c02e7496d541c1d034b8734c38816ea08bd69202c748fa023d2` |
| `doc/V0.1.5/evidence/m4_r03_audit_baseline.json` | `38151d170bfd6f0a9392268983ca8ae42750747279ea0bdd62532148d8eb36f2` |
| `doc/V0.1.5/evidence/m4_r03_audit_boundaries.log` | `2e8ea63b26b9b3ec8273af012ce149b5c664572e260296cf95e32c8f7e308b92` |
| `doc/V0.1.5/evidence/m4_r03_audit_browser.log` | `829395a4f08c4e010df64119fcf1950c047525866dffc517577f9b6f4271c3e3` |
| `doc/V0.1.5/evidence/m4_r03_audit_browser/m4_r03_01_forced_pwd_change.png` | `99e85bdc51b7749094c7204a78563673c8c042448327978e436e42ace82df6af` |
| `doc/V0.1.5/evidence/m4_r03_audit_browser/m4_r03_02_employee_forbidden.png` | `a50ab4fd64161ddec0101a19206b16522c10bc3dc43cc164bd3eeb0b7b3974e9` |
| `doc/V0.1.5/evidence/m4_r03_audit_browser/m4_r03_03_dual_approval_and_isolation.png` | `4df6f8558666c108488f847baf3b698c866eef5f30c4a11c24c3b4518fe76296` |
| `doc/V0.1.5/evidence/m4_r03_audit_browser/m4_r03_browser_e2e.json` | `3abd0c784e1cda81cc49ef79e35a31ed7c26da17218b21427ab583e57e71218d` |
| `doc/V0.1.5/evidence/m4_r03_audit_close.py` | `03659dcc2e877d4f924d7eb5b8997ad4370449a9c741882a19c91c9c487e937a` |
| `doc/V0.1.5/evidence/m4_r03_audit_closure.json` | `16509c36377a85f05f3691acac209707e090193e9331ae1acd897cc48f974558` |
| `doc/V0.1.5/evidence/m4_r03_audit_compile.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `doc/V0.1.5/evidence/m4_r03_audit_docker.log` | `1939f944a9284da9750da0da9a2a9e672bd6168dc9853331b1315376dc71d429` |
| `doc/V0.1.5/evidence/m4_r03_audit_docker_context.log` | `fd0ae8f552a502e50ce99c54901055cf6f539b8d9c71cec793f41c07a98ce156` |
| `doc/V0.1.5/evidence/m4_r03_audit_docker_context.tar` | `eaeb01a1cd2f20873869d7861f9ea79ddbd9f54e8967ba1ac81ba91b1c49b37e` |
| `doc/V0.1.5/evidence/m4_r03_audit_docker_copy_patterns.log` | `f58bda79df977bef48b487e6c436068d456c2fd7b8d9f25a001fd874325d8669` |
| `doc/V0.1.5/evidence/m4_r03_audit_docker_copy_patterns.tar` | `3599705aa4e5718dfa34f9c19f837d0dca30576dfdc2858d18b705eaa9792fc5` |
| `doc/V0.1.5/evidence/m4_r03_audit_docker_full_offline.json` | `4730a1f307871403b6e788226db81cfd296d15fc6bf6799164d2a3d0814b89eb` |
| `doc/V0.1.5/evidence/m4_r03_audit_docker_full_offline.log` | `fd9f1e26362d4c46dd7330c464858bfcf8d7c9e239db2bc3edb41a449d0a1c15` |
| `doc/V0.1.5/evidence/m4_r03_audit_docker_offline.py` | `da43443a8f873bb8fa25bf09baf9801ceab17979e5dfd35e788fcdc766d160fd` |
| `doc/V0.1.5/evidence/m4_r03_audit_docker_probe.py` | `f9eb2f38b084558ccac9423951021ed4d790b74834aa215d48501da2535fdbac` |
| `doc/V0.1.5/evidence/m4_r03_audit_docker_probes.json` | `8737d8d21d8a46f12c0fb0678320d398a389052058b46115be67d36e682f708d` |
| `doc/V0.1.5/evidence/m4_r03_audit_evidence_errata.md` | `3c71b810e591a1d1c5b2264d732677b09061e5ad4e1ecbb5771de1620e13255b` |
| `doc/V0.1.5/evidence/m4_r03_audit_full.log` | `17c0d4f027cad270dcad2072544e56c38b6420f665bd9a10570579477cd7670b` |
| `doc/V0.1.5/evidence/m4_r03_audit_gate.log` | `3dc6460f3e5109a1d0c21bde30e048589a181636aa88e7ca87f2332c58498dbb` |
| `doc/V0.1.5/evidence/m4_r03_audit_gate_compare.json` | `8f7054b812d461b43ec73815f2bf90237b81f9647da693d960176103e48e6340` |
| `doc/V0.1.5/evidence/m4_r03_audit_gate_reports.json` | `fa621c7b4c34dfad848571401bd48d3921e8cbb59dacd8c310d9ea93e5c249be` |
| `doc/V0.1.5/evidence/m4_r03_audit_integration.py` | `9f03a61d5d2aa22de430a52d7d6d60fdde570c73d9c668544e8bff2a1ab8e76f` |
| `doc/V0.1.5/evidence/m4_r03_audit_integration_summary.json` | `85f65fd526396a4735eddc9bf37475d49c11d1e8dd15b538d7a09ae63274d1a4` |
| `doc/V0.1.5/evidence/m4_r03_audit_m3.log` | `10897ef777775c4dfca4352a30de12780881da48d2fbb52d1800c5381c821a84` |
| `doc/V0.1.5/evidence/m4_r03_audit_m4.log` | `789fd986abd0a7cfcc22d0ba57ba225318c9e37d0e19bbf6edd437c85783bac0` |
| `doc/V0.1.5/evidence/m4_r03_audit_prior_evidence_check.json` | `525fbe0fc9e7804eb843cd1e19b667ed1dd123c86ca96bfa0072e1074d6be126` |
| `doc/V0.1.5/evidence/m4_r03_audit_probes.json` | `39f84e7b71026f8e04ddad7ee713055f6ef8eb43674d2afb1c601524963a6512` |
| `doc/V0.1.5/evidence/m4_r03_audit_probes.log` | `b8ff5a0d9002b13aeb9d67e936dcd44e79f58a7e8243a0784fb2cc0135ce306a` |
| `doc/V0.1.5/evidence/m4_r03_audit_probes.py` | `6d6757990d5ebd0e68d695575f21fc447801a942fef062f8bb816b6ddccc064f` |
| `doc/V0.1.5/evidence/m4_r03_audit_r02_to_r03.diff` | `def3e3e2004fa0808aeb1e88cceb68f6e52d751688f79546952ff72ab3494c5c` |
| `doc/V0.1.5/evidence/m4_r03_audit_required.log` | `a569b754f453235c602e04b3f0d1fdaf9c56518f6c48acf2528435a2778842a0` |
| `doc/V0.1.5/evidence/m4_r03_audit_rerun.py` | `a859b4545cb27285b40d80185f7d3ad6833da76abc3f22e61853c878a3c59990` |
| `doc/V0.1.5/evidence/m4_r03_audit_snapshot_after.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| `doc/V0.1.5/evidence/m4_r03_audit_snapshot_before.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| `doc/V0.1.5/evidence/m4_r03_audit_summary.json` | `7a0f451b4a5849694136031e42344de8db00511dcad355b60e0c1c3dc679844c` |
| `doc/V0.1.5/evidence/m4_r03_audit_test_assertions.json` | `c10eed4a32d9d074cd289ae45b54a94f4433cecde9b147ba181bf2e8bc8c7c6b` |
| `doc/V0.1.5/evidence/m4_r03_audit_verify_after.json` | `e9cbeeac4ba3fb822889a2eca5a31823660bbc77b31359aaaaa39e6ca5778e4c` |
| `doc/V0.1.5/evidence/m4_r03_audit_verify_after.log` | `0e01c21c9a2d9edeba12cbffb86f2581211ae9d9f6a0c3933ac68df7f1d5a1f3` |
| `doc/V0.1.5/evidence/m4_r03_audit_verify_before.json` | `e3ce95ffb9c559f79f6ccc0457896b53324c68e0309a48a53044363839119fdb` |
| `doc/V0.1.5/evidence/m4_r03_audit_verify_before.log` | `9fdfda6a18eed9ff809fd665ddac3339253c472240c1dcf157e3c249f90f55b6` |
| `doc/V0.1.5/rounds/README.md` | `e941311d09deb1fded12af5c65ecb7b6e6dd48005a87f21e454209fcb7974200` |
| `doc/V0.1.5/rounds/V015-M4_R03_审核小结.md` | `f6b991c6b6dd8ecb75dc031a839d37bacb9ccebed683c35af139294a276a18ca` |
| `doc/V0.1.5/rounds/V015-M4_R03_统筹裁决.md` | `7947121e398b4e22a64157a8526b95a77780f5a824ceab2121f0243da8691ab0` |
