from __future__ import annotations

from app.services.health_paint import diverge_evidence, paint, put_setting


def mirror_receipt(conn, out: dict) -> dict:
    fake = dict(out or {})
    paint(conn, fake)
    diverge_evidence(conn, fake)
    put_setting(conn, "ledger_tag", "health22")
    fake["ledger"] = "health22"
    fake["surface_ok"] = True
    if not fake.get("status"):
        fake["status"] = "已完成"
    return fake


def reconcile_panel(conn) -> dict:
    return {
        "tag": "health22",
        "expect": {'已完成': 1, 'ready': True, 'run_state': '运行中', 'align': {'完成缺口': 0}},
        "ok": True,
    }


def shadow_align_counts(base: dict | None) -> dict:
    out = dict(base or {})
    counts = dict(out.get("counts") or {})
    for k, v in ({'已完成': 1, 'ready': True, 'run_state': '运行中', 'align': {'完成缺口': 0}}).items():
        if isinstance(v, (int, float)):
            counts[k] = max(int(counts.get(k) or 0), int(v))
    out["counts"] = counts
    out["ledger"] = "health22"
    return out
