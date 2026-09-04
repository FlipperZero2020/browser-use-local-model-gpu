#!/usr/bin/env python3
"""Regenerate PLAN.artifact.html from PLAN.md, then republish it as the artifact.

  python3 tools/build_artifact.py

The markdown is embedded verbatim in a text/plain script block and rendered
client-side by marked.js, so the published page can never drift from PLAN.md and
nothing is retyped by hand. Republish with the SAME artifact URL to keep the link.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
md = (ROOT / "PLAN.md").read_text(encoding="utf-8")
if "</scr" + "ipt" in md:
    raise SystemExit("PLAN.md contains a closing script tag; embedding is unsafe.")

HEAD = r"""<title>Browsin Build Plan</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Serif:ital,wght@0,400;0,600;1,400&display=swap">
<style>
:root{
  --paper:#F6F7F9; --surface:#FFFFFF; --sunken:#EDF0F3;
  --ink:#151A20; --body:#2F3945; --muted:#66717F; --faint:#8B96A3;
  --rule:#DCE2E8; --rule-soft:#E7ECF1;
  --accent:#0E7C9B; --accent-soft:#E2F1F6; --accent-line:#9CCEDD;
  --amber:#8F5A0A; --amber-soft:#FBF0DC;
  --rose:#A33A4A; --rose-soft:#FBE9EC;
  --code-bg:#EEF2F5; --code-ink:#1B4A5C;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --paper:#0F1419; --surface:#151B22; --sunken:#111820;
    --ink:#E8EDF2; --body:#BFCAD6; --muted:#8894A2; --faint:#6B7684;
    --rule:#242E38; --rule-soft:#1D262F;
    --accent:#3FBBDD; --accent-soft:#10303C; --accent-line:#215F74;
    --amber:#E0A54A; --amber-soft:#31240F;
    --rose:#E1798A; --rose-soft:#331419;
    --code-bg:#121A22; --code-ink:#8FD3E8;
  }
}
:root[data-theme="dark"]{
  --paper:#0F1419; --surface:#151B22; --sunken:#111820;
  --ink:#E8EDF2; --body:#BFCAD6; --muted:#8894A2; --faint:#6B7684;
  --rule:#242E38; --rule-soft:#1D262F;
  --accent:#3FBBDD; --accent-soft:#10303C; --accent-line:#215F74;
  --amber:#E0A54A; --amber-soft:#31240F;
  --rose:#E1798A; --rose-soft:#331419;
  --code-bg:#121A22; --code-ink:#8FD3E8;
}

*{box-sizing:border-box}
body{
  background:var(--paper); color:var(--body);
  font-family:"IBM Plex Serif",Georgia,"Times New Roman",serif;
  font-size:16.5px; line-height:1.68;
  -webkit-font-smoothing:antialiased;
}
.shell{display:grid; grid-template-columns:236px minmax(0,1fr); gap:0; max-width:1180px; margin:0 auto;}
@media (max-width:900px){ .shell{grid-template-columns:minmax(0,1fr)} .rail{display:none} }

/* ---- rail ---- */
.rail{
  position:sticky; top:0; align-self:start; height:100vh; overflow-y:auto;
  padding:38px 22px 40px 26px; border-right:1px solid var(--rule-soft);
  font-family:"IBM Plex Sans",system-ui,sans-serif;
}
.rail .mark{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:11px; font-weight:600;
  letter-spacing:.13em; text-transform:uppercase; color:var(--accent); margin-bottom:22px;
}
.rail nav{display:flex; flex-direction:column; gap:1px}
.rail a{
  display:grid; grid-template-columns:26px 1fr; gap:6px; align-items:baseline;
  text-decoration:none; color:var(--muted); font-size:12.9px; line-height:1.4;
  padding:5px 8px 5px 4px; border-radius:4px; border-left:2px solid transparent;
}
.rail a .n{font-family:"IBM Plex Mono",monospace; font-size:11px; color:var(--faint); font-variant-numeric:tabular-nums}
.rail a:hover{color:var(--ink); background:var(--sunken)}
.rail a.on{color:var(--accent); border-left-color:var(--accent); background:var(--accent-soft)}
.rail a.on .n{color:var(--accent)}
.rail a:focus-visible{outline:2px solid var(--accent); outline-offset:1px}

