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
