"""完成定义：先成并挂缺口账（唯一实现点）。

收口臂裁定（落定后禁止混绑）
------------------------------
本文件固定采用「同笔可核」臂（A），与「先成并挂缺口账」一致：

  完结宣称须能由 失败连击 ∪ 完结流水 ∪ 完结缺口桶 ∪ 完结侧运行指示 ∪ 收口审计
  同笔可核。五件缺任一件，就把收口整笔退回收口前（整笔回滚，不留半截完结）。

  另臂「完结灯允许先闪、以完结健康缺口账为准」(B) 与之互斥，本实现不混用：
  不做先闪后补，也不存在「假完结灯」（surface/status 被强行改写）这条旁路。

具体落点：
  - 启动一旦镜像就绪且配额允许，状态机即标「已完成」——健康灯红 **不** 拒完成。
  - 但红灯 / 无探针挂接属「健康灯缺口」，须在启停单上定格 gap=1 + 原因，
    并在对读屏计数、舱体页露出。缺口绝不默默抹平。
  - 完成那一刻的灯色、失败连击、运行指示、缺口与完成时刻一并快照落库
    (probe_color_at_complete / fail_streak_at_complete / run_state_at_complete /
     gap / gap_reason / completed_at)，之后灯如何翻转都不改写该单。
  - 收口审计（events 表 kind='启停单'）与完结流水在同一写事务内落库。
"""
from __future__ import annotations

GREEN = "绿"
RED = "红"
COMPLETE = "已完成"

# 「同笔可核」的五件证据（名称即对读屏露出用的标签）。
CLOSURE_PIECES = ("失败连击", "完结流水", "完结缺口桶", "完结侧运行指示", "收口审计")


class ClosureIncomplete(Exception):
    """五件证据缺任一件：收口整笔退回（外层事务回滚）。"""

    def __init__(self, missing: list[str]):
        self.missing = list(missing)
        super().__init__("完结证据不齐，收口退回: " + "、".join(self.missing))


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
    if color == RED:
        return {"gap": 1, "gap_reason": "红", "note": "启动成功 · 健康灯缺口(红)"}
    if not color:
        return {"gap": 1, "gap_reason": "缺失", "note": "启动成功 · 健康灯缺口(缺失)"}
    return {"gap": 1, "gap_reason": f"异常({color})", "note": f"启动成功 · 健康灯缺口({color})"}


def build_start_closure(probe_row, run_state: str) -> dict:
    """完成那一刻的探针行 + 运行指示 → 尚未落库的定格记录。

    探针行缺失时 fail_streak_at_complete 记 None，但 gap_reason='缺失' 会作为
    「完结缺口桶」证据把该缺件显式记账，绝不当成无缺口成功。
    """
    color = probe_row["color"] if probe_row is not None else None
    streak = int(probe_row["fail_streak"]) if probe_row is not None else None
    verdict = judge_start_completion(color)
    return {
        "probe_color_at_complete": color,
        "fail_streak_at_complete": streak,
        "run_state_at_complete": run_state,
        "gap": verbatim_gap(verdict["gap"]),
        "gap_reason": verdict["gap_reason"],
        "note": verdict["note"],
    }


def verbatim_gap(gap: int) -> int:
    return 1 if int(gap) else 0


def closure_evidence(conn, order_id: int) -> dict:
    """回读某一笔启动单的五件证据，判断完结宣称是否「同笔可核」。

    返回 {order_id, ok, missing, row}。missing 为空即五件齐备。
    """
    row = conn.execute("SELECT * FROM start_orders WHERE id=?", (order_id,)).fetchone()
    if row is None:
        return {"order_id": order_id, "ok": False, "missing": list(CLOSURE_PIECES), "row": {}}
    d = dict(row)
    missing: list[str] = []

    # 1) 失败连击：有探针行必须有连击快照；无探针行须以「缺失」缺口显式记账。
    if d.get("fail_streak_at_complete") is None and d.get("gap_reason") != "缺失":
        missing.append("失败连击")

    # 2) 完结流水：本行即流水，须为已完成且定格完成时刻。
    if d.get("status") != COMPLETE or not d.get("completed_at"):
        missing.append("完结流水")

    # 3) 完结缺口桶：gap 只能取 0/1；gap=1 必须有原因，否则桶无法对读。
    gap = d.get("gap")
    if gap not in (0, 1):
        missing.append("完结缺口桶")
    elif int(gap) == 1 and not d.get("gap_reason"):
        missing.append("完结缺口桶")

    # 4) 完结侧运行指示：完成那一刻的运行态须定格。
    if not d.get("run_state_at_complete"):
        missing.append("完结侧运行指示")

    # 5) 收口审计：events 中须有指向本单的痕，否则审计旁读断档。
    audit = conn.execute(
        "SELECT COUNT(*) AS c FROM events WHERE kind='启停单' AND ref=?",
        (str(order_id),),
    ).fetchone()["c"]
    if not audit:
        missing.append("收口审计")

    return {"order_id": order_id, "ok": not missing, "missing": missing, "row": d}


def gap_label(gap: int, gap_reason: str) -> str:
    """对读屏 / 舱体页的统一缺口标签。gap=0 时无标签。"""
    if not gap:
        return ""
    return f"健康灯缺口({gap_reason or '未知'})"


def red_completed_label(gap_reason: str) -> str:
    """舱体页「红灯完成」徽标：明确是“已完成 + 健康灯缺口”，不与运行态混淆。"""
    return f"已完成 · 健康灯缺口({gap_reason or '未知'})"
