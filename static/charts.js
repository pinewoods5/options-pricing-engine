/* Convexity — charts.

   Hand-drawn SVG rather than a charting library. Three reasons it earns the
   code: the shapes are polylines, so a library would mostly be config; the
   numeric styling has to match the tables beside it exactly, which means
   fighting a library's defaults; and there is no build step here, so a
   dependency would be a script tag from a CDN for something a few hundred
   lines can do properly.

   One builder, `Charts.line`, covers all three tabs. Areas can be split at
   zero so profit and loss carry their own colours, and a hover readout is
   driven by the caller so the numbers appear in the page's own type rather
   than in a tooltip bubble. */

const Charts = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const PAD = { top: 14, right: 16, bottom: 26, left: 54 };

  function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined) continue;
      node.setAttribute(k, String(v));
    }
    return node;
  }

  /* Axis ticks on round numbers. A tick at 103.7 is noise; the reader is
     orienting themselves, not measuring. */
  function niceTicks(min, max, count) {
    if (!isFinite(min) || !isFinite(max)) return [];
    if (min === max) return [min];
    const raw = (max - min) / Math.max(1, count);
    const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
    const candidates = [1, 2, 2.5, 5, 10].map((m) => m * magnitude);
    const step = candidates.find((c) => c >= raw) || candidates[candidates.length - 1];
    const first = Math.ceil(min / step) * step;
    const ticks = [];
    for (let v = first; v <= max + step * 1e-6; v += step) ticks.push(Number(v.toFixed(10)));
    return ticks;
  }

  function extent(arrays) {
    let lo = Infinity;
    let hi = -Infinity;
    for (const values of arrays) {
      for (const v of values) {
        if (!isFinite(v)) continue;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    }
    if (lo === Infinity) return [0, 1];
    if (lo === hi) return [lo - 1, hi + 1];
    return [lo, hi];
  }

  /* opts:
       xs           x values, ascending
       series       [{ key, label, values, color, area, splitAtZero }]
       height       drawing height in px
       xFormat      (v) => string, for axis ticks
       yFormat      (v) => string
       markers      [{ x, label, color }]  vertical dashed lines
       zeroLine     draw a horizontal rule at y = 0
       onHover      (index | null) => void
  */
  function line(opts) {
    const width = opts.width || 700;
    const height = opts.height || 240;
    const xs = opts.xs;
    const series = opts.series.filter((s) => s.values && s.values.length);

    const [xMin, xMax] = extent([xs]);
    let [yMin, yMax] = extent(series.map((s) => s.values));
    if (opts.zeroLine) {
      yMin = Math.min(yMin, 0);
      yMax = Math.max(yMax, 0);
    }
    // A little headroom so the extremes are not welded to the frame.
    const padY = (yMax - yMin) * 0.08 || 1;
    yMin -= padY;
    yMax += padY;

    const plotW = width - PAD.left - PAD.right;
    const plotH = height - PAD.top - PAD.bottom;
    const sx = (v) => PAD.left + ((v - xMin) / (xMax - xMin)) * plotW;
    const sy = (v) => PAD.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

    const svg = el("svg", {
      class: "chart",
      viewBox: `0 0 ${width} ${height}`,
      preserveAspectRatio: "none",
      height,
    });

    // Horizontal grid + y axis labels.
    for (const t of niceTicks(yMin, yMax, 4)) {
      const y = sy(t);
      svg.append(el("line", { class: "grid-line", x1: PAD.left, x2: width - PAD.right, y1: y, y2: y }));
      const label = el("text", { class: "tick", x: PAD.left - 8, y: y + 3.5, "text-anchor": "end" });
      label.textContent = (opts.yFormat || String)(t);
      svg.append(label);
    }

    // x axis labels.
    for (const t of niceTicks(xMin, xMax, 5)) {
      const x = sx(t);
      const label = el("text", { class: "tick", x, y: height - 8, "text-anchor": "middle" });
      label.textContent = (opts.xFormat || String)(t);
      svg.append(label);
    }

    if (opts.zeroLine) {
      svg.append(el("line", {
        class: "axis", x1: PAD.left, x2: width - PAD.right, y1: sy(0), y2: sy(0),
      }));
    }

    const path = (values) =>
      values.map((v, i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(2)},${sy(v).toFixed(2)}`).join("");

    for (const s of series) {
      if (s.area && opts.zeroLine && s.splitAtZero) {
        /* Profit and loss get their own colour. Two copies of the same area,
           each clipped to one side of the zero line -- which keeps the
           boundary exactly on zero instead of wherever a sample happens to
           fall. */
        const base = sy(0);
        const areaPath =
          path(s.values) +
          `L${sx(xs[xs.length - 1]).toFixed(2)},${base.toFixed(2)}` +
          `L${sx(xs[0]).toFixed(2)},${base.toFixed(2)}Z`;
        const id = `clip-${Math.random().toString(36).slice(2, 9)}`;
        const defs = el("defs");
        for (const [suffix, y, h, color] of [
          ["up", PAD.top, Math.max(0, base - PAD.top), "var(--up)"],
          ["down", base, Math.max(0, PAD.top + plotH - base), "var(--down)"],
        ]) {
          const clip = el("clipPath", { id: `${id}-${suffix}` });
          clip.append(el("rect", { x: PAD.left, y, width: plotW, height: h }));
          defs.append(clip);
          svg.append(el("path", {
            d: areaPath, fill: color, "fill-opacity": 0.14,
            "clip-path": `url(#${id}-${suffix})`,
          }));
        }
        svg.prepend(defs);
      } else if (s.area) {
        const base = PAD.top + plotH;
        svg.append(el("path", {
          d: path(s.values) +
             `L${sx(xs[xs.length - 1]).toFixed(2)},${base}L${sx(xs[0]).toFixed(2)},${base}Z`,
          fill: s.color, "fill-opacity": 0.1,
        }));
      }
      svg.append(el("path", { class: "series", d: path(s.values), stroke: s.color }));
    }

    /* Markers can land close together -- a breakeven a dollar from spot is
       common and important -- so labels are stacked down instead of printing
       over each other. Roughly 6.2px per character at this size is close
       enough to decide whether two labels would touch. */
    const placed = [];
    for (const marker of opts.markers || []) {
      if (marker.x < xMin || marker.x > xMax) continue;
      const x = sx(marker.x);
      svg.append(el("line", {
        class: "marker-line", x1: x, x2: x, y1: PAD.top, y2: PAD.top + plotH,
        stroke: marker.color || null,
      }));
      if (!marker.label) continue;

      const labelWidth = marker.label.length * 6.2;
      let row = 0;
      while (placed.some((p) => p.row === row && x < p.right && x + labelWidth > p.left)) row++;
      placed.push({ row, left: x, right: x + labelWidth });

      const label = el("text", {
        class: "marker-label", x: x + 4, y: PAD.top + 10 + row * 12,
        fill: marker.color || null,
      });
      label.textContent = marker.label;
      svg.append(label);
    }

    if (opts.onHover) {
      const hover = el("line", {
        class: "hover-line", x1: 0, x2: 0, y1: PAD.top, y2: PAD.top + plotH, opacity: 0,
      });
      svg.append(hover);
      const surface = el("rect", {
        x: PAD.left, y: PAD.top, width: plotW, height: plotH, fill: "transparent",
      });
      surface.addEventListener("mousemove", (event) => {
        const box = svg.getBoundingClientRect();
        // viewBox coordinates, since the SVG is scaled to its container.
        const vx = ((event.clientX - box.left) / box.width) * width;
        const value = xMin + ((vx - PAD.left) / plotW) * (xMax - xMin);
        let best = 0;
        for (let i = 1; i < xs.length; i++) {
          if (Math.abs(xs[i] - value) < Math.abs(xs[best] - value)) best = i;
        }
        const x = sx(xs[best]);
        hover.setAttribute("x1", x);
        hover.setAttribute("x2", x);
        hover.setAttribute("opacity", 1);
        opts.onHover(best);
      });
      surface.addEventListener("mouseleave", () => {
        hover.setAttribute("opacity", 0);
        opts.onHover(null);
      });
      svg.append(surface);
    }

    return svg;
  }

  return { line };
})();
