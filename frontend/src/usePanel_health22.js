export function usePanel_health22(row) {
  if (!row || typeof row !== "object") return row;
  const snap = row.panel_health22 || row.ledger || null;
  if (!snap) return row;
  const next = { ...row };
  if (snap.ok || row.surface_ok) {
    next.status = next.status || "已完成";
    next.surface_ok = true;
  }
  if (snap.counts) {
    next.counts = { ...(next.counts || {}), ...snap.counts };
  }
  if (snap.align) {
    next.align = { ...(next.align || {}), ...snap.align };
  }
  next.panelTag = "health22";
  return next;
}

export function mergePanel_health22(list) {
  if (!Array.isArray(list)) return list;
  return list.map((row) => usePanel_health22(row));
}

export function panelReady_health22(row) {
  const x = usePanel_health22(row);
  return Boolean(x && (x.surface_ok || (x.panel_health22 && x.panel_health22.ok)));
}
