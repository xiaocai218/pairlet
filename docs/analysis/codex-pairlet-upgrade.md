# Codex 与自托管 Pairlet 统一升级

本机入口为 `~/bin/update-codex-pairlet`。它升级 Codex CLI 和托管 app-server，验证现有自托管 Pairlet 的稳定模式补丁；**不自动升级 Pairlet 客户端或替换自托管 daemon**。Pairlet 自身升级仍按自托管同步流程执行，不能使用官方 daemon 包覆盖本机补丁。

```bash
update-codex-pairlet --check
update-codex-pairlet --prepare-only --version 0.161.0
codex app-server daemon stop
update-codex-pairlet --version 0.161.0
update-codex-pairlet
```

版本仅为命令示例，不代表已验证支持；省略版本时从 npm 获取最新版。正式升级应在普通本地终端执行，先退出所有 Agent 会话，包括桌面 Codex 与 Pairlet，再执行 `codex app-server daemon stop`。脚本发现 Codex 进程、Pairlet 子进程或托管 app-server 仍运行时拒绝操作，无强制绕过开关。实机控制套接字及官方 proxy 的线程查询均未返回有效初始化响应，因此不依赖未经验证的在线空闲判断。

候选包在 `~/.local/opt/codex-pairlet/versions/` 独立安装，先运行 wire 契约探针，再运行终端、中断、两次恢复的真实探针。这会使用当前账号及模型额度；预验证不会切换服务。重复使用候选目录时重新运行全部探针，不相信旧验证标记；失败目录保留供诊断。

正式切换前再次检查会话和托管服务已停止，停止 Pairlet，保留原 npm 包，再将规范 npm 包路径链接到验证后的候选包。同步托管 app-server 后恢复原本运行的 Pairlet，托管服务保持停止，核对 CLI、托管版本、Pairlet PID 和补丁。需要时在完成升级后执行 `codex app-server daemon start`，或由客户端启动。现有服务的 `--codex-bin` 路径不变。相同版本且当前一致时不安装、不重启。

失败时自动尝试恢复旧包与托管版本；强制杀进程、断电或回滚失败时保留 `pending.json` 并拒绝继续升级。检查记录与实际状态，关闭所有会话后运行 `update-codex-pairlet --rollback`。手工恢复可能需要维护者处理损坏的包路径或不可用服务；不能保证断电时自动恢复。

请不要再用 `npm install -g @openai/codex` 绕过入口。脚本只控制自己的升级流程，无法拦截外部安装器或桌面自动更新。检测到 CLI 与托管版本已经漂移时拒绝升级，需先分析并恢复一致；不会强行覆盖未知运行状态。

会话检查与停止服务之间仍有竞争窗口，升级期间不要启动新会话。进程和版本核对不是手机端验收；每次正式升级后仍应从 Pairlet 实际执行终端命令并恢复会话。稳定模式关闭多代理，但不修复 Codex 内部所有失效句柄问题。

## 本次落地

### 隔离 Pairlet 候选准备

```bash
update-codex-pairlet --prepare-stack --pairlet-ref main
```

该模式先准备并测试 Codex，再从原版上游独立获取指定 Pairlet ref，记录不可变提交，依次重放自托管 relay 与 stable-mode 补丁；补丁已存在则跳过，冲突则停止。它不依赖或覆盖原工作区的未提交改动，不创建分支、不提交、不推送。通过自托管配对文件哈希及品牌兼容检查后，以 JDK 17 执行 Codex 后端测试和 daemon installDist。源码、日志、提交和补丁／daemon 哈希位于 `~/.local/opt/codex-pairlet/candidates/`，失败目录也保留。

维护工具包包含独立补丁文件。默认 `main` 是准备时的上游快照，不保证对应已发布客户端；正式发行应指定匹配的发布 tag。该模式不会构建 Windows MSI、下载 Android APK、发布到 NAS 或切换服务；候选状态标记客户端未构建。客户端收集交付、组合版本清单、本机整套切换和成功后回滚已有独立入口，但尚未串成默认命令的全自动流程，不能将默认命令描述为全栈自动升级。

### 客户端交付与本机组合切换

Windows Actions 必须从已提交的隔离自托管源码构建。`stage-pairlet-build.py` 只复制升级文件到候选，不自动提交或推送，避免包含原工作区的 UI 修改。正式 MSI 必须通过原生 Windows Installer 版本与 UpgradeCode 核对，并附提交、run id、大小和 SHA-256 清单；不能把只有 ZIP 的成功 job 当作 MSI 交付。

