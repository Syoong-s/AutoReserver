# 原生客户端与多场馆预约实现方案（v0.5）

## 当前需求与完成标准

- 原生桌面窗口统一管理主页登录、缓存目录、跨场馆日期目标、场地和时段多选、优先级、定时启动、成功弹窗与日志。
- 一个必做目标有多个按优先级排列的候选；候选包含一个场地和一个或多个连续小时。多选多个场地和时段时，可批量生成候选。
- 多个目标均需完成，每个目标提交一个成功订单。默认持续检查，全部目标成功后才正常结束。
- 预约仍停在提交订单，不支付。

## 结构

Python 3.12+、Tkinter/Tcl/Tk、Playwright 和 SQLite。主线程只负责原生窗口及事件队列，不启动 HTTP 服务；后台线程负责登录、一次性静态目录读取和明确启动后的空位监测。

每次浏览器操作由单一工作线程持有 Playwright，避免跨线程调用同步 API。用户在网站主界面完成登录后，点击客户端保存按钮；浏览器所属线程验证会话并捕获 cookies、localStorage、IndexedDB 和 sessionStorage。场馆/项目从本地目录选择，独立于登录。配置和账号状态通过锁保护，运行时不允许编辑。

## 目标与优先级

配置 `goals` 是目标列表，其顺序决定检查优先级。每个目标有稳定的 ID、名称和有序 `candidates`。目标内所有候选属于同一日期，其他日期另建目标。旧版 `targets` 首次打开 GUI 时迁移为一个目标，既有提交记录一并迁移。

候选中：场地名称精确匹配，时间映射经过核对的 07:00–22:00 小时网格。单次选择后同时核对场馆、项目、日期、场地列、时段行、已选数量、订单摘要和金额。多小时候选要求连续且全部可用；网页若取消了先前选择，验证失败而不提交。

## 多目标监测与共享访问预算

1. 从持久记录恢复成功目标，所有未完成目标同时登记为监测中。
2. 以日期为单位读取空位快照；同一日期只读一次，全部候选和目标共享，不因候选数增加请求。
3. 本轮采集完日期后，统一为目标挑选最高优先级的可用候选，组成下单队列。某个目标无空位不阻塞其他目标。
4. 下单按优先级串行执行，必要时重新核对目标日期和网格。成功只完成当前目标，其他目标继续监测。
5. 默认全部目标成功才正常结束；手动停止、未知提交结果或访问拒绝需保留状态并处理。

`ReadPacer` 管理整个任务唯一的 monotonic 访问时限，而不是为每个目标设置独立的高频循环。默认基础间隔 15 秒，加 0–20% 额外随机延迟；最短基础间隔 5 秒。所有由调度器发起的日期读取和必要整页重载共享预算，候选匹配本身不访问网络。

正常轮询只更新日期页签，日期范围 300 秒才刷新一次。初次页面加载包含网站本身的一组正常资源/API 请求，预算针对监测操作，并非拦截延缓每个静态资源请求。原生客户端没有启动前的空位刷新或预览；定时任务到达设定时间后才加载网格。

对原生空位 API 及预约文档响应监听 429/503 和 Retry-After；超时等读错误也全局退避，30 秒起指数增长，默认上限 600 秒；服务器要求更长等待时不截短。403 直接返回 access_limited，不自动再发起读取。限流中的快照不允许拿来下单。

UI 通过事件队列接收每个目标的 last_checked_at/check_count/watch_state 和全局 next_read_at/cooling_down 字段，仅更新变化的行及日志；没有 HTTP 轮询或周期性全量表单重绘。基础间隔并非网站官方阈值，不能保证不会被限流。

目标的持久 key 使用预约上下文和稳定 ID，不使用排序，所以调换优先级、重新打开界面不会造成重复预约。有记录的目标只能改名称或排序，不能更换预约要求。

## 提交与恢复

最终点击之前，用 SQLite 事务写入提交意图及各个时段的唯一占用记录。点击之后只观察网页原生 ConfirmOrder 响应，不自行构造、解密或重放预约请求。

