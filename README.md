# MDCx-New

![python](https://img.shields.io/badge/Python-3.13-3776AB.svg?style=flat&logo=python&logoColor=white)
![platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS-4c8bf5)
![license](https://img.shields.io/badge/License-GPLv3-blue.svg)

MDCx-New 是一个持续维护中的图形化媒体刮削与整理工具分支，面向本地媒体库整理场景，支持多站点元数据获取、封面图片下载、`NFO` 生成、演员信息维护，以及刮削完成后的文件与目录重命名整理。

当前仓库：
- Fork 仓库：[LoongYu/mdcx-new](https://github.com/LoongYu/mdcx-new)
- 上游仓库：[sqzw-x/mdcx](https://github.com/sqzw-x/mdcx)

## 核心亮点

- 持续维护失效站点、更新最新地址与可用域名
- 支持图形化刮削、日志查看、网络检测与多配置切换
- 主界面可直接编辑 `NFO` 信息，并同步重命名文件夹和文件
- 新增“读取已刮削目录”功能，可回读本地结果继续人工修正
- 新增国产站点 `madou` 支持
- 已提供 Windows 与 macOS 安装包

## 下载

普通使用建议直接下载 Release：

- [最新 Release](https://github.com/LoongYu/mdcx-new/releases)

常见安装包：

- Windows：`MDCx-<version>-windows-x86_64.exe`
- macOS：`MDCx-<version>-macos-arm64.dmg`
- macOS：`MDCx-<version>-macos-arm64.zip`

## 快速开始

1. 从 Release 页面下载对应平台的安装包
2. 首次启动后按自己的使用场景配置刮削目录、输出目录、命名规则和站点顺序
3. 如需登录态或特殊站点访问，可在设置中填写 `cookie` 或自定义域名
4. 开始刮削后，可在主界面查看日志、预览结果，并继续人工修正 `NFO`
5. 如需调整已整理内容，可使用“读取已刮削目录”重新载入并编辑

## 主要功能

### 图形化刮削与整理

- 通过图形界面对本地视频进行刮削
- 自动获取标题、演员、简介、标签、发行日期、封面图片等信息
- 自动生成 `NFO` 文件

### 文件与目录命名

刮削完成后可按当前配置规则统一整理：

- 文件夹
- 视频文件
- `NFO`
- 图片
- 字幕
- trailer

主界面编辑 `NFO` 后，也可按最新字段同步更新命名结果。

### 已刮削目录回读

支持将已经刮削完成的目录重新载入主界面，继续进行人工修正，而不需要重新刮削。

### 多配置文件切换

当前版本不再固定写死 `config.json`，可以切换不同配置文件，适合不同目录、不同站点偏好、不同命名规则分别使用。

### 网络检测与站点维护

- 支持检测当前站点连通情况
- 优先使用自定义域名进行网络检测
- 持续清理失效站点与无效配置项

## 运行环境

- Python：`>= 3.13.4`
- 推荐使用 `uv` 管理开发环境

安装依赖：

```bash
uv sync --locked --all-extras --dev
```

源码运行：

```bash
uv run python main.py
```

## 本地构建

### macOS

构建 `.app`：

```bash
uv run scripts/build.py --debug
```

构建 `.app + .dmg`：

```bash
uv run scripts/build.py --create-dmg --debug
```

### Windows

Windows 安装包需要在 Windows 环境下构建：

```bash
uv run scripts/build.py --debug
```

默认产物位置：

- Windows：`dist/MDCx.exe`
- macOS：`dist/MDCx.app`
- macOS：`dist/MDCx.dmg`

## GitHub Actions 发版

仓库已内置发布工作流，Windows 包可通过 GitHub Actions 构建。

典型流程：

```bash
git push fork master
git tag <tag>
git push fork <tag>
gh workflow run release.yml --repo LoongYu/mdcx-new -f tag=<tag> -f release_body=''
```

发布说明优先级：

1. `release-notes/<tag>.md`
2. 手工输入 `release_body`
3. `changelog.md`

## 项目来源

项目演进关系：

- [yoshiko2/Movie_Data_Capture](https://github.com/yoshiko2/Movie_Data_Capture)：早期 CLI 项目
- [moyy996/AVDC](https://github.com/moyy996/AVDC)：早期图形界面 Fork
- `@Hermit/MDCx`：后续分支版本
- [sqzw-x/mdcx](https://github.com/sqzw-x/mdcx)：重构维护版本
- 当前仓库在其基础上继续维护与迭代

向相关开发者表示敬意。

## 授权与使用说明

本项目在 GPLv3 许可授权下发行。此外，如果使用本项目，也默认接受以下约束：

- 本项目仅供学习与技术交流使用
- 请勿在公共社交平台宣传本项目
- 使用本软件时请遵守当地法律法规
- 法律风险及使用后果由使用者自行承担
- 禁止将本项目用于任何商业用途
