# devcache

[![CI](https://github.com/abbhb/devcache/actions/workflows/ci.yml/badge.svg)](https://github.com/abbhb/devcache/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/abbhb/devcache)](https://github.com/abbhb/devcache/releases)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

中文 | [English](README.en.md)

给 macOS 开发工具切换全局缓存位置的 CLI。只有实际检测到、且能成功查询缓存路径的工具才允许启用。

外盘优先模式在**每次启动命令时**按卷 UUID 查找外盘；外盘缺失、未挂载、只读或目录不可用时，不注入外盘配置，让工具按原来的环境变量、配置文件和默认路径运行。重新接盘后下一条命令恢复使用外盘。工具本体、GVM/NVM 版本目录和项目文件不迁移。

## 一键安装

macOS Apple Silicon / Intel，预编译包自带运行时，无须安装 Python、Homebrew，也不需要 `sudo`。安装器按实际架构选择 Release 安装包，校验 SHA-256 后安装到当前用户目录。

```sh
curl -fsSL https://raw.githubusercontent.com/abbhb/devcache/main/install.sh | bash
```

安装不会替你绑定任何磁盘或删除缓存。**新开终端，运行 `devcache`，在菜单中选择「一键配置外盘」**，即可看到你自己的已挂载外盘和已安装工具。

也可以手动配置（`DevSSD` 仅为示例名称）：

```sh
devcache disks
devcache setup "DevSSD"
```

每人独立配置；目标盘使用 UUID 绑定，改盘名仍能识别。换一块物理盘时重新 `setup`。当前每位用户绑定一块目标盘。

重复执行安装命令即可升级，保留磁盘配置、工具选择和缓存。首次使用不会自动搬走旧缓存，迁移/清理由你选择。

<details>
<summary>固定版本、先检查安装脚本或离线安装</summary>

```sh
# 固定安装某个 Release
curl -fsSL https://raw.githubusercontent.com/abbhb/devcache/v0.1.0/install.sh | bash -s -- --version v0.1.0

# 先下载和查看，再执行
curl -fsSL https://raw.githubusercontent.com/abbhb/devcache/main/install.sh -o install.sh
less install.sh
bash install.sh

# 手动下载对应架构的 tar.gz 和 SHA256SUMS 后离线安装
bash install.sh --from /path/to/release-assets

# 不自动修改 zshrc，适合其他 shell 或显式 exec 用法
bash install.sh --no-shell
```

预编译包在 macOS 15 的 Apple Silicon 和 Intel runner 分别构建、验证，目前未做 Apple Developer 公证。无需关闭系统安全设置。较旧系统可选择下述源码方式；未声称已在所有旧版 macOS 验证。

</details>

## 从源码安装

源码方式要求 macOS、Python 3.10+；应用仅用 Python 标准库，不下载或安装 npm/pnpm 等开发工具。

```sh
git clone https://github.com/abbhb/devcache.git
cd devcache
python3 devcache.py install
```

`setup` 会创建 `外盘/DevCaches/devcache-用户名/` 并按 UUID 绑定，启用当前已安装且已适配的工具。不会自动复制或删除旧缓存。zsh 配置追加独立的 `source` 块，并在 `~/.config/devcache/backups/` 保存修改前备份。

打开新终端，或按安装输出运行一次 `source ~/.local/share/devcache/shell.zsh`。以后可直接运行 `devcache` 打开交互菜单。

```sh
devcache scan --sizes                   # 已安装工具、真实路径和内盘占用
devcache status                        # 偏好、实际位置及自动回退原因
devcache use internal                  # 手动回到原配置
devcache use external                  # 外盘优先；缺盘默认自动回内盘
devcache use auto                      # 与 external 相同的自动回退策略
devcache enable npm go pnpm             # 只允许实际安装、查询成功的工具
devcache disable uv                     # 单个工具停止接管
devcache copy --tools go,npm            # 可选：复制原缓存，内容校验后保留原件
devcache copy --tools npm --ask-clean   # 校验通过后再预览并询问清理
devcache clean --dry-run                # 只预览可清理的内盘缓存
devcache clean --tools go               # 显示路径/占用后 y/N 确认
devcache clean --tools npm --yes        # 明确确认清理，供脚本使用
devcache exec -- python3 -m pip cache dir
devcache uninstall                     # 移除新终端接管；缓存和备份保留
devcache uninstall --purge             # 再移除 devcache 命令和运行时；缓存/配置/备份仍保留
```

## 工具支持

| 工具 | 检测 | 外盘配置 | 范围 |
| --- | --- | --- | --- |
| npm/npx | `npm config get cache` | `npm_config_cache` | 下载缓存、npx 缓存；不改全局安装 prefix |
| Go/GVM | `go env -json GOCACHE GOMODCACHE` | `GOCACHE` / `GOMODCACHE` | 编译缓存、模块缓存；回退时使用当前 GOPATH，不固定旧 GVM 版本 |
| pnpm 10 | 版本及 `config get cache-dir` | `npm_config_cache_dir` | 全局 cacheDir；不移动 store 或已有项目依赖 |
| uv | `uv cache dir` | `UV_CACHE_DIR` | 依赖缓存；不改 Python 安装目录或虚拟环境 |
| pip/pip3 | `pip cache dir` | `PIP_CACHE_DIR` | 下载和 wheel 缓存；不改包安装位置 |
| Homebrew | `brew --cache` | `HOMEBREW_CACHE` | 下载缓存；不改 Cellar、Caskroom 或已安装软件 |
| Yarn Classic 1 | 版本及 `yarn cache dir` | `YARN_CACHE_FOLDER` | 全局缓存；Yarn 2+ 暂不提供切换，避免 PnP 运行依赖问题 |

未安装工具不出现在可选列表。只留下缓存目录不算已安装。Corepack 探测禁用联网，不自动下载包管理器。已检测到但尚未适配的工具（例如 Cargo、Maven、Gradle）会明确列为不可切换，不伪装成已经支持。JetBrains、VS Code 等 GUI 应用缓存不在本版接管范围。

pnpm 的 `~/Library/Caches/pnpm` 和 `~/Library/pnpm/store` 是两回事。本工具管理前者；store 与已有项目存在关联，频繁切换可能要求重新安装依赖，因此保留它。uv 缓存与虚拟环境跨文件系统时，uv 可能退回复制，安装速度会受到影响。

## 工作方式和边界

- 提供 PATH shim 和 zsh 函数；zsh 函数在 `nvm use`、`gvm use` 改变 PATH 后仍可生效，真实程序每次从当前 PATH 解析，不锁死版本。
- 不向 `.npmrc`、Go env 文件或其他工具配置永久写入外盘绝对路径，也不把本地缓存目录改成软链接。手动回内盘恢复原来配置，包括用户自定义路径。
- 子命令继承的受管理缓存环境会先还原再决定位置，防止父命令遗留的外盘变量破坏缺盘回退。明确传入的工具命令行参数仍以该工具的优先级为准。
- 外盘创建目录先验证 UUID、外部介质属性和真实挂载点，再通过卷目录描述符创建子目录；拒绝符号链接目标，避免缺盘后在内盘 `/Volumes` 下创建假缓存目录。
- 内外缓存独立，不自动同步。离线回退后缺少依赖仍可能需要网络。正在运行的任务无法在拔盘时迁移打开的文件句柄，可能需要结束并重试。不会自动重试安装等可能有副作用的命令。
- shell 接管作用于新打开或重新加载配置的终端；已运行的 IDE、守护进程及绝对路径调用可能绕过 PATH shim。可用 `devcache exec -- <命令>` 显式启动并注入所选缓存配置。`python -m pip` 也使用此入口。
- `copy` 保留原件、忽略外盘已有文件，再通过 rsync 内容校验；有冲突/变化时停止，不进入清理。复制前应结束安装和编译任务。不同时间生成的缓存可能冲突，可保留双份缓存并按需重建。
- 清理先展示具体路径和占用，再默认拒绝确认；非交互模式必须显式 `--yes`。原生 Go/uv/pip 清理命令直接指定原缓存位置；npm 仅删除已知缓存子目录，保留其他文件。Homebrew 只清下载目录，不调用会清除旧安装版本的 `brew cleanup`。
- 清理仅接受已知的用户内盘缓存路径，拒绝项目、过宽目录、目录符号链接和未知自定义路径。受管理命令运行期间禁止缓存维护；通过其他入口启动的旧进程无法统一加锁，执行维护前请结束它们。
- `du` 显示目录分配空间，APFS 克隆、快照或硬链接可能使实际释放空间不同。
- 升级通过新运行时目录和原子替换入口进行，保留旧运行时供已运行任务完成；`uninstall --purge` 会删除这些 devcache 运行时，不删除开发缓存。

配置：`~/.config/devcache/config.json`；安装文件：`~/.local/share/devcache/`；入口：`~/.local/bin/devcache`。`DEVCACHE_VERBOSE=1` 可显示单条命令使用的位置；`DEVCACHE_STATE_DIR` 可选择独立配置目录，用于隔离测试。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

测试使用临时文件和模拟卷，不删除用户缓存、不卸载真实磁盘。覆盖缺盘、回插、改名、同名不同 UUID、假挂载点、只读卷、嵌套环境回退、动态版本 PATH、未安装/失效工具、复制冲突、清理确认/范围、维护锁及安装/卸载幂等。

CI 分别构建 Apple Silicon / Intel 包，并在带空格的独立用户目录验证首次安装、重复安装/升级、配置保留和卸载。发版方式见 [贡献指南](CONTRIBUTING.md)，变更见 [CHANGELOG](CHANGELOG.md)。

## 许可证

[MIT](LICENSE)。

## 配置依据

- [npm 环境变量与 cache](https://docs.npmjs.com/cli/v11/using-npm/config/)
- [Go 模块缓存](https://go.dev/ref/mod#module-cache) 与 [编译缓存](https://pkg.go.dev/cmd/go#hdr-Build_and_test_caching)
- [pnpm 10 cacheDir](https://pnpm.io/10.x/settings#cachedir)
- [uv 缓存配置和跨文件系统行为](https://docs.astral.sh/uv/concepts/cache/)
- [pip 缓存](https://pip.pypa.io/en/stable/topics/caching/)
- [Homebrew HOMEBREW_CACHE](https://docs.brew.sh/Manpage#environment)
- [Yarn Classic 缓存](https://classic.yarnpkg.com/lang/en/docs/cli/cache/)