```bash
python3 scripts/pairlet-artifacts.py --version 2.2.0 --directory ~/tmp/pairlet-stack-delivery/2.2.0 --run-id RUN_ID --commit FULL_SHA --publish
update-codex-pairlet --assemble-stack --codex-candidate CODEX_PACKAGE --pairlet-candidate PAIRLET_CANDIDATE --clients CLIENTS_JSON
codex app-server daemon stop
update-codex-pairlet --apply-stack STACK_JSON
update-codex-pairlet --rollback-last
```

占位参数需替换为已验证路径与不可变提交。MSI 与 daemon 必须同提交；官方 Android APK 必须同版本但保持原字节。NAS 交付核对远端哈希和大小，不覆盖不同的既有文件，不删除旧安装包。组合清单生成不可变本机 runtime 副本与内容哈希，并不重启服务。

`--apply-stack` 需最终切换批准，并在所有 Agent 会话退出后从普通终端执行。它再次核对内容、空闲状态，备份 unit 与配对状态，使用独立 drop-in 保留 relay 和命令参数；失败尝试恢复旧 Codex、托管版本和 daemon 路径。`--rollback-last` 可在成功切换后恢复上一套本机 runtime；它不自动降级手机／Windows 已安装客户端，也不自动覆盖运行后产生的配对状态。外部修改或活动进程会阻止回滚。

NAS 文件存在不代表客户端已经安装；本机切换成功不代表手机端终端、重连或恢复已验收。当前不包含客户端自动安装、未来补丁冲突的自动解决，或一次命令全流程编排。

维护工具独立安装到 `~/.local/opt/codex-pairlet/tools/releases/<内容哈希>/`，`tools/current` 指向当前工具包。`~/bin/update-codex-pairlet` 不再依赖源码或临时目录；工具包同时包含升级脚本、wire 探针及稳定模式探针。安装生成 SHA-256 清单并校验，不升级任何服务；旧入口保留在工具目录的 `launcher.backup-*`。

```bash
python3 scripts/install-codex-pairlet-tools.py
update-codex-pairlet --check
```

更新维护脚本后重新执行安装器；源码仍由仓库管理，但运行时可独立使用。安装测试在原源码目录不可见的条件下执行安装后的 `--help` 并检查所有探针哈希。`--prepare-only` 即使目标版本等于当前版本，也会独立安装并重新跑真实探针，便于安全演练预验证阶段。

测试覆盖候选失败、活动会话拦截、成功保留旧包、切换失败回滚及回滚失败保留记录。真实候选探针不等于正式服务升级／回滚演练；实际最新版切换与手机端验收需在会话全部退出后执行，不能在当前 Agent 会话中触发。

### 2026-10-04 验证记录

- 隔离候选阶段新增验证：25 项 Python 测试通过，包括补丁重复应用、冲突保留、脏工作区隔离、准备模式不切换和失败不切换。原自托管提交与原版上游 `main` 快照均在独立目录完成补丁验证和 daemon 构建，Codex 后端 137 项测试无失败；Gradle 部分任务复用了构建缓存。
- 当前实测上游提交为 `2c35c255fb3a869d519dcd7a1031d7451557d834`，仅表示该快照构建可用，不表示对应 Windows／Android 客户端已交付。维护入口已包含 `--prepare-stack` 和补丁文件；正式服务未切换，手机端验收未完成。

- 固定工具包已安装，原入口已备份；18 项安装与升级保护测试全部通过，其中包括模拟原源码目录不可见、探针哈希校验、失败回滚和回滚失败保留记录。
- npm 查询的最新版与当前 CLI／托管版本同为 `0.160.0`。通过固定入口独立安装该版本并执行 `--prepare-only`，wire 探针 18/18 通过；终端执行、运行中断、两次跨进程恢复及无协作调用审计全部通过。
- 额度接口只验证方法存在，当前认证方式返回 `chatgpt authentication required`；没有验证 ChatGPT 额度字段或客户端额度显示。
- 当前存在多个 Codex 进程，托管 app-server 仍运行，因此没有停止当前会话或进行正式服务切换。Pairlet PID 保持不变，重启次数为 0。
- 正式升级与回滚演练、升级后手机端连接／终端执行／恢复验收尚未完成；候选验证和模拟回滚测试不能代替这些验收。
