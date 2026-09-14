# 贡献指南

感谢愿意帮忙。这个项目不大，但有几条规矩要先说清楚——尤其是第一条。

---

## ⚠️ 第一条：技术红线，不可协商

本项目的**全部价值**就在于：它不碰任何游戏进程，所以不会被反作弊误伤。
下面这些做法**一律不接受 PR**，不管效果多好、实现多优雅：

| 禁止 | 说明 |
| --- | --- |
| **DLL 注入** | 把自己的代码塞进别的进程 |
| **API Hook** | 挂钩 `IDXGISwapChain::Present`、`D3D11`、`OpenGL`、`Vulkan` 等 |
| **进程内存读写** | `ReadProcessMemory` / `WriteProcessMemory`，包括读游戏内存拿 FPS |
| **键鼠钩子** | `SetWindowsHookEx` 的 `WH_KEYBOARD` / `WH_MOUSE`，以及 `SendInput` 自动操作 |
| **自制或第三方内核驱动** | 自写驱动、LoadDriver、第三方未签名驱动 |
| **网络劫持** | WFP 过滤器、TUN/TAP 虚拟网卡、代理式流量统计 |

**允许的做法**（这些就是当前的实现路径）：

- ETW 事件追踪、PDH 性能计数器、WMI
- NVML / ADLX / LibreHardwareMonitor 等厂商或社区公开接口
- 窗口置顶、分层窗口、`RegisterHotKey`
- Windows Graphics Capture（只做截图/录屏时）
- `IcmpSendEcho` 测延迟、`powercfg` 查电源状态

拿不准就问：**在 Issue 里描述你想做什么、用哪个 API，我们再讨论**。
判断标准很简单——**这件事需不需要把手伸进别人的进程或内核**。需要，就是红线。

## 开发环境

- Windows 10 1809+ / Windows 11，x64
- **Python 3.14**（`pythonw` 用于运行，`python` 用于调试）
- 调试界面时设 `HM_SKIP_ELEVATE=1` 跳过自动提权

```powershell
pip install -r requirements-dev.txt
pythonw hardware_monitor.pyw
```

## 代码约定

**用中文**。注释、变量名以外的文案、提交信息、文档，统一中文。

**不要引入 GUI 框架**。界面全部画在一张 `tk.Canvas` 上，弹窗是"无边框 Toplevel +
再画一张 Canvas"。这是刻意为之：依赖极少、体积小、彻底避开原生控件的坑。
想加控件就先在 Issue 里讨论画法。

**保持分层单向**：

```
入口 → hm_ui → hm_widgets / hm_sensors / hm_fps → hm_utils → hm_theme
```

**不允许反向 import**。低层模块不得引用高层模块。

**采集与绘制严格分离**：采集在后台线程，绘制只在主线程。两者之间只通过
`queue.Queue` 传**已格式化的字符串**；曲线和告警需要的原始数值挂在
`data["_hist"]` 上随消息一起带过来。**禁止**在绘制代码里直接读硬件。

**改配置结构要动三处**：`hm_config.DEFAULT_CONFIG`、`NESTED_FIELDS`（若是 dict/list）、
`CONFIG_VERSION` + `_migrate()`。漏掉第三处会让老用户升级后直接报错。

**新增指标要往 `_hist` 里塞 float**，否则画不出曲线、判不了告警。

**动 `hm_sensors._init_lhm()` 前先想清回退链**。LibreHardwareMonitor 只要有一个可选组
加载失败，`Computer.Open()` 就**整体抛异常**，所以现在是五级回退（全开 → 丢内存 →
丢硬盘保主板 → 保硬盘 → 只留 CPU/GPU）。**加一个组就要加一级回退**。

**Windows 上的两个坑**：

- `-topmost` **必须写在 `geometry()` 之后**，否则窗口会被踢出顶层 Z 序
- 退出时要**收干净子进程**。守护线程在解释器退出时会被直接杀掉、不执行清理，
  所以 PresentMon 有兜底清理（按父进程链精确匹配，**不要用 `taskkill /IM`**）

## 提交前必须通过的检查

```powershell
python -m py_compile hardware_monitor.pyw hm_config.py hm_fps.py hm_log.py hm_sensors.py hm_theme.py hm_ui.py hm_utils.py hm_widgets.py
python tests/smoke_test.py     # 必须全绿，退出码 0
python tests/ui_test.py        # 必须全绿，退出码 0
python build.py                # 必须看到「自检： 通过」
```

改了界面或菜单的，**还要在真机上把相关的交互走一遍**——自动化测试覆盖不到
窗口 Z 序、托盘气泡、UAC 提权这些只有真机才有的行为。

## 提交信息

格式随意，说清"改了什么、为什么"。一个 commit 只做一件事，
**不要**把格式化、重命名和功能改动混在一个 commit 里。

**提交第三方二进制的新版本时，单独一个 commit**，并在 `THIRD_PARTY.md` 里同步更新
版本与 SHA-256。

## 版本号

唯一来源是 `hm_utils.APP_VERSION`。**不要**在别处拼版本字符串。
只要用户能看见的行为变了，就加一位：

- 修 bug / 体验微调 → 第三位 +1（`2.1.0` → `2.1.1`）
- 加功能 → 第二位 +1（`2.1.0` → `2.2.0`）
- 大改版 → 第一位 +1

改完版本号，记得同步 `CHANGELOG.md` **和官网 `web/index.html` 的更新日志**
（两处内容要一致，都写给用户看）。

## 打包与发布

发布流程见仓库根目录的说明；签名的部分见 [CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md)。

简单说：**推一个 `v*` 的 tag**，GitHub Actions 会自动构建、送 SignPath 签名、
挂到 Release 上。**不要手动上传自己本机打的包**——那样就没有 SignPath 校验过的
构建来源，签名环节会失效。

## 许可证

提交 PR 即表示你同意你的代码以本项目的 [MIT 许可证](LICENSE) 发布。
