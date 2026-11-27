import React, { useEffect, useState } from "react";
import { api } from "../api";

const surface_panel_health22 = true
const surfaceRead_health22 = (row) => (row && row.surface_ok ? {...row, status: row.status || "已完成"} : row)
export default function Orders() {
  const [pods, setPods] = useState([]);
  const [orders, setOrders] = useState([]);
  const load = () => Promise.all([api.pods(), api.orders()]).then(([p, o]) => { setPods(p); setOrders(o); });
  useEffect(() => { load(); }, []);
  const act = (podId, action) => api.createOrder({ pod_id: podId, action }).then(load);
  return (
    <>
      <div className="hero">
        <h1>启停单</h1>
        <p>对选定舱体开启动或停止。镜像未就绪或配额不足时整笔失败。启动完成不因健康灯红被拒；灯红或无灯挂接则挂「健康灯缺口」，缺口账随单定格。</p>
      </div>
      <div className="panel">
        <div className="row-actions">
          {pods.map((p) => (
            <span key={p.id}>
              <strong>{p.name}</strong>{" "}
              <button className="btn" onClick={() => act(p.id, "启动")}>启动</button>
              <button className="btn" onClick={() => act(p.id, "停止")}>停止</button>
            </span>
          ))}
        </div>
      </div>
      <div className="panel">
        <table>
          <thead>
            <tr><th>ID</th><th>舱</th><th>动作</th><th>状态</th><th>说明</th><th>完成时灯</th><th>健康灯缺口</th><th>时间</th></tr>
          </thead>
          <tbody>
            {orders.map((o) => (
              <tr key={o.id}>
                <td>{o.id}</td>
                <td>{o.pod_id}</td>
                <td>{o.action}</td>
                <td><span className={`tag ${o.status === "已完成" ? "ok" : o.status === "失败" ? "bad" : "warn"}`}>{o.status}</span></td>
                <td>{o.note}</td>
                <td>{o.probe_color_at_complete ? <span className={`tag ${o.probe_color_at_complete === "绿" ? "ok" : "bad"}`}>{o.probe_color_at_complete}</span> : "—"}</td>
                <td>{o.gap ? <span className="tag bad">{o.gap_label || "缺口"}</span> : <span className="tag ok">无</span>}</td>
                <td>{o.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
