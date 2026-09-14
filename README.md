# 硬件监控

Windows 桌面上的**硬件状态悬浮窗**。CPU、显卡、内存、硬盘、网络的实时读数一屏看完，
外加帧率、帧时间、1% Low 这些游戏性能指标。

**纯本地运行，不联网、不上传任何数据，也不碰任何游戏进程。**

> 仓库：<https://github.com/robotcn1130/hardware_monitor>
> 下载 / 历史版本：<https://github.com/robotcn1130/hardware_monitor/releases>
> 问题反馈：<https://github.com/robotcn1130/hardware_monitor/issues>

---

## 它和别的监控软件有什么不一样

市面上不少硬件监控工具会在游戏里被反作弊踢出去，因为它们的实现方式踩了红线：
注入 DLL 到游戏进程、挂钩 Direct3D 交换链、读写别的进程内存、加载自制内核驱动。

**这个项目一条都没碰。** 它只走 Windows 提供的公开接口：

| 数据 | 来源 | 手段 |
| --- | --- | --- |
| CPU / 内存 / 磁盘 / 网络 / 进程 | `psutil` | 系统公开计数器 |
| CPU、主板、硬盘温度，风扇转速 | LibreHardwareMonitor | 读 MSR / SMBus，配合 PawnIO 内核驱动 |
| 显卡占用、温度、显存、功耗 | NVML / LHM | 厂商公开接口 |
| 帧率、帧时间、1% Low | Intel PresentMon | ETW 事件追踪，**不注入、不挂钩** |

结论：它就像任务管理器一样在"外面看"，没有任何一步需要把手伸进别人的进程里。
唯一需要管理员权限的原因，是读 CPU 寄存器（MSR）和开 ETW 会话这两件事 Windows 只让管理员做。

---

## 功能

**基础监控（15 项，可自由勾选）**

- CPU 使用率、CPU 温度、CPU 频率
- 内存占用（百分比 + 已用/总量）
- 磁盘读写速度（每块盘一行）
- 网络上下行速度
- 显卡（每张卡一行）
- 硬盘温度、主板温度、系统风扇转速
- 电池电量、开机时长

**显卡可单独展开**：占用率、温度、显存、风扇转速、功耗、核心频率，六项各自勾选。

**游戏性能**

- **FPS**：跟随前台游戏自动切换，也可以锁定某个进程只看它
- **帧时间**：单帧耗时（ms），比平均帧率更能反映卡顿
- **低帧**：最近 5 秒窗口内的 1% Low / 0.1% Low，专门抓那种"平均 60 帧但一直顿一下"的情况

**界面**

- 竖版 / 横版两种布局，竖版每项数值右侧带一条迷你历史曲线
- 窗口透明度、刷新间隔可调
- 置顶显示，拖到屏幕任意位置，位置自动记住
- 右键菜单即时切换显示项，勾选时菜单不会关掉，可以连着勾好几项
- 系统托盘图标：隐藏/显示窗口、快速开关指标、一键退出
- 开机自启（用计划任务 + 最高权限实现，开机不弹 UAC）

**阈值告警**

- CPU 使用率、CPU 温度、显卡温度、硬盘温度、内存占用，各项阈值可单独设置
- 超阈值时数值变红，并弹出托盘气泡提醒
- **不想被某项烦的，把它的阈值调成 0 或者直接取消勾选即可**，不影响其它项

---

## 下载与安装

