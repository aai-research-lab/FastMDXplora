/* Optional saved-evidence comparison. These viewers never edit study files. */
(function () {
  "use strict";
  const el = (id) => document.getElementById(id);
  let data=null, study=null, generation=0, drawGeneration=0, viewers=[], models=[], texts=[], selected=null, residue=null, source=null, restoring=false;
  const protein="ALA ARG ASN ASP CYS GLN GLU GLY HIS HID HIE HIP ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL MSE SEC PYL CYX ASH GLH LYN".split(" ");
  const waterIons="HOH WAT SOL TIP3 TIP3P TIP4P NA CL K CA MG ZN FE MN CU CO NI CD BR I CS LI RB F".split(" ");
  const say = (text) => { el("preparation-audit-status").textContent=text; };
  function valid() { return data && data.study === window.FastMDXDashboard?.state.appState?.active_run; }
  function baseStyle(model) {
    model.setStyle({}, {});
    model.setStyle({resn:protein}, {cartoon:{color:"spectrum"}});
    model.setStyle({resn:protein.concat(waterIons),invert:true}, {stick:{colorscheme:"element",radius:0.15}});
  }
  function highlight() {
    models.forEach((group) => group.forEach((model, index) => {
      baseStyle(model);
      if (group.length > 1) model.setStyle({resn:protein}, {cartoon:{color:index ? "orange" : "royalblue",opacity:index ? 0.75 : 1}});
      if (!residue) return;
      const indices=model.selectedAtoms({chain:residue.chain,resi:residue.resseq,resn:residue.resname})
        .filter((atom)=>(atom.icode || "").trim() === (residue.icode || "") && (!residue.atom || atom.atom === residue.atom)).map((atom)=>atom.index);
      if(indices.length) model.setStyle({index:indices}, {stick:{color:"red",radius:0.22},sphere:{color:"red",radius:0.3}});
    }));
    viewers.forEach((viewer)=>viewer.render());
  }
  function bindPick(model, identity) {
    model.setClickable({}, true, (atom) => {
      source=identity;
      residue={chain:atom.chain || "",resname:atom.resn,resseq:Number(atom.resi),icode:(atom.icode || "").trim(),atom:atom.atom,altloc:(atom.altLoc || atom.altloc || "").trim()};
      choose("view-"+identity, false);
      window.FastMDXResearch.selectAudit(selected.id, source, residue);
      el("preparation-evidence").textContent=`Selected ${residue.resname} ${residue.chain || "_"}:${residue.resseq}${residue.icode}, atom ${residue.atom}. `+selected.evidence;
      highlight();
    });
  }
  function link(index) {
    if (!el("preparation-linked").checked || viewers.length!==2) return;
    const a=viewers[index].getView(), other=viewers[1-index], b=other.getView();
    for(let i=3;i<8;i++) b[i]=a[i];
    other.setView(b); other.render();
  }
  function ensureViewers() {
    if(viewers.length) return;
    if(!window.$3Dmol) throw new Error("The molecular renderer is unavailable.");
    for(const [index,id] of ["preparation-before-canvas","preparation-after-canvas"].entries()) {
      viewers.push(window.$3Dmol.createViewer(el(id), {backgroundColor:"white"}));
      for(const event of ["mouseup","touchend","wheel"]) el(id).addEventListener(event,()=>link(index));
    }
  }
  async function display() {
    if(!valid()) return;
    const expected=++drawGeneration, current=generation;
    const ids=[el("preparation-before").value, el("preparation-after").value];
    if(ids.some((id)=>!data.sources[id]?.url)) return;
    say("Loading saved structure comparison…");
    try {
      ensureViewers();
      const rows=ids.map((id)=>data.sources[id]);
      const incoming=await Promise.all(rows.map(async(row)=>{
        if(!row.url.startsWith("/artifacts/setup/")) throw new Error("The saved source address is invalid.");
        const response=await fetch(row.url,{cache:"no-store"});
        if(!response.ok) throw new Error("A saved preparation structure is unavailable.");
        return response.text();
      }));
      if(expected!==drawGeneration || current!==generation || !valid()) return;
      texts=incoming; models=[];
      for(let i=0;i<2;i++) {
        viewers[i].clear();
        const model=viewers[i].addModel(texts[i], rows[i].format);
        baseStyle(model); bindPick(model,ids[i]); models.push([model]);
        viewers[i].zoomTo({resn:protein}); viewers[i].resize(); viewers[i].render();
        el(i ? "preparation-after-title" : "preparation-before-title").textContent=rows[i].label;
      }
      el("preparation-alignment").textContent="Side-by-side coordinates retain their saved positions. Linking shares rotation/zoom while keeping each structure centered.";
      if(el("preparation-overlay").checked) {
        const response=await fetch("/api/research/preparation-audit?before="+encodeURIComponent(ids[0])+"&after="+encodeURIComponent(ids[1]),{cache:"no-store"});
        const aligned=await response.json();
        if(expected!==drawGeneration || current!==generation || !valid()) return;
        if(!aligned.ok) { el("preparation-overlay").checked=false; el("preparation-alignment").textContent=aligned.error; }
        else {
          const extra=viewers[0].addModel(texts[1], rows[1].format), r=aligned.rotation, t=aligned.translation;
          extra.selectedAtoms({}).forEach((atom)=>{
            const p=[atom.x,atom.y,atom.z];
            [atom.x,atom.y,atom.z]=[0,1,2].map((axis)=>p.reduce((sum,value,j)=>sum+value*r[j][axis],t[axis]));
          });
          models[0][0].setStyle({resn:protein},{cartoon:{color:"royalblue"}});
          extra.setStyle({},{}); extra.setStyle({resn:protein},{cartoon:{color:"orange",opacity:0.75}});
          bindPick(extra,ids[1]); models[0].push(extra); viewers[0].render();
          el("preparation-alignment").textContent=`Blue: before; orange: aligned after. ${aligned.matched_atoms} exact heavy-atom matches. ${aligned.notice}`;
        }
      }
      if(residue) highlight();
      say((data.recorded ? "Recorded audit: " : "Historical evidence: ")+data.status+(data.warnings?.length ? " · "+data.warnings.join(" ") : ""));
    } catch(error) { if(expected===drawGeneration && current===generation) say(error.message); }
  }
  function choose(id, redraw=true) {
    if(!valid()) return;
    selected=data.changes.find((row)=>row.id===id);
    if(!selected) return;
    el("preparation-event").value=id;
    if(redraw) { residue=null; source=selected.stage || selected.after || selected.before || null; }
    if(selected.selection) residue={...selected.selection,resseq:Number(selected.selection.resseq)};
    for(const [name,key] of [["preparation-before","before"],["preparation-after","after"]]) {
      if(data.sources[selected[key]]?.url) el(name).value=selected[key];
    }
    window.FastMDXResearch.selectAudit(id,source,residue);
    el("preparation-evidence").textContent=typeof selected.evidence === "string" ? selected.evidence : "Recorded choice from the saved setup record.";
    el("preparation-details").textContent=JSON.stringify(selected.details || selected.evidence || selected, null, 2);
    el("preparation-ask").disabled=false; el("preparation-bookmark").disabled=false;
    if(redraw) return display();
  }
  function inventories() {
    const host=el("preparation-inventory"), stages=el("preparation-stage-strip"); host.replaceChildren(); stages.replaceChildren();
    const rows=Object.entries(data.sources), maximum=Math.max(1,...rows.map(([,row])=>row.counts?.total || 0));
    for(const [identity,row] of rows) {
      const badge=document.createElement("span"); badge.className="chip-btn"; badge.textContent=row.label+(row.url ? "" : " · unavailable"); stages.appendChild(badge);
      if(!row.url) { badge.title=row.unavailable; continue; }
      const button=document.createElement("button"); button.type="button"; button.className="preparation-bar";
      const label=document.createElement("span"), bar=document.createElement("span");
      label.textContent=row.label+": "+(row.counts ? `${row.counts.total} atoms · protein ${row.counts.protein}, water ${row.counts.water}, ions ${row.counts.ions}, other ${row.counts.other}` : "inventory unavailable for this format");
      bar.className="preparation-bar-fill"; bar.style.width=(60*(row.counts?.total || 0)/maximum)+"%";
      button.append(label,bar); button.addEventListener("click",()=>choose("view-"+identity)); host.appendChild(button);
    }
  }
  async function load() {
    const expected=++generation; ++drawGeneration;
    say("Reading saved preparation evidence…"); selected=null; residue=null; source=null;
    data=null;
    for(const id of ["preparation-stage-strip","preparation-inventory","preparation-event","preparation-before","preparation-after"]) el(id).replaceChildren();
    viewers.forEach((viewer)=>viewer.clear());
    el("preparation-ask").disabled=true; el("preparation-bookmark").disabled=true;
    try {
      const response=await fetch("/api/research/preparation-audit",{cache:"no-store"}), received=await response.json();
      if(expected!==generation) return;
      if(!received.ok) throw new Error(received.error);
      data=received; if(!valid()) throw new Error("The study changed while loading the audit.");
      el("preparation-audit-notice").textContent=data.notice;
      inventories();
      const available=Object.entries(data.sources).filter(([,row])=>row.url);
      for(const name of ["preparation-before","preparation-after"]) {
        el(name).replaceChildren();
        for(const [id,row] of available) { const option=document.createElement("option"); option.value=id; option.textContent=row.label; el(name).appendChild(option); }
      }
      if(available.length) el("preparation-after").value=available[available.length-1][0];
      el("preparation-event").replaceChildren();
      for(const row of data.changes) { const option=document.createElement("option"); option.value=row.id; option.textContent=(row.kind === "inventory" ? "Observed: " : row.kind === "recorded" ? "Recorded: " : "Evidence: ")+row.label; el("preparation-event").appendChild(option); }
      el("preparation-details").textContent="";
      el("preparation-evidence").textContent=data.limited ? "The change list is limited; review the saved source/record for remaining evidence." : "Select a change or click an atom in a saved-stage viewer.";
      if(available.length>=2) await display(); else say("Two saved structural stages are unavailable. Recorded choices and missing-evidence notices remain visible.");
    } catch(error) { if(expected===generation) { data=null; viewers.forEach((viewer)=>viewer.clear()); say(error.message); } }
  }
  document.addEventListener("DOMContentLoaded",()=>{
    el("preparation-audit-panel").addEventListener("toggle",()=>{ if(el("preparation-audit-panel").open && !restoring) load(); });
    for(const id of ["preparation-before","preparation-after","preparation-overlay"]) el(id).addEventListener("change",display);
    el("preparation-event").addEventListener("change",()=>choose(el("preparation-event").value));
    el("preparation-ask").addEventListener("click",()=>{ if(valid() && selected) window.FastMDXResearch.ask("Explain this saved preparation change and any selected residue or atom. Separate recorded operations from inventory observations, identify missing evidence, and describe which choices need human review."); });
    window.FastMDXDashboard?.on("app-state",(state)=>{
      if(study !== state.active_run) { study=state.active_run; ++generation; ++drawGeneration; data=null; selected=null; residue=null; viewers.forEach((viewer)=>viewer.clear()); if(el("preparation-audit-panel").open) load(); }
    });
    window.addEventListener("resize",()=>viewers.forEach((viewer)=>viewer.resize()));
  });
  window.FastMDXPreparationAudit={capture() {
    if(!valid() || viewers.length!==2) return null;
    return {before:el("preparation-before").value,after:el("preparation-after").value,
      overlay:el("preparation-overlay").checked,linked:el("preparation-linked").checked,
      before_camera:viewers[0].getView(),after_camera:viewers[1].getView()};
  }, async restore(id,identity,selection,state) {
    restoring=true;
    try {
      el("preparation-audit-panel").open=true;
      await load(); await choose(id);
      if(!valid() || !selected) throw new Error("The saved preparation event is unavailable.");
      if(state) {
        for(const key of ["before","after"]) if(data.sources[state[key]]?.url) el("preparation-"+key).value=state[key];
        for(const key of ["overlay","linked"]) if(typeof state[key]==="boolean") el("preparation-"+key).checked=state[key];
        await display();
        for(const [index,key] of [[0,"before_camera"],[1,"after_camera"]]) if(state[key]) { viewers[index].setView(state[key]); viewers[index].render(); }
      }
      if(identity && data.sources[identity]?.url) { source=identity; residue=selection || null; window.FastMDXResearch.selectAudit(id,source,residue); highlight(); }
      el("preparation-audit-panel").scrollIntoView({block:"start"});
    } finally { restoring=false; }
  }};
})();
