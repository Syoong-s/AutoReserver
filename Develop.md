# AutoReserve 开发者与构建者指南 (Develop.md)

本文档面向 **AutoReserve** 的开发者、代码贡献者以及构建/打包维护人员，汇集了从原 `README.md` 与 `README-CN.md` 中拆分出的技术架构、内部运行机制、本地开发调试、测试验证与自动化构建发布的全部技术说明。

---

## 目录

1. [技术架构与设计理念](#1-技术架构与设计理念)
2. [源码结构与模块分工](#2-源码结构与模块分工)
3. [本地环境配置与源码运行](#3-本地环境配置与源码运行)
4. [首次启动引导与依赖机制 (Bootstrap)](#4-首次启动引导与依赖机制-bootstrap)
5. [测试与质量验证](#5-测试与质量验证)
6. [构建、打包与发布 (Build & Releasing)](#6-构建打包与发布-build--releasing)
7. [核心机制与内部调度算法](#7-核心机制与内部调度算法)
8. [老版本数据与配置迁移](#8-老版本数据与配置迁移)
9. [相关参考文档](#9-相关参考文档)

---

## 1. 技术架构与设计理念

AutoReserve 采用轻量、去中心化且不依赖系统全局侵入的现代化桌面架构：

- **纯原生 GTK 3 + ctypes C ABI 绑定**：
  不依赖重型的 `PyGObject` / GIR 运行时。在 [`autoreserve/gtk_native.py`](autoreserve/gtk_native.py) 中直接通过 Python 标准库 `ctypes` 调用 GTK 3 / GDK / GLib 原生 C 动态库，显式管理函数签名与回调函数生命周期，使便携包分发体积小、无跨发行版绑定冲突。
- **声明式 UI 与样式**：
  界面使用 GtkBuilder XML 定义（[`autoreserve/gtk_ui/main.ui`](autoreserve/gtk_ui/main.ui)）并配合 GTK CSS（[`autoreserve/gtk_ui/style.css`](autoreserve/gtk_ui/style.css)）渲染，不依赖 WebKitGTK 或任何 HTML WebView。
- **主线程与后台 Worker 解耦**：
  GTK 主线程专注于界面渲染、鼠标拖动调节、布局保存与键盘事件；Playwright 浏览器自动化、网络请求及配置持久化均由后台工作线程持有并串行驱动，通过有时间预算的事件批次队列向主线程同步状态。
- **客户端窗口与装饰 (CSD)**：
  主窗口使用 GTK 原生客户端装饰（CSD），在窗口首次映射前完成设置与标题栏配置；设置弹窗与通知弹窗采用**仅有关闭按钮**的原生瞬态子窗口，避免不规范的最小化/最大化状态。
- **免管理员权限（Zero-sudo）便携设计**：
  无论 Linux 还是 Windows，便携包均解压即用。缺失依赖自动下载到程序文件夹（`.venv/`、`.runtime/`），绝不执行 `sudo` 或修改全局系统 Python。

---

## 2. 源码结构与模块分工

```text
SJTUReserver/
├── autoreserve/                  # 核心 Python 代码包
│   ├── gtk_desktop.py            # GTK 原生桌面工作台、窗口布局、事件分发与设置界面
│   ├── gtk_native.py             # 基于 ctypes 的 GTK 3 / GLib C ABI 动态链接绑定
│   ├── gtk_ui/                   # UI 定义文件与样式表
│   │   ├── main.ui               # GtkBuilder 布局定义
│   │   └── style.css             # GTK CSS 样式表
│   ├── desktop_controller.py     # 桌面控制器：负责后台调度、状态机流转与跨线程事件通信
│   ├── workspace.py              # 与图形库无关的工作区状态、候选生成、目标复制与周重复逻辑
│   ├── multivenue.py             # 多场馆/多场地备选管理、候选优先级匹配与下单流转
│   ├── runner.py                 # 预约执行循环、共享限速预算（ReadPacer）与异常退避
│   ├── repeat.py                 # 按周重复规则调度器与目标自动生成
│   ├── sjtu.py                   # Playwright 网页自动化驱动、会话登录与官方订单提交对接
│   └── desktop.py                # 启动兼容入口；包含历史回归测试保留的 Tk 界面代码
├── tools/                        # 维护与构建工具
│   ├── release.py                # Linux 便携包构建工具（自动编译 C 语言原生启动器并打包）
│   └── build_windows.py          # Windows 便携包打包脚本（集成嵌入式 Python 与 GTK DLL）
├── scripts/                      # 辅助调试与测试脚本
├── tests/                        # 单元测试与端到端模拟测试集
├── docs/                         # 深度技术与架构文档
├── run.sh                        # 统一入口脚本（CLI / desktop / check）
├── start-gui.sh                  # 桌面快速启动 Shell 脚本（自动环境预检与启动）
├── test.sh                       # 完整测试执行脚本
└── pyproject.toml                # 标准 Python 打包元数据
```

---

## 3. 本地环境配置与源码运行

### 3.1 环境要求
- **Python**：Python 3.12+（在 3.12、3.13 与 3.14.4 下验证通过）。
- **GTK**：GTK 3.24+ 及其标准运行库（`libgtk-3.so.0`）。
- **显示环境**：Linux 桌面（Wayland 或 X11），或 Windows 11 下的 WSLg。
- **浏览器**：Playwright 及其 Chromium 浏览器驱动。
- **C 编译器**（可选）：仅在从源码构建生成原生二进制可执行文件 `AutoReserve` 时需要（如 `gcc`）。

### 3.2 源码启动命令
在仓库根目录下，可通过以下方式直接运行程序：

```bash
# 1. 推荐：通过桌面启动脚本（自动执行依赖环境预检与补齐）
./start-gui.sh

# 2. 通过统一运行脚本
./run.sh
# 或显式指定入口：
./run.sh gui
./run.sh desktop

# 3. 命令行配置检查与预检诊断（不启动图形界面）
./run.sh check
```

---

## 4. 首次启动引导与依赖机制 (Bootstrap)

发布出的便携包具备全自动依赖自举引导机制（由启动脚本与 `autoreserve` 引导模块驱动）：

1. **私有 Python 环境初始化**：
   - 优先复用宿主机符合版本要求的系统 Python；
   - 若系统无合适 Python，通过锁定的校验和自动下载轻量级 `uv` 并安装私有 Python 3.12 到程序目录；
   - 依赖自动安装到程序根目录下的 `.venv/` 中，无需依赖 Ubuntu 的 `python3-venv` 系统包。
2. **Chromium 浏览器初始化**：
   - 自动下载无头/有头 Chromium 运行库至 `.runtime/browsers/`，完全隔离于系统浏览器。
3. **Linux 缺失原生库解压 (Zero-sudo)**：
   - 在缺乏 GTK 3 相关底层运行库或中文字体的 Ubuntu / Debian 环境下，自动通过 `dpkg-deb --extract` 仅解压所需动态库与字体至 `.runtime/native/`，从不执行 `sudo apt install`。
4. **初始化状态记录**：
   - 成功启动并验证真实浏览器后，`config/config.json` 中的 `initialized` 字段才标记为 `true`；
   - 详细依赖安装回执与异常信息记录在 `config/bootstrap.json` 中。
5. **桌面快捷方式**：
   - 启动器会自动在当前用户的应用菜单中生成 `.desktop` 快捷方式。移动文件夹后，只需在新路径下双击运行一次即可自动修正路径。

详细技术实现请参见 [首次启动说明 (docs/FIRST_RUN.md)](docs/FIRST_RUN.md)。

---

## 5. 测试与质量验证

项目中包含完备的自动化测试体系，覆盖核心逻辑、GTK 窗口行为及端到端流程：

### 5.1 运行测试套件
```bash
# 运行完整测试脚本
./test.sh
```

### 5.2 语法与静态检查
```bash
# Python 字节码静态编译检查
python3 -m compileall -q autoreserve scripts tools tests demo

# JavaScript 语法检查（如有 UI 辅助资源）
node --check autoreserve/ui/app.js

# Shell 脚本语法检查
bash -n run.sh start-gui.sh test.sh
```

### 5.3 无显示设备（Headless）环境测试
在 CI 或无图形界面的 Linux 服务器上，使用 `xvfb-run` 模拟虚拟 X11 显示环境：
```bash
xvfb-run -a ./test.sh
```

### 5.4 测试范围与安全性保证
- **安全隔离**：全部自动化测试均在本地临时目录（临时配置与私有 SQLite）中运行，对接内置的 Mock 模拟 HTTP 站点，**严禁且绝不会**向真实选场平台发起请求或占用名额。
- **覆盖特性**：GTK CSD 首帧标题栏、只保留关闭按钮、FlowBox 留白处滚轮事件穿透、双固定监测区、场馆草稿保存、目标顺延复制、周重复模板引擎、并发安全与协同关闭。

详细测试覆盖矩阵见 [验证记录 (docs/VALIDATION.md)](docs/VALIDATION.md) 与 [架构设计 (docs/ARCHITECTURE.md)](docs/ARCHITECTURE.md)。

---

## 6. 构建、打包与发布 (Build & Releasing)

### 6.1 便携包构建工具 (`tools/release.py`)
便携包打包脚本负责编译原生 C 启动二进制并生成独立的便携压缩包：

```bash
# 生成 Linux 便携包（生成 .tar.gz, .zip 及 SHA256SUMS 到 dist/）
python3 tools/release.py

# 验证发布包校验和
(cd dist && sha256sum -c SHA256SUMS)

# （可选）打包标准 Python Wheel / sdist
python3 -m build
```

### 6.2 打包白名单与隐私安全保护
`tools/release.py` 内置严格的文件白名单机制，在打包时**强制排除**以下敏感文件与运行时产物（即使它们存在于本地）：
- 用户认证凭据与会话缓存（`.auth/`）
- 订单数据库与历史记录
- 个人配置文件（`config/config.json`）
- 虚拟环境与浏览器缓存（`.venv/`, `.runtime/`）
- Python 编译字节码（`__pycache__`, `*.pyc`）
- Git 仓库元数据（`.git/`）

### 6.3 Windows 便携包构建
Windows 版本使用专用脚本 `tools/build_windows.py` 打包，集成私有内嵌 Python 3.14+ 运行时与预编译 GTK DLL 库：
```bash
python tools/build_windows.py
```
具体构建环境与步骤见 [Windows 构建与验证说明 (docs/WINDOWS.md)](docs/WINDOWS.md)。

### 6.4 GitHub Actions 自动化发布
仓库内已配置自动化 CI/CD 工作流：
- 当推送符合语义化版本规范的 Git Tag（例如 `v0.12.0`）时，工作流自动触发；
- 在独立的 Linux 和 Windows Runner 上执行完整测试并构建便携包；
- 汇聚生成统一的 `SHA256SUMS`，并自动发布到 GitHub Releases。
- 详情参见 [发布说明 (docs/RELEASING.md)](docs/RELEASING.md)。

---

## 7. 核心机制与内部调度算法

### 7.1 事件循环批次消费机制
为了保证 GTK 界面在频繁状态更新与长列表渲染下依然保持 60fps 级别的顺滑响应：
- GTK 主线程设置了 **33ms 时间预算** 的事件消费器；
- 后台 Worker 产生的日志、进度事件与空位快照以批次聚合的形式消费，只保留最新状态和最新快照，避免多余重绘；
- 场馆和时段选择完全在内存中完成，不频繁阻塞读写磁盘。

### 7.2 共享访问预算与退避算法 (`ReadPacer`)
- 全局唯一的 `ReadPacer` 控制对目标站点的访问速率，避免单个目标各自建立高频循环导致触发风控；
- 基础查询间隔默认 15 秒，叠加 0%–20% 随机抖动（Jitter）；
- 捕获 HTTP 429 / 503 时，严格遵守响应头中的 `Retry-After`；读超时则触发指数退避（30s 起步，上限 600s）；
- 捕获 HTTP 403 时立即暂停所有监测任务，提示人工介入。

### 7.3 开抢抢购算法 (Rush Mode)
- **提前预热（默认 45 秒）**：验证用户会话活性，估算服务器时钟偏差，预载目标场馆页面 DOM；
- **时钟校准**：通过服务器 HTTP 响应头的 `Date` 字段采样估算本地与服务器时间差，界面展示约为 $\pm 500\text{ms}$ 的不确定度；支持手动微调补偿；
- **开抢突发脉冲**：开抢首秒内以 250ms 为目标间隔进行串行监测，严格限制账号级首秒及滚动 1 秒内最多发起 4 次读取动作；
- **平滑过渡**：第 2–9 秒自动切换为 1000ms 间隔，之后平滑恢复为普通监测频率。

详细算法分析与参数调优参见 [抢购模式文档 (docs/RUSH.md)](docs/RUSH.md)。

### 7.4 目录与场地名称缓存
- 首次保存登录后，客户端以串行（至少 2 秒间隔）方式读取并生成 `.auth/catalog.json` 目录缓存；
- 目录缓存一旦生成永久复用，后续选择场馆均读取本地缓存，不给服务器增加查询压力；
- 实际监控过的项目会自动缓存具体场地名称，供用户在界面中精准指定。

---

## 8. 老版本数据与配置迁移

- **配置路径规范化**：
  默认配置固定保存在程序目录下的 `config/config.json`。若检测到根目录下存在旧版 `config.json`，程序会自动校验并将其迁移到 `config/`，原文件备份为 `config/legacy-config.json`。
- **v0.5 多场馆计划合并**：
  首次升级至多场馆统一工作区时，程序会自动读取旧版 `.auth/plans/` 中分场馆保存的目标，合并生成统一的工作台候选结构，旧配置备份为 `.auth/workspace-v1.json`。
- **持久化订单幂等保护**：
  每次提交前在本地 SQLite 中写入事务锁。订单提交结果与场地时段严格绑定，即使程序崩溃重启，也不会对已成功预订的时段重复下单。

---

## 9. 相关参考文档

- [系统架构与多场馆实现设计 (docs/ARCHITECTURE.md)](docs/ARCHITECTURE.md)
- [测试验证与回归测试记录 (docs/VALIDATION.md)](docs/VALIDATION.md)
- [GitHub 自动化发布与流水线说明 (docs/RELEASING.md)](docs/RELEASING.md)
- [首次运行依赖自举说明 (docs/FIRST_RUN.md)](docs/FIRST_RUN.md)
- [Windows 平台原生支持与打包说明 (docs/WINDOWS.md)](docs/WINDOWS.md)
- [抢购模式参数与时钟校准算法 (docs/RUSH.md)](docs/RUSH.md)
