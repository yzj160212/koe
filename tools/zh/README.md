# Koe 简体中文版 · 自动同步与构建

把官方的 [Koe](https://github.com/missuo/koe)（macOS 语音输入工具）复刻一份，
自动跟上官方新版本，并产出简体中文的 macOS APP。

## 它是怎么工作的

```
官方发新版 (tag vX.Y.Z)
        │
        ▼
  tools/zh/sync.py          ← 你运行这一个命令
        │  1. 拉取官方 tag
        │  2. 在临时 worktree 里从该 tag 重建 zh 分支
        │  3. 应用本地化补丁 + 合并中文词条
        │  4. 新增的英文字符串自动翻译
        │  5. 推送 zh 分支
        ▼
  GitHub Actions            ← 自动触发，无需干预
        │  构建 APP（macos-26 runner）
        │  关掉 Sparkle 自动更新
        │  发到 Releases
        ▼
  tools/zh/install.sh       ← 安装到 /Applications
```

上游实际发版节奏约 **每两周一次**（v1.0.24 → v1.0.33 是两个月内 7 个版本），
而官方 string 表已经连续 5 个版本没加过新 key。所以日常运行绝大多数时候是
**无事发生**，脚本会静默退出。

## 日常使用

### 1. 更新到官方最新版

```bash
cd ~/code/koe && tools/zh/sync.py
```

- 已在最新版 → 打印一行 `nothing to do — already in sync` 后退出，什么都不做
- 有新版本 → 自动重建、翻译、推送，然后 CI 自动开始构建

### 2. 安装构建好的中文版

```bash
curl -fsSL https://raw.githubusercontent.com/<你的账号>/koe/zh-tooling/tools/zh/install.sh | bash
```

它会问一次是否清除隔离标记（ad-hoc 签名的必要步骤），然后装到 `/Applications/Koe.app`。

### 3. 只看不推

```bash
tools/zh/sync.py --no-push     # 本地重建，检查过再手动 push
tools/zh/sync.py --dry-run     # 只列出缺哪些 key，不翻译不推送
tools/zh/translate.py --check  # 只校验中英词条是否对齐
```

## 分支结构

| 分支 | 内容 | 说明 |
| --- | --- | --- |
| `zh-tooling` | **默认分支**。本工具链 | 不会被自动重建，永远不会被上游覆盖 |
| `main` | 官方镜像 | 保持纯净，所以 GitHub 的「Sync fork」按钮安全可用 |
| `zh` | `官方tag + 1 个汉化提交` | 每次从官方 tag 全量重建，历史永远是线性的 |

**为什么工具不放在 `main`：** 如果 `main` 上有你自己的提交，GitHub 的
「Sync fork」按钮会变成「丢弃你的提交」的危险操作。保持 `main` 是纯镜像，
工具走独立分支，这个风险就不存在。

**为什么 `zh` 每次重建而不是合并：** 历史恒定为一个提交，永远不会有累积的
merge 冲突，`--force-with-lease` 推送也永远安全。

## 冲突了怎么办

线上最容易出问题的只有一种情况：**官方改动了某一行，而那一行正好被本地化
改写过**。这时 `sync.py` 不会瞎猜，它会停下来并打印指引。

绝大多数版本的改动不碰这些行，所以通常不会遇到。真遇到了：

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

官方 app 内置自动更新，指向官方 appcast。构建流程里已经**显式删除**
`SUFeedURL`、`SUPublicEDKey` 并关闭 `SUEnableAutomaticChecks`，CI 里还有一步
校验确认这些键确实没了，否则直接让构建失败。

不要手动改回这些键——那是这个中文版能保持中文的唯一保障。

## 上游 PR

本地化的代码侧改动已提给官方：
<https://github.com/missuo/koe/pull/121>

**如果这个 PR 被合并**，情况会大幅简化：代码侧接线由官方维护，本仓库就只剩
一份中文词条数据要跟，冲突面基本归零。届时 `tools/zh/localize.patch` 可以删除。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `translate.py` | 从 `en.lproj` 找出缺的中文词条，走 OpenAI 兼容网关翻译。会校验占位符（`%@`/`%ld`）和首尾空格，不合格就重试、再不合格就报错退出，绝不写入坏词条 |
| `localize.patch` | 代码侧汉化：把硬编码英文字面量换成 `KoeLocalizedString(...)` 的那批改动 |
| `en.strings` / `zh-Hans.strings` | 本仓库携带的中英词条数据（官方只有 69 条，这里有 308 条） |
| `sync.py` | 主入口：重建 `zh` 分支并推送 |
| `zh-release.yml` | GitHub Actions 构建与发布流程 |
| `install.sh` | 一行命令安装 |

## 翻译配置

默认走本机网关，不需要密钥：

```bash
# 默认值（OpenAI 兼容）
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

也可以在 `translate.py` 里用 `--gateway` / `--model` / `--api-key` 单独调。

## 首次从零搭建

```bash
# 1. fork 官方仓库
gh repo fork missuo/koe --clone=false

# 2. 克隆 fork
cd ~/code && git clone https://github.com/<你的账号>/koe.git

# 3. 加官方 remote
cd koe && git remote add origin https://github.com/missuo/koe.git

# 4. 拿到工具分支
git fetch --all && git checkout zh-tooling

# 5. 首次同步（会重建 zh 分支并触发第一次构建）
tools/zh/sync.py
```
