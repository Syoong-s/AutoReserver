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
│   ├── venue_pages.py            # 每个任务内有界的场馆标签页复用与独立日历状态
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

### 7.5 长时间监测的超时诊断与受限自恢复（2026-10-27）

用户报告的是“预约网格加载超时／读取空位失败”，且停止后重新开始通常恢复。这与旧页面、未完成请求或浏览器会话状态卡住相符，但不能据此认定网站已识别自动化。未取得真实故障时的服务器响应与网站内部风控规则，以下区分源码可确认的问题与尚未证实的原因。

| 发现 | 源码及资料支持的结论 | 改进 |
| --- | --- | --- |
| 网格和点击共用原来的 10 秒等待 | 慢请求或加载遮罩未消失会被统一当作网格超时 | 独立 `grid_timeout_seconds=30.0`，范围 0.1–120 秒；设置中可调整，不改变点击超时 |
| 页面导航只清除快照缓存 | 上一文档未完成的请求可能继续影响新文档的就绪判断 | 主框架导航同时清空旧请求集合、错误状态并推进版本号 |
| 日期读取失败后强制整页导航 | 会重复下载资源，并可能重新建立相同的卡住状态 | 已有预约页面时优先恢复日期读取；旧数据请求未结束时先等待，不叠加新的日期点击 |
| 任意一次局部读取成功就清除失败计数 | “页面加载成功、目标网格失败”能持续循环，退避恢复过早 | 仅完整监测轮次恢复失败状态，保留跨局部成功的失败计数 |
| 同一操作触发多个原生接口 | 多个 429/503 响应可能对一次操作重复加倍退避 | 每个读取预算只累计一次服务器失败，保留最长 Retry-After |
| 只等待退避不能修复浏览器状态 | 和手动停止／重开后的恢复现象一致，仍需真实故障日志确认具体原因 | 连续两次网格／操作超时触发一次浏览器重建，第二个浏览器仍失败则明确停止，不无限循环重启 |

实现位于 `monitoring.py`、`sjtu.py`、`runner.py`、`multivenue.py` 和 `desktop_controller.py`。单次监测最多重建一次浏览器；先关闭旧实例，再打开新实例。重建前按手动停止时的方式保存当前已登录会话，沿用同一份订单 SQLite。已提交、结果未知等状态仍由原订单锁保护，不重放提交操作；停止按钮会取消尚未开始的重建。普通固定时长任务保留原截止时刻，恢复不会重新赠予完整监测时长。

HTTP 401 或页面离开场馆站点会明确要求重新登录；403 停止监测。429 及 500/502/503/504 使用共享服务器退避，尊重 Retry-After，不以这些状态触发浏览器轮换。网络／网格失败后普通读取临时降低到原间隔的 2、4、8 倍，恢复间隔上限至少保留用户原间隔、通常不超过 600 秒；三个完整健康轮次才降低一级。恢复观察期间关闭抢购加速，避免故障刚结束就产生高频读取。原有全局最小间隔和非负随机抖动继续生效。

超时信息区分“网格未出现、仍有 N 个请求未完成、加载遮罩未消失、日历未返回、目标日期／项目未就绪”。重建证据写入当前运行目录的 `read-recovery-1/`、`read-recovery-2/`，包含 `result.json` 和尽力保存的页面截图。正常情况下不额外探测接口，不调用隐藏预约接口，不改变浏览器自动化标识。接口限流、服务器繁忙、会话失效和客户端卡住是不同问题，这些恢复改进不保证网站不会识别或限制自动化。

#### 网络调研依据