1. 到 [Releases](https://github.com/robotcn1130/hardware_monitor/releases/latest) 下载 `硬件监控.zip`
2. 解压到任意目录（**不要**放在 C 盘受保护目录下，比如 `Program Files`）
3. 双击 `硬件监控.exe`

**解压即用**，不需要安装 Python 或任何运行库，也可以直接放在 U 盘里带着跑。
设置文件 `config.json` 生成在 exe 同目录，删掉它就会恢复默认设置。

### 运行环境

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 10 1809 及以上 / Windows 11 |
| 架构 | x64 |
| 权限 | 首次启动会请求管理员权限（读温度、开 ETW 会话需要） |
| 网络 | **完全不需要** |

### 首次启动会发生什么

1. 弹出 UAC 提权请求——点"是"。不点也能跑，但温度、帧率那几项会显示"需管理员权限"
2. 自动静默安装 **PawnIO** 内核驱动（读 CPU 温度必需，随程序一起分发）。安装完可能提示需要重启
3. 悬浮窗出现在屏幕右上角，托盘出现图标

> **关于 PawnIO**：它是 LibreHardwareMonitor 0.9.6 起采用的驱动，用来替代已被
> Windows Defender 拉黑的 WinRing0。它是**有正规签名的公开驱动**，源码在
> <https://github.com/namazso/PawnIO>，很多硬件工具（OmenCore、Joular 等）都在用。

---

## 使用说明

**右键**悬浮窗 → 弹出菜单：

- 直接点击指标名即可勾选/取消，菜单会保持打开
- 「设置…」调整阈值告警、透明度、刷新间隔
- 「显卡设置…」选择显示哪张卡、展开哪几项参数
- 「监视 FPS 的程序…」锁定要监控的游戏进程
- 「以管理员身份重启」在降权运行时补救
- 菜单底部灰色显示当前版本号（报问题时请附上）

**左键**点主窗口任意位置即可收起菜单；再次右键会直接替换掉旧菜单，不会一层层叠出来。

**托盘图标**右键：显示窗口 / 快速开关指标 / 开机自启 / 版本 / 退出。

---

## 从源码运行

需要 **Python 3.14**（3.12+ 应该也行，未逐一验证）。

```powershell
pip install -r requirements.txt
pythonw hardware_monitor.pyw
```

`hardware_monitor.pyw` 用 `pythonw` 启动可以不带控制台窗口；调试时用 `python` 启动能看日志。

调试开关：

```powershell
$env:HM_SKIP_ELEVATE = "1"   # 跳过自动提权，方便调界面
```

出错时看 `logs/error.log`（超过 512 KB 自动轮转）。

---

## 自己打包

```powershell
pip install -r requirements-dev.txt
python build.py
```

产物在 `dist/硬件监控/`，整个文件夹拷给别人即可运行。打包完会打印自检结果和体积，
看到 `自检： 通过` 才算成功。

打包需要仓库里这些**第三方二进制**（已随仓库分发，见 [THIRD_PARTY.md](THIRD_PARTY.md)）：

```
PresentMon.exe                  Intel PresentMon 帧捕获工具
PawnIO_setup.exe                传感器驱动安装包
lib/*.dll                       LibreHardwareMonitor 及其依赖
app.ico                         程序图标
```

`build.py` 会检查它们是否齐全，缺任何一个都会直接报错退出。

打包完成后会自动尝试签名。**证书要从环境变量给，没配就直接跳过**（不影响打包）：

```powershell
$env:HM_SIGN_SHA1 = "证书指纹"        # 本机证书存储，云签名服务走这条
python build.py
```

可用的变量：`HM_SIGN_SHA1`（指纹）、`HM_SIGN_SUBJECT`（主题名）、
`HM_SIGN_PFX` + `HM_SIGN_PASS`（老式 PFX 文件）。走 SignPath 路线时**本机不用配任何证书**，
签名在 CI 里完成，见下一节。

> 私钥不要写进配置文件或命令行参数——前者会被提交，后者会留在 shell 历史和进程列表里。

---

## 关于 Windows 智能应用控制（Smart App Control）

Windows 11 的「智能应用控制」只放行两类程序：**带有效数字签名的**，或者微软云端
已经认得的。个人开发者自己打包的 exe 两条都不占，于是会被**硬性拦截**，弹窗
「智能应用控制已阻止可能不安全的应用」，而且**没有「仍要运行」按钮**。

本项目正在申请 [SignPath Foundation](https://signpath.org/) 的开源代码签名（该机构面向
开源项目提供签名服务）。通过之后，从 Releases 下载的 `硬件监控.exe` 会带上有效的
Authenticode 签名（证书签发给 SignPath Foundation），智能应用控制即可正常放行。

在那之前，如果你遇到拦截，可以：

1. **改用源码运行**（见上一节）——Python 解释器本身是签好名的，绕过这项检查；
2. 或者临时关闭智能应用控制：设置 → 隐私和安全性 → Windows 安全中心 →
   应用和浏览器中心 → 智能应用控制设置 → 关闭。
   ⚠️ 部分旧版本 Windows 关闭后无法从设置里重新开启，建议先把系统更新打全。

**无效的做法**（别浪费时间试）：点"仍要运行"（没有这个按钮）、右键"解除锁定"、
以管理员身份运行、加杀毒软件白名单、用自签名证书。微软的受信任根计划不认自签根。

关于签名流程和证书范围的说明见 [CODE_SIGNING_POLICY.md](CODE_SIGNING_POLICY.md)。

---

## 目录结构

```
hardware_monitor.pyw   入口，只负责 main()
hm_theme.py            配色与布局常量
hm_log.py              错误日志（logs/error.log，512KB 轮转）
hm_utils.py            路径常量 / 格式化 / 开机自启 / 提权 / PawnIO
hm_config.py           15 项指标定义、告警默认值、配置读写与版本迁移
hm_fps.py              PresentMon 常驻采样器、5 秒滚动帧窗口
hm_sensors.py          采集器（psutil + LHM + NVML）
hm_widgets.py          rect / 复选框 / 文字截断 / 弹窗外壳 / 右键菜单
hm_ui.py               MonitorApp：渲染、菜单、托盘、弹窗编排
build.py               一键打包
sign.py                本地代码签名工具（自备证书时用）
tests/                 冒烟测试 + 界面与逻辑测试
```

**分层规则**：`入口 → hm_ui → hm_widgets / hm_sensors / hm_fps → hm_utils → hm_theme`，
依赖单向无环，**不允许反向 import**。

**采集与绘制严格分离**：采集跑在后台线程，绘制只在主线程（tkinter 非线程安全），
两者之间只通过 `queue.Queue` 传已格式化的字符串。

界面**没有任何 GUI 框架**——所有控件都是画在一张 `tk.Canvas` 上的图形和文字，
弹窗也是"无边框 Toplevel + 再画一张 Canvas"。所以依赖非常少，体积也小。

---

## 测试

```powershell
python tests/smoke_test.py    # 模块导入 + 真实采集一次 + 建窗口
python tests/ui_test.py       # 帧率算法 + 曲线告警 + 菜单交互 + 各弹窗
```

两个脚本全绿时退出码为 0。

---

## 参与贡献

欢迎提 Issue 和 PR，具体要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。

有一条**硬性红线**：任何形式的**进程注入、D3D 挂钩、读写他人进程内存、
自制内核驱动、键鼠钩子**的改动都不会被合并——这是这个项目存在的理由。
详见 CONTRIBUTING.md 里的说明。

安全问题的报告方式见 [SECURITY.md](SECURITY.md)。

---

## 第三方组件

本仓库随源分发若干第三方二进制文件，版权归各自作者所有，清单与许可证见
[THIRD_PARTY.md](THIRD_PARTY.md)。

---

## 许可证

[MIT](LICENSE)。第三方二进制文件不适用 MIT，遵循其原始许可证。
