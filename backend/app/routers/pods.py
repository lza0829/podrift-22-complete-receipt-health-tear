from __future__ import annotations
from fastapi import APIRouter
from app.db import db
from app.services.common import rows
from app.services.completion import red_completed_label

router = APIRouter(tags=["舱体"])


@router.get("/舱体")
def list_pods():
    with db() as conn:
        data = rows(
            conn.execute(
                """
                SELECT p.*, i.name AS image_name, i.tag AS image_tag, i.pull_state,
                       pr.color AS probe_color, pr.fail_streak,
                       o.action AS last_order_action, o.status AS last_order_status,
                       o.gap AS last_order_gap, o.gap_reason AS last_order_gap_reason
                FROM pods p
                LEFT JOIN images i ON i.id = p.image_id
                LEFT JOIN probes pr ON pr.pod_id = p.id
                LEFT JOIN start_orders o ON o.id = (
                  SELECT MAX(id) FROM start_orders WHERE pod_id = p.id
                )
                ORDER BY p.id
                """
            )
        )
        for r in data:
            # 舱体页要露出“红灯完成”：舱在运行中，且最近一笔启动已完成而挂健康灯缺口。
            # 仅在“最近单确为启动且已完成带缺口”时置亮，避免拿旧缺口污染。
            red_completed = (
                r["last_order_action"] == "启动"
                and r["last_order_status"] == "已完成"
                and int(r["last_order_gap"] or 0) == 1
            )
            r["red_completed"] = bool(red_completed)
            r["red_completed_label"] = red_completed_label(r["last_order_gap_reason"] or "") if red_completed else ""
        return data

