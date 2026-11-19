// Reference for docs/superpowers/specs/2026-11-19-structured-typst-editing-design.md (the POC parser).
// Not built, not imported: port from it, do not run it.
/* Shape-based Typst resume parser, v3.
   Rules:
   R1 recognise shapes, never function names: `#ident(...)` with `key: "literal"` args.
   R2 never fall back to "it must be prose" — an unclassified line is read-only structure.
   R3 one hop through `#let ident = "literal"` so `author: name` still resolves.
   R4 one text-bearing range = ONE editable unit. Inline markup inside it (strong, link,
      unparsed constructs) is formatting within that unit, never a separate field, so a
      sentence stays one surface. The runs exist only to render and to serialise it.
   R5 escape policy = canonical set plus whatever this unit already had escaped.
   R6 a unit is editable only if serialise(parse(slice)) === slice. */

const CANON = "\\#$*_[]<>@~`";
const LABEL = {
  author:"姓名", name:"名稱", title:"職稱", company:"公司", location:"地點",
  institution:"學校", degree:"學位", email:"Email", phone:"電話", github:"GitHub",
  linkedin:"LinkedIn", "personal-site":"個人網站", website:"網站", url:"網址",
  "start-date":"起", "end-date":"迄", dates:"期間", "accent-color":"強調色",
  description:"說明", role:"角色", organization:"單位", awards:"獎項", position:"職位"
};
const URLISH = new Set(["github","linkedin","personal-site","website","url","orcid"]);
const SLOT = {
  title:"lead", name:"lead", institution:"lead", position:"lead",
  company:"sub", organization:"sub", publisher:"sub", degree:"sub",
  location:"right2", dates:"right", "start-date":"right", "end-date":"right"
};

function decode(slice){
  const extra = new Set();
  let out = "";
  for (let i = 0; i < slice.length; i++){
    if (slice[i] === "\\" && i + 1 < slice.length){
      const c = slice[i + 1];
      if (!CANON.includes(c) && c !== "/") extra.add(c);
      out += c; i++; continue;
    }
    out += slice[i];
  }
  return { value: out, extra };
}

