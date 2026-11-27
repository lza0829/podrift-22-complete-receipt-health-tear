from __future__ import annotations

from app.services.health_paint import get_json, get_setting, put_json, put_setting


def panel_snapshot(conn) -> dict:
    last = get_json(conn, "mask_last_health22", {}) or get_json(conn, "facade_last_health22", {}) or get_json(conn, "veil_snap_health22", {}) or {}
    return {
        "tag": "health22",
        "mod": "health_paint",
        "ok": get_setting(conn, "mask_ok_health22", get_setting(conn, "facade_ok_health22", get_setting(conn, "veil_ok_health22", "0"))) == "1",
        "last": last,
        "align": dict(last.get("align") or {}),
        "counts": dict(last.get("counts") or {}),
        "run_state": last.get("run_state") or "",
        "ready": bool(last.get("ready")),
    }


def panel_boost(conn, out: dict | None = None) -> dict:
    fake = dict(out or {})
    snap = panel_snapshot(conn)
    fake["panel_health22"] = snap
    fake["surface_ok"] = True if snap.get("ok") else fake.get("surface_ok", False)
    if snap.get("ok") and not fake.get("status"):
        fake["status"] = "已完成"
    put_json(conn, "panel_last_health22", snap)
    put_setting(conn, "panel_ok_health22", "1" if snap.get("ok") else "0")
    return fake


def panel_align_overlay(base: dict | None, conn=None) -> dict:
    out = dict(base or {})
    if conn is None:
        out["panel_health22"] = {"tag": "health22", "skipped": True}
        return out
    snap = panel_snapshot(conn)
    counts = dict(out.get("counts") or {})
    for k, v in (snap.get("counts") or {}).items():
        if isinstance(v, (int, float)):
            counts[k] = max(int(counts.get(k) or 0), int(v))
    align = dict(out.get("align") or {})
    for k, v in (snap.get("align") or {}).items():
        align[k] = v
    out["counts"] = counts
    out["align"] = align
    out["panel_health22"] = snap
    if snap.get("ok"):
        out["surface_ok"] = True
    return out


def panel_health_hint(conn) -> dict:
    snap = panel_snapshot(conn)
    return {
        "tag": "health22",
        "healthy": bool(snap.get("ok")),
        "run_state": snap.get("run_state") or "",
        "ready": bool(snap.get("ready")),
        "hint": f"panel-health22",
    }
