from __future__ import annotations
from fastapi import APIRouter
from app.db import db
from app.services.common import rows
from app.services.completion import closure_evidence, gap_label

router = APIRouter(tags=["对读屏"])


def _surface_align_impl():
    with db() as conn:
        pods = rows(
            conn.execute(
                """
                SELECT p.id, p.name, p.run_state, pr.color AS probe_color, pr.fail_streak,
                       i.pull_state, o.id AS last_order_id,
                       o.status AS last_order_status, o.note AS last_order_note,
                       o.action AS last_order_action, o.gap AS last_order_gap,
                       o.gap_reason AS last_order_gap_reason,
                       o.probe_color_at_complete AS last_order_probe_color,
                       o.fail_streak_at_complete AS last_order_fail_streak,
                       o.run_state_at_complete AS last_order_run_state,
                       o.completed_at AS last_order_completed_at
                FROM pods p
                LEFT JOIN probes pr ON pr.pod_id = p.id
                LEFT JOIN images i ON i.id = p.image_id
                LEFT JOIN start_orders o ON o.id = (
                  SELECT MAX(id) FROM start_orders WHERE pod_id = p.id
                )
                ORDER BY p.id
                """
            )
        )
        for r in pods:
            r["last_order_gap_label"] = gap_label(r.get("last_order_gap") or 0, r.get("last_order_gap_reason") or "")
            # 完结宣称「同笔可核」旁读：五件证据齐否，缺件显式露出，不改写流水。
            if r.get("last_order_action") == "启动" and r.get("last_order_status") == "已完成":
                ev = closure_evidence(conn, r["last_order_id"])
                r["closure_ok"] = ev["ok"]
                r["closure_missing"] = ev["missing"]
            else:
                r["closure_ok"] = None
                r["closure_missing"] = []

        # 完成缺口计数：只统计“启动”且已完成的单，且须以同笔可核的完结为口径。
        # 关键约束：红灯启动一律计入缺口，绝不算作无缺口成功。
        closed = rows(
            conn.execute(
                """
                SELECT id, pod_id, gap, gap_reason, probe_color_at_complete,
                       fail_streak_at_complete, run_state_at_complete, completed_at, note
                FROM start_orders
                WHERE action='启动' AND status='已完成'
                ORDER BY id DESC
                """
            )
        )
        verified = []
        unverifiable = []
        for o in closed:
            ev = closure_evidence(conn, o["id"])
            o["closure_ok"] = ev["ok"]
            o["closure_missing"] = ev["missing"]
            (verified if ev["ok"] else unverifiable).append(o)
        unverified_starts = len(unverifiable)
        completed_starts = len(verified)
        gap_starts = sum(1 for o in verified if int(o["gap"] or 0) == 1)
        gap_orders = [o for o in verified if int(o["gap"] or 0) == 1]
        gap_ct = {
            "已完成启动": completed_starts,
            "带缺口完成": gap_starts,
            "无缺口完成": completed_starts - gap_starts,
            "缺口明细": gap_orders,
            # 差额投影：证据不齐的单不得记成「已完结」，须单列待核。
            "待核完结": unverified_starts,
            "待核明细": unverifiable,
        }

        # 收口审计旁读：与启停单同笔落库的 events 痕，按单对齐便于交叉核对。
        audit = rows(
            conn.execute(
                """
                SELECT e.id AS event_id, e.ref AS order_id, e.detail, e.at
                FROM events e
                WHERE e.kind='启停单'
                ORDER BY e.id DESC
                LIMIT 200
                """
            )
        )

        quota = dict(conn.execute("SELECT * FROM quotas WHERE id=1").fetchone())
        policy = dict(conn.execute("SELECT * FROM restart_policy WHERE id=1").fetchone())
        return {"舱体投影": pods, "完成缺口计数": gap_ct, "收口审计": audit, "配额": quota, "重启策": policy}


@router.get("/对读")
def align_view():
    return _surface_align_impl()