- `code=0` 且有标量订单 ID：记录成功。
- 明确拒绝：历史写入 attempts；多目标模式可释放此次未成功的占用记录，之后重新检查。
- 安全验证：有界面时等待用户手动完成，由网页原生回调提交。
- 断网、未得到可确认的订单号或提交中断：保留锁，禁止自动再订，标记需要人工核对。

人工处理、失效登录、日期过期等情况不等于目标成功。界面保留每项状态，而不把部分成功显示为全部完成。网站如果禁止未支付订单继续预约，程序也遵守限制，不支付、不绕过。

已知付款路由仍在浏览器中拦截。`submitted` 只代表订单创建，不代表支付、零元确认或预约权益已经生效。

## 本机界面边界

随机请求令牌保护所有 API；校验 Host 与 Origin；静态文件只允许 index.html、app.js、style.css；不开放认证目录或任意文件读取。状态接口不返回 cookies 或 token。所有资源在本机，无第三方脚本/CDN。

在 WSL 缺少中文字体时，使用已存在 Windows 字体在独立运行目录的本地副本，仅为 Chromium 设置局部 Fontconfig 配置，不改全局字体环境、不随项目分发字体。

## 真实网站依据

2026-10-26 已核对其公开发布的 Vue 前端：

- 个人预约路由 `/apointmentDetails/1/:id/:isType/:bizId`。
- `.chart [role=tab]` 选择日期，ID 包含 ISO 日期。
- `.topsiteStyle` 表示场地列，`.inner-seat-wrapper` 按时间行显示网格。
- `.unselected-seat` 可用，`.selected-seat` 已选，`.bought-seat` 不可用。
- `period()` 按 07:00–22:00 小时映射；程序另核对左侧时间轴。
- 原生 `agreeTerms()` 提交 `/venue/personal/ConfirmOrder`，成功返回订单 ID，1002 为人工验证分支。

选择器经过同 DOM 模拟页验证。真实会话过期后需要用户完成主界面登录；自动测试不占用真实预约名额。

## 研究参考与依赖

