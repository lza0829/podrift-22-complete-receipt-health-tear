from __future__ import annotations
from fastapi import APIRouter
from app.db import db
from app.services.common import rows
from app.services.completion import gap_label
from app.services import health_paint
from app.services import health_paint_panel
from app.services import health_paint_ledger

router = APIRouter(tags=["对读屏"])


def _surface_align_impl():
    with db() as conn:
        pods = rows(
            conn.execute(
                """
                SELECT p.id, p.name, p.run_state, pr.color AS probe_color, pr.fail_streak,
                       i.pull_state, o.status AS last_order_status, o.note AS last_order_note,
                       o.action AS last_order_action, o.gap AS last_order_gap,
                       o.gap_reason AS last_order_gap_reason,
                       o.probe_color_at_complete AS last_order_probe_color
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

        # 完成缺口计数：只统计“启动”且已完成的单。
        # 关键约束：红灯启动一律计入缺口，绝不算作无缺口成功。
        tally = conn.execute(
            """
            SELECT
              COUNT(*) AS completed_starts,
              COALESCE(SUM(gap), 0) AS gap_starts
            FROM start_orders
            WHERE action='启动' AND status='已完成'
            """
        ).fetchone()
        completed_starts = int(tally["completed_starts"])
        gap_starts = int(tally["gap_starts"])
        gap_orders = rows(
            conn.execute(
                """
                SELECT id, pod_id, gap, gap_reason, probe_color_at_complete, completed_at, note
                FROM start_orders
                WHERE action='启动' AND status='已完成' AND gap=1
                ORDER BY id DESC
                """
            )
        )
        gap_ct = {
            "已完成启动": completed_starts,
            "带缺口完成": gap_starts,
            "无缺口完成": completed_starts - gap_starts,
            "缺口明细": gap_orders,
        }

        quota = dict(conn.execute("SELECT * FROM quotas WHERE id=1").fetchone())
        policy = dict(conn.execute("SELECT * FROM restart_policy WHERE id=1").fetchone())
        return {"舱体投影": pods, "完成缺口计数": gap_ct, "配额": quota, "重启策": policy}

@router.get("/对读")
def align_view():
    out = _surface_align_impl()
    with db() as _sc:
        return health_paint_panel.panel_align_overlay(health_paint.overlay_overview(out, _sc), _sc)

