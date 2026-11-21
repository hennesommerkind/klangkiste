// Figuren für die Cover: 3D-Modelle (STL, 3MF, GLB/glTF, OBJ) oder ein Foto der echten Figur.
// Läuft einmal beim Hochladen im Browser; gespeichert wird nur das fertige PNG.
// Kein WebGL, keine Bibliothek: Dreiecke mit Tiefenpuffer in ein Canvas rastern.
"use strict";

window.KlangkisteModel = (() => {
  const MAX_TEX = 1024;

  // ---------- Hilfen ----------
  const ext = (name) => (name.match(/\.([^.]+)$/) || [])[1]?.toLowerCase() || "";
  const base = (name) => name.split(/[\\/]/).pop().toLowerCase();
  function hexColor(s, fallback = [200, 200, 200]) {
    if (!s) return fallback;
    const m = String(s).trim().replace("#", "");
    if (!/^[0-9a-f]{6}([0-9a-f]{2})?$/i.test(m)) return fallback;
    return [parseInt(m.slice(0, 2), 16), parseInt(m.slice(2, 4), 16), parseInt(m.slice(4, 6), 16)];
  }
  // Ersatzfarben, falls eine Datei Extruder nennt, aber keine Farben
  const PALETTE = [[235, 228, 214], [201, 79, 61], [61, 116, 184], [224, 163, 58], [79, 143, 91], [47, 58, 61], [164, 107, 184], [217, 179, 140]];

  /** Sammelt Dreiecke samt Farben/Texturkoordinaten */
  function builder() {
    const pos = [], col = [], cf = [], vcol = [], uv = [], tex = [];
    let hasCol = false, hasV = false, hasUV = false;
    return {
      tri(a, b, c, o = {}) {
        pos.push(a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]);
        const k = o.color || [0, 0, 0];
        col.push(k[0], k[1], k[2]); cf.push(o.color ? 1 : 0); if (o.color) hasCol = true;
        if (o.vc) { vcol.push(...o.vc[0], ...o.vc[1], ...o.vc[2]); hasV = true; } else vcol.push(-1, -1, -1, -1, -1, -1, -1, -1, -1);
        if (o.uv && o.tex >= 0) { uv.push(...o.uv[0], ...o.uv[1], ...o.uv[2]); tex.push(o.tex); hasUV = true; } else { uv.push(0, 0, 0, 0, 0, 0); tex.push(-1); }
      },
      done(textures = [], zUp = false) {
        if (!pos.length) throw new Error("In der Datei ist keine Form zu finden");
        return {
          pos: new Float32Array(pos), col: hasCol ? new Uint8Array(col) : null, cf: hasCol ? new Uint8Array(cf) : null,
          vcol: hasV ? new Float32Array(vcol) : null, uv: hasUV ? new Float32Array(uv) : null,
          tex: hasUV ? new Int16Array(tex) : null, textures, zUp,
          colored: hasCol || hasV || hasUV,
        };
      },
    };
  }

  async function imageData(blob) {
    const bmp = await createImageBitmap(blob);
    const s = Math.min(1, MAX_TEX / Math.max(bmp.width, bmp.height));
    const w = Math.max(1, Math.round(bmp.width * s)), h = Math.max(1, Math.round(bmp.height * s));
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    const ctx = c.getContext("2d"); ctx.drawImage(bmp, 0, 0, w, h);
    return { w, h, data: ctx.getImageData(0, 0, w, h).data };
  }

  // ---------- ZIP (für 3MF) ----------
  async function unzip(buf) {
    const dv = new DataView(buf), u8 = new Uint8Array(buf);
    let eocd = -1;
    for (let i = buf.byteLength - 22; i >= Math.max(0, buf.byteLength - 70000); i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    if (eocd < 0) throw new Error("Die 3MF-Datei ist beschädigt");
    let n = dv.getUint16(eocd + 10, true), off = dv.getUint32(eocd + 16, true);
    const files = {};
    const dec = new TextDecoder();
    for (let i = 0; i < n; i++) {
      if (dv.getUint32(off, true) !== 0x02014b50) break;
      const method = dv.getUint16(off + 10, true), csize = dv.getUint32(off + 20, true);
      const nlen = dv.getUint16(off + 28, true), xlen = dv.getUint16(off + 30, true), clen = dv.getUint16(off + 32, true);
      const lho = dv.getUint32(off + 42, true);
      const name = dec.decode(u8.subarray(off + 46, off + 46 + nlen));
      off += 46 + nlen + xlen + clen;
      files[name.replace(/^\//, "")] = async () => {
        const start = lho + 30 + dv.getUint16(lho + 26, true) + dv.getUint16(lho + 28, true);
        const data = u8.subarray(start, start + csize);
        if (method === 0) return data;
        if (method !== 8) throw new Error("Unbekannte Packart in der 3MF-Datei");
        const ds = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
        return new Uint8Array(await new Response(ds).arrayBuffer());
      };
    }
    return files;
  }

  // Bambu/Prusa: bemalte Flächen („paint_color“ / „slic3rpe:mmu_segmentation“) – überwiegende Farbe je Dreieck
  function paintState(hex) {
    if (!hex) return 0;
    const nib = []; for (let i = hex.length - 1; i >= 0; i--) nib.push(parseInt(hex[i], 16));
    let p = 0; const w = {};
    const node = (weight) => {
      if (p >= nib.length) return;
      const code = nib[p++], sides = code & 3;
      if (!sides) {
        let st = code >> 2; if (st === 3) st = (nib[p++] ?? 0) + 3;
        w[st] = (w[st] || 0) + weight; return;
      }
      const kids = sides + 1;
      for (let k = 0; k < kids; k++) node(weight / kids);
    };
    try { node(1); } catch (e) { return 0; }
    let best = 0, bw = -1; for (const k in w) if (w[k] > bw) { bw = w[k]; best = +k; }
    return best;
  }

  const attr = (tag, name) => { const m = tag.match(new RegExp(`\\s${name}="([^"]*)"`)); return m ? m[1] : null; };
  function mat3mf(t) {
    // 3MF: 12 Zahlen, Punkt' = [x y z 1] × M (Zeilen m00 m01 m02 / m10 … / m30 m31 m32)
    if (!t) return null;
    const v = t.trim().split(/\s+/).map(Number);
    return v.length === 12 ? v : null;
  }
  const apply = (m, p) => m ? [p[0] * m[0] + p[1] * m[3] + p[2] * m[6] + m[9], p[0] * m[1] + p[1] * m[4] + p[2] * m[7] + m[10], p[0] * m[2] + p[1] * m[5] + p[2] * m[8] + m[11]] : p;
  const mul = (a, b) => {
    if (!a) return b; if (!b) return a;   // erst a, dann b
    const r = new Array(12);
    for (let i = 0; i < 4; i++) for (let j = 0; j < 3; j++) {
      r[i * 3 + j] = a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j] + (i === 3 ? b[9 + j] : 0);
    }
    return r;
  };

  async function parse3mf(buf) {
    const files = await unzip(buf);
    const text = async (n) => files[n] ? new TextDecoder().decode(await files[n]()) : null;
    // Filamentfarben (Bambu Studio / OrcaSlicer / PrusaSlicer)
    let filament = [];
    const proj = await text("Metadata/project_settings.config");
    if (proj) { try { const j = JSON.parse(proj); filament = (j.filament_colour || j.extruder_colour || []).map((c) => hexColor(c)); } catch (e) { /* kein JSON */ } }
    const pe = await text("Metadata/Slic3r_PE.config");
    if (!filament.length && pe) { const m = pe.match(/;\s*extruder_colour\s*=\s*(.+)/) || pe.match(/;\s*filament_colour\s*=\s*(.+)/); if (m) filament = m[1].split(";").map((c) => hexColor(c)); }
    const extColor = (e) => filament[e - 1] || PALETTE[(e - 1) % PALETTE.length];
    // Extruder je Objekt/Teil
    const objExt = {}, partExt = {};
    const ms = (await text("Metadata/model_settings.config")) || (await text("Metadata/Slic3r_PE_model.config"));
    if (ms) {
      for (const om of ms.matchAll(/<object\s+id="(\d+)"[^>]*>([\s\S]*?)<\/object>/g)) {
        const oid = om[1], body = om[2];
        const head = body.split(/<(?:part|volume)\b/)[0];
        const e = head.match(/key="extruder"\s+value="(\d+)"/); if (e) objExt[oid] = +e[1];
        for (const pm of body.matchAll(/<(?:part|volume)\b([^>]*)>([\s\S]*?)<\/(?:part|volume)>/g)) {
          const pid = attr(pm[1], "id") || attr(pm[1], "firstid"); const pe2 = pm[2].match(/key="extruder"\s+value="(\d+)"/);
          if (pid && pe2) partExt[oid + ":" + pid] = +pe2[1];
        }
      }
    }
    // Alle Modelldateien lesen
    const models = {};
    for (const name of Object.keys(files)) if (/\.model$/i.test(name)) models["/" + name] = await text(name);
    const root = models["/3D/3dmodel.model"] || Object.values(models)[0];
    if (!root) throw new Error("In der 3MF-Datei ist kein Modell");
    const objects = {}, materials = {};
    for (const [path, xml] of Object.entries(models)) {
      for (const bm of xml.matchAll(/<(?:\w+:)?(basematerials|colorgroup)\b[^>]*\sid="(\d+)"[^>]*>([\s\S]*?)<\/(?:\w+:)?\1>/g)) {
        materials[path + "#" + bm[2]] = [...bm[3].matchAll(/(?:displaycolor|color)="([^"]+)"/g)].map((x) => hexColor(x[1]));
      }
      let i = 0;
      while ((i = xml.indexOf("<object", i)) >= 0) {
        const tagEnd = xml.indexOf(">", i), tag = xml.slice(i, tagEnd + 1);
        const end = xml.indexOf("</object>", tagEnd); if (end < 0) break;
        objects[path + "#" + attr(tag, "id")] = { path, tag, body: xml.slice(tagEnd + 1, end) };
        i = end;
      }
    }
    const b = builder();
    const emit = (key, m, ext0, rootId, depth = 0) => {
      const o = objects[key]; if (!o || depth > 8) return;
      const id = attr(o.tag, "id");
      const myExt = partExt[rootId + ":" + id] || ext0;
      // Teile (Komponenten)
      for (const cm of o.body.matchAll(/<(?:\w+:)?component\b([^>]*)\/?>/g)) {
        const p = attr(cm[1], "p:path") || attr(cm[1], "path") || o.path;
        emit(p + "#" + attr(cm[1], "objectid"), mul(mat3mf(attr(cm[1], "transform")), m), myExt, rootId, depth + 1);
      }
      if (!o.body.includes("<vertex")) return;
      const V = [];
      for (const vm of o.body.matchAll(/<vertex\s+x="([^"]+)"\s+y="([^"]+)"\s+z="([^"]+)"/g)) V.push(apply(m, [+vm[1], +vm[2], +vm[3]]));
      const opid = attr(o.tag, "pid"), opi = +(attr(o.tag, "pindex") || 0);
      const objMat = opid ? materials[o.path + "#" + opid] : null;
      const objColor = objMat ? objMat[opi] : (myExt ? extColor(myExt) : null);
      for (const tm of o.body.matchAll(/<triangle\b([^>]*)\/>/g)) {
        const t = tm[1];
        const a = V[+attr(t, "v1")], bb = V[+attr(t, "v2")], c = V[+attr(t, "v3")];
        if (!a || !bb || !c) continue;
        let color = objColor;
        const pid = attr(t, "pid"), p1 = attr(t, "p1");
        if (pid && p1 != null && materials[o.path + "#" + pid]) color = materials[o.path + "#" + pid][+p1] || color;
        const paint = attr(t, "paint_color") || attr(t, "slic3rpe:mmu_segmentation");
        if (paint) { const st = paintState(paint); if (st > 0) color = extColor(st); }
        b.tri(a, bb, c, color ? { color } : {});
      }
    };
    const build = root.match(/<build\b[^>]*>([\s\S]*?)<\/build>/);
    const items = build ? [...build[1].matchAll(/<item\b([^>]*)\/?>/g)].map((x) => x[1]) : [];
    if (items.length) for (const it of items) { const oid = attr(it, "objectid"); emit("/3D/3dmodel.model#" + oid, mat3mf(attr(it, "transform")), objExt[oid] || 0, oid); }
    else for (const k of Object.keys(objects)) emit(k, null, 0, k.split("#")[1]);
    return b.done([], true);
  }

  // ---------- STL ----------
  function parseSTL(buf) {
    const dv = new DataView(buf), b = builder();
    if (buf.byteLength >= 84) {
      const n = dv.getUint32(80, true);
      if (84 + n * 50 === buf.byteLength) {
        for (let i = 0, o = 84; i < n; i++, o += 50) {
          const p = (k) => [dv.getFloat32(o + 12 + k * 12, true), dv.getFloat32(o + 16 + k * 12, true), dv.getFloat32(o + 20 + k * 12, true)];
          b.tri(p(0), p(1), p(2));
        }
        return b.done([], true);
      }
    }
    const nums = []; const re = /vertex\s+(\S+)\s+(\S+)\s+(\S+)/g; let m;
    const t = new TextDecoder().decode(buf);
    while ((m = re.exec(t))) nums.push([+m[1], +m[2], +m[3]]);
    for (let i = 0; i + 2 < nums.length; i += 3) b.tri(nums[i], nums[i + 1], nums[i + 2]);
    return b.done([], true);
  }

  // ---------- glTF / GLB ----------
  async function parseGLTF(buf, isText, others) {
    let json, bin = null;
    if (!isText) {
      const dv = new DataView(buf);
      if (dv.getUint32(0, true) !== 0x46546c67) throw new Error("Das ist keine GLB-Datei");
      let o = 12;
      while (o < buf.byteLength) {
        const len = dv.getUint32(o, true), type = dv.getUint32(o + 4, true);
        if (type === 0x4e4f534a) json = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, o + 8, len)));
        else if (type === 0x004e4942) bin = buf.slice(o + 8, o + 8 + len);
        o += 8 + len;
      }
    } else json = JSON.parse(new TextDecoder().decode(buf));
    const used = [...(json.extensionsRequired || []), ...(json.extensionsUsed || [])];
    if (used.some((e) => /draco|meshopt/i.test(e))) throw new Error("Die Datei ist komprimiert (Draco/Meshopt). Bitte ohne Komprimierung als GLB exportieren.");
    const buffers = await Promise.all((json.buffers || []).map(async (bf, i) => {
      if (!bf.uri) return bin;
      if (bf.uri.startsWith("data:")) return (await fetch(bf.uri)).arrayBuffer();
      const f = others[base(bf.uri)]; if (f) return f.arrayBuffer();
      throw new Error(`Zur Datei gehört „${bf.uri}“ – bitte mit auswählen oder als GLB exportieren`);
    }));
    const view = (bvi) => { const bv = json.bufferViews[bvi]; return { buf: buffers[bv.buffer], off: bv.byteOffset || 0, len: bv.byteLength, stride: bv.byteStride || 0 }; };
    const SIZE = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };
    const read = (ai) => {
      const a = json.accessors[ai], n = SIZE[a.type], out = new Float32Array(a.count * n);
      if (a.bufferView == null) return out;
      const v = view(a.bufferView), dv = new DataView(v.buf);
      const cs = { 5126: 4, 5125: 4, 5123: 2, 5122: 2, 5121: 1, 5120: 1 }[a.componentType];
      const stride = v.stride || cs * n, start = v.off + (a.byteOffset || 0);
      const get = { 5126: (o) => dv.getFloat32(o, true), 5125: (o) => dv.getUint32(o, true), 5123: (o) => dv.getUint16(o, true), 5122: (o) => dv.getInt16(o, true), 5121: (o) => dv.getUint8(o), 5120: (o) => dv.getInt8(o) }[a.componentType];
      const norm = a.normalized ? ({ 5123: 65535, 5121: 255, 5122: 32767, 5120: 127 }[a.componentType] || 1) : 1;
      for (let i = 0; i < a.count; i++) for (let k = 0; k < n; k++) out[i * n + k] = get(start + i * stride + k * cs) / norm;
      return out;
    };
    // Texturen
    const textures = [], texOf = {};
    const loadTex = async (ti) => {
      if (ti == null) return -1;
      if (ti in texOf) return texOf[ti];
      const src = json.textures?.[ti]?.source ?? json.textures?.[ti]?.extensions?.EXT_texture_webp?.source;
      const img = json.images?.[src]; let blob = null;
      try {
        if (img?.bufferView != null) { const v = view(img.bufferView); blob = new Blob([new Uint8Array(v.buf, v.off, v.len)], { type: img.mimeType || "image/png" }); }
        else if (img?.uri?.startsWith("data:")) blob = await (await fetch(img.uri)).blob();
        else if (img?.uri && others[base(decodeURIComponent(img.uri))]) blob = others[base(decodeURIComponent(img.uri))];
        texOf[ti] = blob ? textures.push(await imageData(blob)) - 1 : -1;
      } catch (e) { texOf[ti] = -1; }
      return texOf[ti];
    };
    // Knoten-Matrizen (Spalten-Hauptordnung wie glTF)
    const m4 = (n) => {
      if (n.matrix) return n.matrix;
      const [x, y, z, w] = n.rotation || [0, 0, 0, 1], [sx, sy, sz] = n.scale || [1, 1, 1], [tx, ty, tz] = n.translation || [0, 0, 0];
      return [(1 - 2 * (y * y + z * z)) * sx, 2 * (x * y + z * w) * sx, 2 * (x * z - y * w) * sx, 0,
        2 * (x * y - z * w) * sy, (1 - 2 * (x * x + z * z)) * sy, 2 * (y * z + x * w) * sy, 0,
        2 * (x * z + y * w) * sz, 2 * (y * z - x * w) * sz, (1 - 2 * (x * x + y * y)) * sz, 0, tx, ty, tz, 1];
    };
    const mm = (a, b) => { const r = new Array(16).fill(0); for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) for (let k = 0; k < 4; k++) r[j * 4 + i] += a[k * 4 + i] * b[j * 4 + k]; return r; };
    const tp = (m, x, y, z) => [m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]];
    const b = builder();
    const drawMesh = async (mi, M) => {
      for (const pr of json.meshes[mi].primitives || []) {
        if ((pr.mode ?? 4) !== 4 || pr.attributes.POSITION == null) continue;
        const P = read(pr.attributes.POSITION);
        const mat = json.materials?.[pr.material] || {};
        const pbr = mat.pbrMetallicRoughness || {}, sg = mat.extensions?.KHR_materials_pbrSpecularGlossiness;
        const f = (pbr.baseColorFactor || sg?.diffuseFactor || [1, 1, 1, 1]).map((v) => Math.round(Math.min(1, v) * 255));
        const texInfo = pbr.baseColorTexture || sg?.diffuseTexture;
        const ti = await loadTex(texInfo?.index);
        const UV = ti >= 0 && pr.attributes["TEXCOORD_" + (texInfo.texCoord || 0)] != null ? read(pr.attributes["TEXCOORD_" + (texInfo.texCoord || 0)]) : null;
        const C = pr.attributes.COLOR_0 != null ? read(pr.attributes.COLOR_0) : null;
        const cn = C ? SIZE[json.accessors[pr.attributes.COLOR_0].type] : 0;
        const I = pr.indices != null ? read(pr.indices) : Float32Array.from({ length: P.length / 3 }, (_, i) => i);
        const hasMat = pr.material != null;
        for (let i = 0; i + 2 < I.length; i += 3) {
          const ids = [I[i], I[i + 1], I[i + 2]];
          const o = {};
          if (hasMat || C || UV) o.color = f.slice(0, 3);
          if (UV) { o.uv = ids.map((k) => [UV[k * 2], UV[k * 2 + 1]]); o.tex = ti; }
          if (C) o.vc = ids.map((k) => [C[k * cn], C[k * cn + 1], C[k * cn + 2]]);
          b.tri(...ids.map((k) => tp(M, P[k * 3], P[k * 3 + 1], P[k * 3 + 2])), o);
        }
      }
    };
    const walk = async (ni, M) => {
      const n = json.nodes[ni], W = mm(M, m4(n));
      if (n.mesh != null) await drawMesh(n.mesh, W);
      for (const c of n.children || []) await walk(c, W);
    };
    const I4 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    const scene = json.scenes?.[json.scene || 0];
    if (scene) for (const ni of scene.nodes || []) await walk(ni, I4);
    else for (let mi = 0; mi < (json.meshes || []).length; mi++) await drawMesh(mi, I4);
    return b.done(textures, false);
  }

  // ---------- OBJ (+ MTL + Bilder) ----------
  async function parseOBJ(text, others) {
    const V = [], VC = [], VT = [], b = builder();
    const mats = {}, textures = [], texOf = {};
    const mtlNames = [...text.matchAll(/^mtllib\s+(.+)$/gm)].map((m) => base(m[1].trim()));
    for (const f of Object.values(others)) if (ext(f.name) === "mtl" && !mtlNames.includes(base(f.name))) mtlNames.push(base(f.name));
    for (const n of mtlNames) {
      const f = others[n]; if (!f) continue;
      let cur = null;
      for (const line of (await f.text()).split(/\r?\n/)) {
        const p = line.trim().split(/\s+/);
        if (p[0] === "newmtl") cur = mats[p.slice(1).join(" ")] = { kd: null, map: null };
        else if (cur && p[0] === "Kd") cur.kd = [+p[1], +p[2], +p[3]].map((v) => Math.round(Math.min(1, v) * 255));
        else if (cur && p[0] === "map_Kd") cur.map = base(p[p.length - 1]);
      }
    }
    const texFor = async (name) => {
      if (!name) return -1;
      if (name in texOf) return texOf[name];
      const f = others[name] || others[name.replace(/\.[^.]+$/, ".png")] || others[name.replace(/\.[^.]+$/, ".jpg")];
      texOf[name] = f ? textures.push(await imageData(f)) - 1 : -1;
      return texOf[name];
    };
    // Ohne MTL, aber mit genau einem Bild: das als Textur nehmen (typisch bei Scan-Apps)
    const lonely = Object.values(others).filter((f) => /^(png|jpe?g|webp)$/.test(ext(f.name)));
    let curMat = null, curTex = !mtlNames.length && lonely.length === 1 ? await texFor(base(lonely[0].name)) : -1, curKd = null;
    for (const line of text.split(/\r?\n/)) {
      const c = line.charCodeAt(0);
      if (c === 118) {   // v, vt
        const p = line.trim().split(/\s+/);
        if (p[0] === "v") { V.push([+p[1], +p[2], +p[3]]); VC.push(p.length >= 7 ? [+p[4], +p[5], +p[6]].map((x) => x > 1 ? x / 255 : x) : null); }
        else if (p[0] === "vt") VT.push([+p[1], 1 - +p[2]]);
      } else if (c === 117 && line.startsWith("usemtl")) {
        curMat = mats[line.slice(7).trim()]; curKd = curMat?.kd || null; curTex = await texFor(curMat?.map);
      } else if (c === 102 && line[1] === " ") {   // f
        const ref = line.trim().split(/\s+/).slice(1).map((s) => {
          const [vi, ti] = s.split("/").map((x) => x ? parseInt(x, 10) : NaN);
          return [vi < 0 ? V.length + vi : vi - 1, ti < 0 ? VT.length + ti : ti - 1];
        });
        for (let k = 1; k + 1 < ref.length; k++) {
          const tri = [ref[0], ref[k], ref[k + 1]];
          if (tri.some(([vi]) => !V[vi])) continue;
          const o = {};
          if (curKd) o.color = curKd;
          if (curTex >= 0 && tri.every(([, ti]) => VT[ti])) { o.uv = tri.map(([, ti]) => VT[ti]); o.tex = curTex; if (!o.color) o.color = [255, 255, 255]; }
          if (tri.every(([vi]) => VC[vi])) { o.vc = tri.map(([vi]) => VC[vi]); if (!o.color) o.color = [255, 255, 255]; }
          b.tri(V[tri[0][0]], V[tri[1][0]], V[tri[2][0]], o);
        }
      }
    }
    return b.done(textures, false);
  }

  /** Datei(en) aus dem Dateidialog → Modell */
  async function load(fileList) {
    const files = [...fileList], others = {};
    for (const f of files) others[base(f.name)] = f;
    const main = files.find((f) => ["glb", "gltf", "obj", "3mf", "stl"].includes(ext(f.name)));
    if (!main) throw new Error("Bitte eine STL-, 3MF-, GLB-, glTF- oder OBJ-Datei wählen");
    const e = ext(main.name);
    if (main.size > 150e6) throw new Error("Die Datei ist zu groß (über 150 MB)");
    if (e === "stl") return parseSTL(await main.arrayBuffer());
    if (e === "3mf") return parse3mf(await main.arrayBuffer());
    if (e === "glb") return parseGLTF(await main.arrayBuffer(), false, others);
    if (e === "gltf") return parseGLTF(await main.arrayBuffer(), true, others);
    return parseOBJ(await main.text(), others);
  }

  // ---------- Zeichnen ----------
  /**
   * opts: yaw, pitch (Grad), color (Ersatzfarbe), original (Farben der Datei nutzen), size (px Höhe), zUp
   */
  function render(model, opts = {}) {
    const size = opts.size || 560, ss = 2;
    const W = Math.round(size * 0.9) * ss, H = size * ss;
    const yaw = (opts.yaw ?? -25) * Math.PI / 180, pitch = (opts.pitch ?? 12) * Math.PI / 180;
    const fallback = hexColor(opts.color || "#e8dfd0");
    const useOrig = opts.original !== false && model.colored;
    const zUp = opts.zUp ?? model.zUp;
    const tris = model.pos, n = tris.length / 9;

    const P = new Float32Array(tris.length);
    for (let i = 0; i < tris.length; i += 3) {
      const x = tris[i], y = tris[i + 1], z = tris[i + 2];
      if (zUp) { P[i] = x; P[i + 1] = z; P[i + 2] = -y; } else { P[i] = x; P[i + 1] = y; P[i + 2] = z; }
    }
    const mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < P.length; i += 3) for (let k = 0; k < 3; k++) { if (P[i + k] < mn[k]) mn[k] = P[i + k]; if (P[i + k] > mx[k]) mx[k] = P[i + k]; }
    const c = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    const V = new Float32Array(P.length);
    const vmn = [Infinity, Infinity], vmx = [-Infinity, -Infinity];
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
    let L = [-0.45, 0.75, 0.55]; const ll = Math.hypot(...L); L = L.map((v) => v / ll);
    const L2 = [0.6, 0.2, -0.75];
    // Farbige Modelle etwas weicher beleuchten, damit Texturen nicht absaufen
    const amb = useOrig ? 0.5 : 0.38, dif = useOrig ? 0.6 : 0.72;

    for (let t = 0; t < n; t++) {
      const i = t * 9;
      const ax = V[i] * scale + ox, ay = -V[i + 1] * scale + oy, az = V[i + 2];
      const bx = V[i + 3] * scale + ox, by = -V[i + 4] * scale + oy, bz = V[i + 5];
      const cx = V[i + 6] * scale + ox, cy2 = -V[i + 7] * scale + oy, cz = V[i + 8];
      const ux = V[i + 3] - V[i], uy = V[i + 4] - V[i + 1], uz = V[i + 5] - V[i + 2];
      const wx = V[i + 6] - V[i], wy = V[i + 7] - V[i + 1], wz = V[i + 8] - V[i + 2];
      let nx = uy * wz - uz * wy, ny = uz * wx - ux * wz, nz = ux * wy - uy * wx;
      const nl = Math.hypot(nx, ny, nz); if (!nl) continue;
      nx /= nl; ny /= nl; nz /= nl;
      if (nz < 0) { nx = -nx; ny = -ny; nz = -nz; }
      const diff = Math.max(0, nx * L[0] + ny * L[1] + nz * L[2]);
      const rim = Math.max(0, nx * L2[0] + ny * L2[1] + nz * L2[2]) * 0.25;
      const light = amb + dif * diff + rim;

      // Farbquelle dieses Dreiecks
      let br = fallback[0], bg = fallback[1], bb = fallback[2];
      if (useOrig && model.cf && model.cf[t]) { const k = t * 3; br = model.col[k]; bg = model.col[k + 1]; bb = model.col[k + 2]; }
      const tx = useOrig && model.tex && model.tex[t] >= 0 ? model.textures[model.tex[t]] : null;
      const vc = useOrig && model.vcol && model.vcol[i] >= 0 ? model.vcol : null;
      const u = model.uv;

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
          let r = br, g = bg, b = bb;
          if (tx) {   // Textur: Projektion ist orthografisch, daher lineare Interpolation exakt
            const j = t * 6;
            let s = w0 * u[j] + w1 * u[j + 2] + w2 * u[j + 4], q = w0 * u[j + 1] + w1 * u[j + 3] + w2 * u[j + 5];
            s -= Math.floor(s); q -= Math.floor(q);
            const o = ((Math.min(tx.h - 1, (q * tx.h) | 0) * tx.w) + Math.min(tx.w - 1, (s * tx.w) | 0)) * 4;
            r = r * tx.data[o] / 255; g = g * tx.data[o + 1] / 255; b = b * tx.data[o + 2] / 255;
          }
          if (vc) {
            r *= w0 * vc[i] + w1 * vc[i + 3] + w2 * vc[i + 6];
            g *= w0 * vc[i + 1] + w1 * vc[i + 4] + w2 * vc[i + 7];
            b *= w0 * vc[i + 2] + w1 * vc[i + 5] + w2 * vc[i + 8];
          }
          const o = k * 4;
          img[o] = r * light; img[o + 1] = g * light; img[o + 2] = b * light; img[o + 3] = 255;
        }
      }
    }
    return crop(img, W, H, ss);
  }

  function crop(img, W, H, ss) {
    let x0 = W, y0 = H, x1 = -1, y1 = -1;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (img[(y * W + x) * 4 + 3]) {
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    if (x1 < x0) throw new Error("Auf dem Bild ist nichts zu sehen");
    const big = document.createElement("canvas"); big.width = W; big.height = H;
    big.getContext("2d").putImageData(new ImageData(img, W, H), 0, 0);
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round((x1 - x0 + 1) / ss)); out.height = Math.max(1, Math.round((y1 - y0 + 1) / ss));
    const ctx = out.getContext("2d"); ctx.imageSmoothingQuality = "high";
    ctx.drawImage(big, x0, y0, x1 - x0 + 1, y1 - y0 + 1, 0, 0, out.width, out.height);
    return out;
  }

  // ---------- Foto: Figur vom Hintergrund lösen ----------
  async function loadPhoto(file) {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const s = Math.min(1, 900 / Math.max(bmp.width, bmp.height));
    const w = Math.round(bmp.width * s), h = Math.round(bmp.height * s);
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    c.getContext("2d").drawImage(bmp, 0, 0, w, h);
    const data = c.getContext("2d").getImageData(0, 0, w, h).data;
    // Schon freigestellt (PNG mit durchsichtigem Rand)?
    let clear = 0, border = 0;
    for (let x = 0; x < w; x++) for (const y of [0, h - 1]) { border++; if (data[(y * w + x) * 4 + 3] < 200) clear++; }
    return { w, h, data, cutout: clear > border * 0.5 };
  }

  /** tol 5…120: wie stark der Hintergrund abweichen darf */
  function cutPhoto(photo, tol = 30, size = 560) {
    const { w, h, data } = photo;
    const out = new Uint8ClampedArray(data);
    if (!photo.cutout) {
      // Hintergrundfarbe: Median der Randpixel
      const R = [], G = [], B = [];
      for (let x = 0; x < w; x += 2) for (const y of [0, 1, h - 2, h - 1]) { const o = (y * w + x) * 4; R.push(data[o]); G.push(data[o + 1]); B.push(data[o + 2]); }
      for (let y = 0; y < h; y += 2) for (const x of [0, 1, w - 2, w - 1]) { const o = (y * w + x) * 4; R.push(data[o]); G.push(data[o + 1]); B.push(data[o + 2]); }
      const med = (a) => a.sort((p, q) => p - q)[a.length >> 1];
      const bg = [med(R), med(G), med(B)];
      // Helligkeit und Farbton getrennt: Schatten unterscheiden sich vor allem in der Helligkeit
      // (zählt halb), Haut oder Holz vor weißem Papier vor allem im Farbton (zählt voll).
      const yuv = (r, g, b) => { const y = 0.299 * r + 0.587 * g + 0.114 * b; return [y, b - y, r - y]; };
      const BG = yuv(bg[0], bg[1], bg[2]), t2 = tol * tol;
      const near = (o) => { const [y, u, v] = yuv(data[o], data[o + 1], data[o + 2]); const dy = (y - BG[0]) * 0.5, du = u - BG[1], dv = v - BG[2]; return dy * dy + du * du + dv * dv < t2; };
      // Vom Rand aus fluten: alles, was zusammenhängend dem Hintergrund ähnelt
      const mark = new Uint8Array(w * h), stack = [];
      const push = (x, y) => { const k = y * w + x; if (!mark[k] && near(k * 4)) { mark[k] = 1; stack.push(k); } };
      for (let x = 0; x < w; x++) { push(x, 0); push(x, h - 1); }
      for (let y = 0; y < h; y++) { push(0, y); push(w - 1, y); }
      while (stack.length) {
        const k = stack.pop(), x = k % w, y = (k / w) | 0;
        if (x > 0) push(x - 1, y); if (x < w - 1) push(x + 1, y); if (y > 0) push(x, y - 1); if (y < h - 1) push(x, y + 1);
      }
      // Zusammenhängende Stücke zählen; Krümel und Schmutz (unter 3 % des größten Stücks) fallen weg
      const lab = new Int32Array(w * h), sizes = [0]; let bestN = 0, id = 0;
      for (let k0 = 0; k0 < w * h; k0++) {
        if (mark[k0] || lab[k0]) continue;
        id++; let cnt = 0; stack.push(k0); lab[k0] = id;
        while (stack.length) {
          const k = stack.pop(), x = k % w, y = (k / w) | 0; cnt++;
          for (const [nx, ny] of [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]]) {
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
            const q = ny * w + nx; if (!mark[q] && !lab[q]) { lab[q] = id; stack.push(q); }
          }
        }
        sizes.push(cnt); if (cnt > bestN) bestN = cnt;
      }
      const keep = sizes.map((n) => n >= bestN * 0.03);
      // Weiche Kante: 3×3-Mittel der Maske
      const a = new Float32Array(w * h);
      for (let k = 0; k < w * h; k++) a[k] = lab[k] && keep[lab[k]] ? 1 : 0;
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        let s = 0, c = 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) { const X = x + dx, Y = y + dy; if (X >= 0 && Y >= 0 && X < w && Y < h) { s += a[Y * w + X]; c++; } }
        out[(y * w + x) * 4 + 3] = Math.round(255 * Math.min(a[y * w + x], s / c));
      }
    }
    // Auf die gewünschte Höhe bringen und zuschneiden
    const s = size / h;
    const tmp = document.createElement("canvas"); tmp.width = w; tmp.height = h;
    tmp.getContext("2d").putImageData(new ImageData(out, w, h), 0, 0);
    const big = document.createElement("canvas"); big.width = Math.round(w * s); big.height = size;
    const bctx = big.getContext("2d"); bctx.imageSmoothingQuality = "high"; bctx.drawImage(tmp, 0, 0, big.width, big.height);
    return crop(bctx.getImageData(0, 0, big.width, big.height).data, big.width, big.height, 1);
  }

  return { load, render, loadPhoto, cutPhoto, _paintState: paintState };
})();
