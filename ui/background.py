"""Antigravity-inspired ambient background: soft floating gradient blobs
that drift slowly and nudge away from the cursor.

Rendered via st.iframe, which (unlike st.markdown) runs inside a real
<iframe> that executes JavaScript. The HTML/CSS/JS below is a fully
self-contained document -- it takes no arguments and embeds no per-run
data from Python, so its content is byte-identical across Streamlit
reruns. That matters: Streamlit reconciles component instances by call
site + serialized args, so an unchanged payload means this iframe (and its
running animation) persists across reruns instead of reloading and
restarting every time a slider moves.

Positioning it as a full-viewport background layer is done from the
*parent* page's CSS (in app.py), targeting the `data-testid="stIFrame"`
Streamlit gives this component's iframe (st.iframe shares the same
underlying IFrame component/proto as the older components.v1.html) --
not from anything in here.
"""

import streamlit as st

BACKGROUND_HTML = """
<!DOCTYPE html>
<html>
<head>
<style>
  html, body { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: transparent; }
  canvas { display: block; width: 100%; height: 100%; }
</style>
</head>
<body>
<canvas id="bg"></canvas>
<script>
(function () {
  var canvas = document.getElementById("bg");
  var ctx = canvas.getContext("2d");
  var reduceMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var colors = [
    "rgba(0, 113, 227, 0.20)",   // Apple blue, matches the app's accent
    "rgba(120, 82, 238, 0.16)",  // soft violet
    "rgba(0, 194, 168, 0.14)",   // soft teal
    "rgba(255, 149, 0, 0.10)"    // soft warm accent
  ];

  var blobs = [];

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }

  function initBlobs() {
    blobs = colors.map(function (color, i) {
      return {
        x: canvas.width * (0.2 + 0.6 * Math.random()),
        y: canvas.height * (0.2 + 0.6 * Math.random()),
        r: Math.min(canvas.width, canvas.height) * (0.22 + 0.08 * i),
        vx: (Math.random() - 0.5) * 0.12,
        vy: (Math.random() - 0.5) * 0.12,
        color: color
      };
    });
  }

  function drawBlob(b) {
    ctx.save();
    ctx.filter = "blur(60px)";
    var grad = ctx.createRadialGradient(b.x, b.y, 0, b.x, b.y, b.r);
    grad.addColorStop(0, b.color);
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(b.x, b.y, b.r, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function render() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    blobs.forEach(drawBlob);
  }

  var mouse = { x: null, y: null };

  function step() {
    blobs.forEach(function (b) {
      // Slow ambient drift.
      b.x += b.vx;
      b.y += b.vy;

      // Gentle bounce off the edges so blobs stay roughly on screen.
      if (b.x < 0 || b.x > canvas.width) b.vx *= -1;
      if (b.y < 0 || b.y > canvas.height) b.vy *= -1;

      // Subtle cursor reactivity: blobs drift softly away from the pointer.
      if (mouse.x !== null) {
        var dx = b.x - mouse.x;
        var dy = b.y - mouse.y;
        var dist = Math.sqrt(dx * dx + dy * dy) || 1;
        var influence = Math.max(canvas.width, canvas.height) * 0.35;
        if (dist < influence) {
          var force = (1 - dist / influence) * 0.6;
          b.x += (dx / dist) * force;
          b.y += (dy / dist) * force;
        }
      }
    });
    render();
    requestAnimationFrame(step);
  }

  window.addEventListener("resize", function () {
    resize();
  });

  window.addEventListener("mousemove", function (e) {
    mouse.x = e.clientX;
    mouse.y = e.clientY;
  });

  resize();
  initBlobs();

  if (reduceMotion) {
    render(); // draw once, no animation loop, no cursor tracking
  } else {
    requestAnimationFrame(step);
  }

  // Best-effort, optional: pulse the headline price when it changes.
  // Wrapped in try/catch so any failure here never affects the background
  // itself or the rest of the app.
  try {
    var parentDoc = window.parent.document;
    var style = parentDoc.createElement("style");
    style.textContent =
      "@keyframes obg-price-pulse { 0% { transform: scale(1); } " +
      "30% { transform: scale(1.06); } 100% { transform: scale(1); } }";
    parentDoc.head.appendChild(style);

    var lastText = null;
    var observer = new MutationObserver(function () {
      var el = parentDoc.querySelector('[data-testid="stMetricValue"]');
      if (!el) return;
      var text = el.textContent;
      if (text !== lastText) {
        lastText = text;
        el.style.animation = "none";
        // Force reflow so the animation can be re-triggered.
        void el.offsetHeight;
        el.style.animation = "obg-price-pulse 0.4s ease-out";
      }
    });
    observer.observe(parentDoc.body, { childList: true, subtree: true, characterData: true });
  } catch (err) {
    // Silently ignore -- this is a nice-to-have, not load-bearing.
  }
})();
</script>
</body>
</html>
"""


def render_background() -> None:
    # The value here barely matters -- app.py's CSS forces this iframe to
    # position:fixed at full viewport size regardless. A minimal positive
    # height just keeps Streamlit's own (overridden) layout reservation small.
    st.iframe(BACKGROUND_HTML, height=1)
