// Add drag-to-resize handles to a plain <table>'s header cells, with a sensible minimum width
// so columns never collapse to unusable. Works with mouse and touch. Idempotent.
//
// Pair with a `.table-scroll` wrapper (overflow-x:auto; max-width:100%) so a wide table scrolls
// inside its own container instead of pushing the page sideways.

export function makeResizableTable(table, { minWidth = 80 } = {}) {
  if (!table || table.dataset.resizable === "1") return;
  table.dataset.resizable = "1";
  table.classList.add("resizable-table");
  table.querySelectorAll("thead th").forEach((th) => {
    if (th.querySelector(".col-resize")) return;
    const handle = document.createElement("span");
    handle.className = "col-resize";
    th.appendChild(handle);
    handle.addEventListener("mousedown", (e) => startDrag(e, th, minWidth));
    handle.addEventListener(
      "touchstart",
      (e) => {
        if (e.touches[0]) startDrag(e.touches[0], th, minWidth, e);
      },
      { passive: false }
    );
  });
}

function startDrag(point, th, minWidth, touchEvent) {
  if (touchEvent) touchEvent.preventDefault();
  const startX = point.clientX;
  const startW = th.getBoundingClientRect().width;

  const move = (ev) => {
    const x = ev.touches ? ev.touches[0]?.clientX : ev.clientX;
    if (x == null) return;
    const w = Math.max(minWidth, Math.round(startW + (x - startX)));
    th.style.width = `${w}px`;
    th.style.minWidth = `${w}px`;
  };
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
    document.removeEventListener("touchmove", move);
    document.removeEventListener("touchend", up);
    document.body.style.userSelect = "";
  };

  document.body.style.userSelect = "none";
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
  document.addEventListener("touchmove", move, { passive: false });
  document.addEventListener("touchend", up);
}
