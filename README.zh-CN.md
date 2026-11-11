# Codex Long Jobs：后台任务与会话自动唤醒

[English](README.md) | 中文

[![CI](https://github.com/zycdev/codex-long-jobs/actions/workflows/ci.yml/badge.svg)](https://github.com/zycdev/codex-long-jobs/actions/workflows/ci.yml)

**让耗时任务在后台运行，无需模型轮询；任务完成后，唤醒原来的 Codex 会话，检查结果并继续已授权的工作。**

`codex-long-jobs` 是面向 OpenAI Codex CLI 和 VS Code Codex 扩展的 Skill 与本地进程监督工具。
它支持在已测试的 Linux 环境中无人值守地继续工作，包括 Remote-SSH 场景：启动任务，结束当前轮次，任务完成后在同一个会话中触发后续轮次。
可用时优先采用原生 `codex queue --resume`，同时保留 RPC 辅助进程，用于旧版兼容和仅重试唤醒的恢复操作。

> **v0.5.0 beta。** 需要 Python 3.10+，Linux 是主要测试平台。
> 自动续作依赖可访问且兼容的 Codex daemon，以及对原始会话和所需工具的访问能力。
> tmux 为可选依赖。详见[兼容性与运行要求](#兼容性与运行要求)。

## 快速开始

### 1. 安装 Skill

在运行任务的主机上，向 Codex 发送：

```text
将 https://github.com/zycdev/codex-long-jobs 安装为用户级 Codex Skill，并验证安装结果。
```

如果 Codex 没有发现新 Skill，请重启 Codex，再确认它出现在 `/skills` 中。
从本地克隆安装的方法见[手动安装与更新](#手动安装与更新)。

### 2. 运行后台任务

例如，在模型训练项目中，Codex 已经了解训练命令和配置后，可以这样说：

```text
用 $codex-long-jobs 在后台运行模型训练，完成后检查最终 checkpoint。
```

Codex 会启动独立的后台任务，报告任务名称和日志路径，确认一次运行状态，然后结束当前轮次。
本地工作进程负责等待任务完成；随后触发的 Codex 轮次会检查结果证据，并执行你已经授权的后续工作。

训练期间，你仍然可以在同一个会话中与 Codex 继续交互，也可以从这个会话启动更多后台任务。
每个任务完成后，都可以唤醒原来的会话。
使用队列投递时，完成通知会等待当前轮次结束，并遵循已有输入的排队顺序。

Codex 必须在获得相应主机执行权限后启动任务，因为其常规工具沙箱可能在工具调用结束时回收已经脱离终端的子进程。
如果自动投递不可用，任务仍可记录结果，但不会自动唤醒会话。

### 3. 按需查看或取消任务

你可以在任意终端或编辑器中打开报告的日志文件。
需要查看状态时，可以说：

```text
用 $codex-long-jobs 查看你在这个会话中启动的任务状态和日志路径。
```

需要停止任务时，请明确要求取消：

```text
用 $codex-long-jobs 取消你在这个会话中启动的任务，并报告最终状态。
```

关闭日志查看窗口不会取消任务。
即使命令成功退出，Codex 仍需检查输出，才能确认整个任务已经完成。

**导航：**[为什么选择它](#为什么选择-codex-long-jobs) · [主要功能](#主要功能) · [兼容性](#兼容性与运行要求) · [唤醒原理](#会话唤醒的工作原理) · [进阶用法](#进阶用法与恢复) · [常见问题](#常见问题) · [测试与贡献](#测试与贡献)

## 为什么选择 codex-long-jobs？

长时间构建、测试套件、模型训练、评估和数据处理通常不需要在运行过程中持续关注。
为了反复确认命令是否结束而保持智能体活跃，会增加仅用于查询进度的模型轮次。
这个 Skill 将等待交给本地进程，在有结果需要检查时再将控制权交回 Codex。

```text
Codex 启动任务并结束当前轮次
    -> 独立的工作进程在本地等待
    -> 命令完成、失败或被取消
    -> 完成通知投递到原始会话
    -> Codex 检查结果并继续已授权的工作
```

普通后台进程工具可以让命令持续运行，但本身不会将任务完成事件关联到所属的 Codex 会话。

| 能力 | `nohup` / Shell 后台任务 | 普通 tmux 任务 | `codex-long-jobs` |
| --- | --- | --- | --- |
| 独立于当前 Codex 轮次持续运行 | 取决于 Shell 配置和主机策略 | 支持 | 由独立的工作进程和监督进程负责 |
| 保存结构化退出状态、失败原因和日志路径 | 需自行配置 | 需自行配置 | 内置 |
| 通知并唤醒原始 Codex 会话 | 需自行集成 | 需自行集成 | 队列投递与唤醒恢复，受客户端可用性限制 |
| 关闭日志查看窗口后继续运行 | 取决于配置 | 取决于会话生命周期 | 支持，任务不依附于日志查看窗口 |

工作进程在等待期间不消耗模型 token。
启动任务和处理完成结果仍会使用正常的 Codex 轮次。
本项目不承诺通用的 token 节省比例：一次阻塞至命令退出的前台工具调用，本身也可能不产生中间模型轮次。

## 主要功能

- **唤醒原始会话。** 完成通知发送到记录的 Codex 会话，优先使用原生 `queue --resume`，并保留 RPC 兼容恢复。
- **无需模型轮询。** 本地进程监督任务并等待投递条件满足，不会仅为查询进度而调用模型。
- **持久化结果。** 无论成功还是失败，日志和结构化状态都会保留退出码、信号、失败原因及投递状态。
- **投递与唤醒恢复相互独立。** 已接受的队列通知不会重复发送。`retry-wake` 仅重试唤醒，不新增消息。
- **明确取消任务。** 对经过身份校验的命令进程组先尝试正常终止，必要时在限定等待后强制终止。
- **处理异常。** 监督进程检测工作进程意外退出；日志写入错误或未出现配置的成功标记时，不会误报成功。
- **日志不依赖终端会话。** 可在终端或 IDE 中读取日志，也可以按需使用 tmux 查看窗口。

## 兼容性与运行要求

| 组件 | 要求或已验证范围 |
| --- | --- |
| 主机 | Linux 是主要测试平台。任务在本机运行，受主机进程策略约束。不支持原生 Windows；其他平台没有同等范围的验收覆盖。 |
| Python | Python 3.10+，没有第三方 Python 运行时依赖。 |
| Codex CLI 或 VS Code 扩展 | 队列投递需要所属会话的 ID，以及支持 `codex queue --thread` 的可执行文件。无人值守续作还依赖可访问且兼容的 daemon、会话访问权限和所需工具。 |
| 原生恢复 | 选中的可执行文件声明支持 `queue --resume` 时使用。支持队列的旧版 CLI 可以使用 RPC 兼容路径。 |
| tmux | 日志查看窗口的可选依赖；仅旧版 TUI 投递必须使用。tmux 不负责监督任务进程。 |
| 启动权限 | 需要在 Codex 工具沙箱之外，以限定范围的主机权限执行启动操作。 |

可执行文件发现顺序为：`CODEX_LONG_JOBS_CODEX_BIN` 指定的路径、PATH 中可信的 CLI、可信的 VS Code 扩展内置二进制文件。
如果扩展内置二进制文件可用，就不必另行安装独立 CLI。
扩展 App Server 与共享 daemon 是不同进程，版本也可能不同。

检查选中的可执行文件及其实际能力：

```bash
SKILL_ROOT="${CODEX_HOME:-$HOME/.codex}/skills/codex-long-jobs"
"$SKILL_ROOT/scripts/codex-long-jobs" doctor
```

对于低于 0.166.0 且不支持原生恢复的独立 CLI，`doctor` 可能建议可选升级。
它不会安装软件或更改默认 CLI，普通任务也不会重复提示升级。
执行路径依据实际能力检测选择，而不是仅依据版本号。

### 已验证的 VS Code Remote-SSH 自动续作

2026 年 11 月的验收覆盖了在线和忙碌会话、未被选中的会话、主动关闭窗口，以及主动断开 Remote-SSH 连接。
在 CLI 0.167.0、扩展二进制文件 0.164.0-alpha.11.1 和 daemon 0.167.0 的组合下，关窗和断连场景均在**重新打开窗口或重新连接之前**完成了实际工具调用与结果验证。
维护者返回后，确认原始会话中显示了相应回复。
11 月较早的测试也验证了旧版 CLI 0.164.0 兼容路径的续作能力。

这些结论针对具体测试环境，不代表任意网络故障或客户端关闭情况下都能恢复。
仍在运行的 App Server 可能持有会话写锁；客户端提供的工具不可用、认证缺失或 daemon 不可用，都可能阻止后续工作。
辅助进程不会替换 daemon、绕过写锁或代替用户批准操作。
详见[有人参与的验收记录](references/testing.md#staged-supervised-acceptance-2026-11-08)和 [VS Code 恢复限制](references/operations.md#vs-code-client-recovery)。

## 会话唤醒的工作原理

启动时，控制器记录所属会话的 `CODEX_THREAD_ID`，并在 tmux 之外启动独立的监督进程、工作进程和任务命令。
命令进入终态后，结果会被持久化，并向原始会话发送包含状态文件与日志路径的完成提示。
进程输出被视为不可信的证据，不会直接写入该提示。

在支持的环境中，投递使用 `codex queue --thread OWNER --message PROMPT --resume`。
通知被接受后，独立的 RPC 辅助进程通过现有本地 daemon 执行仅针对唤醒的恢复，也适用于支持队列的旧版 CLI 和原生恢复结果不确定的情况。
恢复操作保留已有输入的顺序，不打断正在进行的轮次。
已有监督进程可以在辅助进程启动失败或异常退出后接管恢复。

**队列接受通知、轮次启动、后续工作完成，是三个不同的事实：**

| 观察结果 | 能够证明什么 |
| --- | --- |
| `delivery.status: queued` | CLI 已接受完成消息，不代表会话已经消费该消息。 |
| `queue_wake.status: started` | 已确认关联目标输入的轮次启动，但该轮次的工作仍可能失败。 |
| `queue_wake.status: absent` 或消费相关观察结果 | 未找到目标队列项，或该项已经消失。仅凭这一点无法证明处理成功。 |
| 经验证的后续结果 | 实际续作已检查状态、日志和所需产物，可以据此判断具体任务是否成功。 |

已接受的通知不会重发，即使界面尚未显示它。
队列调用超时或被中断、导致接受状态不确定时，记录会保持待处理状态供检查，而不会自动重发。
通过 `status --json` 查看命令结果和独立的 `queue_wake` 观察结果。

对于不支持队列的旧版 CLI，兼容投递会校验 tmux 中的原始 TUI，并等待轮次空闲、输入框为空。
这些本地就绪检查不会调用模型。
完整行为约定见[队列投递](references/operations.md#queue-delivery-state-machine)、[无人值守唤醒恢复](references/operations.md#unattended-queue-wake)和 [TUI 投递](references/operations.md#tui-delivery-state-machine)。

## 进阶用法与恢复

### 带产物检查的训练任务

[快速开始](#2-运行后台任务)中的训练任务，对应的控制器命令可以如下所示。
请将训练脚本、配置和 checkpoint 路径替换为项目中的实际路径，并为每次运行选择新的任务名称。

```bash
SKILL_ROOT="${CODEX_HOME:-$HOME/.codex}/skills/codex-long-jobs"
"$SKILL_ROOT/scripts/codex-long-jobs" start \
  --name train-model-run-01 \
  --log ./logs/train-model-run-01.log \
  --success-pattern '^TRAINING_COMPLETE$' \
  --delivery auto \
  --viewer none \
  -- bash -c '
    python3 train.py \
      --config configs/train.yaml \
      --output-dir checkpoints/run-01 &&
    test -f checkpoints/run-01/final.pt &&
    printf "TRAINING_COMPLETE\n"
  '
```

只有训练成功且 checkpoint 存在时，才会输出成功标记。
成功标记使用多行正则表达式语义，因此 `^TRAINING_COMPLETE$` 匹配日志中的一整行；仅在退出码为零足以判定成功时，才省略这一检查。
checkpoint 文件存在，仍不等于模型本身或评估结果已经通过验证。

### 状态、日志与取消

使用上文定义的 Skill 安装路径：

```bash
# 查看命令结果，以及独立的投递和唤醒信息。
"$SKILL_ROOT/scripts/codex-long-jobs" status --name train-model-run-01 --json

# 在自己的终端中持续查看日志。
"$SKILL_ROOT/scripts/codex-long-jobs" tail --name train-model-run-01

# 明确停止记录的进程组，并等待进入终态。
"$SKILL_ROOT/scripts/codex-long-jobs" cancel --name train-model-run-01

# 可选：创建或恢复 tmux 日志查看窗口。
"$SKILL_ROOT/scripts/codex-long-jobs" view --name train-model-run-01
```

`start` 和 `status` 会报告日志路径及可直接复制的 `log_follow` 命令。
默认的 `--viewer none` 不会创建日志查看窗口，即使当前处于 tmux 中也是如此。

取消操作先发送 `SIGTERM`，默认等待 10 秒后，必要时升级为 `SIGKILL`；可通过 `--grace-seconds` 调整等待时间。
如果命令在信号生效前已经自然结束，会保留其实际结果。
`cancel` 以零退出，仅表示任务已经进入持久化终态，不一定说明取消生效；应检查 `status` 和 `cancel_effective_at`。
逃离所记录 OS session 的进程、调度系统中的任务，以及容器，需要使用各自的取消机制。

### 恢复已接受的通知

如果任务已经结束，通知状态为 `queued`，请先检查任务状态和原始会话，必要时仅重试唤醒步骤：

```bash
"$SKILL_ROOT/scripts/codex-long-jobs" retry-wake --name train-model-run-01
```

命令成功退出只表示辅助进程已启动，不代表后续工作已经完成。
辅助进程会在限定时间内于本地等待，默认上限为 24 小时，不会重发已接受的消息。
安装更新不会替换正在运行的工作进程已经加载的代码；不要仅为更新通知行为而重启健康运行的任务。

对于仍在等待的旧版 TUI 投递，请在 tmux 中恢复同一个原始 Codex 会话，然后执行：

```bash
"$SKILL_ROOT/scripts/codex-long-jobs" rebind --all
```

队列模式的任务不需要重新绑定 pane。
对于尚未被接受的待投递通知，`retry-delivery --name NAME` 会在通过安全检查后重试投递。
通过 `retry-delivery --name NAME --use-queue` 迁移时，旧投递进程必须已经退出，并有持久化证据证明提示尚未粘贴；记录不明确时，必须明确确认接受重复投递风险。
介入恢复前，请阅读[会话恢复](references/operations.md#session-exit-and-resume)和[队列恢复](references/operations.md#unattended-queue-wake)。

### 选择投递模式

| 模式 | 行为 |
| --- | --- |
| `auto` | 支持队列且已知所属会话时优先使用队列，否则使用经过校验的旧版 TUI 路径，或仅持久化完成事件。 |
| `queue` | 明确要求按会话 ID 进行队列投递。 |
| `tui` | 要求使用经过校验、运行在 tmux 中的 Codex CLI TUI。 |
| `event-only` | 仅记录完成事件，不启动新的 Codex 轮次。 |
| `headless` | 仅在明确授权后启动独立的 `codex exec resume`；不保证与已打开的 TUI 实时同步。 |

### 手动安装与更新

对于保留本地克隆的用户，离线安装器会创建固定版本的 release worktree，避免开发中的修改影响已安装 Skill：

```bash
git clone https://github.com/zycdev/codex-long-jobs.git \
  "$HOME/workspaces/codex-long-jobs"
cd "$HOME/workspaces/codex-long-jobs"
python3 scripts/install_skill.py install --ref v0.5.0        # 预览。
python3 scripts/install_skill.py install --ref v0.5.0 --yes  # 安装指定版本。
python3 scripts/install_skill.py verify
```

安装器会将 `${CODEX_HOME:-$HOME/.codex}/skills/codex-long-jobs` 链接到 `${XDG_STATE_HOME:-$HOME/.local/state}/codex-long-jobs/release` 下的独立 worktree。
重启 Codex 后加载已安装的指令。
私有任务状态默认保存在 `${CODEX_HOME:-$HOME/.codex}/long-jobs`。

新安装默认启用每周一次的版本检查，只在使用 Skill 且达到检查间隔时执行，并且仅作通知。
安装更新需要明确请求；网络失败不会阻止任务操作，重新安装也会保留已有偏好设置。
关闭检查：

```bash
SKILL_ROOT="${CODEX_HOME:-$HOME/.codex}/skills/codex-long-jobs"
python3 "$SKILL_ROOT/scripts/check_updates.py" configure --enabled no
```

检查间隔、复制安装后的初始化和更新流程，见[版本检查与手动更新](references/updates.md)。

## 常见问题

### 关闭 VS Code 或 SSH 后，Codex 还能自动恢复吗？

在远端主机、兼容 daemon、会话访问权限和所需工具仍可用时，可以恢复。
[Remote-SSH 验收](#已验证的-vs-code-remote-ssh-自动续作)在特定配置下验证了重新打开窗口或重新连接前的工具调用与续作。
任务继续运行和模型继续工作是两件事：即使 Codex 无法恢复，独立的后台任务仍可能正常完成。
这不代表对任意断连、主机重启或 daemon 丢失的恢复保证。

### 是否完全不再轮询，也不使用模型？

等待任务期间无需模型轮询。
本地监督进程仍会检查进程和取消状态，恢复逻辑也可能检查本地就绪条件。
启动任务和处理完成结果会使用正常的 Codex 轮次，已授权的后续工作也可能继续使用模型轮次。

### 为什么任务已经 queued，Codex 却还没有继续？

`queued` 仅表示通知已被接受。
请检查 `status --json`，特别是 `queue_wake`，并查看原始会话。
会话忙碌、已有排队输入、写锁、daemon 不可用或缺少客户端工具，都可能延迟或阻止续作。
应使用[仅重试唤醒的恢复操作](#恢复已接受的通知)，而不是重发已接受的通知。

### 是否需要 tmux 或独立安装的 Codex CLI？

如果队列投递和可信扩展二进制文件发现正常工作，两者都不是必需的。
tmux 仍可用于查看日志和旧版 TUI 投递。
运行 `doctor` 检查环境，详见[兼容性与运行要求](#兼容性与运行要求)。

### 能否与正在进行的 Codex Goal 一起使用？

可以，但完成通知不会管理 Goal 的生命周期。
处于 active 状态的 Goal 可能继续产生额外的等待轮次。
请遵循 [Goal 等待指引](SKILL.md#goal-aware-waits)，包括暂停 Goal 需要明确授权这一要求；队列通知本身不会将 Goal 改回 `active`。

### 有哪些安全与恢复限制？

任务无法在主机重启，或工作进程与监督进程同时丢失后继续运行。
仅监督进程丢失时，工作进程仍可完成任务，但不会有替代监督进程来检测之后可能发生的工作进程故障。
如果需要主机故障后的恢复，应使用服务管理器或集群调度系统。

状态记录会保存 argv 和工作目录，因此不要将秘密信息放入命令参数。
完成提示仅引用日志路径，不直接插入日志内容；日志应始终视为不可信数据。
通知投递尽力而为，进程完成不等于产物正确。
完整的[安全边界](references/operations.md#security-boundaries)和[已知限制](references/operations.md#known-limitations)维护在运维参考文档中。

## 测试与贡献

在仓库克隆目录中运行：

```bash
scripts/smoke_test.sh
scripts/coverage_test.sh
scripts/stress_test.sh 10
ruff check scripts/*.py tests
ruff format --check scripts/*.py tests
shellcheck scripts/codex-long-jobs scripts/*.sh
```

CI 覆盖 Python 3.10 至 3.14，并要求至少 80% 的覆盖率。
自动化测试涵盖生命周期故障、取消、队列与唤醒恢复、重复投递抑制、并发、参数与路径安全，以及旧版 TUI 行为。
使用隔离模型测试桩的真实 CLI 测试，与有人参与的 VS Code 测试，分别提供不同层面的证据；两者都不代表覆盖所有客户端版本或平台。

复现命令、Skill 校验、带日期的验收结果及剩余覆盖限制，见[测试与发布验收](references/testing.md)。
贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，仓库约定见 [AGENTS.md](AGENTS.md)。
请通过 [GitHub Issues](https://github.com/zycdev/codex-long-jobs/issues)反馈 Bug、提出功能建议，并在修改持久化状态结构或投递行为前先讨论。
变更历史见 [Changelog](CHANGELOG.md)，已发布版本与发布说明见 [GitHub Releases](https://github.com/zycdev/codex-long-jobs/releases)。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
