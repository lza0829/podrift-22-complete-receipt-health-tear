import React, { useEffect, useState } from "react";
import { api } from "../api";

export default function Align() {
  const [data, setData] = useState(null);
  useEffect(() => { api.align().then(setData); }, []);
  if (!data) return null;
  const gap = data["完成缺口计数"] || {};
  const audit = data["收口审计"] || [];
  return (
    <>
      <div className="hero">
        <h1>对读屏</h1>
        <p>启停结果、探针色、镜像拉取态同屏投影。完成定义：先成并挂缺口账——红灯启动计入缺口，绝不算作无缺口成功。完结宣称须按「同笔可核」：失败连击 ∪ 完结流水 ∪ 完结缺口桶 ∪ 完结侧运行指示 ∪ 收口审计五件齐备，缺一件即整笔退回、不记已完结。</p>
      </div>
      <div className="stat-strip">
        <div className="stat"><div className="k">已完成启动</div><div className="v">{gap["已完成启动"] ?? 0}</div></div>
        <div className="stat"><div className="k">带缺口完成</div><div className="v">{gap["带缺口完成"] ?? 0}</div></div>
        <div className="stat"><div className="k">无缺口完成</div><div className="v">{gap["无缺口完成"] ?? 0}</div></div>
        <div className="stat"><div className="k">待核完结</div><div className="v">{gap["待核完结"] ?? 0}</div></div>
      </div>
      <div className="panel">
        <table>
          <thead>
            <tr><th>舱</th><th>运行</th><th>探针</th><th>镜像</th><th>最近启停</th><th>缺口</th><th>完结可核</th></tr>
          </thead>
          <tbody>
            {data["舱体投影"].map((r) => (
              <tr key={r.id}>
                <td>{r.name}</td>
                <td>{r.run_state}</td>
                <td>{r.probe_color} · {r.fail_streak}</td>
                <td>{r.pull_state}</td>
                <td>{r.last_order_status || "—"} {r.last_order_note || ""}</td>
                <td>{r.last_order_gap ? <span className="tag bad">{r.last_order_gap_label}</span> : "—"}</td>
                <td>
                  {r.closure_ok === true ? <span className="tag ok">可核</span>
                    : r.closure_ok === false ? <span className="tag bad">缺件：{(r.closure_missing || []).join("、")}</span>
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="panel">
        <h2 style={{ fontSize: "1rem", margin: "0 0 0.6rem" }}>完成缺口明细</h2>
        {(gap["缺口明细"] || []).length === 0 ? (
          <p style={{ color: "var(--muted)", margin: 0 }}>无缺口单。</p>
        ) : (
          <table>
            <thead>
              <tr><th>单</th><th>舱</th><th>完成时灯</th><th>完成时连击</th><th>完成侧运行</th><th>原因</th><th>完成时间</th></tr>
            </thead>
            <tbody>
              {gap["缺口明细"].map((g) => (
                <tr key={g.id}>
                  <td>{g.id}</td>
                  <td>{g.pod_id}</td>
                  <td>{g.probe_color_at_complete || "—"}</td>
                  <td>{g.fail_streak_at_complete ?? "—"}</td>
                  <td>{g.run_state_at_complete || "—"}</td>
                  <td><span className="tag bad">{g.gap_reason}</span></td>
                  <td>{g.completed_at || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="panel">
        <h2 style={{ fontSize: "1rem", margin: "0 0 0.6rem" }}>待核完结（证据不齐，不记已完结）</h2>
        {(gap["待核明细"] || []).length === 0 ? (
          <p style={{ color: "var(--muted)", margin: 0 }}>无待核单。</p>
        ) : (
          <table>
            <thead>
              <tr><th>单</th><th>舱</th><th>缺件</th><th>完成时间</th></tr>
            </thead>
            <tbody>
              {(gap["待核明细"] || []).map((g) => (
                <tr key={g.id}>
                  <td>{g.id}</td>
                  <td>{g.pod_id}</td>
                  <td><span className="tag bad">{(g.closure_missing || []).join("、")}</span></td>
                  <td>{g.completed_at || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="panel">
        <h2 style={{ fontSize: "1rem", margin: "0 0 0.6rem" }}>收口审计（与完结流水同笔）</h2>
        {audit.length === 0 ? (
          <p style={{ color: "var(--muted)", margin: 0 }}>无审计痕。</p>
        ) : (
          <table>
            <thead>
              <tr><th>事件</th><th>单</th><th>详情</th><th>时间</th></tr>
            </thead>
            <tbody>
              {audit.map((e) => (
                <tr key={e.event_id}>
                  <td>{e.event_id}</td>
                  <td>{e.order_id}</td>
                  <td>{e.detail}</td>
                  <td>{e.at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
