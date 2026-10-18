# Koe 简体中文版 · 自动同步与构建

把官方的 [Koe](https://github.com/missuo/koe)（macOS 语音输入工具）复刻一份，
自动跟上官方新版本，并产出简体中文的 Apple Silicon (arm64) **DMG**。

仓库：`https://github.com/<你的账号>/koe`

## 它是怎么工作的

全部自动，**你不需要做任何事**：

```
GitHub 定时任务（每天两次）
        │  比对上游最新 release tag 与 zh 分支当前所基于的 tag
        │  已最新 → 只写一个心跳提交，其他什么都不做
        ▼
   发现新版
        │  1. 从上游新 tag 重建 zh 分支
        │  2. 应用本地化补丁 + 合并中文词条
        │  3. 新增字符串交给翻译（CI 里网关不可达 → 暂用英文原文）
        │  4. 推送 zh 分支，并 dispatch 构建
        ▼
   中文版构建 (macOS runner)
        │  构建 → 关闭 Sparkle 自更新 → 打包 DMG → 校验 → 发 Release
        ▼
  Release 里的 Koe-zh-macOS-arm64.dmg
```

上游发版节奏约 **每两周一次**，且官方 string 表连续多个版本没加过 key，
所以绝大多数检查都是"无事发生"。

## 日常使用

### 拿到中文版

下载 Release 里的 `Koe-zh-macOS-arm64.dmg`，把 Koe 拖进「应用程序」。

或一行命令（自动下载 DMG、挂载、安装）：

```bash
curl -fsSL https://raw.githubusercontent.com/$(gh api user --jq .login)/koe/zh-tooling/tools/zh/install.sh | bash
```

脚本会问一次是否清除隔离标记（ad-hoc 签名的必要步骤）。

### 想立刻检查上游有没有新版

不用等定时任务，手动点一下：

```bash
gh workflow run zh-watch.yml --repo $(gh api user --jq .login)/koe --ref zh-tooling
```

### 本地跑一遍（可选，好处是能用你的翻译网关）

CI 的 runner 访问不到你本机的翻译网关，所以新增字符串在 CI 里会暂用英文原文。
在本地跑一次就会真正翻译，然后自动构建：

```bash
cd ~/code/koe && tools/zh/sync.py
```

### 只看不推

```bash
tools/zh/sync.py --no-push     # 本地重建，检查过再手动 push
tools/zh/sync.py --dry-run     # 只列出缺哪些 key
tools/zh/translate.py --check  # 只校验中英词条是否对齐
tools/zh/translate.py --probe  # 只测翻译网关是否可达
```

## 构建产物

| 产物 | 说明 |
| --- | --- |
| `Koe-zh-macOS-arm64.dmg` | **唯一产物**。拖入「应用程序」即可安装 |

架构：**arm64 专用**（Intel 机器不能运行）。构建时会校验架构，不是 arm64 直接失败。

## 分支结构

| 分支 | 内容 | 说明 |
| --- | --- | --- |
| `zh-tooling` | **默认分支**。本工具链 + 监控 workflow | 不会被自动重建 |
| `main` | 官方镜像 | 保持纯净，所以 GitHub 的「Sync fork」按钮安全可用 |
| `zh` | `官方tag + 1 个汉化提交` | 每次从官方 tag 全量重建，历史永远是线性的 |

**为什么工具不放在 `main`：** 如果 `main` 上有你自己的提交，GitHub 的
「Sync fork」按钮会变成「丢弃你的提交」的危险操作。保持 `main` 是纯镜像，
工具走独立分支，这个风险就不存在。

**为什么 `zh` 每次重建而不是合并：** 历史恒定为一个提交，永远不会有累积的
merge 冲突。

## 关于签名与证书

**不涉及任何证书。** 构建走的是 ad-hoc 本地签名：没有 Apple Developer ID，
也没有公证。这是刻意的——它让这个构建成为个人自用产物，而不是对外分发的二进制。

代价只有一个：从网上下载后 macOS 会打上隔离标记，首次打开需要放行
（`install.sh` 会问你要不要清掉这个标记）。

## 自动监控的两个已知限制

理解这两点，才不会在它们发生时以为坏了。

**1. 公开仓库的定时任务会在 60 天无活动后被自动禁用。**
这是 GitHub 的行为，不是本流程的 bug。上游发版算**他们**仓库的活动，不算你的，
所以长期安静会把这个定时任务关掉。为此每次检查都会写一个心跳提交
（`.watch/last-check`），既是活动痕迹也方便你看"最后一次检查是什么时候"。
真被禁用了，去 Actions 页面点一下 Enable 即可，手动按钮任何时候都能用。

**2. CI 里翻译会降级。**
你的翻译网关跑在本机 `127.0.0.1`，GitHub runner 访问不到。所以 CI 遇到新增
字符串时，会**暂用英文原文**并把构建跑完（而不是卡住不发版）。你本地跑一次
`tools/zh/sync.py` 就会把它们翻译好并重新构建。多数版本新增 key 是 0～2 个，
甚至完全不触发。

## 冲突了怎么办

线上最容易出问题的只有一种情况：**官方改动了某一行，而那一行正好被本地化
改写过**。这时 `sync.py` 不会瞎猜，它会停下来并打印指引。

真遇到了：

```bash
# sync.py 会把临时 worktree 路径打印出来，cd 进去
cd /var/folders/.../koe-zh-build-xxxxx

# 1. 编辑冲突文件：语义没变就保留 KoeLocalizedString(...)
#    官方新增了字符串 → 往 en.lproj 加 key 并在调用处使用它
# 2. 补翻译
python3 ~/code/koe/tools/zh/translate.py --repo .

# 3. 重新生成补丁
git diff v1.0.34 -- \
  KoeApp/Koe/Overlay/SPOverlayPanel.m \
  KoeApp/Koe/Settings/SPSettingsSidebar.m \
  KoeApp/Koe/SetupWizard/SPSetupWizardWindowController.m \
  KoeApp/Koe/StatusBar/SPStatusBarManager.m \
  > ~/code/koe/tools/zh/localize.patch

# 4. 提交补丁后重跑
cd ~/code/koe && git add tools/zh/localize.patch && git commit -m "fix(zh): rebase patch on v1.0.34"
tools/zh/sync.py --force
```

## 两个必须知道的坑

### 1. Homebrew 会覆盖中文版

`brew upgrade --cask koe` 会用官方英文版覆盖 `/Applications/Koe.app`。
想长期用中文版就先 `brew uninstall --cask koe`（app 会留着）。

### 2. 官方版的 Sparkle 自更新会把你变回英文版

官方 app 内置自动更新，指向官方 appcast。构建流程里已**显式删除**
`SUFeedURL`、`SUPublicEDKey` 并关闭 `SUEnableAutomaticChecks`，CI 里还有一步
校验确认这些键确实没了，否则直接让构建失败。

不要手动改回这些键——那是这个中文版能保持中文的唯一保障。

## 上游 PR

本地化的代码侧改动已提给官方：
<https://github.com/missuo/koe/pull/121>

**如果这个 PR 被合并**，情况会大幅简化：代码侧接线由官方维护，本仓库就只剩
一份中文词条数据要跟，冲突面基本归零。届时 `tools/zh/localize.patch` 可以删除。

## 本机旧产物说明

手工汉化时代的产物仍在磁盘上，**新流水线不依赖它们**：

| 路径 | 内容 |
| --- | --- |
| `~/code/koe-zh/` | 旧中文版 app + 一次性补丁（补丁已失效） |
| `~/code/koe-backup/Koe-1.0.33-original.app` | 官方原版备份 |
| `~/code/koe-backup/Koe-1.0.33-pre-pipeline.app` | 旧手工汉化版备份 |

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `.github/workflows/zh-watch.yml` | 定时检查上游版本，发现新版就重建并触发构建 |
| `.github/workflows/zh-release.yml` | 唯一的构建定义：构建 → 打包 DMG → 校验 → 发 Release |
| `tools/zh/translate.py` | 从 `en.lproj` 找出缺的中文词条并翻译。校验占位符（`%@`/`%ld`）与首尾空格，不合格就重试，再不合格就退出，绝不写入坏词条 |
| `tools/zh/localize.patch` | 代码侧汉化补丁 |
| `tools/zh/en.strings` / `zh-Hans.strings` | 本仓库携带的中英词条数据（官方 69 条，这里 308 条） |
| `tools/zh/sync.py` | 重建 `zh` 分支并推送 |
| `tools/zh/make-dmg.sh` | 从 .app 生成 arm64 DMG（含架构校验、内容物验证） |
| `tools/zh/install.sh` | 一行命令安装 |

## 翻译配置

默认走本机网关，不需要密钥：

```bash
KOE_ZH_GATEWAY=http://127.0.0.1:3425/v1
KOE_ZH_MODEL=group/deepseek-v4-1-flash
```

换任何 OpenAI 兼容服务：

```bash
KOE_ZH_GATEWAY=https://api.example.com/v1 \
KOE_ZH_MODEL=some-model \
KOE_ZH_API_KEY=sk-xxx \
  tools/zh/sync.py
```

## 首次从零搭建

```bash
# 1. fork 官方仓库
gh repo fork missuo/koe --clone=false

# 2. 克隆 fork
cd ~/code && git clone https://github.com/<你的账号>/koe.git

# 3. 加官方 remote
cd koe && git remote add origin https://github.com/missuo/koe.git

# 4. 拿到工具分支并设为默认
git fetch --all && git checkout zh-tooling
gh api -X PATCH repos/<你的账号>/koe -f default_branch=zh-tooling

# 5. 首次同步
tools/zh/sync.py
```
