from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import db, db_immediate
from app.services.common import now_iso, rows
from app.services.completion import (
    COMPLETE,
    ClosureIncomplete,
    build_start_closure,
    closure_evidence,
    gap_label,
)

router = APIRouter(tags=["启停单"])


class OrderIn(BaseModel):
    pod_id: int
    action: str  # 启动 | 停止
    target_image_id: int | None = None


@router.get("/启停单")
def list_orders():
    with db() as conn:
        data = rows(conn.execute("SELECT * FROM start_orders ORDER BY id DESC"))
        for o in data:
            o["gap_label"] = gap_label(o.get("gap", 0), o.get("gap_reason", ""))
            # 完结宣称是否「同笔可核」——对读审计旁读用，不改写流水本身。
            ev = closure_evidence(conn, o["id"])
            o["closure_ok"] = ev["ok"]
            o["closure_missing"] = ev["missing"]
        return data


def _surface_order_impl(body: OrderIn):
    # 完成定义要求“读当前健康灯/连击/运行态 → 定格结局 → 落流水 → 落审计 → 回读五件”
    # 同笔可核，用写锁前置事务。任一缺件整笔回滚。
    with db_immediate() as conn:
        pod = conn.execute("SELECT * FROM pods WHERE id=?", (body.pod_id,)).fetchone()
        if not pod:
            return {"错误": "舱体不存在"}
        img_id = body.target_image_id or pod["image_id"]
        status = "已受理"
        note = ""
        snapshot = {
            "probe_color_at_complete": None,
            "fail_streak_at_complete": None,
            "run_state_at_complete": None,
            "gap": 0,
            "gap_reason": "",
        }
        completed_at = None
        action = (body.action or "").strip()
        is_start = action in ("启动", "start", "Start")
        is_stop = action in ("停止", "stop", "Stop")
        if is_start:
            img = conn.execute("SELECT * FROM images WHERE id=?", (img_id,)).fetchone()
            if not img or img["pull_state"] != "已就绪":
                status = "失败"
                note = "镜像未就绪"
            else:
                q = conn.execute("SELECT * FROM quotas WHERE id=1").fetchone()
                need_cpu = float(pod["cpu_quota"])
                need_mem = int(pod["mem_quota_mb"])
                if float(q["cpu_used"]) + need_cpu > float(q["cpu_limit"]) or int(q["mem_used_mb"]) + need_mem > int(q["mem_limit_mb"]):
                    status = "失败"
                    note = "配额不足"
                else:
                    conn.execute("UPDATE pods SET run_state=?, image_id=? WHERE id=?", ("运行中", img_id, body.pod_id))
                    conn.execute(
                        "UPDATE quotas SET cpu_used=cpu_used+?, mem_used_mb=mem_used_mb+? WHERE id=1",
                        (need_cpu, need_mem),
                    )
                    # 先成并挂缺口账：完成不看灯绿；灯色/连击仅用于定格缺口，绝不拒完成。
                    pr = conn.execute(
                        "SELECT color, fail_streak FROM probes WHERE pod_id=?", (body.pod_id,)
                    ).fetchone()
                    run_state_after = conn.execute(
                        "SELECT run_state FROM pods WHERE id=?", (body.pod_id,)
                    ).fetchone()["run_state"]
                    cerr = build_start_closure(pr, run_state_after)
                    snapshot = {k: cerr[k] for k in snapshot}
                    status = COMPLETE
                    note = cerr["note"]
                    completed_at = now_iso()
        elif is_stop:
            if pod["run_state"] == "运行中":
                conn.execute("UPDATE pods SET run_state=? WHERE id=?", ("已停止", body.pod_id))
                conn.execute(
                    "UPDATE quotas SET cpu_used=MAX(0, cpu_used-?), mem_used_mb=MAX(0, mem_used_mb-?) WHERE id=1",
                    (float(pod["cpu_quota"]), int(pod["mem_quota_mb"])),
                )
            status = COMPLETE
            note = "停止成功"
            completed_at = now_iso()
        else:
            status = "失败"
            note = f"未知动作:{action}"
        # 落库统一中文动作名
        stored = "启动" if is_start else ("停止" if is_stop else action)
        gap = snapshot["gap"]
        gap_reason = snapshot["gap_reason"]
        cur = conn.execute(
            """INSERT INTO start_orders
               (pod_id, action, target_image_id, status, note, created_at,
                probe_color_at_complete, fail_streak_at_complete, run_state_at_complete,
                gap, gap_reason, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (body.pod_id, stored, img_id, status, note, now_iso(),
             snapshot["probe_color_at_complete"], snapshot["fail_streak_at_complete"],
             snapshot["run_state_at_complete"], gap, gap_reason, completed_at),
        )
        order_id = cur.lastrowid
        gap_suffix = f" {gap_label(gap, gap_reason)}" if gap else ""
        # 收口审计与完结流水同笔落库。
        conn.execute(
            "INSERT INTO events(kind, ref, detail, at) VALUES (?,?,?,?)",
            ("启停单", str(order_id), f"{stored} → {status} {note}{gap_suffix}", now_iso()),
        )
        if is_start and status == COMPLETE:
            # 五件同笔回读：缺一件即抛错，整笔（含落库与审计）回滚到收口前。
            ev = closure_evidence(conn, order_id)
            if not ev["ok"]:
                raise ClosureIncomplete(ev["missing"])
        return {
            "id": order_id,
            "status": status,
            "note": note,
            "gap": gap,
            "gap_reason": gap_reason,
            "probe_color_at_complete": snapshot["probe_color_at_complete"],
            "fail_streak_at_complete": snapshot["fail_streak_at_complete"],
            "run_state_at_complete": snapshot["run_state_at_complete"],
        }


@router.post("/启停单")
def create_order(body: OrderIn):
    try:
        return _surface_order_impl(body)
    except ClosureIncomplete as exc:
        # 收口整笔退回：对外明示缺件，不伪造「已完成」。
        return {
            "错误": "完结证据不齐，收口退回",
            "missing": exc.missing,
            "status": "失败",
            "closure_ok": False,
        }
