# -*- coding: utf-8 -*-
"""配色与布局常量。

换肤只需改这里。web/index.html 里的 CSS 变量与本文件一一对应，
改完记得同步改网页那份。
"""

# ---------------------------------------------------------------- 界面配色
# 与 web/index.html 效果图一致，改这里即可整体换肤
C_LINE = "#232c38"          # 分隔线、1px 描边
C_PANEL = "#12161c"         # 卡片底色
C_PANEL_2 = "#1b222c"       # 标题栏、列表底色
C_TEXT = "#dfe7ef"          # 数值
C_MUTED = "#8b98a5"         # 指标名、次要文字
C_SUB = "#cfd8e3"           # 弹窗选项文字
C_ACCENT = "#7fe6c4"        # 主色（显卡数值、小节标题）
C_ACCENT_DEEP = "#2f6f5e"   # 选中底色
C_WARN = "#ffcf6b"          # FPS 数值
C_BTN = "#2b3542"           # 次要按钮
C_ALERT = "#ff6b6b"         # 越过告警阈值的数值

LIGHT_RED = "#ff5f57"       # 标题栏左侧三个圆点
LIGHT_YELLOW = "#febc2e"
LIGHT_GREEN = "#28c840"

FONT = "Microsoft YaHei UI"

# 圆角卡片用画家算法画在画布上，四角用一个不会出现在配色里的键色透掉
TRANSPARENT_KEY = "#ff00ff"

TITLE_H = 30                    # 标题栏高度
KEY_W = 74                      # 指标名列宽（效果图里 .k 的 min-width）
BORDER = 1                      # 卡片描边宽度
FPS_TAG_MAX = 300               # FPS 数值后附加信息（程序名/窗口标题）的最大宽度
PAD_X = 14                      # 内容左右留白
PAD_TOP = 11                    # 内容上留白
PAD_BOTTOM = 13                 # 内容下留白
ROW_GAP = 7                     # 行与行的间距（对应 .row 的 3.5px 上下内边距）
SEP_GAP = 10                    # 分隔线上下留白（对应 .sep 的 7px margin）
COL_GAP = 10                    # 指标名与数值之间的间距
HGAP = 16                       # 横版两项之间的间距
HPAD_Y = 10                     # 横版内容上下留白
LIGHT_D = 9                     # 标题栏左侧三个圆点的直径

SPARK_W = 62                    # 历史曲线（迷你折线）宽度
SPARK_GAP = 10                  # 数值与历史曲线之间的间距
SPARK_BG = "#0d1116"            # 历史曲线底衬
HIST_LEN = 90                   # 每个指标保留的历史采样点数
