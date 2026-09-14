# 代码签名政策 / Code Signing Policy

本文件说明本项目如何对发布产物做数字签名。SignPath Foundation 要求开源项目
提供这样一份说明，以便用户能验证"下载到的 exe 确实由本仓库的源码构建而来"。

---

## 1. 我们签什么

签名对象**只有一类**：从本仓库源码构建出来的主程序

```
硬件监控/硬件监控.exe
```

**明确不签名的文件**（这些是上游第三方二进制，本项目无权代签）：

| 文件 | 原因 |
| --- | --- |
| `_internal/PresentMon.exe` | Intel 的产物（MIT），版权归 Intel |
| `_internal/PawnIO_setup.exe` | namazso 的产物，专有许可 |
| `_internal/lib/*.dll` | LibreHardwareMonitor 及其 .NET 依赖 |
| `_internal/python314.dll`、`tcl90.dll`、`zlib1.dll` 等 | Python 官方运行时与第三方库 |
| `_internal/*/Python.Runtime.dll`、`ClrLoader.dll` | pythonnet 项目 |
| `_internal/pywin32_system32/*.dll` | pywin32 |

这些文件**保持其原始签名状态**（Python 官方的 DLL 大多已由 Python Software
Foundation 签过名），我们不会覆盖、不会重签。

## 2. 签名怎么产生

签名**只在持续集成里进行，不经过任何人的本地机器**。

```
push tag v2.1.0
      ↓
GitHub Actions（github.com 托管的 windows-latest 运行器）
      ↓  pip install -r requirements-dev.txt && python build.py
   dist/硬件监控/            ← 未签名产物
      ↓  actions/upload-artifact
   GitHub 工作流产物          ← SignPath 从这里取，确保来源不可伪造
      ↓  SignPath/github-action-submit-signing-request
   SignPath.io 服务           ← 私钥保存在 SignPath Foundation 的 HSM 中
      ↓
   已签名的 硬件监控.exe
      ↓
   GitHub Release（发布）
```

要点：

- **私钥永不离开 SignPath 的硬件安全模块**。项目维护者没有任何形式的私钥访问权。
- 签名请求由 **SignPath 校验过的构建来源**发起：SignPath 的 GitHub 连接器会核对
  "这次构建确实由 GitHub 工作流产生"，构建来源元数据由 GitHub 提供、脚本无法伪造。
- 签名证书**签发给 SignPath Foundation**，由该基金会为项目背书，因此用户看到的是
  "SignPath Foundation" 这个发布者，而不是个人开发者姓名。

本项目使用的 SignPath 配置标识：

| 项 | 值 |
| --- | --- |
| 组织 / Organization | `SIGNPATH_ORGANIZATION_ID`（GitHub 仓库变量） |
| 项目 / Project slug | `SIGNPATH_PROJECT_SLUG` |
| 签名策略 / Signing policy | `SIGNPATH_POLICY_SLUG`（发布用 `release-signing`） |
| 产物配置 / Artifact configuration | `SIGNPATH_ARTIFACT_CONFIG_SLUG` |
| 触发条件 | 推送形如 `v*` 的 tag |
| 审批方式 | 自动（仅限由上述工作流产出的产物） |

产物配置的参考副本在 [`.signpath/artifact-configuration.xml`](.signpath/artifact-configuration.xml)。

## 3. 谁批准

签名请求的审批人（SignPath 里的 **approver** 角色）为：

- 仓库维护者（GitHub 组织/仓库的 owner）

审批角色与提交角色分离，且**所有具备仓库写权限的成员都必须启用双重认证（MFA）**。

## 4. 用户如何验证

拿到 exe 后，在 PowerShell 里执行：

```powershell
Get-AuthenticodeSignature "硬件监控.exe" | Format-List Status, SignerCertificate

# 或用 Windows SDK 的 signtool
signtool verify /pa /v "硬件监控.exe"
```

`Status` 应为 `Valid`，签名者主体应为 **SignPath Foundation**。
也可以右键 exe → 属性 → 数字签名，查看签名是否有效、是否带可信时间戳。

签名带 **RFC3161 时间戳**，因此即使证书后续轮换或过期，**已发布的旧版本签名依然有效**。

## 5. 用户可以自行校验构建来源

发布页的每个版本都附带源码归档与构建日志链接，任何人都可以：

```powershell
git checkout v2.1.0
pip install -r requirements-dev.txt
python build.py
```

把自己构建出的 `硬件监控.exe` 与发布版比对。由于 PyInstaller 产物不是逐字节可复现的，
**哈希不会完全一致**，但可以用 `_internal/` 里 Python 字节码、资源文件的内容来核对
功能一致性。

---

## English summary

This project signs **only its own executable** (`硬件监控.exe`) — never upstream
third-party binaries that it redistributes.

Signing is performed exclusively in CI on GitHub-hosted runners, via
[SignPath.io](https://signpath.io/)'s GitHub integration. The signing certificate
is issued to **SignPath Foundation**; the private key is stored in SignPath's HSM
and is never accessible to the project maintainers.

The signing workflow is [`.github/workflows/release.yml`](.github/workflows/release.yml),
triggered by pushing a `v*` tag. The artifact configuration is kept in
[`.signpath/artifact-configuration.xml`](.signpath/artifact-configuration.xml).

Verification:

```powershell
Get-AuthenticodeSignature "硬件监控.exe"
# Status: Valid, signer: SignPath Foundation
```

All repository members with write access are required to use multi-factor
authentication. The approver role for signing requests is held by the repository
maintainer and is separate from the submitter role.
