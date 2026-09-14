# -*- coding: utf-8 -*-
"""给打包产物做代码签名（对付 Windows 智能应用控制）。

## 为什么需要这个

Windows 11 的「智能应用控制」(Smart App Control, SAC) **只放行两类程序**：

1. 微软云端信誉服务能判为安全的（要有大量用户在跑）；
2. **带有效数字签名的**——证书必须来自微软「受信任根计划」内的 CA。

两条都不占的程序会被**硬性拦截**，弹窗是「智能应用控制已阻止可能不安全的应用」，
**没有「仍要运行」按钮**，右键解除锁定、管理员运行、命令行启动全都绕不过去。
我们自己用 PyInstaller 打的 exe 正是这种情况，所以只能签名。

## 三条硬规则（踩过的坑）

- **自签证书无效**。微软官方明确说 SAC 不认本地加到「受信任的根证书颁发机构」里的
  自签证书，别浪费时间试。必须买受信任 CA 的证书。
- **必须是 RSA 证书**。SAC 的签名校验不支持 ECC/ECDSA。
- **要签的不只有主 exe**。SAC 会校验它加载的每一个 PE 映像，任何一个 DLL 没签
  都会被拦。实测 `dist/硬件监控` 下 137 个 PE 文件里有 **12 个**没有有效签名
  （Python 官方那些 DLL 大多已经签过了，不用管）。本脚本只签没签过的。

## ⚠️ 第三方 DLL 怎么办（要实测的风险）

那 12 个文件里只有 **`硬件监控.exe`** 是我们自己生成的，其余 11 个都是上游项目的
二进制（`LibreHardwareMonitorLib.dll`、`tcl90.dll`、`Python.Runtime.dll`、
`ClrLoader.dll`、`zlib1.dll` …）。

- 走 **SignPath Foundation** 时：条款明令**只能签自己的代码**，允许在签名包里
  「包含」未签名的上游二进制，但不能代签。所以第三方 DLL 仍然是未签名的。
- 走 **自己买的证书** 时：可以给全部 12 个都签上。PyInstaller 生态里这么做很常见。

理论上 SAC 也可能在加载未签名的 `LibreHardwareMonitorLib.dll` 时拦一下
（表现为温度读不到，而其它指标正常）。**这一点只能真机实测**，先按主 exe 签，
真被拦了再考虑自己买证书把第三方一起签。

---

## 证书怎么来

按「能不能过 SAC / 要不要钱」排：

| 方案 | 费用 | 过 SAC | 门槛 |
| --- | --- | --- | --- |
| **SignPath Foundation** | **免费** | ✅ OV 级、受信任根内 | 必须开源：公开仓库 + CI + 代码签名政策页 + MFA + 评审流程，审核约 1 周 |
| Certum 开源开发者证书 | 已不免费（约 €100+/年，云签名最低约 $108/年） | ✅ | 个人身份验证 + 项目链接 |
| Azure Artifact Signing | ~$9.99/月 | ✅ | **个人仅限美国/加拿大**，2026-03 起个人 onboarding 暂停 |
| 传统 OV 证书 | $150–300/年 | ✅ | 需硬件令牌或 CA 云签名服务 |
| EV 证书 | $400+/年 | ✅ 但**已无优势** | 2024-08 微软取消了 EV 的独立待遇，别多花这个钱 |
| 自签 / Sigstore / GPG | 免费 | ❌ | SAC 不认本地自签根；Fulcio 根也不在受信任根计划里，Windows 根本不看 |

## 2026 年的两条新规矩（会改变你的操作）

- **私钥不能再放 PFX 文件里**。2023-06 起 CA/B Forum 要求 OV 证书的私钥也必须存在
  硬件令牌 / HSM / CA 云签名服务中。所以 `--pfx` 只对老证书和自签有意义，
  **新证书基本都用 `/sha1` 或 `/n` 从 Windows 证书存储里选**——云签名服务
  （Certum SimplySign、SignPath）会把它自己伪装成一张智能卡，signtool 照常能用。
- **单张证书最长 459 天（约 15 个月）**。2026-02/03 起生效。现在卖的「3 年」
  其实是 3 年订阅，CA 每 15 个月给你免费换发一张新的，不用重新掏钱。

## 用法

```powershell
python sign.py --check                       # 只看哪些文件没签名，不签
python sign.py --sha1 证书指纹               # 用证书存储里的证书（云签名走这条）
python sign.py --n "证书主题名"               # 按主题名选证书
python sign.py --pfx mycert.pfx --pass 密码   # 老式 PFX（2023 年前发的证书）
python sign.py --sha1 指纹 --ts http://time.certum.pl
```

签完记得**重新打 zip 并更新官网**——签名是在打包之后做的，顺序不能反：

```
python build.py   →   python sign.py ...   →   打 web/硬件监控.zip
```

依赖：Windows SDK 里的 `signtool.exe`。没有的话脚本会告诉你去哪装。
"""

import argparse
import os
import subprocess
import sys

# 同 build.py：CI 上 stdout 接管道，编码取自区域设置（英文 runner 是 cp1252），
# 中文 print 会 UnicodeEncodeError。强制 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 时间戳服务器：证书过期后签名依然有效，必须要
DEFAULT_TS = "http://timestamp.digicert.com"

DIST = os.path.join("dist", "硬件监控")


