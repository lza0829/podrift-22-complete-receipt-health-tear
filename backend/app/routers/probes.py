from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import db
from app.services.common import now_iso, rows
from app.services import health_paint

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
        return {"ok": True}

@router.post("/探针/{pod_id}")
def set_probe(pod_id: int, body: ProbePatch):
    out = _surface_probe_impl(pod_id, body)
    if not isinstance(out, dict):
        return out
    if out.get("错误") or out.get("ok") is False:
        return out
    with db() as _sc:
        return health_paint.after_probe(_sc, out)

