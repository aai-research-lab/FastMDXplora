/* Render saved trajectory frames; no coordinate manipulation or model tools. */
(function () {
  "use strict";
  const el = (id) => document.getElementById(id);
  let active = false, cancelled = false, playbackInfo = null, previewBytes = null;
  const say = (text) => { el("clip-status").textContent = text; };
  async function post(payload) {
    const response = await fetch("/api/research/clips", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || "Clip export failed.");
    return data;
  }
  function options() {
    const first=Number(el("clip-first").value), last=Number(el("clip-last").value), stride=Number(el("clip-stride").value);
    if (![first,last,stride].every(Number.isInteger) || first < 0 || last <= first || stride < 1) throw new Error("Choose increasing frame indices and a positive integer stride.");
    const length=Math.floor((last-first)/stride)+1;
    if (length < 2 || length > 120) throw new Error("Choose 2–120 frames. Increase the stride for a longer trajectory range.");
    const rotation=Number(el("clip-rotation").value), fps=Number(el("clip-fps").value);
    if (!Number.isFinite(rotation) || Math.abs(rotation)>360 || !Number.isFinite(fps) || fps<1 || fps>30) throw new Error("Choose a rate of 1–30 fps and rotation within −360 to 360 degrees.");
    const labels={residues:el("clip-residues").checked, atoms:el("clip-atoms").checked, frame:el("clip-frame").checked, time:el("clip-time").checked, study:el("clip-study-label").checked, caption:el("clip-caption-label").checked};
    const captions={study:el("clip-study-title").value.trim(), caption:el("clip-caption").value.trim()};
    if (labels.study && !captions.study || labels.caption && !captions.caption) throw new Error("Enter text for the selected title/caption overlays.");
    const dimensions=el("clip-resolution").value.split("x").map(Number);
    return {frames:Array.from({length}, (_, i) => first+i*stride), labels, captions, dimensions, fps, rotation, format:el("clip-format").value, scope:el("clip-label-scope").value};
  }
  function estimate(opts, info = playbackInfo) {
    const first=opts.frames[0], last=opts.frames[opts.frames.length-1];
    const source=index => info?.frame_indices?.[index] ?? "unavailable";
    const time=index => Number.isFinite(info?.frame_times_ns?.[index]) ? info.frame_times_ns[index].toFixed(3)+" ns" : "unrecorded";
    el("clip-estimate").textContent = `${opts.frames.length} frames · ${(opts.frames.length/opts.fps).toFixed(2)} seconds playback · ${opts.dimensions.join(" × ")} pixels. Browser ${first}–${last}; source ${source(first)}–${source(last)}; physical time ${time(first)}–${time(last)}. ` +
      (previewBytes ? `Approximate PNG upload ${(previewBytes*opts.frames.length/1e6).toFixed(1)} MB from preview samples; final GIF/MP4 size and encoding time vary.` : "Preview to estimate PNG upload size. Final encoded size and export time depend on the scene and encoder.");
  }
  function lockControls(locked) {
    el("clip-export-start").disabled=locked; el("clip-preview").disabled=locked;
    el("clip-export-dialog").querySelectorAll("input,select").forEach(control => { control.disabled=locked; });
  }
  async function imageFrame(uri, opts, source, time) {
    const image = new Image(); image.src = uri; await image.decode();
    if (image.width < opts.dimensions[0] || image.height < opts.dimensions[1]) throw new Error("The molecular renderer could not produce the selected resolution.");
    const canvas = document.createElement("canvas");
    [canvas.width, canvas.height] = opts.dimensions;
    const context = canvas.getContext("2d");
    context.fillStyle="white"; context.fillRect(0, 0, canvas.width, canvas.height); context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const parts = [];
    if (opts.labels.study) parts.push(opts.captions.study);
    if (opts.labels.caption) parts.push(opts.captions.caption);
    if (opts.labels.frame) parts.push("Source frame " + source);
    if (opts.labels.time) parts.push(Number.isFinite(time) ? time.toFixed(3) + " ns" : "Simulation time not recorded");
    if (parts.length) {
      const font=Math.max(14, Math.min(18, Math.floor(canvas.width/60))), lineHeight=font+6, lines=[];
      context.font=font+"px sans-serif";
      for (const part of parts) {
        let line="";
        for (const character of part) {
          if (line && context.measureText(line+character).width > canvas.width-24) { lines.push(line); line=""; }
          line+=character;
        }
        if(line) lines.push(line);
      }
      const top=canvas.height-lines.length*lineHeight-16;
      context.fillStyle="rgba(255,255,255,0.94)"; context.fillRect(0, top, canvas.width, canvas.height-top);
      context.fillStyle="black"; lines.forEach((line,index) => context.fillText(line, 12, top+font+8+index*lineHeight));
    }
    const encoded = canvas.toDataURL("image/png").split(",")[1];
    if (encoded.length > 930000) throw new Error("The rendered frame exceeds the upload limit. Reduce the viewer size or simplify its display.");
    return encoded;
  }
  async function start() {
    if (active) return;
    let opts;
    try { opts=options(); } catch(error) { return say(error.message); }
    const {frames,labels,rotation,fps}=opts;
    active=true; cancelled=false;
    lockControls(true); el("clip-export-cancel").textContent="Cancel export";
    el("clip-download").hidden=true; el("clip-download-mp4").hidden=true; el("clip-metadata").hidden=true;
    const layout=document.querySelector(".viewer-layout"), wasInert=layout.inert;
    layout.inert=true;
    let session=null, upload=null, view=window.FastMDXResearch.capture();
    try {
      say("Loading saved trajectory frames…");
      session=await window.FastMDXMoleculeViewer.clipSession({...labels, scope:opts.scope, dimensions:opts.dimensions});
      view.camera=session.camera;
      if (frames[frames.length-1] >= session.count) throw new Error("The last frame is outside this trajectory's browser frame range.");
      estimate(opts, session.payload);
      upload=await post({action:"start", study:view.study, format:opts.format, frames, fps, rotation, labels, label_scope:opts.scope, dimensions:opts.dimensions, captions:opts.captions, signature:session.signature, view});
      for (let i=0; i<frames.length; i++) {
        if (cancelled) throw new Error("Clip export cancelled.");
        say(`Rendering frame ${i+1} of ${frames.length}…`);
        const frame=frames[i], uri=await session.render(frame, rotation*i/(frames.length-1));
        const png=await imageFrame(uri, opts, session.payload.frame_indices[frame], session.payload.frame_times_ns?.[frame]);
        if (cancelled) throw new Error("Clip export cancelled.");
        await post({action:"frame", study:view.study, id:upload.id, index:i, png:png});
      }
      if (cancelled) throw new Error("Clip export cancelled.");
      say("Encoding and saving clip…");
      el("clip-export-cancel").disabled=true;
      const result=await post({action:"finish", study:view.study, id:upload.id}); upload=null;
      for (const [id,url] of [["clip-download",result.url],["clip-metadata",result.metadata_url]]) { el(id).href=url+"?download=1"; el(id).hidden=false; }
      if (result.urls?.gif && result.urls?.mp4) { el("clip-download").textContent="Download GIF"; el("clip-download-mp4").href=result.urls.mp4+"?download=1"; el("clip-download-mp4").hidden=false; }
      else el("clip-download").textContent="Download clip";
      say("Clip and source/view metadata saved with the study.");
    } catch (error) {
      say(error.message);
      if (upload) try { await post({action:"cancel", study:view.study, id:upload.id}); } catch (_) {}
    } finally {
      try { if(session) await session.restore(); } catch (_) { say(el("clip-status").textContent + " Viewer changed; select its frame again."); }
      layout.inert=wasInert; active=false;
      lockControls(false); el("clip-export-cancel").disabled=false; el("clip-export-cancel").textContent="Close";
    }
  }
  async function preview() {
    if(active) return;
    let opts;
    try { opts=options(); } catch(error) { return say(error.message); }
    active=true; cancelled=false; lockControls(true); el("clip-export-cancel").textContent="Cancel preview";
    const layout=document.querySelector(".viewer-layout"), wasInert=layout.inert;
    layout.inert=true;
    let session;
    try {
      say("Rendering the selected first and last frames…");
      session=await window.FastMDXMoleculeViewer.clipSession({...opts.labels, scope:opts.scope, dimensions:opts.dimensions});
      previewBytes=0;
      for(const [id,frame,angle] of [["clip-preview-first",opts.frames[0],0],["clip-preview-last",opts.frames[opts.frames.length-1],opts.rotation]]) {
        if(cancelled) throw new Error("Clip preview cancelled.");
        if(frame >= session.count) throw new Error("A preview frame is outside the saved trajectory.");
        const uri=await session.render(frame,angle), png=await imageFrame(uri,opts,session.payload.frame_indices[frame],session.payload.frame_times_ns?.[frame]);
        previewBytes=Math.max(previewBytes,png.length*0.75);
        if(cancelled) throw new Error("Clip preview cancelled.");
        el(id).src="data:image/png;base64,"+png; el(id).hidden=false;
      }
      playbackInfo=session.payload; estimate(opts);
      say("Preview ready. First and last selected frames use the export renderer and overlays; intermediate frames are saved samples with no coordinate interpolation.");
    } catch(error) { say(error.message); }
    finally {
      try { if(session) await session.restore(); } catch(_) { say("Viewer changed; reopen its saved frame."); }
      layout.inert=wasInert; active=false; lockControls(false); el("clip-export-cancel").textContent="Close";
    }
  }
  document.addEventListener("DOMContentLoaded", () => {
    el("clip-export-open").addEventListener("click", async () => {
      el("clip-export-dialog").showModal();
      try {
        const response=await fetch("/api/playback-info", {cache:"no-store"});
        const info=await response.json();
        playbackInfo=info; previewBytes=null;
        el("clip-preview-first").hidden=true; el("clip-preview-last").hidden=true;
        if (!info.playback_available) return say("Saved trajectory playback is unavailable in this study.");
        el("clip-first").value="0"; el("clip-last").value=String(info.n_frames_browser-1);
        el("clip-stride").value=String(Math.max(1, Math.ceil((info.n_frames_browser-1)/119)));
        if (!el("clip-study-title").value) el("clip-study-title").value=(window.FastMDXResearch.capture().study || "Study").split(/[\\/]/).pop();
        estimate(options());
        say(`${info.n_frames_browser} saved browser frames available. Labels describe the saved playback representation.`);
      } catch (_) { say("Playback information could not be loaded. Check the current study and retry."); }
    });
    el("clip-export-start").addEventListener("click", start);
    el("clip-preview").addEventListener("click", preview);
    el("clip-export-dialog").querySelectorAll("input,select").forEach(control => control.addEventListener("input", () => {
      previewBytes=null; el("clip-preview-first").hidden=true; el("clip-preview-last").hidden=true;
      try { estimate(options()); } catch(error) { el("clip-estimate").textContent=error.message; }
    }));
    el("clip-export-cancel").addEventListener("click", () => { if(active) cancelled=true; else el("clip-export-dialog").close(); });
    el("clip-export-dialog").addEventListener("cancel", (event) => { if(active) {event.preventDefault(); cancelled=true;} });
  });
})();