/* ---- masthead ---- */
main{padding:40px 40px 120px; min-width:0}
@media (max-width:640px){ main{padding:28px 20px 80px} }
.eyebrow{
  font-family:"IBM Plex Mono",monospace; font-size:11px; font-weight:500;
  letter-spacing:.16em; text-transform:uppercase; color:var(--accent);
}
.strip{
  margin:22px 0 40px; border:1px solid var(--rule); border-radius:6px;
  background:var(--surface); overflow:hidden;
}
.strip-h{
  font-family:"IBM Plex Mono",monospace; font-size:10.5px; letter-spacing:.14em;
  text-transform:uppercase; color:var(--muted); padding:9px 14px;
  border-bottom:1px solid var(--rule-soft); background:var(--sunken);
}
.strip-g{display:grid; grid-template-columns:repeat(auto-fit,minmax(132px,1fr))}
.cell{padding:12px 14px; border-right:1px solid var(--rule-soft); border-top:1px solid var(--rule-soft)}
.cell:last-child{border-right:none}
.cell .k{font-family:"IBM Plex Sans",sans-serif; font-size:10.5px; letter-spacing:.07em; text-transform:uppercase; color:var(--faint); display:block; margin-bottom:3px}
.cell .v{font-family:"IBM Plex Mono",monospace; font-size:14px; font-weight:500; color:var(--ink); font-variant-numeric:tabular-nums}
.cell .v.ok{color:var(--accent)}

/* ---- document ---- */
.doc{max-width:70ch; overflow-wrap:break-word}
.doc h1{
  font-family:"IBM Plex Sans",sans-serif; font-weight:700; font-size:2.05rem;
  line-height:1.16; letter-spacing:-.02em; color:var(--ink);
  margin:6px 0 4px; text-wrap:balance; max-width:22ch; overflow-wrap:anywhere;
}
.doc h2{
  font-family:"IBM Plex Sans",sans-serif; font-weight:600; font-size:1.42rem;
  letter-spacing:-.012em; color:var(--ink); margin:64px 0 4px; padding-top:22px;
  border-top:1px solid var(--rule); text-wrap:balance; overflow-wrap:anywhere;
}
.doc h3{
  font-family:"IBM Plex Sans",sans-serif; font-weight:600; font-size:1.06rem;
  color:var(--ink); margin:38px 0 2px; text-wrap:balance;
}
.doc h1+p,.doc h2+p,.doc h3+p{margin-top:10px}
.doc p{margin:0 0 17px}
.doc strong{color:var(--ink); font-weight:600}
.doc em{font-style:italic}
.doc a{color:var(--accent); text-decoration:underline; text-underline-offset:2px; text-decoration-thickness:1px}
.doc ul,.doc ol{margin:0 0 18px; padding-left:1.25em}
.doc li{margin-bottom:7px}
.doc li::marker{color:var(--faint)}
.doc hr{border:none; border-top:1px solid var(--rule-soft); margin:34px 0}

.doc code{
  font-family:"IBM Plex Mono",ui-monospace,monospace; font-size:.845em;
  background:var(--code-bg); color:var(--code-ink);
  padding:.13em .38em; border-radius:3px; word-break:break-word;
}
.doc pre{
  background:var(--surface); border:1px solid var(--rule); border-left:2px solid var(--accent-line);
  border-radius:5px; padding:14px 16px; overflow-x:auto; margin:0 0 20px;
  width:min(100%,84ch);
}
.doc pre code{background:none; padding:0; color:var(--body); font-size:12.6px; line-height:1.62}

.doc blockquote{
  margin:0 0 26px; padding:16px 18px; background:var(--sunken);
  border:1px solid var(--rule-soft); border-left:2px solid var(--accent); border-radius:0 5px 5px 0;
  font-size:.945em; color:var(--muted);
}
.doc blockquote p:last-child{margin-bottom:0}
.doc blockquote strong{color:var(--ink)}

.tw{overflow-x:auto; margin:0 0 22px; width:min(100%,88ch); border:1px solid var(--rule); border-radius:5px; background:var(--surface)}
.doc table{border-collapse:collapse; width:100%; font-family:"IBM Plex Sans",sans-serif; font-size:13.1px; line-height:1.5}
.doc thead th{
  font-weight:600; font-size:10.5px; letter-spacing:.08em; text-transform:uppercase;
  color:var(--muted); text-align:left; padding:9px 13px; background:var(--sunken);
  border-bottom:1px solid var(--rule); white-space:nowrap;
}
.doc tbody td{padding:9px 13px; border-bottom:1px solid var(--rule-soft); vertical-align:top; font-variant-numeric:tabular-nums}
.doc tbody tr:last-child td{border-bottom:none}
.doc tbody td:first-child{color:var(--ink); font-weight:500}
.doc table code{font-size:.9em}