- [Playwright Page.route](https://playwright.dev/python/docs/api/class-page#page-route)：路由会禁用 HTTP 缓存。当前程序为阻断付款必须保留路由，因此减少整页重载尤其有价值；本次未解除付款阻断或强制绕过缓存约束。
- [RFC 6585 §4：429 Too Many Requests](https://www.rfc-editor.org/rfc/rfc6585.txt)：429 表示请求速率受限，可以带 Retry-After；它与仅有 DOM 网格超时不是同一证据。
- [Amazon Builders’ Library：Timeouts, retries, and backoff with jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/)：重试可能放大故障负载，应限制重试层次和次数，使用退避与抖动；超时不能证明有副作用的请求没有执行。
- [Playwright Authentication](https://playwright.dev/python/docs/auth)：已保存登录状态可以复用，但会过期；因此明确识别登录失效，不把它持续当作网格慢响应重试。

#### 验证与运行环境

使用已有 Python 3.14.4、Playwright 1.64.1 和本机 Chromium（Playwright revision 1248）；没有新增依赖，也未生成 Windows 包。源码语法检查使用 `python -m compileall -q autoreserve scripts tests`。单元检查使用 `python -m unittest discover -s tests -p test_read_recovery.py -v` 和 `python -m unittest discover -s tests -p test_monitoring.py -v`。浏览器测试先通过 `scripts.bootstrap.environment()` 加载现有私有运行库，再设置 `RUN_BROWSER_TESTS=1` 运行 `test_browser`、`test_multivenue`、`test_rush_browser`；界面测试另设置 `RUN_GTK_TESTS=1` 运行 `test_gtk`。

测试仅使用本机回环站点与临时配置。恢复场景将旧浏览器的网格读取固定为失败，再让新浏览器执行真实本地预约流程，检查只重建一次且只提交一单；单元测试同时验证已确认订单跨浏览器保留、持续故障不第三次重启、HTTP 拒绝不轮换浏览器、停止取消恢复、普通任务的截止时刻不被重置。开发说明集中在本文件，README 文件不作修改。

---

### 7.6 场地时段浮窗的滚动定位竞态（2026-10-27）

发布工作流的 `test_slot_scroller_retains_scroll_position_on_selection` 曾出现 `84.25 != 80`。根因是浮窗打开后 25 毫秒执行的初始定位，与用户滚动／修改选择并发：回调按已选时段重新计算行偏移，覆盖用户刚设置的 GtkAdjustment。此前工作区初始化还会在浮窗未显示时安排另一轮定位，增加分配时序的不确定性。

源码修复集中在 `gtk_desktop.py`：移除隐藏浮窗的初始化定位；每次打开只保留一个由窗口拥有的定位计时器。修改场地／时段选择、关闭浮窗或销毁窗口时取消计时器；回调执行时检查浮窗是否可见，以及滚动位置是否已从打开时发生变化。已经手动滚动时保留当前位置，不再执行初始定位。不修改滚动条的正常 GTK 行为，也不放宽测试的 1 像素误差。

回归测试先完成一次原生布局，再重新打开浮窗并立即交互，稳定构造“交互发生在延迟定位之前”的顺序；分别覆盖滚动、选择和关闭。用 Git HEAD 中的旧实现加载相同测试，可重现原始 `84.25 != 80` 断言，以及选择早间时段后误跳到顶部的问题。测试环境继续使用已有 Python 3.14.4 与 GTK 3，无新增依赖；使用 `RUN_GTK_TESTS=1` 和 `scripts.bootstrap.environment()` 运行 `test_gtk`，CI 对应的 X11 后端设置为 `GDK_BACKEND=x11`。语法检查使用 `python -m compileall -q autoreserve scripts tools tests demo`。未生成 Windows 包，README 文件保持不变。

验证结果：X11 下完整 GTK 套件 26 项通过；两个滚动测试各重复 10 次，以及布局恢复重复 3 次，合计 23 项通过。Python 3.14 的全套 discover 执行了 262 项，其中 236 项通过、26 项因当前解释器缺少 tkinter 无法导入／启动；因此不将该次运行记为全套通过。随后使用已有 Python 3.12.13（tkinter 8.6）运行 `test_chrome`、`test_gui_startup`、`test_native`，34 项全部通过，覆盖上述遗漏场景。两个环境合计覆盖 265 个独立用例，不代表同一解释器下单次完整运行结果。日志分别位于 `.runtime/gtk-slot-scroll-full-tests.log`、`.runtime/gtk-slot-scroll-legacy-tests.log` 和 `.runtime/gtk-slot-scroll-repeat.log`。

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

## v0.16.0：独立任务与整轮监测

原生工作台为每个目标或重复模板保存一个独立 worker。每个 worker 拥有自己的线程、Playwright 实例、Chromium 浏览器、停止事件、运行参数、日志、进度和结果；重复模板的日期实例仍归属于模板。各任务只共享受锁保护的配置修改、静态场地缓存、完成通知集合，以及统一的 SQLite 订单日志。浏览器 API、页面读取和会话快照采集不得持有工作台共享锁，避免一个慢浏览器阻塞其他任务。

当前实现用 scoped controller view 复用已验证的 runner/recovery 路径，创建 view 时显式替换全部可变执行状态。任务注册表按目标 ID 管理；单任务停止只设置其自己的 event，程序退出则统一通知全部 worker。新任务的默认参数可在其他任务运行时保存，已经启动的配置保持冻结。配置、缓存和会话 JSON 使用独立临时文件原子替换；SQLite 在检查占位前使用 `BEGIN IMMEDIATE`，确保并发浏览器只有一个能取得同一时段的提交许可。

普通多场馆监测以整轮为单位计算间隔。每轮依次读取去重后的场馆/日期组合，页面初始化与日期选择归为一次读取动作；场馆之间保持短串行间隔，下一轮按本轮起点加配置间隔和抖动安排。抢购阶段继续使用任务自身的首秒及后续加速窗口，服务器退避仍优先。日志记录轮次、逐场馆结果、汇总与下一次检查时间；瞬时加载和倒计时仅更新状态。`inventories` 进度字段携带全部场馆的结果、关注时段、轮次、检查时间和错误状态，GTK 同时保留各场馆卡片。

本地回归使用 Python 3.14.4、GTK 3.24.52 和 Playwright 1.64.1；Windows 便携包使用固定的 Python 3.14.5、GTK 3.24.52 和 Playwright 1.64.1。没有新增运行时依赖。源代码启动保持 `./start-gui.sh`；发布包使用 Linux `./AutoReserve` 或 Windows `AutoReserve.exe`。本地构建命令为 `python3 tools/release.py --expected-tag v0.16.0` 和 `python3 tools/build_windows.py --expected-tag v0.16.0`。并发与整轮时序用例位于 `tests/test_independent_workers.py`；测试只使用隔离账户目录和本地模拟网页，不访问真实账户。

## v0.16.1：场馆网页复用与检查耗时

多场馆目标在自己的 Chromium 进程内保留最多 4 个场馆标签页，按最近使用顺序回收额外页面；原始页面保留给登录状态保存。每个标签页独立维护适配器、日历刷新时刻、选中状态和失败恢复标记。普通轮次通过原生日期按钮刷新空位，不再因切换场馆重载整个前端。单场馆原本已有的页面复用保持相同策略。首次初始化、日历过期、日期未开放及读取失败仍会触发完整加载；抢购准备时未开放的目标会精确失效对应场馆的日历。每个场馆的读取时长写入执行日志及进度事件的 `read_seconds`。

标签页只缓存网页环境，不缓存本轮空位授权。返回其他场馆下单前再次请求该日期的最新网格；选择、金额核对、抢购时间门、限流退避及共享 SQLite 提交锁继续生效。停止和异常退出关闭额外标签页并移除观察器。超过 4 个场馆仍逐个检查，重新进入已回收的场馆时重新初始化。

本地解释器为 Python 3.14.4，浏览器依赖为 Playwright 1.64.1，界面为 GTK 3.24.52；Windows 包沿用 Python 3.14.5 及原固定运行库。本次不新增依赖、不修改全局环境。源码启动为 `./start-gui.sh`；本地构建为 `python3 tools/release.py --expected-tag v0.16.1` 和 `python3 tools/build_windows.py --expected-tag v0.16.1`。语法检查为 `python -m compileall -q autoreserve scripts tests`；专项回归为 `RUN_BROWSER_TESTS=1 python -m unittest discover -s tests -p test_multivenue.py -v` 和 `python -m unittest discover -s tests -p test_venue_pages.py -v`。加载现有私有运行库的方法见前文与 `docs/VALIDATION.md`。

## v0.17.0：可编辑已完成目标、状态重置与客户端登录

GTK 仅在当前目标运行期间冻结其要求，已完成/失败/待核对的普通目标仍可编辑。保存修改保留原执行结果和订单详情；显式点击顶部“重置目标状态”才调用 `Journal.reset_goal`：通过 `BEGIN IMMEDIATE` 把当前任务行完整归档到 `attempts`，删除该任务的当前占位和状态。重复点击不会重复归档。控制器只清理当前目标的运行缓存/检查次数/通知去重，其他目标及其占位继续有效。重置不取消网站订单，也不写入或丢弃编辑器草稿；用户可保存新要求后重新启动。生成的重复日期实例仍由模板管理。

`login_surface.py` 将真实官方登录页运行在全新 headless Chromium 上下文中，由 `login_dialog.py` 在 GTK transient/modal 子窗口显示。画面通过单个有锁的内存帧缓冲传递，鼠标按下/释放/拖动/滚轮、键盘导航、GTK 输入法提交和显式粘贴通过有界队列送回拥有浏览器的 worker。GTK 主线程从不调用浏览器 API。PNG 只在内存解码到 GtkImage，输入和画面不写入文件或日志，取消后清空缓冲。官方 OAuth 路由取自体育平台公开前端的“校内人员登录”逻辑；账号密码和验证码仍由官方网页处理。

客户端仅在返回体育平台同源且 currentUser 响应含有效身份时自动保存会话。取消或未核验失败保留原会话；网页导航期间的短暂上下文变化允许有界重试。场馆目录不存在时，另开初始化进度窗口；目录读取继续遵守两秒间隔，显示当前场馆及计数。失败后保留已确认的登录，可单独重试初始化；有效目录缓存不触发进度弹窗或网络刷新。

没有新增依赖。Linux 本地测试使用 Python 3.14.4、GTK 3.24.52、Playwright 1.64.1 和 Chromium revision 1248；Windows 沿用 Python 3.14.5 及原固定 GTK/Playwright 运行库。源码启动仍为 `./start-gui.sh`，发布包通过 `AutoReserve` / `AutoReserve.exe` 启动。语法检查为 `python -m compileall -q autoreserve scripts tests`；回归为 `./test.sh`，或在既有私有环境内设置 `RUN_BROWSER_TESTS=1 RUN_GTK_TESTS=1` 后运行 `python -m unittest discover -s tests -v`。打包为 `python3 tools/release.py --expected-tag v0.17.0` 与 `python3 tools/build_windows.py --expected-tag v0.17.0`。

## v0.17.1：登录回跳识别与弹窗适配

旧的内嵌登录核验把前端 `sessionStorage.token` 作为请求前提，并把完整身份响应限制为 1.2 秒；官方首页只在部分回跳参数存在时初始化该字段，Cookie 登录已经成功时也可能一直等待。新流程直接异步核验同源 `currentUser` 返回的有效身份，每个页面最多一个在途请求，完整响应上限 15 秒，失败后有间隔地重试。读取期间持续绘制和处理输入。仅在服务器确认身份后，按官方首页逻辑补齐响应中签发的 `params.JSESSIONID` 与相关显示字段；已有有效字段保留。仅有本地 token 或匿名接口的成功状态均不能确认登录。

浏览器使用 1100×760 的布局视口；普通 jAccount 页面显示 `.login-layout` 的实际范围，出现外部验证弹层时显示完整视口。`LoginFrame` 同时携带图片和裁切坐标，GTK 按父窗口所在显示器的可用区域等比缩放，并以实际内容的自然尺寸调整子窗口。鼠标坐标按当前帧反向换算，周围留白不会触发网页点击，拖动到边界外释放仍能结束按键。显示图片前不接受盲点输入。无新增依赖；仍使用 Python 3.14、GTK 3.24.52、Playwright 1.64.1 与既有浏览器运行时。

源码启动保持 `./start-gui.sh`，检查命令为 `python -m compileall -q autoreserve scripts tests` 与 `RUN_BROWSER_TESTS=1 RUN_GTK_TESTS=1 python -m unittest discover -s tests -v`（加载既有私有运行库后执行）。构建使用 `python3 tools/release.py --expected-tag v0.17.1` 和 `python3 tools/build_windows.py --expected-tag v0.17.1`。测试包含无前端 token 的 Cookie 登录、延迟 2.5 秒的身份响应、失败身份与伪旧 token、登录卡片/验证弹层裁切，以及原生缩放与坐标映射。
