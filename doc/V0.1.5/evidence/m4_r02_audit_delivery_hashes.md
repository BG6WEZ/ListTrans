# V015-M4 R01 独立审核与统筹裁决交付SHA256

结论：退回整改；仅下发同包R03，不放行M4/M5或后续版本。

HEAD: `e964fb16848de9dece8e729e801b61c7d81dd341`。本轮未改实现/开发测试，未执行Git写操作。

命令：`.\venv\Scripts\python.exe doc/V0.1.5/evidence/m4_r02_audit_close.py`。

清单不含自身；Docker tar仅含合成canary，不含真实账号/秘密。

| 文件 | SHA256 |
|---|---|
| `doc/00_项目管理/版本路线图.md` | `7c68ebb9b08c4fb276c69746f93a5af4f7912f77451c874308ce5edf56e9ef3f` |
| `doc/00_项目管理/进度总表.md` | `51f80d0b7dc226179d716a1d413c90bd830398f20dfeb5407aafe9eaf368fc37` |
| `doc/00_项目管理/问题与版本归属总台账.md` | `947092c5cc0480cb9023b3c39deb07fab51dba97c9215cdbf1fe11cc8cacb2e3` |
| `doc/README.md` | `ce737cf17e0608c533b082f671885db4f23d8db1ccf8e1e5903472dab7279505` |
| `doc/V0.1.5/01_实施方案与任务包.md` | `b9dc64ec4326aa0af4ea0f5c16df2a41399f0c5476f1626c2cccd3432c3cf944` |
| `doc/V0.1.5/02_任务台账.md` | `9022899602de95f31604140ff1e431e1e137ec0b42ace33a8a9d5d9888d9ec8f` |
| `doc/V0.1.5/03_终审与版本准入.md` | `586ddf043e8c2f6c30ae44fc196cc9cb23cfd96bfe3c6e10e8b8da8ef5b4858b` |
| `doc/V0.1.5/evidence/README.md` | `95e843d0decac986e6fab5bedaeacaf73cc378a0a584332ee4c809cfbf9ff49a` |
| `doc/V0.1.5/evidence/m4_r02_audit_baseline.json` | `b1564565632a361eb7cd153c10158b7edd85b565fc054a2052a404e762f96b4c` |
| `doc/V0.1.5/evidence/m4_r02_audit_boundaries.log` | `c351db9c6fedacbfe114eee821c98a9ab95d6a3e9be497f495f0049afc1f6714` |
| `doc/V0.1.5/evidence/m4_r02_audit_browser.log` | `7a6967ba22c5cc73af60245d72d4a78ee24c2187df44ef98a52cb357dab8c948` |
| `doc/V0.1.5/evidence/m4_r02_audit_browser/m4_r02_01_forced_pwd_change.png` | `99e85bdc51b7749094c7204a78563673c8c042448327978e436e42ace82df6af` |
| `doc/V0.1.5/evidence/m4_r02_audit_browser/m4_r02_02_employee_forbidden.png` | `304f19266964eb27472e02f56a8bf6c2312a000f608ba0cd057e881f9b3493ef` |
| `doc/V0.1.5/evidence/m4_r02_audit_browser/m4_r02_03_dual_approval_and_isolation.png` | `e28e0ae7feb92a05b61afb1366f20efd85b6b63b5a0206074e4ae20faa1c2625` |
| `doc/V0.1.5/evidence/m4_r02_audit_browser/m4_r02_browser_e2e.json` | `c3862806a0a01d514ccd3735548810e5a90cb38e8a8c385e75b950bda0217dfb` |
| `doc/V0.1.5/evidence/m4_r02_audit_close.py` | `9eb7eae58bdce918c0e456081141d8f8c6fa2df2214a19b3db7eeacb28f5a352` |
| `doc/V0.1.5/evidence/m4_r02_audit_closure.json` | `08dcc86487cefb1d2bd4959ec0d0deb6ed0a61df12625a9b714835aa935e1438` |
| `doc/V0.1.5/evidence/m4_r02_audit_compile.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `doc/V0.1.5/evidence/m4_r02_audit_docker.log` | `1939f944a9284da9750da0da9a2a9e672bd6168dc9853331b1315376dc71d429` |
| `doc/V0.1.5/evidence/m4_r02_audit_docker_context.log` | `706d4320e65a4778e4f70dbf72aa1f52ffedce579fb8a7102d53af73dcbf9160` |
| `doc/V0.1.5/evidence/m4_r02_audit_docker_context.tar` | `155892c3e99c3ef4ac1bd82e165fd864133149b6f407b7092b44eef67c663303` |
| `doc/V0.1.5/evidence/m4_r02_audit_docker_copy_patterns.log` | `eae757947dc28268c94db31f5f23c2efb77b17131cde51afb8612edfef891868` |
| `doc/V0.1.5/evidence/m4_r02_audit_docker_copy_patterns.tar` | `8f11bb4708d95f3176d0378022c0d0dd73d648ed9468e50117dc594994280a90` |
| `doc/V0.1.5/evidence/m4_r02_audit_docker_full_offline.json` | `89fb522e7dae8230a6a1c0dc773f185365c1f6dc087a2783236f85573f500d6d` |
| `doc/V0.1.5/evidence/m4_r02_audit_docker_full_offline.log` | `f19b1024407f9c77f594c63336ad7ff905f533d42ebfec82623252b6900c1b6a` |
| `doc/V0.1.5/evidence/m4_r02_audit_docker_offline.py` | `81f8b9ca437e2b201c52e7d850d458083e07a188b34ddf0f6ce3bda88ddd3fb2` |
| `doc/V0.1.5/evidence/m4_r02_audit_docker_probe.py` | `76e9f3122e7a313f370e13f813c6342ff30914ed7bbf643aab5b7adaa5b1b569` |
| `doc/V0.1.5/evidence/m4_r02_audit_docker_probes.json` | `4efd3d6671ea7f44f2277950531f500aa7485ec203022755fba78c3c4204dd98` |
| `doc/V0.1.5/evidence/m4_r02_audit_full.log` | `f3956a6a0e7f3f0dee0daf1d71466dacebd1a970870d444aab154743cc6530e8` |
| `doc/V0.1.5/evidence/m4_r02_audit_gate.log` | `84eb988489583c136d3576fd6b3c2f01cf35b268920992ee2a556851703615cc` |
| `doc/V0.1.5/evidence/m4_r02_audit_gate_compare.json` | `8f7054b812d461b43ec73815f2bf90237b81f9647da693d960176103e48e6340` |
| `doc/V0.1.5/evidence/m4_r02_audit_gate_reports.json` | `4b4397567325d1f18f414062ce9bda3fde6d047cb9ce7ed6a028df87b1937628` |
| `doc/V0.1.5/evidence/m4_r02_audit_integration.py` | `2cccc554c6c604ab9cec2c7171c2f55a8cf1261592a337e9be4252eade4f44d9` |
| `doc/V0.1.5/evidence/m4_r02_audit_integration_summary.json` | `17f57164ea5a2f0b4cdeac7e29d99da232f442b52bf49532bb461b6e43bc6e06` |
| `doc/V0.1.5/evidence/m4_r02_audit_m3.log` | `ee9402945086f71cab8493b7856eeeadea9018737bb01a805208cce1b342c715` |
| `doc/V0.1.5/evidence/m4_r02_audit_m4.log` | `e503682a2f9b4df25aa5c50191998c88384ad23de0178a4c5a6766c72c8977a6` |
| `doc/V0.1.5/evidence/m4_r02_audit_probes.json` | `83d5422083b0127f90221254ea966fbbc2a495d56557aad75cf92e5af161d17d` |
| `doc/V0.1.5/evidence/m4_r02_audit_probes.log` | `c0a4a3d20c2af2a07bca4e92abaeffce6b2f7361815cd8bfba718ac00c7f70e0` |
| `doc/V0.1.5/evidence/m4_r02_audit_probes.py` | `be5c7e58351a8d288ef2400e072bc4ceae9b4e0e9dfcefe9365704a08f0e70c1` |
| `doc/V0.1.5/evidence/m4_r02_audit_r01_to_r02.diff` | `fa3417087bfd76df716c7bd471115a1898759198ed24b8f7fc5da6a2b39ace5d` |
| `doc/V0.1.5/evidence/m4_r02_audit_required.log` | `7209e2c744825942e3198b03b8710a62c4302b65338562d9385796216db55d4a` |
| `doc/V0.1.5/evidence/m4_r02_audit_rerun.py` | `3e6498d3c6017c8f827039eb083948b5d1e0494b2bfd297becf568f60998f8bc` |
| `doc/V0.1.5/evidence/m4_r02_audit_snapshot_after.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| `doc/V0.1.5/evidence/m4_r02_audit_snapshot_before.json` | `5768bdcabea117274da0ebf58378a61d5bffae7698d1e1fb3d4f5f5f68bd5c7e` |
| `doc/V0.1.5/evidence/m4_r02_audit_summary.json` | `d2dfa991a46478ede6e168fd61dbcc3efcd8189ead8e7656137a0b236da2caa0` |
| `doc/V0.1.5/evidence/m4_r02_audit_test_assertions.json` | `5d45c9887b2f3da6531adb5dfcfe45f5412a4360223689f3ef9f7f7969aef180` |
| `doc/V0.1.5/evidence/m4_r02_audit_verify_after.json` | `facf33eb5b082b011c2ad8f2247e966bb22de9c1949af8dbfa94363206754355` |
| `doc/V0.1.5/evidence/m4_r02_audit_verify_after.log` | `a168988b68d360f3315433b85acf44acd80b912ae420f33563bd6854f03df051` |
| `doc/V0.1.5/evidence/m4_r02_audit_verify_before.json` | `36cda4083d753cec57fbd73ee17be667e27b1f5060d0514dfd0338755f8738b5` |
| `doc/V0.1.5/evidence/m4_r02_audit_verify_before.log` | `797caad6e2e28db0246cf34cf74e9419a6f37c398fa40db51e49da1cbbbdb7d9` |
| `doc/V0.1.5/rounds/README.md` | `2fe5e96159b85fc7d52b03b35309b7699fbc82b592fd4c5e72323c60768ee2de` |
| `doc/V0.1.5/rounds/V015-M4_R02_审核小结.md` | `02cd18b58644bc9ef5348bcfc3844f7a3de8ff22fadd72120f6d7fd0a73a8a13` |
| `doc/V0.1.5/rounds/V015-M4_R02_统筹裁决.md` | `84761a9f676d1bb47c542d9fde2584a875ec51cd638ffd6afbf73fe614dc745c` |