[Playwright 自动等待](https://playwright.dev/python/docs/actionability)、[登录状态](https://playwright.dev/python/docs/auth)、[browser-use](https://github.com/browser-use/browser-use)、[已归档的 12306 订票项目](https://github.com/testerSunshine/12306)。固定预约流程使用明确状态机，避免每次点击都依赖模型推理。

复用已获同意的独立依赖：playwright 1.64.1、pyee 14.0.0、greenlet 3.5.6、typing_extensions 4.16.0。本版经用户批准新增 Tcl/Tk 原生组件，清单见下文。浏览器复用已有 Chromium，系统库只从独立目录局部加载。

## v0.4 原生客户端与静态目录（2026-10-26）

当前交付改为 Tkinter 原生桌面应用，desktop、gui 及默认入口均不再启动 Web 工作台。仅登录和网站安全验证使用官方网页。主线程运行 Tk，后台线程独占 Playwright；通过事件队列增量更新进度、日志和网格，避免 HTTP 全量状态轮询与周期性重建表单。

登录保存与预约位置解耦。用户在主界面登录后，由 `/system/user/currentUser` 确认会话，再保存 cookies、localStorage、sessionStorage；不要求进入预约详情页，不改已有预约目标。启动前对 `/manage/fieldDetail/` 整类请求进行浏览器路由阻断，避免用户手动进入详情页也触发空位读取。

主动登录采用全新 BrowserContext，不加载旧 storage_state，也不注册旧 sessionStorage 的初始化脚本，避免过期 token 或用户名在导航/刷新时被反复恢复。只有普通监测、目录读取复用已保存状态。新登录须经服务器确认并由用户点击保存才替换本机认证文件；取消或失败时不删除旧文件，不影响目录、目标或 journal。

目录仅允许 `/manage/venue/list`、`/manage/venue/queryVenueById` 及上述身份确认端点。列表使用个人预约 flag=0，按分页读取可见场馆，读取每个场馆的 motionTypes，并串行间隔至少 2 秒。最终只缓存场馆 ID、名称、校区和项目 ID、名称，不缓存身份或库存。缓存存在则直接返回，不过期、不后台刷新；缓存损坏明确报错。首次失败不写入半份缓存。

选择缓存场馆和项目时，由目录生成已验证格式的个人预约路由。v0.5 将各项目旧目标合并进统一工作台，每个候选分别保存上下文，origin 保留旧订单身份；添加器中的场馆选择不再切换或清空目标。订单 journal 不随场馆选择清理。

具体场地选项来自该项目监测中已读到的列名，保存在独立 courts.json；从未监测的项目可选“任意可用场地”。开始监测后才展开为真实网格列，保留候选及时间优先级，并使用具体场地核对摘要、金额和持久提交锁。定时任务在到达启动时间之前不加载网格。

原生界面保留多日期目标、多选场地/时段、连续时段合并、目标/候选排序、全局间隔和随机延迟、退避、金额上限、全部完成后停止、停止续跑，以及结果未知时的人工核对保护。关闭正在工作的客户端先请求停止，已点击提交则等待结果。

本地运行/测试：Python 3.14.4，Playwright 1.64.1，Tk 8.6.17，Chromium 152.0.7977.8；运行 `./start-gui.sh`，测试 `./test.sh`。通用 Linux 使用 Python 3.12+、Tkinter/Tcl/Tk 及 requirements.txt，不需要前端构建。

经用户确认安装清单：python3.14-tk 3.14.4-1ubuntu0.2、libtk8.6 8.6.17-1build1、libtcl8.6 8.6.17+dfsg-1build1。仅从 Ubuntu 发行源下载并解压到已有隔离依赖目录 .native，启动时按进程设置 PYTHONPATH、LD_LIBRARY_PATH、TCL_LIBRARY、TK_LIBRARY，未修改系统或全局 Pixi 环境。

## v0.5 跨场馆目标、现代原生界面与成功通知

候选 Target 增加可选 BookingContext，包含 booking_url、venue、sport；旧候选继承原配置。一个 Goal 可在同一日期包含多个场馆/项目候选，按候选顺序匹配，只完成一个订单。新增 origin 固定旧目标身份，workspace_version=2 标记已合并的统一工作台；首次迁移保留原配置备份及项目文件，补齐候选上下文并保留旧任务 key。

旧三字段 Target 的 task_key 序列化公式不变。slot_keys 同时包含旧路由 hash 和基于场馆 ID/项目的规范 hash；迁移为已记录的实际时段补充别名。不同入口的同一实体场地不再绕开提交去重。

multivenue.py 使用一个页面、一份 ReadPacer。按场馆/项目/日期分组共享快照；同一轮采集完成后统一选择候选，下单前再激活并核对对应上下文。场馆切换、日期点击、重载均收费到同一全局预算。429/503 的等待对所有场馆生效，403 暂停；部分读取失败不使用不完整候选集合下单。结果未知时不尝试其他场馆。切换适配器时释放旧请求监听，避免长期监测积累。

原生布局为浅灰侧栏、白色主区、圆角黑白操作按钮、扁平输入和无网格线候选列表。目标与候选可跨场馆，添加器场馆切换不再切换整个工作区。设置与活动区按需打开，隐藏网格不绘制；编辑操作移到 desktop_model.py，目标选择和进度处理只用内存状态。事件消费每批限制 8ms，进度合并，倒计时文本不变时不写控件。

Controller 为新出现的 submitted 且有有效订单号的状态发送独立 goal_completed 事件，在启动时登记旧成功记录，避免重复通知。UI 使用非模态、置顶 Toplevel，通知内容包含场馆/项目/场地/日期/时段/订单号；多个快速完成合并到同一窗口，关闭通知不停止后台。未知结果与拒绝不发成功弹窗。

运行仍为 ./start-gui.sh，测试为 ./test.sh；复用 Python 3.14.4、Tk 8.6.17、Playwright 1.64.1 和已有隔离依赖，不新增软件或环境。


## v0.6 交互延迟与配置复用

新增 desktop_events.py，通过非阻塞 Unix pipe + Tk createfilehandler 交付后台队列事件，worker 只写描述符，不跨线程调用 Tk。主线程每批仍最多处理 8ms，余下事件通过短续调继续；每秒维护倒计时，取消原先常驻 80ms 轮询。销毁 root 释放描述符，晚到通知安全忽略。Canvas 按钮缓存宽度、禁用/悬停/焦点状态，使用 FocusIn/Out 事件代替同步 X11 focus_get。busy 状态不变时不批量 configure 控件，候选及目录不变时不重绘、清空选择。输入、下拉和滚动条使用无立体边框样式。

用户点击保存及开始监测前的保存均交给独立持久化线程，冻结一份工作副本；通过 saved/save_failed 事件回到 Tk 后解锁编辑，并在成功时继续原操作。工作完成后的 SQLite 状态恢复也由后台执行。保存失败保留未保存标记，不启动监测；关闭时等待正在进行的保存或提交观察。

Config.repeat_rules 保存最多 10 条 RepeatRule（id、name、candidates、days_ahead、at、enabled）；Goal.repeat_id 标记自动生成的日期目标。旧配置默认空规则，旧 goal/task/slot key 算法不变。复制生成新 UUID 目标，深拷贝候选，清除 repeat_id，保留原有场馆来源上下文；不继承 journal 状态。

每日规则仅在用户启动监测后激活。multivenue 在每轮开始按本地分钟检查到期规则；目标 ID 为 daily-{rule_id}-{当天日期}，日期为当天加 days_ahead，冻结候选场馆上下文。新增目标先原子持久化，再通知界面、加入共享监测；其稳定 ID 与原有 journal 双重防止重复启动重复下单。生成失败时停止，不提交未保存目标。模板和已生成订单目标相互独立。

每日模式在当前全部完成时不结束：没有待监测目标则每 250ms 本地等待、可停止，不访问库存；到期再生成新的日期目标。旧未完成的未来日期目标继续参加同一快照/全局请求预算。每日模式忽略普通时长/全部完成退出条件，一次性 start_at 仍控制本次运行最早开始时间。进程关闭不运行，重开不会自动恢复下单，须用户再次点击开始；错过时刻只补当天，不追溯历史。

每日目标到日期过后，安全的已完成/已过期/未提交记录从工作列表滚动整理；SQLite journal 与 slots 不删除，未知结果及待验证目标保留。普通手工目标不自动整理。最大 100 目标/1000 候选，超限明确停止，无静默丢弃或超限提交。原有全局间隔、随机延迟、指数退避、403 暂停、提交前核对、订单锁和成功弹窗继续生效。

环境和命令不变：本地 Python 3.14.4、Tk 8.6.17、Playwright 1.64.1、Chromium 152.0.7977.8；通用 Linux Python 3.12+ 与匹配 Tk。启动 ./start-gui.sh，测试 ./test.sh，无新增依赖或前端构建步骤。


## v0.6.1 窗口显示与图形服务诊断

gui_startup.py 在启动时读取最多 2MiB 的 weston.log 尾部，仅根据最后一次 use_gfxredir 状态识别 COPY MODE；不修改日志、系统挂载或图形服务。服务重启必须由用户明确授权，避免关闭其他 Linux 图形应用。Tk Map 成功只表示 X11 窗口映射，不保证 WSLg/Windows 合成正常显示，不能以截图替代最终可见性验证。

serve_desktop 创建 AutoReserve 类窗口后先 withdraw，保留 root.app，构建完成后 deiconify/lift/focus_force，并将 topmost 设置临时提升 400ms 后恢复。Map 回调仅报告一次实际根窗口映射。正常退出及初始化异常统一释放 root。脚本将用户传入的全局选项放在 desktop 子命令前，支持独立配置路径。

本地环境 Python 3.14.4、Tk 8.6.17；通用 Linux Python 3.12+ 与匹配 Tk，命令 ./start-gui.sh。无需构建和新依赖。验证命令 ./test.sh（包含新增启动测试）；本轮按变更范围针对性执行启动、原生交互、运行环境检查。


## v0.7.0 无装饰窗口与目标内每周重复

WindowChrome 保持 X11 managed 窗口，给 Tk 外层窗口写入 `_MOTIF_WM_HINTS` 的 decorations=0，不使用 override-redirect，因此保留任务栏/最小化。标题在客户端中绘制，系统标题使用 ASCII。7px 边缘、14px 角命中区域捕获指针；拖动按 root 坐标计算、after_idle 合并更新、释放时刷新最后位置。西/北缩放保持对边锚点，最小尺寸保护；绝对负坐标使用 Tk 的 `+-N` 表示法，避免误解释为屏幕右锚。双击/标题按钮经窗口管理器控制最大化。

GoalList 以 retained cards 替代旧 Treeview，每行有 + 和 ×，保留控制器使用的稳定 ID/selection/item 接口。ChoiceGrid 以可键盘操作的蓝色多选卡片替代 Listbox，并保留 get/curselection 等编辑 API。ScrollPanel 把滚轮事件限制在自身后代，不覆盖其他窗口或嵌套列表。工作台单独滚动，主执行按钮保留在外部；候选表高度依数量调整。待加入场馆卡片按可用宽度换行，显示场地并提供独立删除操作；各目标保留自己的未完成选择。

普通目标仍存在 Config.goals，重复模板仍存在 Config.repeat_rules；侧栏以 `rule:{id}` 地址展示模板，不改变持久化 ID。切换为重复目标会移除可编辑的原普通目标，避免模板和原目标重复执行；切回普通目标为预计预约日期创建新 ID。已生成的 Goal.repeat_id 实例与模板独立，编辑模板不会修改已有订单。所有转换使用现有严格 schema，已有订单的普通目标继续禁止改写。

RepeatRule 新增 weekdays，0–6 对应周一到周日；旧 JSON 未包含此字段时默认七天全选。拒绝空列表、重复、布尔值和越界值。materialize_daily 仅在执行日属于 weekdays 且已到 at 时生成；next_daily 在未来八个本地日期中找到首个启用、未生成的日期，涵盖当日已执行后的下周同一天。days_ahead 仍相对于执行日，星期限制作用于生成日；已生成且未完成的实例继续监测。稳定规则/日期 ID、持久化先于下单、journal 身份和跨场馆共享预算保持兼容。

本地测试环境为 Python 3.14.4 / Tk 8.6.17 / Playwright 1.64.1，未新增依赖。通用 Linux 运行环境为 Python 3.12+、匹配的 Tkinter、X11/WSLg 和 requirements.txt；本项目为图形桌面程序，无 HPC 部署目标。无需编译，便携启动为 `./start-gui.sh`，验证为 `./test.sh`。


## v0.8.0：参考 TeleMail 重建 Linux 原生桌面层

Tk 版自绘 Canvas 控件、手动滚轮 bindtags 和 X11 Motif 提示无法可靠地同时处理首帧窗口装饰、滚动边界和事件传播。生产入口已切换到 GTK 3，不再调用这些 Tk 窗口与滚动实现。参考的是本地 TeleMail 的 GTK C-ABI、GtkBuilder/CSS、GtkApplication 激活与主线程队列架构；未依赖或修改该项目的邮件模块、数据或配置。

### 分层

`workspace.py` 只维护纯 Python 数据，负责稳定 ID、草稿、日期、复制、普通/每周转换与 schema 验证。`gtk_desktop.py` 只持有原生控件、事件与显示缓存；后台仍使用原有 DesktopController 和预约引擎。`gtk_native.py` 是有限的 ctypes C-ABI 适配层，给每个 GTK/GLib 函数声明类型，按 widget destroy 释放信号回调，并保留正在跨 C 返回的超时 trampoline 至下一批事件。GtkBuilder 文件与 CSS 单独维护。

主窗口 GtkHeaderBar 在第一次 show 前由 Builder 创建；设置、成功通知、退出确认也在 show 前配置原生 titlebar、transient 关系和 `:close` 装饰布局。子窗不允许最大化，没有最小化按钮。主窗口保留 GTK 的原生标题拖动、双击和边缘缩放；不再发送 X11 属性来延迟隐藏外框。使用按配置路径生成的 GtkApplication ID，重复启动同一工作区时呈现已有 GTK 窗口。

侧栏 ListBox 和时段 FlowBox 均放在 GtkScrolledWindow 中；GtkAdjustment 维护真实边界，滚轮由原生事件系统分发。侧栏只在成员或顺序变化时重建行，状态变化更新保留行。编辑区单独滚动，两个固定监测框放在它的兄弟容器内。显式固定 sidebar 的横向 expand-set 和监测区的纵向 expand-set，避免子控件的扩展属性把侧栏和监测框意外撑大。记录保留最近 250 条，追加时仅在用户已处于底部时跟随。

后台浏览器仍单线程串行，保存用独立线程；GTK 主线程每 33ms 消费有 6ms 预算的事件队列，合并 progress、保留完成事件、按内容变化绘制空位。不在每次 tick 中读磁盘或 journal。关闭执行中的窗口先请求停止，待本轮结果后销毁。终端 SIGINT 经 GLib 延迟到正常关闭路径，不从 ctypes 回调中抛出 KeyboardInterrupt。

调度、候选、订单身份、每周生成 ID 和完成记录保持原有 schema；旧每日规则依旧默认七天全选。编辑与浏览场馆不读取库存。普通目标转换每周模板时移除原可编辑目标，避免多调度一次；生成实例与模板独立。

原 Tk 类和旧 Web 代码仅作为历史兼容回归保存，Tk 控件仅在构造历史客户端时延迟导入；CLI facade `desktop.serve_desktop` 委托给 `gtk_desktop.serve_gtk`，默认入口、gui 和 desktop 使用同一个 GTK 实现。启动器预检现有 GTK 动态库和 Playwright ABI，不安装软件。运行环境为 Python 3.12+ / GTK 3.24+ / 图形桌面 / requirements.txt，本机测试 Python 3.14.4 / GTK 3.24.52，无编译步骤；`./start-gui.sh` 启动、`./test.sh` 验证。

原生滚动 API 和测试事件布局依据 GTK 官方的 [GtkScrolledWindow adjustment 文档](https://docs.gtk.org/gtk3/method.ScrolledWindow.get_vadjustment.html) 与 [GdkEventScroll 文档](https://docs.gtk.org/gdk3/struct.EventScroll.html)，并以真实 GTK 输入事件回归。


## v0.9.0：状态可见性、固定配置与独立发布

目标行除文字外增加整行状态底色和左侧色条；普通待启动、运行中、重复、暂停、完成、需处理各自保留选中态。控件只在成员或排序变化时重建，标题/详情及状态类仅在值变化时写入 GTK。周模板在右侧继续使用蓝色边框。

`paths.py` 将默认配置固定为程序目录 `config/config.json`、私有状态固定为 `.auth/`，不依赖启动时的 cwd。仅对默认位置迁移旧根目录配置：先严格校验、原子保存并设为 0600，再把原文件移入 config 私有备份。新目标文件优先，显式路径不迁移，既有 journal 不移动。

本独立仓库 采用明确的公开源文件清单，排除账号、订单、配置、缓存、证据和构建目录。标准库构建器提供可复现 tar.gz/ZIP、可执行启动脚本和 SHA256SUMS；setuptools 构建包含 GTK 资源的 Python wheel/sdist。便携包不内置 Python/GTK，也不隐式安装运行库。

GitHub Actions 在 Ubuntu 上安装隔离测试依赖，先执行模拟站和原生 GUI 回归，再构建并上传产物；仅匹配应用版本的标签触发 Release，手动执行只构建。动作固定到提交，发布作业单独申请 contents:write。未推送远端或创建真实 GitHub Release。


## v0.10.0：程序目录内的首次初始化与鼠标入口

改动仅位于独立 repo。`Config.initialized` 为严格布尔值，兼容旧配置缺字段时的 false；不参与目标/订单身份计算。初始化状态与依赖收据分开：config/config.json 保存可见完成标志，config/bootstrap.json 保存精确依赖摘要、Python 版本、架构与错误，均使用私有配置路径。

scripts/bootstrap.py 在一个应用内 flock 下检查包版本/导入 ABI、GTK soname、Chromium 与无头浏览器 ELF 及动态库；完成实际本地无头启动后才验证/迁移配置并写成功。缺 Python 包时用固定 SHA256 的 uv 创建 .venv（不依赖系统 python3-venv）；所有 UV 安装/缓存、临时文件、浏览器均显式指向 .runtime。失败保留 false、保留目标；之后启动可恢复。

scripts/bootstrap_native.py 使用只读系统 dpkg 状态、本地 apt lists/cache/log/state 及关闭的系统 hooks 下载缺失包，然后 dpkg-deb --extract 到 .runtime/native；不调用 sudo、不安装系统包、不替换 libc/loader。按 ldd 缺失 soname 仅修复实际缺少的 Chromium 库；缺中文字体时补齐私有 Noto。Fontconfig 绝对路径随目录移动重建，已装依赖可断网复用。

native/launcher.c 提供与 TeleMail 类似的原生 ELF 入口，解析自身位置并以隔离系统 Python 执行 bootstrap，原样传递 argv。首次需要安装时显示独立 GTK 进度窗口；后台执行下载，主线程泵原生事件，不在常规已初始化启动时闪出该窗口。桌面快捷方式以当前用户 XDG 目录为限，用 Python execv trampoline 和 freedesktop quoting 保留含空格、百分号、引号、等号的路径。

左栏删除旧标语，并调整图例在控件层级的位置。主工作台、逐目标周规则和预约执行逻辑不变。Release 构建器忽略整个 .runtime/.venv 子树，编译并注入当前架构 ELF、保留可执行权限，公开配置样例 initialized=false。源码与运行包不包含个人初始化标志、目标、登录或订单。

## Timed-release scheduling (v0.11)

`rush.py` converts per-goal release timestamps to monotonic deadlines and exposes preparation/release stages plus clock uncertainty. Its bounded same-origin HEAD estimator shares one approximate server clock, while goals disabling sync retain their own local-clock basis. `monitoring.ReadPacer` owns every inventory action, accelerated window and Retry-After deadline across the account. `multivenue.py` preempts ordinary budget waits at preparation/release boundaries, discards prewarm inventory, and prioritizes released rush goals without weakening candidate order or durable submission protection. `repeat.py` materializes timed weekly occurrences at preparation time, including the preceding night for a midnight release.

The native editor exposes per-goal `rush` configuration through its persistent top switch and three GtkExpander modules. Fold state stays in presentation memory, configuration changes pass through `Workspace`, and progress/countdown updates remain on the GTK main thread. Clock probes, authentication rechecks, prewarming and reservation work execute in the existing worker. This adds no dependency or second browser and does not change order identity. Details and research references are in [RUSH.md](RUSH.md).

## Native Windows 11 (v0.12)

The Python reservation/editor code is shared between Linux and Windows. `native_runtime.py` resolves the same GTK C ABI to either Linux SONAMEs or explicit app-private Windows DLL paths, retains DLL-directory cookies and enables Win32/per-monitor DPI behavior. `filelock.py` preserves POSIX locks on Linux and implements a stable one-byte CRT lock on Windows. The Windows payload includes tzdata for IANA timezone support and loads all text/configuration using UTF-8.

`native/windows-launcher.cs` is compiled to a GUI-subsystem x64 EXE using Windows' existing .NET Framework compiler. It forwards arguments directly to embedded CPython, cleans inherited Python overrides, shows asynchronous first-run progress and propagates the child result. `scripts/windows_launch.py` verifies app-local dependencies and starts the existing GtkApplication in the same Python process. A separate desktop lock prevents duplicate native Windows editors for the same folder; account locking still surrounds actual worker sessions.

`tools/windows_runtime.py` reads exact CPython/wheel/MSYS2 pins, validates archive hashes and extracts only private runtime payloads. `tools/build_windows.py` recreates staging, copies the Windows runtime allowlist and constructs an independent Windows ZIP with Python, GTK data/DLLs/licenses and the launcher; Linux uses a separate bootstrap allowlist. Repository metadata, tests and build inputs stay outside portable archives. The Windows workspace sets its own icon and AppUserModelID before mapping, uses the system titlebar and sizes itself against the monitor work area in GDK application pixels. `bootstrap_windows.py` checks real imports/native exports, repairs missing private components, creates relocatable image/font caches and verifies a blank Chromium launch before persisting initialized=true. It never installs system packages or modifies global PATH. The full download inventory, build steps, filesystem limitations and hosted Windows test job are documented in [WINDOWS.md](WINDOWS.md).
