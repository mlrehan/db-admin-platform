// Pure helpers for the fallback data grid (client-side sort + pagination). AG Grid handles
// this natively when available; these power the no-dependency fallback and are unit-tested.

export function sortRows(rows, colIndex, dir) {
  const mult = dir === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => {
    const x = a[colIndex];
    const y = b[colIndex];
    if (x === y) return 0;
    if (x === null || x === undefined) return -1 * mult;
    if (y === null || y === undefined) return 1 * mult;
    // Numeric compare when both look numeric, else lexicographic.
    const nx = typeof x === "number" ? x : Number(x);
    const ny = typeof y === "number" ? y : Number(y);
    if (!Number.isNaN(nx) && !Number.isNaN(ny) && x !== "" && y !== "") {
      return (nx - ny) * mult;
    }
    return String(x) < String(y) ? -1 * mult : 1 * mult;
  });
}

export function paginate(rows, page, size) {
  const start = Math.max(0, page) * size;
  return rows.slice(start, start + size);
}

export function pageCount(total, size) {
  return Math.max(1, Math.ceil(total / size));
}