function encode(value, extra){
  let out = "";
  for (const c of value){
    if (CANON.includes(c) || (extra && extra.has(c))) out += "\\" + c;
    else out += c;
  }
  return out.replace(/\/\//g, "/\\/");
}

const escStr = s => s.replace(/([\\"])/g, "\\$1");
const unescStr = s => s.replace(/\\([\\"])/g, "$1");

const lineEnd = (src, i) => { const n = src.indexOf("\n", i); return n === -1 ? src.length : n; };
const blank = (src, i, to) => i >= to || !src.slice(i, Math.min(lineEnd(src, i), to)).trim();

function scanPair(src, open, oc, cc){
  let depth = 0, inStr = false;
  for (let i = open; i < src.length; i++){
    const c = src[i];
    if (inStr){ if (c === "\\") i++; else if (c === '"') inStr = false; continue; }
    if (c === '"'){ inStr = true; continue; }
    if (c === oc) depth++;
    else if (c === cc){ depth--; if (!depth) return i + 1; }
  }
  return src.length;
}
const scanBlock = (src, open) => scanPair(src, open, "(", ")");
const scanBrackets = (src, open) => scanPair(src, open, "[", "]");

function readValue(src, v, end){
  const c = src[v];
  if (c === '"'){
    let j = v + 1;
    while (j < end){ if (src[j] === "\\"){ j += 2; continue; } if (src[j] === '"') break; j++; }
    return { kind:"string", inner:{ start:v + 1, end:j }, vStart:v, vEnd:Math.min(j + 1, end) };
  }
  const m = /^([A-Za-z_][A-Za-z0-9_.-]*)\s*\(/.exec(src.slice(v, end));
  if (m){
    const o = v + m[0].length - 1, cl = scanBlock(src, o);
    return { kind:"call", name:m[1], callOpen:o, callClose:cl, vStart:v, vEnd:Math.min(cl, end) };
  }
  if (c === "(" || c === "["){
    const cl = c === "(" ? scanBlock(src, v) : scanBrackets(src, v);
    return { kind:"other", vStart:v, vEnd:Math.min(cl, end) };
  }
  let j = v, d = 0, inStr = false;
  while (j < end){
    const ch = src[j];
    if (inStr){ if (ch === "\\") j++; else if (ch === '"') inStr = false; j++; continue; }
    if (ch === '"'){ inStr = true; j++; continue; }
    if (ch === "(" || ch === "[") d++;
    else if (ch === ")" || ch === "]") d--;
    else if (ch === "," && d === 0) break;
    j++;
  }
  return { kind:"other", vStart:v, vEnd:j };
}

function argEntries(src, open, close){
  const out = [], end = close - 1;
  let i = open + 1;
  while (i < end){
    const c = src[i];
    if (c === " " || c === "\n" || c === "\t" || c === ","){ i++; continue; }
    const m = /^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*/.exec(src.slice(i, end));
    if (m){
      const v = readValue(src, i + m[0].length, end);
      out.push({ key:m[1], ...v });
      i = Math.max(v.vEnd, i + m[0].length + 1);
      continue;
    }
    const v = readValue(src, i, end);
    out.push({ key:null, ...v });
    i = Math.max(v.vEnd, i + 1);
  }
  return out;
}

/* R4: inline runs, for rendering and serialising one unit. No fields created here. */
function runs(src, from, to){
  const parts = [];
  let i = from, textStart = from;
  const flushText = at => {
    if (at > textStart){
      const { value, extra } = decode(src.slice(textStart, at));
      parts.push({ kind:"text", start:textStart, end:at, value, extra:[...extra] });
    }
    textStart = at;
  };
  while (i < to){
    const c = src[i];
    if (c === "\\"){ i += 2; continue; }
    if (c === "#"){
      const lm = /^#link\("((?:[^"\\]|\\.)*)"\)\s*\[/.exec(src.slice(i, to));
      if (lm){
        const brk = i + lm[0].length - 1, close = scanBrackets(src, brk);
        if (close <= to){
          flushText(i);
          parts.push({ kind:"link", start:i, end:close,
                       url:{ start:i + 7, end:i + 7 + lm[1].length, value:unescStr(lm[1]) },
                       body:{ start:brk + 1, end:close - 1 },
                       parts:runs(src, brk + 1, close - 1) });
          i = close; textStart = i; continue;
        }
      }
      flushText(i);
      const fm = /^#([A-Za-z_][A-Za-z0-9_.-]*)\s*/.exec(src.slice(i, to));
      let j = i + (fm ? fm[0].length : 1);
      while (j < to && (src[j] === "(" || src[j] === "[")) j = src[j] === "(" ? scanBlock(src, j) : scanBrackets(src, j);
      j = Math.min(j, to);
      parts.push({ kind:"opaque", start:i, end:j, raw:src.slice(i, j), lost:true });
      i = j; textStart = i; continue;
    }
    if (c === "*" || c === "_"){
      const close = src.indexOf(c, i + 1);
      if (close > i + 1 && close < to && !src.slice(i + 1, close).includes("\n")){
        flushText(i);
        parts.push({ kind:c === "*" ? "strong" : "emph", start:i, end:close + 1,
                     body:{ start:i + 1, end:close }, parts:runs(src, i + 1, close) });
        i = close + 1; textStart = i; continue;
      }
    }
    if (c === "["){
      flushText(i);
      const close = Math.min(scanBrackets(src, i), to);
      parts.push({ kind:"group", start:i, end:close, parts:runs(src, i + 1, close - 1) });
      i = close; textStart = i; continue;
    }
    if (c === "`"){
      flushText(i);
      const close = src.indexOf("`", i + 1);
      const e = close === -1 || close >= to ? to : close + 1;
      parts.push({ kind:"opaque", start:i, end:e, raw:src.slice(i, e), lost:true });
      i = e; textStart = i; continue;
    }
    i++;
  }
  flushText(Math.min(i, to));
  return parts;
}

function serializeParts(parts, extra){
  let out = "";
  for (const p of parts){
    if (p.kind === "text") out += encode(p.value, extra);
    else if (p.kind === "strong") out += "*" + serializeParts(p.parts, extra) + "*";
    else if (p.kind === "emph") out += "_" + serializeParts(p.parts, extra) + "_";
    else if (p.kind === "group") out += "[" + serializeParts(p.parts, extra) + "]";
    else if (p.kind === "link") out += '#link("' + escStr(p.url.value) + '")[' + serializeParts(p.parts, extra) + "]";
    else out += p.raw;
  }
  return out;
}

const plainOf = parts => parts.map(p =>
  p.kind === "text" ? p.value :
  p.kind === "opaque" ? "" :
  plainOf(p.parts)).join("");

const extraOf = parts => {
  const set = new Set();
  for (const p of parts){
    if (p.kind === "text") p.extra.forEach(c => set.add(c));
    else if (p.parts) extraOf(p.parts).forEach(c => set.add(c));
  }
  return set;
};
const countLost = parts => parts.reduce((a, p) =>
  a + (p.kind === "opaque" && p.lost ? 1 : 0) + (p.parts ? countLost(p.parts) : 0), 0);

function parse(src){
  const fields = [], sections = [];
  const report = { structures:{}, skipped:{}, opaque:0, heldContent:0, lints:[] };
  const bump = (bag, key) => { bag[key] = (bag[key] || 0) + 1; };

  const argField = (owner, label, key, span) => {
    if (!span || span.end <= span.start) return null;
    const slice = src.slice(span.start, span.end);
    const value = unescStr(slice);
    const f = { id:"f" + fields.length, kind:"arg", key, label, owner,
                start:span.start, end:span.end, value, reversible:escStr(value) === slice };
    fields.push(f);
    return f;
  };

  const richField = (owner, label, span, role) => {
    if (!span || span.end < span.start) return null;
    const parts = runs(src, span.start, span.end);
    const extra = extraOf(parts);
    const slice = src.slice(span.start, span.end);
    const f = { id:"f" + fields.length, kind:"rich", label, owner, role:role || "line",
                start:span.start, end:span.end, parts, extra:[...extra],
                value:plainOf(parts), lost:countLost(parts),
                reversible:serializeParts(parts, extra) === slice };
    fields.push(f);
    report.opaque += f.lost;
    return f;
  };

  const lets = new Map();
  const letRe = /^#let\s+([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*"/gm;
  let lm;
  while ((lm = letRe.exec(src))){
    const start = lm.index + lm[0].length;
    let j = start;
    while (j < src.length){ if (src[j] === "\\"){ j += 2; continue; } if (src[j] === '"') break; j++; }
    lets.set(lm[1], { start, end:j, used:false });
  }

  const addArgs = (item, open, close, prefix) => {
    for (const a of argEntries(src, open, close)){
      if (!a.key) continue;
      const label = (prefix ? prefix + " " : "") + (LABEL[a.key] || a.key);
      if (a.kind === "string"){
        const f = argField(item.id, label, a.key, a.inner);
        if (f){
          item.args.push(f.id);
          if (URLISH.has(a.key) && !/[./]/.test(f.value))
            report.lints.push(`\`${a.key}\` 的值是 "${f.value}"，看起來是標籤而不是網址（這個套件會連到 https://${f.value}）`);
        }
        continue;
      }
      if (a.kind === "call"){ addArgs(item, a.callOpen, a.callClose, LABEL[a.key] || a.key); continue; }
      const raw = src.slice(a.vStart, a.vEnd).trim();
      const bound = lets.get(raw);
      if (bound){
        bound.used = true;
        const f = argField(item.id, label + `（經由 #let ${raw}）`, a.key, { start:bound.start, end:bound.end });
        if (f) item.args.push(f.id);
        continue;
      }
      bump(report.skipped, a.key);
    }
  };

  const bulletsFor = (item, from, to) => {
    let j = from;
    while (j < to){
      if (blank(src, j, to)) break;
      const le = Math.min(lineEnd(src, j), to);
      const line = src.slice(j, le);
      if (/^\s*\/\//.test(line)){
        item.notes.push({ outer:{ start:j, end:Math.min(le + 1, to) }, text:line.trim() });
        bump(report.structures, "註解 //");
        j = le + 1; continue;
      }
      const bm = line.match(/^\s*[-+]\s+/);
      if (!bm) break;
      const b = { id:item.id + "b" + item.bullets.length, outer:{ start:j, end:Math.min(le + 1, to) } };
      b.field = richField(item.id, "要點 " + (item.bullets.length + 1), { start:j + bm[0].length, end:le }, "bullet");
      item.bullets.push(b);
      j = le + 1;
    }
    return j;
  };

  function scanBody(from, to, sec){
    let i = from;
    while (i < to){
      if (blank(src, i, to)){ i = lineEnd(src, i) + 1; continue; }
      const le = Math.min(lineEnd(src, i), to);
      const line = src.slice(i, le);
      const mk = (kind, extra) => Object.assign(
        { id:sec.id + "i" + sec.items.length, kind, outer:{ start:i, end:Math.min(le + 1, to) },
          args:[], bullets:[], notes:[], contentEnd:Math.min(le + 1, to) }, extra || {});

      if (/^\s*\/\//.test(line)){
        sec.items.push(mk("ro", { reason:"註解", raw:line.trim(), setup:false }));
        bump(report.structures, "註解 //");
        i = le + 1; continue;
      }

      const letM = /^#let\s+([A-Za-z_][A-Za-z0-9_-]*)\s*=/.exec(line);
      if (letM){
        sec.items.push(mk("let", { name:letM[1], binding:lets.get(letM[1]) }));
        i = le + 1; continue;
      }

      const showM = /^#show:\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\(/.exec(line);
      const callM = /^#([A-Za-z_][A-Za-z0-9_.-]*)\s*\(/.exec(line);
      const bodyM = /^#([A-Za-z_][A-Za-z0-9_.-]*)\s*\[/.exec(line);
      if (showM || callM || bodyM){
        const name = (showM || callM || bodyM)[1];
        const item = mk("comp", { name, headStart:i, headEnd:0, body:null, bodyItems:null });
        let after = i;
        if (showM || callM){
          const open = i + (showM || callM)[0].length - 1, close = scanBlock(src, open);
          item.open = open; item.close = close;
          addArgs(item, open, close);
          after = close;
        }
        if (bodyM || src[after] === "["){
          const brk = bodyM ? i + bodyM[0].length - 1 : after;
          const close = scanBrackets(src, brk);
          item.body = { start:brk + 1, end:close - 1 };
          after = close;
        }
        item.headEnd = after;
        item.contentEnd = Math.min(lineEnd(src, after) + 1, to);
        if (item.body){
          const sub = { id:item.id, items:[] };
          scanBody(item.body.start, item.body.end, sub);
          item.bodyItems = sub.items;
        }
        let j = bulletsFor(item, item.contentEnd, to);
        item.contentEnd = j;
        while (j < to && blank(src, j, to)) j = lineEnd(src, j) + 1;
        item.outer.end = Math.min(j, to);
        const leaves = item.args.length + item.bullets.length +
          (item.bodyItems ? item.bodyItems.reduce((a, x) => a + x.args.length + (x.field ? 1 : 0), 0) : 0);
        if (!leaves){
          item.kind = "ro"; item.reason = "#" + name;
          item.setup = !item.body && !item.bullets.length;
          bump(report.structures, "#" + name);
          if (!item.setup) report.heldContent++;
        }
        sec.items.push(item);
        i = item.outer.end > i ? item.outer.end : i + 1;
        continue;
      }

      if (/^#/.test(line)){
        sec.items.push(mk("ro", { reason:line.split(/[\s(]/)[0], raw:line.trim(), setup:true }));
        bump(report.structures, line.split(/[\s(]/)[0]);
        i = le + 1; continue;
      }

      const bm = line.match(/^\s*[-+]\s+/);
      const indent = line.match(/^\s*/)[0].length;
      const item = mk(bm ? "bullet" : "para");
      const n = sec.items.filter(x => x.kind === item.kind).length + 1;
      item.field = richField(item.id, bm ? "第 " + n + " 行" : "內文 " + n,
                             { start:i + (bm ? bm[0].length : indent), end:le },
                             bm ? "bullet" : "para");
      let j = le + 1;
      while (j < to && blank(src, j, to)) j = lineEnd(src, j) + 1;
      item.outer.end = Math.min(j, to);
      sec.items.push(item);
      i = item.outer.end > i ? item.outer.end : i + 1;
    }
  }

  const firstHead = (() => { const m = /^== /m.exec(src); return m ? m.index : src.length; })();
  const pre = { id:"s0", kind:"pre", title:"文件開頭", titleField:null,
                outer:{ start:0, end:firstHead }, bodyStart:0, items:[] };
  scanBody(0, firstHead, pre);
  sections.push(pre);

  const heads = [];
  const hre = /^== (.+)$/gm;
  let hm;
  while ((hm = hre.exec(src))) heads.push({ idx:hm.index, ts:hm.index + 3, te:hm.index + 3 + hm[1].length });
  heads.forEach((h, n) => {
    const end = n + 1 < heads.length ? heads[n + 1].idx : src.length;
    const sec = { id:"s" + (n + 1), kind:"section", title:src.slice(h.ts, h.te), titleField:null,
                  outer:{ start:h.idx, end }, bodyStart:Math.min(lineEnd(src, h.idx) + 1, end), items:[] };
    sec.titleField = richField(sec.id, "段落標題", { start:h.ts, end:h.te }, "heading");
    scanBody(sec.bodyStart, end, sec);
    sections.push(sec);
  });

  for (const [name, b] of lets) if (!b.used) report.lints.push(`\`#let ${name}\` 沒有被任何參數用到（死綁定）`);
  return { fields, sections, report, lets };
}

function markLets(p){
  for (const sec of p.sections)
    for (const it of sec.items)
      if (it.kind === "let") it.dead = !(p.lets.get(it.name) || {}).used;
}

function verdict(p, src){
  const owned = p.fields.reduce((a, f) => a + (f.end - f.start), 0);
  const bad = p.fields.filter(f => !f.reversible).length;
  const structures = Object.values(p.report.structures).reduce((a, n) => a + n, 0);
  const held = p.report.heldContent;
  const editable = p.fields.filter(f => f.reversible).length;
  const tier = !editable ? "C" : (bad || p.report.opaque || held) ? "B" : "A";
  return { owned, bad, editable, structures, held, opaque:p.report.opaque, tier,
           pct: src.length ? Math.round(owned / src.length * 100) : 0 };
}

if (typeof module !== "undefined")
  module.exports = { parse, markLets, verdict, runs, serializeParts, plainOf, extraOf,
                     decode, encode, escStr, unescStr, scanBlock, scanBrackets, argEntries, lineEnd, SLOT, LABEL };
