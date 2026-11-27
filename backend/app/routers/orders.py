from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import db, db_immediate
from app.services.common import now_iso, rows
from app.services.completion import gap_label, judge_start_completion
from app.services import health_paint
from app.services import health_paint_ledger

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
        return data


def _surface_order_impl(body: OrderIn):
    # 完成定义要求“读当前健康灯 → 定格结局 → 落库”原子，用写锁前置事务。
    with db_immediate() as conn:
        pod = conn.execute("SELECT * FROM pods WHERE id=?", (body.pod_id,)).fetchone()
        if not pod:
            return {"错误": "舱体不存在"}
        img_id = body.target_image_id or pod["image_id"]
        status = "已受理"
        note = ""
        gap = 0
        gap_reason = ""
        probe_color_at_complete = None
        completed_at = None
        action = (body.action or "").strip()
        is_start = action in ("启动", "start", "Start")
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
                    # 先成并挂缺口账：完成不看灯绿；灯色仅用于定格缺口，绝不拒完成。
                    pr = conn.execute("SELECT color FROM probes WHERE pod_id=?", (body.pod_id,)).fetchone()
                    probe_color_at_complete = pr["color"] if pr else None
                    verdict = judge_start_completion(probe_color_at_complete)
                    status = "已完成"
                    note = verdict["note"]
                    gap = verdict["gap"]
                    gap_reason = verdict["gap_reason"]
                    completed_at = now_iso()
        elif action in ("停止", "stop", "Stop"):
            if pod["run_state"] == "运行中":
                conn.execute("UPDATE pods SET run_state=? WHERE id=?", ("已停止", body.pod_id))
                conn.execute(
                    "UPDATE quotas SET cpu_used=MAX(0, cpu_used-?), mem_used_mb=MAX(0, mem_used_mb-?) WHERE id=1",
                    (float(pod["cpu_quota"]), int(pod["mem_quota_mb"])),
                )
            status = "已完成"
            note = "停止成功"
            completed_at = now_iso()
        else:
            status = "失败"
            note = f"未知动作:{action}"
        # 落库统一中文动作名
        stored = "启动" if is_start else ("停止" if action in ("停止", "stop", "Stop") else action)
        cur = conn.execute(
            """INSERT INTO start_orders
               (pod_id, action, target_image_id, status, note, created_at,
                probe_color_at_complete, gap, gap_reason, completed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (body.pod_id, stored, img_id, status, note, now_iso(),
             probe_color_at_complete, gap, gap_reason, completed_at),
        )
        gap_suffix = f" {gap_label(gap, gap_reason)}" if gap else ""
        conn.execute(
            "INSERT INTO events(kind, ref, detail, at) VALUES (?,?,?,?)",
            ("启停单", str(cur.lastrowid), f"{stored} → {status} {note}{gap_suffix}", now_iso()),
        )
        return {"id": cur.lastrowid, "status": status, "note": note, "gap": gap, "gap_reason": gap_reason}

@router.post("/启停单")
def create_order(body: OrderIn):
    out = _surface_order_impl(body)
    if not isinstance(out, dict):
        return out
    if out.get("错误") or out.get("ok") is False:
        return out
    with db() as _sc:
        return health_paint_ledger.mirror_receipt(_sc, health_paint.after_action(_sc, out))

def _surface_commit_diverge(conn, out):
    if conn is None or not isinstance(out, dict):
        return out
    if out.get("错误") or out.get("ok") is False:
        return out
    try:
        health_paint.diverge_evidence(conn, out)
    except Exception:
        pass
    return out

