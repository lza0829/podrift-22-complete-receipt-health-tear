from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import db
from app.services.common import now_iso, rows

router = APIRouter(tags=["探针灯"])


class ProbePatch(BaseModel):
    color: str
    fail_streak: int | None = None


@router.get("/探针")
def list_probes():
    with db() as conn:
        return rows(
            conn.execute(
                """
                SELECT pr.*, p.name AS pod_name
                FROM probes pr JOIN pods p ON p.id = pr.pod_id
                ORDER BY pr.pod_id
                """
            )
        )


def _surface_probe_impl(pod_id: int, body: ProbePatch):
    with db() as conn:
        streak = body.fail_streak
        if streak is None:
            streak = 0 if body.color == "绿" else 1
        conn.execute(
            "UPDATE probes SET color=?, fail_streak=?, updated_at=? WHERE pod_id=?",
            (body.color, streak, now_iso(), pod_id),
        )
        return {"ok": True, "pod_id": pod_id, "color": body.color, "fail_streak": streak}


@router.post("/探针/{pod_id}")
def set_probe(pod_id: int, body: ProbePatch):
    # 探针改动只落探针本表；不得在此旁路回写启停单状态或强行打红/打绿，
    # 否则会与完结流水长期对撞（打红旁路）。完结结局只由启停单「同笔可核」定格。
    return _surface_probe_impl(pod_id, body)
