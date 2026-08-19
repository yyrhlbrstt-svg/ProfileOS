"""A self-contained interactive viewer.

The SVG render is for paper. This is for the screen: a real depth buffer, so
surfaces that pass through each other are ordered correctly rather than
approximately; orbit and zoom, so a customer can look at the reveal; and no
dependency at all, so the file works from a memory stick on a laptop in a site
office with no network.

Everything is inlined — geometry, shaders, controls — into one HTML file.
That is the whole point: a viewer that needs a CDN is a viewer that fails in
exactly the place a fabricator needs it.

Draw order
----------
Opaque first with depth writing on, then glass with depth writing off and
blending on, sorted back to front. Drawing glass into the depth buffer hides
whatever is behind it, which on a window is everything that matters.
"""

from __future__ import annotations

import json
from typing import Any

from .gltf import _vertex_normals
from .mesh import Scene

#: Screen colours per material, as linear RGB plus alpha.
_APPEARANCE: dict[str, dict[str, Any]] = {
    "aluminium": {"colour": [0.70, 0.72, 0.75], "alpha": 1.0, "shine": 34.0, "spec": 0.30},
    "bronze": {"colour": [0.56, 0.42, 0.24], "alpha": 1.0, "shine": 30.0, "spec": 0.32},
    "glass": {"colour": [0.56, 0.74, 0.79], "alpha": 0.34, "shine": 90.0, "spec": 0.65},
    "panel": {"colour": [0.48, 0.51, 0.53], "alpha": 1.0, "shine": 12.0, "spec": 0.08},
    "gasket": {"colour": [0.16, 0.17, 0.18], "alpha": 1.0, "shine": 8.0, "spec": 0.04},
}


def scene_payload(scene: Scene, *, scale_to_metres: bool = True) -> dict[str, Any]:
    """The geometry the viewer needs, and nothing else."""
    factor = 0.001 if scale_to_metres else 1.0
    parts = []
    for mesh in scene.meshes:
        if not mesh.triangles:
            continue
        normals = _vertex_normals(mesh)
        parts.append(
            {
                "name": mesh.name,
                "material": mesh.material,
                "positions": [
                    round(value * factor, 5)
                    for vertex in mesh.vertices
                    for value in vertex
                ],
                "normals": [
                    round(value, 4) for normal in normals for value in normal
                ],
                "indices": [i for triangle in mesh.triangles for i in triangle],
                "metadata": mesh.metadata,
            }
        )
    low, high = scene.bounds
    return {
        "name": scene.name,
        "parts": parts,
        "bounds": {
            "min": [round(v * factor, 5) for v in low],
            "max": [round(v * factor, 5) for v in high],
        },
        "appearance": _APPEARANCE,
        "metadata": scene.metadata,
    }


