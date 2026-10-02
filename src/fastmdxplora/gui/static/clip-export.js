/* Render saved trajectory frames; no coordinate manipulation or model tools. */
(function () {
  "use strict";
  const el = (id) => document.getElementById(id);
  let active = false, cancelled = false;
  const say = (text) => { el("clip-status").textContent = text; };
  async function post(payload) {
    const response = await fetch("/api/research/clips", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || "Clip export failed.");
    return data;
  }
  async function imageFrame(uri, labels, source, time) {
    const image = new Image(); image.src = uri; await image.decode();
    const scale = Math.min(1, 1280 / image.width, 960 / image.height);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(2, Math.floor(image.width * scale)); canvas.height = Math.max(2, Math.floor(image.height * scale));
    const context = canvas.getContext("2d");
    context.fillStyle="white"; context.fillRect(0, 0, canvas.width, canvas.height); context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const parts = [];
    if (labels.frame) parts.push("Source frame " + source);
    if (labels.time) parts.push(Number.isFinite(time) ? time.toFixed(3) + " ns" : "Simulation time not recorded");
    if (parts.length) {
      context.font="14px sans-serif";
      context.fillStyle="rgba(255,255,255,0.9)"; context.fillRect(0, canvas.height-30, canvas.width, 30);
      context.fillStyle="black"; context.fillText(parts.join(" · "), 10, canvas.height-10);
    }
    const encoded = canvas.toDataURL("image/png").split(",")[1];
    if (encoded.length > 930000) throw new Error("The rendered frame exceeds the upload limit. Reduce the viewer size or simplify its display.");
    return encoded;
  }
  async function start() {
    if (active) return;
    const first=Number(el("clip-first").value), last=Number(el("clip-last").value), stride=Number(el("clip-stride").value);
    if (![first,last,stride].every(Number.isInteger) || first < 0 || last <= first || stride < 1) return say("Choose increasing frame indices and a positive integer stride.");
    const length=Math.floor((last-first)/stride)+1;
    if (length < 2 || length > 120) return say("Choose 2–120 frames. Increase the stride for a longer trajectory range.");
    const frames=Array.from({length:length}, (_, i) => first+i*stride);
    const labels={residues:el("clip-residues").checked, atoms:el("clip-atoms").checked, frame:el("clip-frame").checked, time:el("clip-time").checked};
    const rotation=Number(el("clip-rotation").value), fps=Number(el("clip-fps").value);
    if (!Number.isFinite(rotation) || Math.abs(rotation)>360 || !Number.isFinite(fps) || fps<1 || fps>30) return say("Choose a rate of 1–30 fps and rotation within −360 to 360 degrees.");
    active=true; cancelled=false;
    el("clip-export-start").disabled=true; el("clip-export-cancel").textContent="Cancel export";
    el("clip-download").hidden=true; el("clip-download-mp4").hidden=true; el("clip-metadata").hidden=true;
    const layout=document.querySelector(".viewer-layout"), wasInert=layout.inert;
    layout.inert=true;
    let session=null, upload=null, view=window.FastMDXResearch.capture();
    try {
      say("Loading saved trajectory frames…");
      session=await window.FastMDXMoleculeViewer.clipSession({...labels, scope:el("clip-label-scope").value});
      view.camera=session.camera;
      if (last >= session.count) throw new Error("The last frame is outside this trajectory's browser frame range.");
      upload=await post({action:"start", study:view.study, format:el("clip-format").value, frames:frames, fps:fps, rotation:rotation, labels:labels, label_scope:el("clip-label-scope").value, signature:session.signature, view:view});
      for (let i=0; i<frames.length; i++) {
        if (cancelled) throw new Error("Clip export cancelled.");
        say(`Rendering frame ${i+1} of ${frames.length}…`);
        const frame=frames[i], uri=await session.render(frame, rotation*i/(frames.length-1));
        const png=await imageFrame(uri, labels, session.payload.frame_indices[frame], session.payload.frame_times_ns?.[frame]);
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
      el("clip-export-start").disabled=false; el("clip-export-cancel").disabled=false; el("clip-export-cancel").textContent="Close";
    }
  }
  document.addEventListener("DOMContentLoaded", () => {
    el("clip-export-open").addEventListener("click", async () => {
      el("clip-export-dialog").showModal();
      try {
        const response=await fetch("/api/playback-info", {cache:"no-store"});
        const info=await response.json();
        if (!info.playback_available) return say("Saved trajectory playback is unavailable in this study.");
        el("clip-first").value="0"; el("clip-last").value=String(info.n_frames_browser-1);
        el("clip-stride").value=String(Math.max(1, Math.ceil((info.n_frames_browser-1)/119)));
        say(`${info.n_frames_browser} saved browser frames available. Labels describe the saved playback representation.`);
      } catch (_) { say("Playback information could not be loaded. Check the current study and retry."); }
    });
    el("clip-export-start").addEventListener("click", start);
    el("clip-export-cancel").addEventListener("click", () => { if(active) cancelled=true; else el("clip-export-dialog").close(); });
    el("clip-export-dialog").addEventListener("cancel", (event) => { if(active) {event.preventDefault(); cancelled=true;} });
  });
})();
