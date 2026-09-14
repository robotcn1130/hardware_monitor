# -*- coding: utf-8 -*-
"""硬件传感器采集。

数据来源：
    psutil                          CPU/内存/磁盘/网络/开机时长
    nvidia-smi                      NVIDIA 显卡
    LibreHardwareMonitor (pythonnet) 温度/功耗/风扇/电压（需 PawnIO 内核驱动）
    WMI                             CPU 名称、显卡名（可选降级）
全部为系统公开接口，不接触游戏进程。
"""
import os
import platform
import subprocess
import sys
import time

import psutil

from hm_fps import _window_titles, fps_sampler, pick_fps
from hm_log import log_error
from hm_utils import (CREATE_NO_WINDOW, _find_bundled, _fmt_bytes, _fmt_duration,
                      _fmt_speed, is_admin, pawnio_just_installed)

# 采集结果里携带原始数值的特殊键：界面用它画历史曲线，不直接显示。
# 值为 {指标键: float}，没有对应数值的项不会出现在里面。
HIST_KEY = "_hist"


class Collector:
    """采集硬件信息，静态信息只取一次，动态信息按需刷新。"""

    def __init__(self, cfg):
        self.cfg = cfg             # 直接引用配置，菜单改动即时生效
        self.wmi_conn = None
        self.cpu_name = "CPU"
        self.gpus = []           # [{"name", "hw_idx", "kind", "nvidia_idx"}]
        self.nvidia_smi = False
        self.lhm = None          # LibreHardwareMonitor 的 Computer 对象
        self.lhm_error = None    # 加载失败原因，用于提示
        self._lhm_hw = []
        self.prev_net = None
        self.prev_net_t = None
        self.tick = 0
        self.cache_temp = None
        self.cache_slow = {}     # 主板/硬盘/风扇/电池等慢速项，两次采样刷新一次
        self.cache_gpu = []
        psutil.cpu_percent(interval=None)
        self._init_static()

    # ---- 初始化静态信息
    def _init_static(self):
        try:
            import wmi

            self.wmi_conn = wmi.WMI()
        except Exception:
            self.wmi_conn = None

        wmi_gpus = []
        if self.wmi_conn is not None:
            try:
                for p in self.wmi_conn.Win32_Processor():
                    if p.Name:
                        self.cpu_name = p.Name.strip()
                        break
            except Exception:
                pass
            try:
                for v in self.wmi_conn.Win32_VideoController():
                    if v.Name:
                        wmi_gpus.append(v.Name.strip())
            except Exception:
                pass
        if self.cpu_name == "CPU":
            self.cpu_name = platform.processor() or "CPU"

        # nvidia-smi
        nvidia_names = []
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=3,
                creationflags=CREATE_NO_WINDOW,
            )
            if out.returncode == 0 and out.stdout.strip():
                nvidia_names = [ln.strip() for ln in out.stdout.strip().splitlines() if ln.strip()]
                self.nvidia_smi = True
        except Exception:
            self.nvidia_smi = False

        # LibreHardwareMonitor（读取温度、功耗、风扇等）
        self._init_lhm()
        self._build_gpus(wmi_gpus, nvidia_names)

    # ---- 汇总所有显卡（多显卡时依次编号，用于 gpu1 / gpu2 / ...）
    def _build_gpus(self, wmi_gpus, nvidia_names):
        gpus = []
        for idx, hw in enumerate(self._lhm_hw):
            try:
                kind = str(hw.HardwareType)
            except Exception:
                continue
            if not kind.startswith("Gpu"):
                continue
            name = ""
            try:
                name = hw.Name or ""
            except Exception:
                pass
            gpus.append({"name": name, "hw_idx": idx, "kind": kind, "nvidia_idx": None})

        # 记录 NVIDIA 显卡在 nvidia-smi 中的下标
        n = 0
        for g in gpus:
            if g["kind"] == "GpuNvidia":
                g["nvidia_idx"] = n
                n += 1

        if not gpus:
            # 传感器库不可用时的兜底：仅有 NVIDIA 卡仍可通过 nvidia-smi 读数
            for i, name in enumerate(nvidia_names):
                gpus.append({"name": name, "hw_idx": None, "kind": "GpuNvidia", "nvidia_idx": i})
        if not gpus:
            for name in wmi_gpus:
                gpus.append({"name": name, "hw_idx": None, "kind": "", "nvidia_idx": None})

        self.gpus = gpus
        self.cache_gpu = [None] * len(gpus)

    # ---- 加载 LibreHardwareMonitor 传感器库
    def _init_lhm(self):
        dll = _find_bundled(os.path.join("lib", "LibreHardwareMonitorLib.dll"))
        lib_dir = os.path.dirname(dll)
        if not os.path.exists(dll):
            self.lhm_error = "缺少 lib/LibreHardwareMonitorLib.dll"
            return
        try:
            if lib_dir not in sys.path:
                sys.path.append(lib_dir)
            import clr

            clr.AddReference(dll)
            from LibreHardwareMonitor.Hardware import Computer  # noqa: F401
        except Exception as exc:
            self.lhm_error = f"传感器库加载失败：{exc}"
            return

        # 开启传感器模块是有副作用的：某个模块依赖的原生库缺失时，Computer.Open()
        # 会整个抛异常，结果是所有传感器一起失效（不是"这一组读不到"）。
        # 实测缺的依赖：
        #   Memory  组 -> RAMSPDToolkit-NDD.dll（内存 SPD）
        #   Storage 组 -> DiskInfoToolkit.dll（硬盘 SMART）
        # 因此按「从全到简」逐级回退，优先牺牲最可能缺依赖的组，尽量保住核心项。
        #
        # 我们不用的组直接恒关（省枚举时间）：
        #   Controller / Psu -> 本程序不读这两类传感器
        #   Battery          -> 电池走 psutil，比传感器库更直接
        #   Network          -> 网速走 psutil，且网卡枚举慢
        comp = None
        last_exc = None
        for level, (mem, mb, storage) in enumerate(
                ((True, True, True),      # 1 全开
                 (False, True, True),     # 2 丢内存（缺 RAMSPDToolkit-NDD）
                 (False, True, False),    # 3 再丢硬盘（缺 DiskInfoToolkit），保主板
                 (False, False, True),    # 4 改保硬盘（主板组异常时）
                 (False, False, False)),  # 5 只留 CPU/GPU
                1):
            c = None
            try:
                c = Computer()
                c.IsCpuEnabled = True
                c.IsGpuEnabled = True
                c.IsMemoryEnabled = mem
                c.IsMotherboardEnabled = mb
                c.IsStorageEnabled = storage
                c.IsControllerEnabled = False
                c.IsNetworkEnabled = False
                c.IsPsuEnabled = False
                c.IsBatteryEnabled = False
                c.Open()
                comp = c
                if level > 1:
                    log_error("lhm.open.fallback",
                              f"已降级到级别 {level}（cpu/gpu=True, mem={mem}, "
                              f"motherboard={mb}, storage={storage}）")
                break
            except Exception as exc:
                last_exc = exc
                log_error(f"lhm.open.level{level}", exc)
                if c is not None:
                    try:
                        c.Close()
                    except Exception:
                        pass
        if comp is None:
            self.lhm_error = f"传感器初始化失败：{last_exc}"
            return

        self.lhm = comp
        self._lhm_hw = list(comp.Hardware)
        for hw in self._lhm_hw:
            try:
                hw.Update()
                kind = str(hw.HardwareType)
                if kind == "Cpu" and hw.Name:
                    self.cpu_name = hw.Name
            except Exception:
                continue

    # ---- 刷新传感器并生成读数快照 {(硬件下标, 硬件类型, 传感器类型, 名称): 数值}
    def _read_sensors(self):
        snap = {}
        if self.lhm is None:
            return snap
        for hw in self._lhm_hw:
            try:
                hw.Update()
            except Exception:
                pass
            try:
                for sub in hw.SubHardware:
                    try:
                        sub.Update()
                    except Exception:
                        pass
            except Exception:
                pass
        for idx, hw in enumerate(self._lhm_hw):
            try:
                self._collect_sensors(hw, idx, snap)
                for sub in hw.SubHardware:
                    self._collect_sensors(sub, idx, snap)
            except Exception:
                continue
        return snap

    @staticmethod
    def _collect_sensors(hw, idx, snap):
        kind = str(hw.HardwareType)
        for s in hw.Sensors:
            try:
                value = s.Value
                if value is None:
                    continue
                snap[(idx, kind, str(s.SensorType), (s.Name or "").strip())] = float(value)
            except Exception:
                continue

    @staticmethod
    def _pick(snap, hw_prefix, sensor_type, keywords, skip_zero=True, hw_idx=None):
        """在快照里按关键字优先级查找传感器值；hw_idx 可限定某块硬件。"""
        for kw in keywords:
            for (idx, kind, stype, name), value in snap.items():
                if not kind.startswith(hw_prefix) or stype != sensor_type:
                    continue
                if hw_idx is not None and idx != hw_idx:
                    continue
                if kw.lower() in name.lower():
                    if skip_zero and value == 0:
                        continue
                    return value
        return None

    # ---- 慢速传感器：主板温度、硬盘温度、系统风扇、电池 + 显卡数值
    # 与 CPU 温度同频刷新（每 2 次采样一次），避免每次采样都去啃传感器。
    def _slow_metrics(self, snap):
        out = {}

        mb = self._pick(snap, "Motherboard", "Temperature",
                        ["Motherboard", "System", "Mainboard", "Board", "Temperature"])
        if mb is not None:
            out["mb_temp"] = mb

        # 有多块硬盘时取最热的那块
        temps = [v for (_i, kind, stype, _n), v in snap.items()
                 if kind.startswith("Storage") and stype == "Temperature" and v > 0]
        if temps:
            out["hdd_temp"] = max(temps)

        fan = self._pick(snap, "Motherboard", "Fan", ["System", "Chassis", "Fan"])
        if fan is None:
            fan = self._pick(snap, "Motherboard", "Control",
                             ["System", "Chassis", "Fan"])
        if fan is not None:
            out["sys_fan"] = fan

        # 显卡数值：主要给历史曲线用，优先 nvidia-smi（更准）
        load = temp = None
        if self.gpus:
            g = self.gpus[0]
            hw_idx = g.get("hw_idx")
            load = self._pick(snap, "Gpu", "Load", ["GPU Core", "D3D 3D", "GPU"],
                              skip_zero=False, hw_idx=hw_idx)
            temp = self.gpu_temperature(snap, hw_idx=hw_idx)
            if g.get("kind") == "GpuNvidia" and self.nvidia_smi:
                info = self._nvidia_query(g.get("nvidia_idx") or 0)
                if info:
                    if info.get("load") is not None:
                        load = info["load"]
                    if info.get("temp") is not None:
                        temp = info["temp"]
        if load is not None:
            out["gpu_load"] = load
        if temp is not None:
            out["gpu_temp"] = temp

        # 电池：psutil 比传感器库更直接，也覆盖没有 LHM 的情况
        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                out["battery"] = bat.percent
                out["battery_plugged"] = bool(bat.power_plugged)
        except Exception:
            pass
        return out

    # ---- CPU 温度（需管理员权限，AMD 平台依赖内核驱动）
    def cpu_temperature(self, snap):
        t = self._pick(
            snap, "Cpu", "Temperature",
            ["Core (Tctl/Tdie)", "Tctl/Tdie", "CPU Package", "Core Average", "Core Max", "CPU"],
        )
        if t is not None:
            return t
        # 部分机器 ACPI 温度区可用
        if self.wmi_conn is not None:
            try:
                for z in self.wmi_conn.MSAcpi_ThermalZoneTemperature():
                    return z.CurrentTemperature / 10.0 - 273.15
            except Exception:
                pass
        return None

    # ---- 显卡温度
    def gpu_temperature(self, snap, hw_idx=None):
        return self._pick(
            snap, "Gpu", "Temperature",
            ["GPU Core", "GPU Hot Spot", "GPU", "Core"],
            hw_idx=hw_idx,
        )

    def _nvidia_query(self, idx=0):
        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total,"
                    "fan.speed,power.draw,clocks.current.graphics",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True, text=True, timeout=3,
                creationflags=CREATE_NO_WINDOW,
            )
            if out.returncode != 0 or not out.stdout.strip():
                return None
            lines = [ln for ln in out.stdout.strip().splitlines() if ln.strip()]
            if idx >= len(lines):
                return None
            parts = [p.strip() for p in lines[idx].split(",")]

            def num(i):
                # 显卡不支持该项时 nvidia-smi 返回 [N/A]
                try:
                    return float(parts[i])
                except (IndexError, ValueError):
                    return None

            return {
                "load": num(0),
                "temp": num(1),
                "mem_used": num(2),
                "mem_total": num(3),
                "fan": num(4),
                "power": num(5),
                "clock": num(6),
            }
        except Exception:
            return None

    # ---- 读不到传感器时的原因文案
    def _unavailable(self, group="core"):
        """给出「为什么读不到」的文案，条件按可操作性从强到弱排列。

        三种情况对用户的意义完全不同（能不能自己解决）：
          传感器库没加载  -> 不可用（改不了）
          没提权          -> 需管理员权限（用户能解决）
          刚装完内核驱动  -> 请重启电脑（用户能解决）
        group 参数保留给「提权也没用」的组（如 Storage 缺依赖 DLL）留扩展位。
        """
        if self.lhm is None:
            return "不可用"
        if not is_admin():
            return "需管理员权限"
        if pawnio_just_installed():
            return "请重启电脑"
        return "不可用"

    # ---- 采集一次
    def sample(self):
        self.tick += 1
        data = {}
        hist = {}                # 供历史曲线使用的原始数值（不直接显示）

        # 传感器读数（一次刷新供各项复用）
        snap = self._read_sensors()

        cpu_pct = psutil.cpu_percent(interval=None)
        data["cpu"] = f"{cpu_pct:.0f}%  ({psutil.cpu_count()} 核)"
        hist["cpu"] = cpu_pct

        # 温度与慢速项每 2 次采样刷新一次（读传感器较慢）
        if self.tick % 2 == 1 or self.cache_temp is None:
            self.cache_temp = self.cpu_temperature(snap)
            self.cache_slow = self._slow_metrics(snap)
        slow = self.cache_slow or {}
        if self.cache_temp is not None:
            data["cpu_temp"] = f"{self.cache_temp:.0f}°C"
            hist["cpu_temp"] = self.cache_temp
        else:
            data["cpu_temp"] = self._unavailable()

        freq = None
        try:
            f = psutil.cpu_freq()
            if f and f.current:
                freq = f.current
        except Exception:
            freq = None
        if freq:
            data["cpu_freq"] = f"{freq / 1000:.2f} GHz"
            hist["cpu_freq"] = freq / 1000
        else:
            data["cpu_freq"] = "不可用"

        vm = psutil.virtual_memory()
        data["mem"] = (
            f"{vm.percent:.0f}%  "
            f"({_fmt_bytes(vm.used)} / {_fmt_bytes(vm.total)})"
        )
        hist["mem"] = vm.percent

        disk_lines = []
        try:
            for part in psutil.disk_partitions(all=False):
                if "cdrom" in part.opts or not part.fstype:
                    continue
                try:
                    u = psutil.disk_usage(part.mountpoint)
                except Exception:
                    continue
                disk_lines.append(
                    f"{part.device.rstrip(chr(92))} {u.percent:.0f}%  "
                    f"({_fmt_bytes(u.free)} 可用 / {_fmt_bytes(u.total)})"
                )
        except Exception:
            pass
        data["disk"] = "\n".join(disk_lines) if disk_lines else "不可用"

        try:
            io = psutil.net_io_counters()
            now = time.time()
            if self.prev_net is not None and self.prev_net_t:
                dt = max(now - self.prev_net_t, 0.001)
                up = (io.bytes_sent - self.prev_net.bytes_sent) / dt
                down = (io.bytes_recv - self.prev_net.bytes_recv) / dt
                data["net"] = f"↓ {_fmt_speed(down)}   ↑ {_fmt_speed(up)}"
            else:
                data["net"] = "↓ 0B/s   ↑ 0B/s"
            self.prev_net = io
            self.prev_net_t = now
        except Exception:
            data["net"] = "不可用"

        # 显卡：每 2 次采样刷新一次；选「全部显卡」时每张卡各占一行
        if self.tick % 2 == 1 or not self.cache_gpu:
            self.cache_gpu = self._gpu_text(snap)
        data["gpu"] = "\n".join(self.cache_gpu) if self.cache_gpu else "不可用"
        if slow.get("gpu_load") is not None:
            hist["gpu_load"] = slow["gpu_load"]
        if slow.get("gpu_temp") is not None:
            hist["gpu_temp"] = slow["gpu_temp"]

        # FPS：读取后台采集器最近一次统计（尚未采到数据时显示不可用）。
        # 帧率、帧时间、1% / 0.1% 低帧都来自同一份逐帧数据。
        try:
            stats, proc = pick_fps(fps_sampler().snapshot(),
                                   self.cfg.get("fps_target"))
        except Exception as exc:
            log_error("collector.fps", exc)
            stats, proc = None, None
        if stats is not None:
            suffix = []
            if self.cfg.get("fps_show_title") and proc:
                title = _window_titles().get(os.path.basename(proc).lower(),
                                             ("", ""))[1]
                if title:
                    suffix.append(title)
            if self.cfg.get("fps_show_proc") and proc:
                suffix.append(os.path.basename(proc))
            data["fps"] = (f"{stats['fps']:.0f} FPS"
                           + (f"  {'  '.join(suffix)}" if suffix else ""))
            data["frametime"] = f"{stats['frametime']:.1f} ms"
            data["fps_low"] = self._low_text(stats)
            hist["fps"] = stats["fps"]
            hist["frametime"] = stats["frametime"]
        else:
            data["fps"] = "不可用"
            data["frametime"] = "不可用"
            data["fps_low"] = "不可用"

        # 主板 / 硬盘 / 风扇 / 电池（阶段一新增，与温度同频刷新）
        # 主板温度与系统风扇来自 SuperIO 芯片，需要内核驱动 + 管理员权限做
        # I/O 端口访问，因此失败文案与 CPU 温度一致（提示去提权，而不是干说"不可用"）。
        if slow.get("mb_temp") is not None:
            data["mb_temp"] = f"{slow['mb_temp']:.0f}°C"
            hist["mb_temp"] = slow["mb_temp"]
        else:
            data["mb_temp"] = self._unavailable("superio")
        if slow.get("hdd_temp") is not None:
            data["hdd_temp"] = f"{slow['hdd_temp']:.0f}°C"
            hist["hdd_temp"] = slow["hdd_temp"]
        else:
            # 硬盘温度属于 Storage 组，需要 lib/DiskInfoToolkit.dll；
            # 缺这个 DLL 时提权也没用，所以不给"需管理员权限"的误导提示。
            data["hdd_temp"] = "不可用"
        if slow.get("sys_fan") is not None:
            data["sys_fan"] = f"{slow['sys_fan']:.0f} RPM"
        else:
            data["sys_fan"] = self._unavailable("superio")
        data["battery"] = self._battery_text(slow)

        try:
            data["uptime"] = _fmt_duration(time.time() - psutil.boot_time())
        except Exception:
            data["uptime"] = "不可用"

        data[HIST_KEY] = hist
        return data

    # ---- 低帧文字：1% / 0.1% Low 都是「最慢的那部分帧」换算出的 FPS
    @staticmethod
    def _low_text(stats):
        parts = []
        if stats.get("low1") is not None:
            parts.append(f"1% {stats['low1']:.0f}")
        if stats.get("low01") is not None:
            parts.append(f"0.1% {stats['low01']:.0f}")
        return "   ".join(parts) if parts else "不可用"

    @staticmethod
    def _battery_text(slow):
        pct = slow.get("battery")
        if pct is None:
            return "未检测到电池"
        return f"{pct:.0f}%  " + ("充电中" if slow.get("battery_plugged") else "放电中")

    # ---- 显卡信息列表（每块卡一条，只拼装被勾选的参数）
    def _gpu_text(self, snap):
        if not self.gpus:
            return ["不可用"]
        target = self.cfg.get("gpu_target", -1)
        if isinstance(target, int) and 0 <= target < len(self.gpus):
            chosen = [self.gpus[target]]
        else:
            chosen = self.gpus
        out = []
        for i, gpu in enumerate(chosen):
            text = self._one_gpu_text(snap, gpu)
            # 同时显示多张卡时带上名称，便于区分是哪一张
            if len(chosen) > 1:
                name = (gpu.get("name") or "").strip() or f"显卡{i + 1}"
                text = f"{name}  {text}"
            out.append(text)
        return out

    def _one_gpu_text(self, snap, gpu):
        hw_idx = gpu.get("hw_idx")
        want = self.cfg.get("gpu_params") or {}

        def w(key):
            return bool(want.get(key, True))

        # NVIDIA 优先用 nvidia-smi（数据更准）
        if gpu.get("kind") == "GpuNvidia" and self.nvidia_smi:
            info = self._nvidia_query(gpu.get("nvidia_idx") or 0)
            if info:
                parts = []
                if w("load") and info.get("load") is not None:
                    parts.append(f"占用 {info['load']:.0f}%")
                if w("temp") and info.get("temp") is not None:
                    parts.append(f"{info['temp']:.0f}°C")
                if (w("mem") and info.get("mem_used") is not None
                        and info.get("mem_total")):
                    parts.append(f"显存 {info['mem_used']:.0f}/{info['mem_total']:.0f} MB")
                if w("fan") and info.get("fan") is not None:
                    parts.append(f"风扇 {info['fan']:.0f}%")
                if w("power") and info.get("power") is not None:
                    parts.append(f"功耗 {info['power']:.0f}W")
                if w("clock") and info.get("clock") is not None:
                    parts.append(f"频率 {info['clock']:.0f}MHz")
                if parts:
                    return "   ".join(parts)

        if self.lhm is None:
            return "不可用"

        parts = []
        if w("load"):
            load = self._pick(
                snap, "Gpu", "Load",
                ["GPU Core", "D3D 3D", "GPU"], skip_zero=False, hw_idx=hw_idx,
            )
            if load is not None:
                parts.append(f"占用 {load:.0f}%")
        if w("temp"):
            temp = self.gpu_temperature(snap, hw_idx=hw_idx)
            if temp is not None:
                parts.append(f"{temp:.0f}°C")
        if w("mem"):
            mem_used = self._pick(snap, "Gpu", "SmallData", ["GPU Memory Used"],
                                  skip_zero=False, hw_idx=hw_idx)
            mem_total = self._pick(snap, "Gpu", "SmallData", ["GPU Memory Total"],
                                   skip_zero=False, hw_idx=hw_idx)
            if mem_used is not None and mem_total:
                parts.append(f"显存 {mem_used:.0f}/{mem_total:.0f} MB")
        if w("fan"):
            fan = self._pick(snap, "Gpu", "Control", ["GPU Fan"], skip_zero=False, hw_idx=hw_idx)
            if fan is not None:
                parts.append(f"风扇 {fan:.0f}%")
        if w("power"):
            power = self._pick(snap, "Gpu", "Power", ["GPU Package", "GPU Power", "GPU"],
                               skip_zero=False, hw_idx=hw_idx)
            if power is not None:
                parts.append(f"功耗 {power:.0f}W")
        if w("clock"):
            clk = self._pick(snap, "Gpu", "Clock", ["GPU Core"], skip_zero=False, hw_idx=hw_idx)
            if clk is not None:
                parts.append(f"频率 {clk:.0f}MHz")

        return "   ".join(parts) if parts else "不可用"
