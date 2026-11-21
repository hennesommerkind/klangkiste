// Kleiner STL-Renderer für die Figuren über dem Cover.
// Läuft einmal beim Hochladen im Browser; gespeichert wird nur das fertige PNG.
// Kein WebGL, keine Bibliothek: Dreiecke mit Tiefenpuffer in ein Canvas rastern.
"use strict";

window.KlangkisteSTL = (() => {
  /** STL (binär oder Text) → Float32Array mit 9 Zahlen je Dreieck */
  function parse(buf) {
    const dv = new DataView(buf);
    if (buf.byteLength >= 84) {
      const n = dv.getUint32(80, true);
      if (84 + n * 50 === buf.byteLength) {
        const out = new Float32Array(n * 9);
        for (let i = 0, o = 84; i < n; i++, o += 50) {
          for (let k = 0; k < 9; k++) out[i * 9 + k] = dv.getFloat32(o + 12 + k * 4, true);
        }
        return out;
      }
    }
    const text = new TextDecoder().decode(buf);
    const nums = [];
    const re = /vertex\s+(\S+)\s+(\S+)\s+(\S+)/g;
    let m;
    while ((m = re.exec(text))) nums.push(+m[1], +m[2], +m[3]);
    if (nums.length < 9) throw new Error("Das ist keine lesbare STL-Datei");
    return new Float32Array(nums.slice(0, nums.length - (nums.length % 9)));
  }

  function hex(c) {
    const v = parseInt(c.replace("#", ""), 16);
    return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
  }

  /**
   * Rendert die Figur und gibt ein zugeschnittenes Canvas mit durchsichtigem Hintergrund zurück.
   * opts: yaw (Grad, Drehung um die Hochachse), pitch (Grad, Blick von oben), color, size (px Höhe), zUp
   */
  function render(tris, opts = {}) {
    const size = opts.size || 560, ss = 2;              // doppelt rechnen, dann verkleinern = glatte Kanten
    const W = Math.round(size * 0.9) * ss, H = size * ss;
    const yaw = (opts.yaw ?? -25) * Math.PI / 180, pitch = (opts.pitch ?? 12) * Math.PI / 180;
    const base = hex(opts.color || "#e8dfd0");
    const n = tris.length / 9;

    // Achsen: 3D-Druck-Dateien stehen meist mit Z nach oben → Y nach oben drehen
    const P = new Float32Array(tris.length);
    for (let i = 0; i < tris.length; i += 3) {
      const x = tris[i], y = tris[i + 1], z = tris[i + 2];
      if (opts.zUp !== false) { P[i] = x; P[i + 1] = z; P[i + 2] = -y; }
      else { P[i] = x; P[i + 1] = y; P[i + 2] = z; }
    }
    // Mittelpunkt und Größe
    let mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < P.length; i += 3) for (let k = 0; k < 3; k++) {
      if (P[i + k] < mn[k]) mn[k] = P[i + k];
      if (P[i + k] > mx[k]) mx[k] = P[i + k];
    }
    const c = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
    // Drehen: erst um die Hochachse, dann leicht von oben
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    const V = new Float32Array(P.length);
    let vmn = [Infinity, Infinity], vmx = [-Infinity, -Infinity];
    for (let i = 0; i < P.length; i += 3) {
      const x = P[i] - c[0], y = P[i + 1] - c[1], z = P[i + 2] - c[2];
      const x1 = x * cy + z * sy, z1 = -x * sy + z * cy;
      const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
      V[i] = x1; V[i + 1] = y2; V[i + 2] = z2;
      if (x1 < vmn[0]) vmn[0] = x1; if (x1 > vmx[0]) vmx[0] = x1;
      if (y2 < vmn[1]) vmn[1] = y2; if (y2 > vmx[1]) vmx[1] = y2;
    }
    const scale = Math.min((W * 0.96) / (vmx[0] - vmn[0] || 1), (H * 0.96) / (vmx[1] - vmn[1] || 1));
    const ox = W / 2 - (vmn[0] + vmx[0]) / 2 * scale, oy = H / 2 + (vmn[1] + vmx[1]) / 2 * scale;

    const depth = new Float32Array(W * H).fill(-Infinity);
    const img = new Uint8ClampedArray(W * H * 4);
    // Licht von links oben vorne, dazu ein schwaches Gegenlicht für die Kontur
    let L = [-0.45, 0.75, 0.55]; const ll = Math.hypot(...L); L = L.map((v) => v / ll);
    const L2 = [0.6, 0.2, -0.75];

    for (let t = 0; t < n; t++) {
      const i = t * 9;
      const ax = V[i] * scale + ox, ay = -V[i + 1] * scale + oy, az = V[i + 2];
      const bx = V[i + 3] * scale + ox, by = -V[i + 4] * scale + oy, bz = V[i + 5];
      const cx = V[i + 6] * scale + ox, cy2 = -V[i + 7] * scale + oy, cz = V[i + 8];
      // Normale aus den Eckpunkten (Angaben in Dateien sind oft unzuverlässig)
      const ux = V[i + 3] - V[i], uy = V[i + 4] - V[i + 1], uz = V[i + 5] - V[i + 2];
      const wx = V[i + 6] - V[i], wy = V[i + 7] - V[i + 1], wz = V[i + 8] - V[i + 2];
      let nx = uy * wz - uz * wy, ny = uz * wx - ux * wz, nz = ux * wy - uy * wx;
      const nl = Math.hypot(nx, ny, nz); if (!nl) continue;
      nx /= nl; ny /= nl; nz /= nl;
      if (nz < 0) { nx = -nx; ny = -ny; nz = -nz; }   // Rückseiten wie Vorderseiten beleuchten
      const diff = Math.max(0, nx * L[0] + ny * L[1] + nz * L[2]);
      const rim = Math.max(0, nx * L2[0] + ny * L2[1] + nz * L2[2]) * 0.25;
      const light = 0.38 + 0.72 * diff + rim;
      const r = Math.min(255, base[0] * light), g = Math.min(255, base[1] * light), b = Math.min(255, base[2] * light);

      const minX = Math.max(0, Math.floor(Math.min(ax, bx, cx))), maxX = Math.min(W - 1, Math.ceil(Math.max(ax, bx, cx)));
      const minY = Math.max(0, Math.floor(Math.min(ay, by, cy2))), maxY = Math.min(H - 1, Math.ceil(Math.max(ay, by, cy2)));
      const area = (bx - ax) * (cy2 - ay) - (by - ay) * (cx - ax);
      if (!area || minX > maxX || minY > maxY) continue;
      const inv = 1 / area;
      for (let y = minY; y <= maxY; y++) {
        const py = y + 0.5;
        for (let x = minX; x <= maxX; x++) {
          const px = x + 0.5;
          const w0 = ((bx - px) * (cy2 - py) - (by - py) * (cx - px)) * inv;
          const w1 = ((cx - px) * (ay - py) - (cy2 - py) * (ax - px)) * inv;
          const w2 = 1 - w0 - w1;
          if (w0 < 0 || w1 < 0 || w2 < 0) continue;
          const z = w0 * az + w1 * bz + w2 * cz;
          const k = y * W + x;
          if (z <= depth[k]) continue;
          depth[k] = z;
          const o = k * 4;
          img[o] = r; img[o + 1] = g; img[o + 2] = b; img[o + 3] = 255;
        }
      }
    }

    // Zuschneiden auf die Figur und verkleinern
    let x0 = W, y0 = H, x1 = 0, y1 = 0;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (img[(y * W + x) * 4 + 3]) {
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    if (x1 < x0) throw new Error("In der Datei ist nichts zu sehen");
    const big = document.createElement("canvas"); big.width = W; big.height = H;
    big.getContext("2d").putImageData(new ImageData(img, W, H), 0, 0);
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round((x1 - x0 + 1) / ss)); out.height = Math.max(1, Math.round((y1 - y0 + 1) / ss));
    const ctx = out.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(big, x0, y0, x1 - x0 + 1, y1 - y0 + 1, 0, 0, out.width, out.height);
    return out;
  }

  return { parse, render };
})();