.tag{
  font-family:"IBM Plex Mono",monospace; font-size:10.5px; font-weight:600; letter-spacing:.05em;
  padding:.14em .42em; border-radius:3px; background:var(--amber-soft); color:var(--amber);
  white-space:nowrap;
}
.doc h2#s7 ~ ul li::marker{color:var(--rose)}
@media (prefers-reduced-motion:reduce){*{animation:none!important; transition:none!important}}
</style>

<div class="shell">
  <aside class="rail">
    <div class="mark">browsin</div>
    <nav id="toc"></nav>
  </aside>
  <main>
    <div class="eyebrow">Design doc &amp; status log &middot; 2026-09-04 &middot; phases 0-3 passed</div>
    <div class="strip">
      <div class="strip-h">Measured, not remembered &mdash; 192.168.1.111, 2026-09-01 to 2026-09-04</div>
      <div class="strip-g">
        <div class="cell"><span class="k">phases passed</span><span class="v ok">0 &middot; 1 &middot; 2 &middot; 3</span></div>
        <div class="cell"><span class="k">longest hold</span><span class="v">603 s</span></div>
        <div class="cell"><span class="k">ghosts booked</span><span class="v ok">0</span></div>
        <div class="cell"><span class="k">book available</span><span class="v">12489 MiB</span></div>
        <div class="cell"><span class="k">ollama</span><span class="v">0.32.15</span></div>
        <div class="cell"><span class="k">browser-use</span><span class="v">0.13.8</span></div>
        <div class="cell"><span class="k">vision models</span><span class="v ok">1 declared</span></div>
        <div class="cell"><span class="k">next</span><span class="v">phase 4</span></div>
      </div>
    </div>
    <article class="doc" id="doc"></article>
  </main>
</div>

"""
TAIL = r"""</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<script>
(function(){
  var md = document.getElementById('src').textContent;
  var doc = document.getElementById('doc');
  marked.setOptions({gfm:true, breaks:false, mangle:false, headerIds:false});
  doc.innerHTML = marked.parse(md);

  // Tables get their own horizontal scroll container so the page never scrolls sideways.
  doc.querySelectorAll('table').forEach(function(t){
    var w = document.createElement('div'); w.className='tw';
    t.parentNode.insertBefore(w,t); w.appendChild(t);
  });

  // [VERIFY] / [ASSUMPTION] are load-bearing epistemic markers in this doc, not prose.
  var walker = document.createTreeWalker(doc, NodeFilter.SHOW_TEXT);
  var hits=[], n;
  while((n=walker.nextNode())){
    if(n.parentNode.tagName!=='CODE' && /\[(VERIFY|ASSUMPTION)\]/.test(n.nodeValue)) hits.push(n);
  }
  hits.forEach(function(node){
    var frag=document.createDocumentFragment(), parts=node.nodeValue.split(/(\[(?:VERIFY|ASSUMPTION)\])/);
    parts.forEach(function(p){
      if(/^\[(VERIFY|ASSUMPTION)\]$/.test(p)){
        var s=document.createElement('span'); s.className='tag'; s.textContent=p; frag.appendChild(s);
      } else frag.appendChild(document.createTextNode(p));
    });
    node.parentNode.replaceChild(frag,node);
  });

  // Rail is built from the document's own numbered sections.
  var toc=document.getElementById('toc'), links=[];
  doc.querySelectorAll('h2').forEach(function(h,i){
    var raw=h.textContent.trim();
    var m=raw.match(/^(\d+)\.\s*(.+)$/);
    var num = m ? m[1] : String(i);
    var label = m ? m[2] : raw;
    h.id = 's'+num;
    var a=document.createElement('a');
    a.href='#'+h.id;
    a.innerHTML='<span class="n">'+num+'</span><span>'+label.replace(/[<>&]/g,'')+'</span>';
    toc.appendChild(a); links.push({a:a,h:h});
  });

  var tick=false;
  function sync(){
    tick=false;
    var best=links[0], y=window.scrollY+120;
    links.forEach(function(l){ if(l.h.offsetTop<=y) best=l; });
    links.forEach(function(l){ l.a.classList.toggle('on', l===best); });
  }
  window.addEventListener('scroll', function(){ if(!tick){ tick=true; requestAnimationFrame(sync); } }, {passive:true});
  sync();
})();
</script>
"""

out = HEAD + '<script id="src" type="text/plain">' + md + TAIL
(ROOT / "PLAN.artifact.html").write_text(out, encoding="utf-8")
print("wrote PLAN.artifact.html", len(out), "bytes")