def find_signtool():
    """找 signtool.exe：先看 PATH，再扫 Windows SDK / VS 的常见安装位置。"""
    from shutil import which

    hit = which("signtool") or which("signtool.exe")
    if hit:
        return hit

    roots = [
        r"C:\Program Files (x86)\Windows Kits\10\bin",
        r"C:\Program Files\Windows Kits\10\bin",
        r"C:\Program Files (x86)\Microsoft SDKs\Windows",
    ]
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, dn, fn in os.walk(root):
            if "signtool.exe" in fn:
                # 优先 64 位（x64 子目录）
                p = os.path.join(dp, "signtool.exe")
                found.append((0 if "x64" in dp.lower() else 1, p))
    if found:
        found.sort()
        return found[0][1]
    return None


def unsigned_pe(directory):
    """列出目录下所有「没有有效签名」的 PE 文件（.exe / .dll）。

    判断交给 PowerShell 的 Get-AuthenticodeSignature——Python 标准库没有
    验证 PE 签名的能力，也不想为此多引一个依赖。

    结果**走临时文件中转**而不是直接读 stdout：PowerShell 在中文 Windows 上
    输出的是 GBK，直接喂给 Python 的 `text=True` 会 UnicodeDecodeError（路径里
    有中文时必炸）。让 PowerShell 自己以 UTF-8 落盘最省事。
    """
    import tempfile

    tmp = os.path.join(tempfile.gettempdir(), "_hm_unsigned.txt")
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass

    ps = (
        "Get-ChildItem -Path '%s' -Recurse -File -Include *.exe,*.dll "
        "| Get-AuthenticodeSignature "
        "| Where-Object { $_.Status -ne 'Valid' } "
        "| ForEach-Object { $_.Path } "
        "| Out-File -FilePath '%s' -Width 1000 -Encoding UTF8"
    ) % (os.path.abspath(directory), tmp)

    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=300,
        )
    except Exception as exc:
        print("调用 PowerShell 失败：%s" % exc)
        return []

    if not os.path.exists(tmp):
        print("PowerShell 没有产出结果（返回码 %s）" % r.returncode)
        return []

    with open(tmp, encoding="utf-8-sig") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    try:
        os.remove(tmp)
    except OSError:
        pass
    return lines


def main():
    ap = argparse.ArgumentParser(description="给打包产物做代码签名")
    ap.add_argument("--pfx", help="PFX 证书文件路径（只适用于 2023 年前发的证书）")
    ap.add_argument("--pass", dest="pwd", help="PFX 密码")
    ap.add_argument("--sha1", help="证书指纹（用本机证书存储里的证书，云签名走这条）")
    ap.add_argument("--n", dest="subject", help="证书主题名，如 \"Zhang San\"")
    ap.add_argument("--ts", default=DEFAULT_TS, help="时间戳服务器")
    ap.add_argument("--dir", default=DIST, help="要签名的目录，默认 dist/硬件监控")
    ap.add_argument("--check", action="store_true", help="只检查不签名")
    args = ap.parse_args()

    if not os.path.isdir(args.dir):
        print("目录不存在：%s（先跑 python build.py）" % args.dir)
        return 1

    print("扫描 %s ..." % args.dir)
    targets = unsigned_pe(args.dir)
    print("需要签名的文件：%d 个" % len(targets))
    for t in targets:
        print("  " + os.path.relpath(t, args.dir))

    if args.check:
        return 0
    if not targets:
        print("全部已签名，无需处理。")
        return 0
    if not args.pfx and not args.sha1 and not args.subject:
        print("\n未指定证书。三选一：")
        print("  --sha1 <指纹>        从证书存储按指纹选（云签名服务走这条）")
        print("  --n \"证书主题名\"      从证书存储按主题名选")
        print("  --pfx a.pfx --pass x  老式 PFX 文件（2023 年前的证书）")
        return 1
    if args.pfx and not os.path.isfile(args.pfx):
        print("PFX 文件不存在：%s" % args.pfx)
        return 1

    tool = find_signtool()
    if not tool:
        print("\n找不到 signtool.exe。装 Windows SDK 后重试：")
        print("  https://developer.microsoft.com/windows/downloads/windows-sdk/")
        print("（勾选 Signing Tools for Desktop Apps，或直接用 VS 安装器里的 Windows 10/11 SDK）")
        return 1
    print("\n使用签名工具：%s" % tool)

    cmd = [tool, "sign", "/fd", "sha256", "/td", "sha256", "/tr", args.ts]
    if args.sha1:
        cmd += ["/sha1", args.sha1]
    elif args.subject:
        cmd += ["/n", args.subject]
    else:
        cmd += ["/f", args.pfx, "/p", args.pwd or ""]

    # 命令行长度有上限，分批提交；一批 20 个足够保守
    fail = []
    for i in range(0, len(targets), 20):
        batch = targets[i:i + 20]
        print("签名 %d/%d ..." % (i + len(batch), len(targets)))
        r = subprocess.run(cmd + batch, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(r.stdout[-800:] if r.stdout else "")
            print(r.stderr[-800:] if r.stderr else "")
            fail.extend(batch)

    if fail:
        print("\n以下文件签名失败（%d 个）：" % len(fail))
        for f in fail:
            print("  " + os.path.relpath(f, args.dir))
        return 1

    left = unsigned_pe(args.dir)
    print("\n签名完成，仍无有效签名的：%d 个" % len(left))
    print("\n下一步：重新打 zip 并更新官网（签名是在打包之后做的，顺序不能反）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
