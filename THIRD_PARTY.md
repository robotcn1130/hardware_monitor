# 第三方组件清单

本仓库为了"解压即用"随源分发了一批第三方二进制文件。它们**不受本项目的 MIT 许可约束**，
版权归各自作者，遵循各自的原始许可证。

分发时请一并保留本文件与各自的许可证文本。

---

## 1. 随源分发的二进制

### 1.1 可执行文件

| 文件 | 大小 | 版本 | 来源 | 许可证 | SHA-256 |
| --- | --- | --- | --- | --- | --- |
| `PresentMon.exe` | 956,768 | 未在文件属性中标注 | [Intel / GameTechDev PresentMon](https://github.com/GameTechDev/PresentMon) | MIT | `9bec3083069f58f911e6a512f4806db51a27bd096103087bc1d05ef54c80a191` |
| `PawnIO_setup.exe` | 3,410,960 | 2.2.0.0 | [namazso / PawnIO](https://github.com/namazso/PawnIO)，官方发布页 <https://pawnio.eu/> | 见 §3 说明 | `1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032` |

两者都是**原样分发的上游官方二进制，未做任何修改**。

> `PresentMon.exe` 的文件属性里没有版本信息（Intel 没有给它写版本资源），所以
> **SHA-256 才是它的权威标识**。升级时请同步更新上表中的哈希。

### 1.2 `lib/` —— LibreHardwareMonitor 及其依赖

来自 [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
0.9.6 的 .NET 运行时依赖集。`LibreHardwareMonitorLib.dll` 为 **MPL-2.0**；
其中的 `System.*` / `Microsoft.Bcl.*` 系列为 **MIT**（Microsoft）；
`HidSharp.dll` 为 **Apache-2.0**（Illusory Studios LLC）。

本程序以**动态加载、不加修改**的方式使用这些库，未修改其源码。

```
d86690efde30ea9179f669320f39148853793b743a98b531afeaf30598e22f54  262,792  HidSharp.dll
6ebc194316536ba61af5be24508ad9fcbb2ecc685e716c12e787c79530f66bf0 1,203,200  LibreHardwareMonitorLib.dll
bda069b17261a50f36b3a79a88b0243d7b9f6508725598adf88d42471977720e  27,960  Microsoft.Bcl.AsyncInterfaces.dll
3a4e851ee5fc0f6182aa5a3d65dc56fcd6979b65334b5c3b92fbdc791457c0ab  26,920  Microsoft.Bcl.HashCode.dll
2d78d770c9cb997199154ae8c018b9f1d1efbc86729f7264dde6dbad2a12cac3  23,816  System.Buffers.dll
e9bca8ecab5f02df389ee4b306c32a93d7822b524a6eca90e449e13d4b4283a3  34,064  System.CodeDom.dll
8c0f09a8b1fd730da3f3d2c4c831cdd76e3fca6bc588523d8f7eacf16d5a24e9 253,232  System.Collections.Immutable.dll
27514fbffb0776c37ede88ec371310844f7bec55a017627aad60180f268769e4  71,472  System.Formats.Nrbf.dll
52a813c62932462d8f070f001acd1f264c0766a11a78c6d3c4f81ee3b1d67223  85,768  System.IO.Pipelines.dll
d5e8e4866f9cfa66f7765660f84b210198893e55335487afe5ebda342c0e913d 145,200  System.Memory.dll
20c2fa81b8c70d651099d762954f285fd4f942e63b2d7217c145dab8d4b2f4c9 110,344  System.Numerics.Vectors.dll
6c1fe5d140f0f21fac0eea7b1d3e3bc2526c0027e857bcf5227af722a9b32a0d 515,336  System.Reflection.Metadata.dll
c3748f17d2f5209cc795a88ad4946c12c3f6ef73b3015dc92c43f932a342b17c 115,984  System.Resources.Extensions.dll
08cbd7278b66f1e68425a82d4b97181a4130d93e3dd91831407aba7212ccdacf  19,256  System.Runtime.CompilerServices.Unsafe.dll
ff14c5f628b9a6798d173aefbba0a43d61e66f715108e2576ac0d3dfab9071d0  35,952  System.Security.AccessControl.dll
b4d8e15adc235d0e858e39b5133e5d00a4baa8c94f4f39e3b5e791b0f9c0c806  18,312  System.Security.Principal.Windows.dll
43e6dfb4aa333848be5066dcb3d490afc417d0f896aada2f17b5db5fcbb819fc  87,816  System.Text.Encodings.Web.dll
7a7f2e0b942782739551b37546efc842f53c7fce3a8f0f56ad45a5cf78e2c214 778,504  System.Text.Json.dll
5e3a3902f04f840c0fc1f9c2f249f804f6cfdf7901b2e06778bc2a4603ae4694  34,568  System.Threading.AccessControl.dll
af4e13ef418fd89b432de20cd1dfb7fa10a7179c8dd049c9856316e50c5949ca  27,960  System.Threading.Tasks.Extensions.dll
```

### 1.3 `app.ico`

本项目自己生成（`build.py` 里有生成代码），属于本项目作品，适用 MIT。
SHA-256：`e7dc8458513db076b5bbc7cd64cead663e65e2fcb781629bd2970153b64c0550`

---

## 2. Python 依赖

由 PyInstaller 打进产物，或通过 `pip install -r requirements.txt` 安装。

| 包 | 许可证 | 用途 |
| --- | --- | --- |
| [psutil](https://github.com/giampaolo/psutil) | BSD-3-Clause | CPU / 内存 / 磁盘 / 网络 / 进程读数 |
| [pystray](https://github.com/moses-palmer/pystray) | **LGPL-3.0** | 系统托盘图标 |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU | 托盘图标绘制、迷你历史曲线 |
| [pythonnet](https://github.com/pythonnet/pythonnet) + [clr_loader](https://github.com/pythonnet/clr-loader) | MIT | 调用 .NET 的 LibreHardwareMonitor |
| [wmi](https://github.com/tjguk/wmi) + [pywin32](https://github.com/mhammond/pywin32) | MIT / PSF-2.0 | 电池、开机时长等 WMI 查询 |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller)（仅打包时） | GPL-2.0 **带例外条款** | 打包成免安装 exe |
| [numpy](https://github.com/numpy/numpy) | BSD-3-Clause | **本项目未直接使用**，见 §4 |

> **pystray 是 LGPL-3.0**。本项目以未修改的库形式使用它，并通过动态导入的方式
> 打包（PyInstaller 的 `--hidden-import pystray._win32`），使用者可以自行替换
> 该库的版本。如果你要把本项目用于闭源分发，请注意这一条的合规要求。
>
> **PyInstaller 是 GPL-2.0 但带明确例外**：用它打包出的产物**不受 GPL 传染**，
> 可以按你选择的许可分发。这是官方在 `COPYING.txt` 里写明的。

---

## 3. 关于 PawnIO 的许可证

PawnIO 是**双许可**的，来源页面写得很明确：

- **官方发布的二进制**（就是本仓库里的 `PawnIO_setup.exe`）：**专有许可，但允许再分发安装包**。
  本仓库正是按这一条原样分发的，没有修改、没有重新打包。
- **开源部分**：驱动本体 **GPL-2.0 带特殊例外**，库部分 **LGPL-2.1**。
  所以它是 OSI 认可的许可，但注意：本程序**不链接、不调用 PawnIO 的用户态库**，
  只是把安装包交给用户安装，由 LibreHardwareMonitor 在自己进程内与驱动通过
  DeviceIoControl 通信。

如果你对再分发条件有疑虑，可以让用户改为自行到 <https://pawnio.eu/> 下载安装，
再把 `build.py` 里 `ADD_BINARIES` 的 PawnIO 那一项去掉即可。

---

## 4. 一个待清理的项：多余打包进来的 numpy

`build.py` 的产物里目前**混进了 numpy 及其依赖的 OpenBLAS**（`numpy/` 6 MB +
`numpy.libs/` 20 MB，合计约 26 MB），**源码里没有任何一处引用到它**。

疑似是 PyInstaller 某个 hook 顺着 `try: import numpy` 之类的可选导入链收进来的。
处理办法是在 `build.py` 的 PyInstaller 参数里加：

```python
args += ["--exclude-module", "numpy"]
```

去掉之后产物大约能瘦 26 MB，分发包从 41 MB 降到 25 MB 左右，同时少一个未签名的
`libscipy_openblas64_*.dll`（对智能应用控制的签名覆盖面也是好事）。
**改动后必须重跑 `tests/` 和真机回归**，确认温度、显卡、帧率读数不受影响。

---

## 更新这些二进制时

1. 从上游官方发布页下载，**不要用第三方转存的版本**
2. 替换文件后重新计算 SHA-256 并更新本文件
3. 跑一次 `python build.py`，确认 `自检： 通过`
4. 在本仓库提 PR 时，**单独一个 commit 只干这一件事**，方便审查
