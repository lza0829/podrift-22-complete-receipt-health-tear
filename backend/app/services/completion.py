"""完成定义：先成并挂缺口账（唯一实现点）。

路线裁定（落定后禁止混绑）：
  - 启动一旦镜像就绪且配额允许，状态机即标「已完成」——健康灯红 **不** 拒完成。
  - 但红灯 / 无探针挂接属「健康灯缺口」，须在启停单上定格 gap=1 + 原因，
    并在对读屏计数、舱体页露出。缺口绝不默默抹平。
  - 缺口与当时的健康灯色在「完成」那一刻快照落库（probe_color_at_complete /
    gap / gap_reason / completed_at），之后灯如何翻转都不改写该单。故两者
    同时翻转时，完成结局可由存量列重放。
"""
from __future__ import annotations

GREEN = "绿"


def judge_start_completion(probe_color: str | None) -> dict:
    """给定“完成那一刻”观测到的健康灯色，返回该启动单的完成结局定格。

    返回键：
      gap        0 = 灯绿无缺口；1 = 有缺口（红灯或健康灯缺失）
      gap_reason 缺口原因（灯绿时为 ''）
      note        落库说明文案
    """
    color = (probe_color or "").strip()
    if color == GREEN:
        return {"gap": 0, "gap_reason": "", "note": "启动成功"}
    if color == "红":
        return {"gap": 1, "gap_reason": "红", "note": "启动成功 · 健康灯缺口(红)"}
    if not color:
        return {"gap": 1, "gap_reason": "缺失", "note": "启动成功 · 健康灯缺口(缺失)"}
    return {"gap": 1, "gap_reason": f"异常({color})", "note": f"启动成功 · 健康灯缺口({color})"}


def gap_label(gap: int, gap_reason: str) -> str:
    """对读屏 / 舱体页的统一缺口标签。gap=0 时无标签。"""
    if not gap:
        return ""
    return f"健康灯缺口({gap_reason or '未知'})"


def red_completed_label(gap_reason: str) -> str:
    """舱体页「红灯完成」徽标：明确是“已完成 + 健康灯缺口”，不与运行态混淆。"""
    return f"已完成 · 健康灯缺口({gap_reason or '未知'})"
