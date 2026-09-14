# -*- coding: utf-8 -*-
"""指标定义与配置读写。

config.json 是用户数据：升级程序时要保证旧配置能被正确读进来，
因此 load_config 里带一层版本迁移逻辑（见 CONFIG_VERSION 与 _migrate）。
写入走「临时文件 + 替换」，避免断电或崩溃时把配置写坏。
"""
import json
import os

from hm_log import log_error
from hm_utils import CONFIG_PATH

# 配置结构版本：新增/调整字段时 +1，并在 _migrate 里补上迁移
CONFIG_VERSION = 2

# 指标 & 显示名
METRICS = [
    ("cpu", "CPU 使用率"),
    ("cpu_temp", "CPU 温度"),
    ("cpu_freq", "CPU 频率"),
    ("mem", "内存"),
    ("disk", "磁盘"),
    ("net", "网络"),
    ("gpu", "显卡"),
    ("fps", "FPS"),
    ("frametime", "帧时间"),
    ("fps_low", "低帧"),
    ("hdd_temp", "硬盘温度"),
    ("mb_temp", "主板温度"),
    ("sys_fan", "系统风扇"),
    ("battery", "电池"),
    ("uptime", "开机时长"),
]
METRIC_ORDER = [k for k, _ in METRICS]
METRIC_LABEL = dict(METRICS)

# 横版一行放不下全名，用短名（与效果图一致）
METRIC_SHORT = {
    "cpu": "CPU",
    "cpu_temp": "温度",
    "cpu_freq": "频率",
    "mem": "内存",
    "disk": "磁盘",
    "net": "网络",
    "gpu": "显卡",
    "fps": "FPS",
    "frametime": "帧时",
    "fps_low": "低帧",
    "hdd_temp": "硬盘",
    "mb_temp": "主板",
    "sys_fan": "风扇",
    "battery": "电池",
    "uptime": "开机",
}

# 显卡可单独勾选的参数（键, 显示名）
GPU_PARAMS = [
    ("load", "占用"),
    ("temp", "温度"),
    ("mem", "显存"),
    ("fan", "风扇"),
    ("power", "功耗"),
    ("clock", "频率"),
]

# 告警项：键 -> (显示名, 单位, 步进, 上限)
# 阈值填 0 表示该项不告警
ALERT_ITEMS = [
    ("cpu", "CPU 使用率", "%", 5, 100),
    ("cpu_temp", "CPU 温度", "°C", 1, 120),
    ("gpu_temp", "显卡温度", "°C", 1, 120),
    ("hdd_temp", "硬盘温度", "°C", 1, 90),
    ("mem", "内存", "%", 5, 100),
]
ALERT_LABEL = {k: label for k, label, _u, _s, _m in ALERT_ITEMS}

DEFAULT_ALERTS = {
    "enabled": True,
    "cpu": 95,
    "cpu_temp": 85,
    "gpu_temp": 88,
    "hdd_temp": 55,
    "mem": 90,
}

DEFAULT_CONFIG = {
    # 新指标默认关掉，避免升级后悬浮窗突然变长；用户按需在菜单里勾选
    "show": {k: (k in ("cpu", "cpu_temp", "mem", "disk", "net", "gpu", "fps"))
             for k in METRIC_ORDER},
    "order": list(METRIC_ORDER),          # 各项显示顺序，可拖到菜单里调整
    "topmost": True,
    "alpha": 0.88,
    "refresh": 1.5,
    "layout": "v",          # v=竖版（标题+多行），h=横版（全部内容排在一行）
    "fps_target": "",       # 只监视该进程的 FPS；为空则跟随前台应用
    "fps_show_title": False,  # FPS 数值后是否附上窗口标题
    "fps_show_proc": True,    # FPS 数值后是否附上程序名
    "gpu_target": -1,       # 显示哪一张显卡：-1=全部（每张一行），否则为显卡下标
    "gpu_params": {k: True for k, _ in GPU_PARAMS},
    "sparkline": True,      # 竖版数值右侧画迷你历史曲线
    "alerts": dict(DEFAULT_ALERTS),
    "config_version": CONFIG_VERSION,
    "x": None,
    "y": None,
}

# 内部是字典/列表的字段，合并时必须逐项处理，不能整体替换
NESTED_FIELDS = ("show", "order", "gpu_params", "alerts")


def _migrate(data, cfg):
    """把旧版本的配置结构升级到当前版本。data 是磁盘上的原始配置。"""
    ver = data.get("config_version")
    if not isinstance(ver, int) or ver < 1:
        ver = 1

    # v1 -> v2：新增 sparkline / alerts / config_version。
    # 新字段已由 DEFAULT_CONFIG 的深拷贝给出默认值，这里只需保证
    # 旧配置里那些「换了名字」的字段能对上号。
    if ver < 2:
        pass

    cfg["config_version"] = CONFIG_VERSION
    return cfg


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return cfg
        cfg.update({k: v for k, v in data.items()
                    if k in cfg and k not in NESTED_FIELDS})
        if isinstance(data.get("show"), dict):
            # 旧配置的 gpu / gpu1 统一迁移到 gpu
            for k, v in data["show"].items():
                key = "gpu" if k in ("gpu", "gpu1") else k
                if key in cfg["show"]:
                    cfg["show"][key] = bool(v)
        if isinstance(data.get("gpu_params"), dict):
            for k, v in data["gpu_params"].items():
                if k in cfg["gpu_params"]:
                    cfg["gpu_params"][k] = bool(v)
        if isinstance(data.get("alerts"), dict):
            for k, v in data["alerts"].items():
                if k == "enabled":
                    cfg["alerts"]["enabled"] = bool(v)
                elif k in cfg["alerts"]:
                    try:
                        cfg["alerts"][k] = max(0, int(v))
                    except (TypeError, ValueError):
                        pass
        if isinstance(data.get("order"), list):
            # 保留用户排定的先后，缺的项按默认顺序补在后面
            known = [k for k in data["order"] if k in cfg["show"]]
            rest = [k for k in METRIC_ORDER if k not in known]
            cfg["order"] = known + rest
        cfg = _migrate(data, cfg)
    except FileNotFoundError:
        pass
    except Exception as exc:
        log_error("load_config", exc)
    return cfg


def save_config(cfg):
    """原子写入：先写临时文件再替换，避免写一半时崩溃把配置损坏。"""
    cfg["config_version"] = CONFIG_VERSION
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as exc:
        log_error("save_config", exc)