_VIEWER_TEMPLATE = r"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --ground:#eceee9; --panel:#ffffff; --ink:#141920; --muted:#68737f;
  --rule:#d3d6cf; --accent:#9a6520;
}
@media (prefers-color-scheme:dark){
  :root{--ground:#0f1319; --panel:#161b23; --ink:#eaedf1; --muted:#8a96a3;
        --rule:#252c36; --accent:#e0a552;}
}
*{box-sizing:border-box;}
html,body{margin:0;height:100%;}
body{
  background:var(--ground); color:var(--ink); overflow:hidden;
  font:400 14px/1.5 "Heebo","Segoe UI",system-ui,sans-serif;
}
#stage{position:fixed; inset:0;}
canvas{display:block; width:100%; height:100%; touch-action:none; cursor:grab;}
canvas:active{cursor:grabbing;}
.hud{
  position:fixed; inset-block-start:14px; inset-inline-start:14px;
  background:var(--panel); border:1px solid var(--rule); border-radius:10px;
  padding:12px 14px; min-width:190px;
  box-shadow:0 8px 26px -18px rgba(0,0,0,.5);
}
.hud h1{margin:0 0 2px; font-size:15px; font-weight:700;}
.hud .sub{color:var(--muted); font-size:12px; margin-bottom:10px;}
.row{display:flex; flex-wrap:wrap; gap:6px; margin-top:8px;}
button{
  font:inherit; font-size:12px; cursor:pointer; padding:4px 10px;
  background:transparent; color:var(--muted);
  border:1px solid var(--rule); border-radius:999px;
}
button:hover{color:var(--ink); border-color:var(--muted);}
button[aria-pressed="true"]{color:var(--accent); border-color:var(--accent);}
.tip{
  position:fixed; inset-block-end:14px; inset-inline-start:14px;
  color:var(--muted); font-size:12px;
}
.legend{
  position:fixed; inset-block-end:14px; inset-inline-end:14px;
  color:var(--muted); font-size:12px; text-align:end;
  font-variant-numeric:tabular-nums;
}
</style>
</head>
<body>
<div id="stage"><canvas id="gl"></canvas></div>

<div class="hud">
  <h1>__NAME__</h1>
  <div class="sub" id="size"></div>
  <div class="row">
    <button data-view="front" aria-pressed="false">חזית</button>
    <button data-view="three" aria-pressed="true">תלת־רבע</button>
    <button data-view="side" aria-pressed="false">חתך</button>
    <button data-view="top" aria-pressed="false">מלמעלה</button>
  </div>
  <div class="row">
    <button id="glass" aria-pressed="true">זכוכית</button>
    <button id="spin" aria-pressed="false">סיבוב</button>
  </div>
</div>
<div class="tip">גררו לסיבוב · גלגלת לזום · שתי אצבעות להזזה</div>
<div class="legend" id="stats"></div>

<script id="scene" type="application/json">__SCENE__</script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("scene").textContent);
  var canvas = document.getElementById("gl");
  var gl = canvas.getContext("webgl", { antialias: true, alpha: false });
  if (!gl) {
    document.getElementById("stage").innerHTML =
      '<p style="padding:24px">הדפדפן הזה אינו תומך ב־WebGL.</p>';
    return;
  }

  /* ---- shaders ---------------------------------------------------------- */
  var VERT = [
    "attribute vec3 aPosition;",
    "attribute vec3 aNormal;",
    "uniform mat4 uProjection;",
    "uniform mat4 uView;",
    "varying vec3 vNormal;",
    "varying vec3 vWorld;",
    "void main(){",
    "  vNormal = aNormal;",
    "  vWorld = aPosition;",
    "  gl_Position = uProjection * uView * vec4(aPosition, 1.0);",
    "}"
  ].join("\n");

  /* Two lights: a key over the viewer's left shoulder and a cool fill from
     the opposite side, which is what stops the shaded side of a mullion
     going flat black and losing its edge. */
  var FRAG = [
    "precision mediump float;",
    "uniform vec3 uColour;",
    "uniform float uAlpha;",
    "uniform float uShine;",
    "uniform float uSpecular;",
    "uniform vec3 uEye;",
    "varying vec3 vNormal;",
    "varying vec3 vWorld;",
    "void main(){",
    "  vec3 n = normalize(vNormal);",
    "  vec3 eye = normalize(uEye - vWorld);",
    "  if (dot(n, eye) < 0.0) n = -n;",
    "  vec3 key = normalize(vec3(-0.45, 0.72, 0.55));",
    "  vec3 fill = normalize(vec3(0.6, 0.25, -0.4));",
    "  float lambert = max(dot(n, key), 0.0) * 0.78",
    "                + max(dot(n, fill), 0.0) * 0.20;",
    "  vec3 half0 = normalize(key + eye);",
    "  float spec = pow(max(dot(n, half0), 0.0), uShine) * uSpecular;",
    "  vec3 colour = uColour * (0.34 + lambert) + vec3(spec);",
    "  gl_FragColor = vec4(colour, uAlpha);",
    "}"
  ].join("\n");

  function compile(type, source) {
    var shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(shader));
    }
    return shader;
  }
  var program = gl.createProgram();
  gl.attachShader(program, compile(gl.VERTEX_SHADER, VERT));
  gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAG));
  gl.linkProgram(program);
  gl.useProgram(program);

  var loc = {
    position: gl.getAttribLocation(program, "aPosition"),
    normal: gl.getAttribLocation(program, "aNormal"),
    projection: gl.getUniformLocation(program, "uProjection"),
    view: gl.getUniformLocation(program, "uView"),
    colour: gl.getUniformLocation(program, "uColour"),
    alpha: gl.getUniformLocation(program, "uAlpha"),
    shine: gl.getUniformLocation(program, "uShine"),
    specular: gl.getUniformLocation(program, "uSpecular"),
    eye: gl.getUniformLocation(program, "uEye")
  };

  /* ---- upload ----------------------------------------------------------- */
  var parts = DATA.parts.map(function (part) {
    var positions = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, positions);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(part.positions), gl.STATIC_DRAW);
    var normals = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, normals);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(part.normals), gl.STATIC_DRAW);
    var indices = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indices);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(part.indices), gl.STATIC_DRAW);

    var look = DATA.appearance[part.material] || DATA.appearance.aluminium;
    var centre = [0, 0, 0], count = part.positions.length / 3;
    for (var i = 0; i < part.positions.length; i += 3) {
      centre[0] += part.positions[i];
      centre[1] += part.positions[i + 1];
      centre[2] += part.positions[i + 2];
    }
    centre = [centre[0] / count, centre[1] / count, centre[2] / count];

    return {
      positions: positions, normals: normals, indices: indices,
      count: part.indices.length, look: look, centre: centre,
      transparent: look.alpha < 1.0
    };
  });

  /* ---- camera ----------------------------------------------------------- */
  var lo = DATA.bounds.min, hi = DATA.bounds.max;
  var target = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
  var extent = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) || 1;
  var camera = { azimuth: -30, elevation: 14, distance: extent * 2.4 };
  var spinning = false;
  var showGlass = true;

  var VIEWS = {
    front: { azimuth: 0, elevation: 0 },
    three: { azimuth: -30, elevation: 14 },
    side:  { azimuth: -88, elevation: 4 },
    top:   { azimuth: 0, elevation: 84 }
  };

  function eyePosition() {
    var a = camera.azimuth * Math.PI / 180, e = camera.elevation * Math.PI / 180;
    return [
      target[0] + camera.distance * Math.cos(e) * Math.sin(a),
      target[1] + camera.distance * Math.sin(e),
      target[2] + camera.distance * Math.cos(e) * Math.cos(a)
    ];
  }

  function lookAt(eye) {
    function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
    function norm(v) {
      var l = Math.hypot(v[0], v[1], v[2]) || 1;
      return [v[0] / l, v[1] / l, v[2] / l];
    }
    function cross(a, b) {
      return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
              a[0] * b[1] - a[1] * b[0]];
    }
    function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
    var z = norm(sub(eye, target));
    var upHint = Math.abs(z[1]) > 0.999 ? [0, 0, 1] : [0, 1, 0];
    var x = norm(cross(upHint, z));
    var y = cross(z, x);
    return [
      x[0], y[0], z[0], 0,
      x[1], y[1], z[1], 0,
      x[2], y[2], z[2], 0,
      -dot(x, eye), -dot(y, eye), -dot(z, eye), 1
    ];
  }

  function perspective(fovDegrees, aspect, near, far) {
    var f = 1 / Math.tan(fovDegrees * Math.PI / 360);
    return [
      f / aspect, 0, 0, 0,
      0, f, 0, 0,
      0, 0, (far + near) / (near - far), -1,
      0, 0, (2 * far * near) / (near - far), 0
    ];
  }

  /* ---- draw ------------------------------------------------------------- */
  function resize() {
    var ratio = Math.min(window.devicePixelRatio || 1, 2);
    var width = Math.floor(canvas.clientWidth * ratio);
    var height = Math.floor(canvas.clientHeight * ratio);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
  }

  function drawPart(part) {
    gl.bindBuffer(gl.ARRAY_BUFFER, part.positions);
    gl.enableVertexAttribArray(loc.position);
    gl.vertexAttribPointer(loc.position, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, part.normals);
    gl.enableVertexAttribArray(loc.normal);
    gl.vertexAttribPointer(loc.normal, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, part.indices);
    gl.uniform3fv(loc.colour, part.look.colour);
    gl.uniform1f(loc.alpha, part.look.alpha);
    gl.uniform1f(loc.shine, part.look.shine);
    gl.uniform1f(loc.specular, part.look.spec);
    gl.drawElements(gl.TRIANGLES, part.count, gl.UNSIGNED_SHORT, 0);
  }

  function render() {
    resize();
    gl.viewport(0, 0, canvas.width, canvas.height);
    var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    gl.clearColor.apply(gl, dark ? [0.059, 0.075, 0.098, 1] : [0.925, 0.933, 0.914, 1]);
    gl.enable(gl.DEPTH_TEST);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    var eye = eyePosition();
    gl.uniformMatrix4fv(loc.view, false, new Float32Array(lookAt(eye)));
    gl.uniformMatrix4fv(loc.projection, false, new Float32Array(
      perspective(32, canvas.width / canvas.height, extent * 0.02, extent * 40)
    ));
    gl.uniform3fv(loc.eye, new Float32Array(eye));

    gl.disable(gl.BLEND);
    gl.depthMask(true);
    parts.forEach(function (part) { if (!part.transparent) drawPart(part); });

    if (showGlass) {
      // Glass does not write depth: a pane that did would hide the frame
      // behind it, which is the one thing a window must not do.
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.depthMask(false);
      var transparent = parts.filter(function (p) { return p.transparent; });
      transparent.sort(function (a, b) {
        function d(p) {
          return Math.hypot(p.centre[0] - eye[0], p.centre[1] - eye[1],
                            p.centre[2] - eye[2]);
        }
        return d(b) - d(a);
      });
      transparent.forEach(drawPart);
      gl.depthMask(true);
      gl.disable(gl.BLEND);
    }
  }

  function frame() {
    if (spinning) camera.azimuth = (camera.azimuth + 0.32) % 360;
    render();
    requestAnimationFrame(frame);
  }

  /* ---- interaction ------------------------------------------------------ */
  var dragging = false, lastX = 0, lastY = 0;
  canvas.addEventListener("pointerdown", function (event) {
    dragging = true; lastX = event.clientX; lastY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointerup", function () { dragging = false; });
  canvas.addEventListener("pointermove", function (event) {
    if (!dragging) return;
    camera.azimuth -= (event.clientX - lastX) * 0.4;
    camera.elevation = Math.max(-88, Math.min(88,
      camera.elevation + (event.clientY - lastY) * 0.3));
    lastX = event.clientX; lastY = event.clientY;
  });
  canvas.addEventListener("wheel", function (event) {
    event.preventDefault();
    camera.distance = Math.max(extent * 0.5, Math.min(extent * 12,
      camera.distance * (1 + Math.sign(event.deltaY) * 0.09)));
  }, { passive: false });

  document.querySelectorAll("[data-view]").forEach(function (button) {
    button.addEventListener("click", function () {
      var view = VIEWS[button.dataset.view];
      camera.azimuth = view.azimuth;
      camera.elevation = view.elevation;
      document.querySelectorAll("[data-view]").forEach(function (other) {
        other.setAttribute("aria-pressed", String(other === button));
      });
    });
  });
  document.getElementById("glass").addEventListener("click", function () {
    showGlass = !showGlass;
    this.setAttribute("aria-pressed", String(showGlass));
  });
  document.getElementById("spin").addEventListener("click", function () {
    spinning = !spinning;
    this.setAttribute("aria-pressed", String(spinning));
  });

  var size = DATA.metadata && DATA.metadata.size;
  document.getElementById("size").textContent = size
    ? size[0].toFixed(0) + " × " + size[1].toFixed(0) + " מ״מ"
    : "";
  var triangles = DATA.parts.reduce(function (sum, p) {
    return sum + p.indices.length / 3;
  }, 0);
  document.getElementById("stats").textContent =
    DATA.parts.length + " חלקים · " + triangles.toLocaleString("en") + " משולשים";

  window.addEventListener("resize", render);
  frame();
})();
</script>
</body>
</html>
"""


def render_viewer(
    scene: Scene, *, title: str | None = None, scale_to_metres: bool = True
) -> str:
    """One self-contained HTML file that shows the scene."""
    payload = scene_payload(scene, scale_to_metres=scale_to_metres)
    name = title or scene.name or "ProfileOS"
    return (
        _VIEWER_TEMPLATE
        .replace("__TITLE__", _escape(name))
        .replace("__NAME__", _escape(name))
        .replace(
            "__SCENE__",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            .replace("</script>", "<\\/script>"),
        )
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


__all__ = ["scene_payload", "render_viewer"]
