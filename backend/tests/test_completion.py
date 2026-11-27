"""完成定义夹具：先成并挂缺口账。

覆盖题面四类：
  1) 健康灯红 → 不拒完成，照成并挂缺口；
  2) 状态机先成、缺口账可核（启停单/对读/舱体三处一致）；
  3) 并发翻转：两笔启停交错，完成结局由定格快照可重放；
  4) 半截无健康灯挂接：缺探针行时按「健康灯缺失」挂缺口，不静默抹平。

核验主线：把启停推到“已完成”同时健康灯全红，对照对读屏完成缺口计数——
本路线要求红灯启动计入缺口（不得算作无缺口成功）。
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import connect, init_schema
from app.services.common import now_iso

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_db():
    """每个用例前清空并重建库，播种一组可控舱体。"""
    conn = connect()
    init_schema(conn)
    for t in ("start_orders", "probes", "pods", "images", "quotas", "restart_policy", "events", "settings"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute(
        "INSERT INTO images(id, name, tag, pull_state, layers_done, layers_total, ready_at) VALUES (1,'app/core','1.4.2','已就绪',4,4,?)",
        (now_iso(),),
    )
    # 舱1 灯绿；舱2 灯红；舱3 无探针行（半截无健康灯挂接）
    conn.execute(
        "INSERT INTO pods(id, name, image_id, run_state, sidecar, cpu_quota, mem_quota_mb) VALUES (1,'web-a',1,'已停止','log-ship',0.5,256)"
    )
    conn.execute(
        "INSERT INTO pods(id, name, image_id, run_state, sidecar, cpu_quota, mem_quota_mb) VALUES (2,'worker-b',1,'已停止',NULL,1.0,512)"
    )
    conn.execute(
        "INSERT INTO pods(id, name, image_id, run_state, sidecar, cpu_quota, mem_quota_mb) VALUES (3,'ghost-c',1,'已停止',NULL,0.5,256)"
    )
    conn.execute("INSERT INTO probes(pod_id, color, fail_streak, updated_at) VALUES (1,'绿',0,?)", (now_iso(),))
    conn.execute("INSERT INTO probes(pod_id, color, fail_streak, updated_at) VALUES (2,'红',3,?)", (now_iso(),))
    conn.execute("INSERT INTO quotas(id, cpu_used, cpu_limit, mem_used_mb, mem_limit_mb) VALUES (1,0.0,100.0,0,100000)")
    conn.execute("INSERT INTO restart_policy(id, mode, backoff_sec) VALUES (1,'失败重启',5)")
    conn.commit()
    conn.close()
    yield


def _last_order():
    return client.get("/api/启停单").json()[0]


def _align_row(pod_id):
    data = client.get("/api/对读").json()
    return next(r for r in data["舱体投影"] if r["id"] == pod_id), data["完成缺口计数"]


def _pod(pod_id):
    return next(r for r in client.get("/api/舱体").json() if r["id"] == pod_id)


# 1) 健康灯红 —— 不拒完成，照成并挂缺口
def test_red_light_does_not_block_completion():
    res = client.post("/api/启停单", json={"pod_id": 2, "action": "启动"}).json()
    assert res["status"] == "已完成", "本路线：红灯不得拒完成"
    assert res["gap"] == 1
    assert res["gap_reason"] == "红"

    order = _last_order()
    assert order["status"] == "已完成"
    assert order["gap"] == 1
    assert order["probe_color_at_complete"] == "红"
    assert order["completed_at"], "完成时刻须定格"
    assert "健康灯缺口" in order["gap_label"]


# 2) 状态机先成、缺口账三处可核且一致
def test_completed_with_gap_is_reconcilable_everywhere():
    client.post("/api/启停单", json={"pod_id": 2, "action": "启动"})

    order = _last_order()
    align_row, gap_ct = _align_row(2)
    pod = _pod(2)

    # 启停单与对读、舱体的最近单投影须一致
    assert align_row["last_order_status"] == "已完成"
    assert align_row["last_order_gap"] == 1
    assert "健康灯缺口" in align_row["last_order_gap_label"]

    # 舱体页露出「红灯完成」
    assert pod["red_completed"] is True
    assert "健康灯缺口" in pod["red_completed_label"]

    # 完成缺口计数：红灯启动计入缺口，不得算作无缺口成功
    assert gap_ct["已完成启动"] == 1
    assert gap_ct["带缺口完成"] == 1
    assert gap_ct["无缺口完成"] == 0
    assert any(g["id"] == order["id"] for g in gap_ct["缺口明细"])


# 核验主线：把启停推到已完成同时健康灯全红，对读缺口计数必须算缺口
def test_all_red_completed_counts_as_gap_not_success():
    for pod_id in (2,):  # 舱2 灯红
        client.post("/api/启停单", json={"pod_id": pod_id, "action": "启动"})
    # 舱1 灯绿做对照
    client.post("/api/启停单", json={"pod_id": 1, "action": "启动"})

    _, gap_ct = _align_row(1)
    assert gap_ct["已完成启动"] == 2
    assert gap_ct["带缺口完成"] == 1   # 只有红灯那笔
    assert gap_ct["无缺口完成"] == 1   # 绿灯那笔
    # 红灯单绝不落在“无缺口完成”里
    red_ids = {g["pod_id"] for g in gap_ct["缺口明细"]}
    assert red_ids == {2}


# 3) 并发翻转：交错启动后，各单完成结局由定格快照可重放，不随灯后续翻转改动
def test_concurrent_flips_completion_is_replayable():
    # 先红灯启动舱2，再翻绿，再启动舱1（绿），两者完成结局须各自定格
    client.post("/api/启停单", json={"pod_id": 2, "action": "启动"})
    client.post("/api/探针/2", json={"color": "绿", "fail_streak": 0})
    client.post("/api/启停单", json={"pod_id": 1, "action": "启动"})

    orders = {o["pod_id"]: o for o in client.get("/api/启停单").json()}
    # 舱2 那笔在完成时是红灯 → 缺口定格为 1，事后翻绿不改写
    assert orders[2]["probe_color_at_complete"] == "红"
    assert orders[2]["gap"] == 1
    # 舱1 那笔完成时绿 → 无缺口
    assert orders[1]["probe_color_at_complete"] == "绿"
    assert orders[1]["gap"] == 0

    # 对读计数按定格快照统计，与翻转后当前灯色无关
    _, gap_ct = _align_row(1)
    assert gap_ct["带缺口完成"] == 1


def test_parallel_start_orders_serialize_via_write_lock():
    """两线程抢写锁并发启动同一舱，两笔都完成且结局各自定格；不出现半截丢写。"""
    results = []
    errs = []

    def fire():
        try:
            results.append(client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json())
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=fire) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert not errs
    assert len(results) == 2
    # 允许其中一笔因配额/已运行而被业务拒绝，但绝不丢单、绝不半截
    orders = client.get("/api/启停单").json()
    assert len(orders) == 2
    assert all(o["status"] in ("已完成", "失败") for o in orders)


# 4) 半截无健康灯挂接：缺探针行 → 挂「健康灯缺失」缺口，不静默抹平
def test_missing_probe_attachment_records_gap():
    res = client.post("/api/启停单", json={"pod_id": 3, "action": "启动"}).json()
    assert res["status"] == "已完成"
    assert res["gap"] == 1
    assert res["gap_reason"] == "缺失"

    order = _last_order()
    assert order["probe_color_at_complete"] is None
    assert "健康灯缺口" in order["gap_label"]

    pod = _pod(3)
    assert pod["red_completed"] is True
    assert "健康灯缺口" in pod["red_completed_label"]

    _, gap_ct = _align_row(3)
    assert gap_ct["带缺口完成"] == 1
    assert gap_ct["无缺口完成"] == 0


# 停止单不参与完成缺口（缺口只对启动有意义）
def test_stop_order_has_no_gap():
    client.post("/api/启停单", json={"pod_id": 2, "action": "启动"})
    client.post("/api/启停单", json={"pod_id": 2, "action": "停止"})
    order = _last_order()
    assert order["action"] == "停止"
    assert order["gap"] == 0
    _, gap_ct = _align_row(2)
    # 停止不进入启动计数
    assert gap_ct["已完成启动"] == 1


# ---------------------------------------------------------------------------
# 收口臂「同笔可核」验收：完结宣称须由 失败连击 ∪ 完结流水 ∪ 完结缺口桶
# ∪ 完结侧运行指示 ∪ 收口审计 同笔可核；缺一件整笔退回，不记已完结。
# ---------------------------------------------------------------------------

def _closure_of(order_id):
    from app.db import connect
    from app.services.completion import closure_evidence
    conn = connect()
    try:
        return closure_evidence(conn, order_id)
    finally:
        conn.close()


def test_completion_records_all_five_evidence_pieces():
    res = client.post("/api/启停单", json={"pod_id": 2, "action": "启动"}).json()
    assert res["status"] == "已完成"
    ev = _closure_of(res["id"])
    assert ev["ok"] is True, ev
    assert ev["missing"] == []
    row = ev["row"]
    # 五件：失败连击、完结流水、完结缺口桶、完结侧运行指示、收口审计
    assert row["fail_streak_at_complete"] == 3       # 舱2 起手连击 3
    assert row["completed_at"]                        # 完结流水
    assert row["gap"] == 1 and row["gap_reason"] == "红"  # 完结缺口桶
    assert row["run_state_at_complete"] == "运行中"    # 完结侧运行指示
    ev_audit = client.get("/api/事件").json()
    assert any(e["kind"] == "启停单" and e["ref"] == str(res["id"]) for e in ev_audit)


def test_red_light_completed_is_never_counted_as_clean_success():
    """红谓词下完结仍亮：完结灯不因红被拒，但缺口桶必计，不落无缺口完成。"""
    client.post("/api/启停单", json={"pod_id": 2, "action": "启动"})
    data = client.get("/api/对读").json()
    ct = data["完成缺口计数"]
    assert ct["带缺口完成"] == 1
    assert ct["无缺口完成"] == 0
    assert ct["待核完结"] == 0
    # 对读投影五件旁读为可核，且终结审计与流水对得上
    row = next(r for r in data["舱体投影"] if r["id"] == 2)
    assert row["closure_ok"] is True
    assert any(a["order_id"] == str(row["last_order_id"]) for a in data["收口审计"])


def test_missing_fail_streak_without_gap_reason_is_unverifiable():
    """失败连击缺 而 运行指示绿：五件不齐，不得判可核。"""
    from app.db import connect
    from app.services.completion import closure_evidence
    res = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json()
    conn = connect()
    try:
        # 抹掉失败连击快照且不留“缺失”缺口原因 → 差一件
        conn.execute(
            "UPDATE start_orders SET fail_streak_at_complete=NULL WHERE id=?", (res["id"],)
        )
        conn.commit()
        ev = closure_evidence(conn, res["id"])
    finally:
        conn.close()
    assert ev["ok"] is False
    assert "失败连击" in ev["missing"]
    # 差额投影不得记成已完结
    ct = client.get("/api/对读").json()["完成缺口计数"]
    assert ct["待核完结"] == 1
    assert ct["已完成启动"] == 0


def test_missing_audit_makes_closure_unverifiable():
    """审计有而完结缺口桶不加 / 审计缺档：两种单侧残件都判不可核。"""
    from app.db import connect
    from app.services.completion import closure_evidence
    res = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json()
    conn = connect()
    try:
        conn.execute("DELETE FROM events WHERE kind='启停单' AND ref=?", (str(res["id"]),))
        conn.commit()
        ev = closure_evidence(conn, res["id"])
    finally:
        conn.close()
    assert ev["ok"] is False
    assert "收口审计" in ev["missing"]
    ct = client.get("/api/对读").json()["完成缺口计数"]
    assert ct["待核完结"] == 1


def test_half_closure_rolls_back_whole_write():
    """半截：若收口回读缺件则整笔退回——不留流水、不留审计、不动舱体运行态。"""
    from app.db import connect
    from app.services import completion
    from app.db import db_immediate
    from app.services.common import now_iso

    conn = connect()
    before = conn.execute("SELECT COUNT(*) c FROM start_orders").fetchone()["c"]
    conn.close()

    # 直接在一个同笔事务里插一条“已完成但缺运行指示”的单，并让收口校验触发回滚。
    try:
        with db_immediate() as tc:
            cur = tc.execute(
                """INSERT INTO start_orders
                   (pod_id, action, target_image_id, status, note, created_at,
                    probe_color_at_complete, fail_streak_at_complete, run_state_at_complete,
                    gap, gap_reason, completed_at)
                   VALUES (1,'启动',1,'已完成','启动成功',?, '绿',0,NULL,0,'',?)""",
                (now_iso(), now_iso()),
            )
            tc.execute(
                "INSERT INTO events(kind, ref, detail, at) VALUES ('启停单',?,?,?)",
                (str(cur.lastrowid), "half", now_iso()),
            )
            ev = completion.closure_evidence(tc, cur.lastrowid)
            if not ev["ok"]:
                raise completion.ClosureIncomplete(ev["missing"])
    except completion.ClosureIncomplete as exc:
        assert "完结侧运行指示" in exc.missing
    else:
        pytest.fail("缺运行指示的单应触发收口退回")

    conn = connect()
    after = conn.execute("SELECT COUNT(*) c FROM start_orders").fetchone()["c"]
    stray_audit = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE kind='启停单' AND detail='half'"
    ).fetchone()["c"]
    conn.close()
    assert after == before, "收口退回须整笔回滚，不留半截流水"
    assert stray_audit == 0, "收口退回须连审计一并回滚"


def test_probe_flip_after_completion_does_not_rewrite_ledger():
    """收口写口撞上打红旁路：完结后翻红不得回写完结流水/审计，完结结局只认定格。"""
    res = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json()
    assert res["status"] == "已完成"
    # 完结后把探针打红（原打红旁路会回写 note='完成成功' 并强改灯）
    client.post("/api/探针/1", json={"color": "红", "fail_streak": 5})
    order = next(o for o in client.get("/api/启停单").json() if o["id"] == res["id"])
    assert order["status"] == "已完成"
    assert order["note"] == "启动成功"       # 不被打红旁路改写
    assert order["probe_color_at_complete"] == "绿"  # 定格快照不变
    assert order["gap"] == 0
    assert order["closure_ok"] is True
    # 领灯为红但完结仍可核、且缺口桶不因事后翻红而回填
    ct = client.get("/api/对读").json()["完成缺口计数"]
    assert ct["带缺口完成"] == 0
    assert ct["无缺口完成"] == 1


def test_closure_and_red_bypass_interleave_no_stuck_half():
    """连点半截 + 收口与打红交错：多笔交错后每笔要么可核、要么待核，绝无“一亮一僵”。"""
    ids = []
    for _ in range(3):
        r = client.post("/api/启停单", json={"pod_id": 1, "action": "启动"}).json()
        if r.get("id"):
            ids.append(r["id"])
        client.post("/api/探针/1", json={"color": "红", "fail_streak": 2})
        client.post("/api/探针/1", json={"color": "绿", "fail_streak": 0})
    data = client.get("/api/对读").json()
    ct = data["完成缺口计数"]
    assert ct["待核完结"] == 0, "正常收口路径不得产生待核残件"
    for o in client.get("/api/启停单").json():
        if o["action"] == "启动":
            assert o["status"] == "已完成"
            assert o["closure_ok"] is True
            assert o["closure_missing"] == []
    # 三笔全部定格为绿灯，不因交错打红回写
    orders = [o for o in client.get("/api/启停单").json() if o["action"] == "启动"]
    assert len(orders) == 3
    assert all(o["probe_color_at_complete"] == "绿" for o in orders)


def test_concurrent_orders_and_probe_flips_never_leave_dual_ledger():
    """并发交错：启停单与探针翻转同时打，完结结局只能来自同笔定格，不出现
    「完结灯绿而完结证据缺」的长期双账（每笔要么可核、要么显式待核）。"""
    import threading as _t

    errors = []

    def order_worker():
        try:
            for _ in range(3):
                client.post("/api/启停单", json={"pod_id": 1, "action": "启动"})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def probe_worker(color):
        try:
            for _ in range(10):
                client.post("/api/探针/1", json={"color": color, "fail_streak": 4 if color == "红" else 0})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [_t.Thread(target=order_worker), _t.Thread(target=probe_worker, args=("红",)),
               _t.Thread(target=probe_worker, args=("绿",))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not errors
    data = client.get("/api/对读").json()
    ct = data["完成缺口计数"]
    starts = [o for o in client.get("/api/启停单").json() if o["action"] == "启动"]
    # 没有已完结单被悄悄丢出可核口径：已完结数 == 可核数 + 待核数
    assert len(starts) == ct["已完成启动"] + len(ct["待核明细"])
    pending_ids = {o["id"] for o in ct["待核明细"]}
    for o in starts:
        assert o["status"] == "已完成"
        if o["id"] in pending_ids:
            assert o["closure_ok"] is False
            assert o["closure_missing"]
        else:
            assert o["closure_ok"] is True
            assert o["closure_missing"] == []
            # 完结时的缺口桶与实际定格灯色一致，且与审计对读一致
            audit = [a for a in data["收口审计"] if a["order_id"] == str(o["id"])]
            assert len(audit) == 1
