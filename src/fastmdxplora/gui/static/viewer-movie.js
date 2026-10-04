/* A movie of the study's frames, made from the Viewer.
 *
 * Each frame of the movie is a frame of the trajectory shown as the Viewer
 * shows it (the representation, the colouring, the parts shown, the
 * superposition, the camera) and rendered as a picture at the movie's size,
 * with the time and the colour bar of a result in its corners. The pictures
 * are sent one by one to the GUI's server, where ffmpeg on this computer
 * encodes them as they arrive (movies.py): an MP4 (H.264), or a WebM where
 * that ffmpeg has no H.264 encoder. The movie is kept with the study in
 * movies/ and downloaded. While it is made the Viewer is held still, and
 * afterwards the frame and the camera are put back.
 */
(function () {
  "use strict";

  var making = null;

  function byId(id) { return document.getElementById(id); }

  function viewer() { return window.FastMDXMoleculeViewer; }

  function say(text) {
    var said = byId("movie-said");
    if (said) said.textContent = text;
    var live = byId("sr-live");
    if (live) live.textContent = text;
  }

  function whole(id, fallback) {
    var input = byId(id);
    var raw = input ? String(input.value).trim() : "";
    if (raw === "") return fallback;
    var value = Number(raw);
    return Number.isFinite(value) ? Math.round(value) : fallback;
  }

  function even(value) {
    return Math.max(16, 2 * Math.round(value / 2));
  }

  /** The movie's size in pixels: as the view is shown, 1920 across (2160
   * high at most), or one of the video sizes. */
  function size() {
    var chosen = (byId("movie-size") || {}).value || "shown";
    var fixed = /^(\d+)x(\d+)$/.exec(chosen);
    if (fixed) return [Number(fixed[1]), Number(fixed[2])];
    var canvas = document.querySelector("#viewer-canvas canvas");
    var aspect = canvas && canvas.clientHeight ? canvas.clientWidth / canvas.clientHeight : 16 / 9;
    var width = 1920;
    var height = width / aspect;
    if (height > 2160) {
      height = 2160;
      width = height * aspect;
    }
    return [even(width), even(height)];
  }

  /** The frames of the trajectory the movie shows, in order. */
  function framesOf(count) {
    var from = Math.max(0, Math.min(count - 1, whole("movie-from", 0)));
    var to = Math.max(0, Math.min(count - 1, whole("movie-to", count - 1)));
    var every = Math.max(1, whole("movie-every", 1));
    var frames = [];
    if (from <= to) for (var i = from; i <= to; i += every) frames.push(i);
    else for (var j = from; j >= to; j -= every) frames.push(j);
    return frames;
  }

  /** A time to as many places as tell one frame of the movie from the next. */
  function timeSaid(time, step) {
    var places = step > 0 ? Math.max(0, Math.min(4, Math.ceil(-Math.log10(step) - 1e-9))) : 2;
    return Number(time).toFixed(places) + " ns";
  }

  function drawTime(context, text) {
    var canvas = context.canvas;
    var scale = Math.max(1, canvas.width / 900);
    context.save();
    context.font = "600 " + (14 * scale) + "px sans-serif";
    context.textBaseline = "middle";
    var pad = 10 * scale;
    var width = context.measureText(text).width + 2 * pad;
    var height = 30 * scale;
    var left = canvas.width - 14 * scale - width;
    var top = canvas.height - 14 * scale - height;
    context.fillStyle = "rgba(5, 5, 5, 0.82)";
    context.fillRect(left, top, width, height);
    context.fillStyle = "#f5f5f7";
    context.fillText(text, left + pad, top + height / 2);
    context.restore();
  }

  function post(url, body, type) {
    return fetch(url, {
      method: "POST",
      headers: { "content-type": type || "application/json" },
      body: type ? body : JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, reason: "HTTP " + r.status }; });
    }).catch(function () { return { ok: false, reason: "The server did not answer." }; });
  }

  function pictureOf(canvas) {
    return new Promise(function (resolve, reject) {
      canvas.toBlob(function (blob) {
        if (blob) resolve(blob);
        else reject(new Error("the frame could not be encoded"));
      }, "image/png");
    });
  }

  /** The Viewer held still and the rest of the page out of reach while the
   * movie is made, or given back. */
  function holding(on) {
    var settings = byId("movie-settings");
    if (settings) settings.disabled = on;
    var cancel = byId("movie-cancel");
    if (cancel) cancel.hidden = !on;
    var progress = byId("movie-progress");
    if (progress) progress.hidden = !on;
    var parts = [byId("viewer-canvas"), document.querySelector(".viewer-under"),
      document.getElementById("sequence-strip")];
    document.querySelectorAll(".viewer-side > .side-section").forEach(function (section) {
      if (section.id !== "side-movie") parts.push(section);
    });
    parts.forEach(function (part) {
      if (part) part.inert = on;
    });
  }

  function about(frames, times, fps) {
    var first = frames[0];
    var last = frames[frames.length - 1];
    var every = frames.length > 1 ? Math.abs(frames[1] - frames[0]) : 1;
    var text = "frames " + first + " to " + last + (every > 1 ? " every " + every : "")
      + ", " + fps + " frames a second";
    if (times[first] != null && times[last] != null) {
      text += ", " + Number(times[first]).toFixed(3) + " to " + Number(times[last]).toFixed(3) + " ns";
    }
    var study = viewer() && viewer().STATE && viewer().STATE.structureInfo;
    return text + (study && study.name ? ", " + study.name : "");
  }

  async function make() {
    if (making) return;
    var view = viewer();
    var state = view && view.STATE;
    if (!view || !view.movie || !state || !state.engine) {
      say("There is no molecule shown to make a movie of.");
      return;
    }
    var name = String((byId("movie-name") || {}).value || "").trim();
    var fps = whole("movie-fps", 24);
    var turn = !!(byId("movie-turn") || {}).checked;
    var stamp = !!(byId("movie-time") || {}).checked;
    making = { cancelled: false };
    holding(true);
    say("Loading the frames…");
    var engine = state.engine;
    var held = view.movie.hold();
    state.makingMovie = true;
    var shownBefore = view.movie.shownFrame();
    var camera = engine.cameraSnapshot();
    var generation = state.viewerGeneration;
    var id = null;
    var outcome = null;
    try {
      var loaded = await view.movie.frames();
      if (!loaded || !loaded.count) {
        say("The study has no frames to make a movie of yet.");
        return;
      }
      var frames = framesOf(loaded.count);
      if (!frames.length) {
        say("No frames are in that range.");
        return;
      }
      var dims = size();
      var started = await post("/api/movies", {
        name: name, fps: fps, width: dims[0], height: dims[1],
        about: about(frames, loaded.times, fps),
      });
      if (!started.ok) {
        say("The movie was not made: " + (started.reason || "no reason given"));
        return;
      }
      id = started.id;
      var progress = byId("movie-progress");
      if (progress) { progress.max = frames.length; progress.value = 0; }
      var step = frames.length > 1 && loaded.times[frames[0]] != null && loaded.times[frames[1]] != null
        ? Math.abs(loaded.times[frames[1]] - loaded.times[frames[0]]) : 0;
      var frameCanvas = document.createElement("canvas");
      frameCanvas.width = dims[0];
      frameCanvas.height = dims[1];
      var context = frameCanvas.getContext("2d");
      // One frame is sent while the next is rendered.
      var sending = Promise.resolve({ ok: true });
      for (var k = 0; k < frames.length; k += 1) {
        if (making.cancelled) break;
        await view.movie.showFrame(frames[k]);
        if (state.viewerGeneration !== generation) {
          outcome = "The structure shown changed while the movie was made, so it was stopped.";
          break;
        }
        // One turn over the movie, the last frame a step short of the first
        // so that the movie loops without a pause.
        if (turn) engine.turnCamera(camera, 2 * Math.PI * k / frames.length);
        var still = await engine.still(dims[0], dims[1]);
        if (!still) {
          outcome = "The frame could not be rendered.";
          break;
        }
        context.drawImage(still, 0, 0, dims[0], dims[1]);
        if (stamp) {
          var time = loaded.times[frames[k]];
          drawTime(context, time != null ? timeSaid(time, step) : "Frame " + frames[k]);
        }
        view.movie.legend(context);
        var blob = await pictureOf(frameCanvas);
        var sent = await sending;
        if (!sent.ok) {
          outcome = "The movie was not made: " + (sent.reason || "no reason given");
          break;
        }
        sending = post("/api/movies/" + id + "/frame", blob, "image/png");
        if (progress) progress.value = k + 1;
        say("Rendered frame " + (k + 1) + " of " + frames.length + "…");
      }
      var last = await sending;
      if (!outcome && !last.ok) outcome = "The movie was not made: " + (last.reason || "no reason given");
      if (making.cancelled || outcome) {
        await post("/api/movies/" + id + "/cancel");
        id = null;
        say(outcome || "The movie was cancelled; nothing was kept.");
        return;
      }
      say("Encoding the movie…");
      var done = await post("/api/movies/" + id + "/finish");
      id = null;
      if (!done.ok) {
        say("The movie was not made: " + (done.reason || "no reason given"));
        return;
      }
      var link = document.createElement("a");
      link.href = "/artifacts/" + done.file.split("/").map(encodeURIComponent).join("/") + "?download=1";
      link.download = done.file.split("/").pop();
      document.body.appendChild(link);
      link.click();
      link.remove();
      say("Made " + done.file + ": " + done.frames + " frames, " + done.seconds + " s, "
        + done.width + " × " + done.height + ", " + done.format.toUpperCase()
        + " (" + done.codec + "), " + (done.bytes / 1e6).toFixed(1) + " MB.");
      window.dispatchEvent(new CustomEvent("dashboard:movie-made", { detail: done }));
    } catch (error) {
      console.warn("the movie was not made", error);
      if (id) await post("/api/movies/" + id + "/cancel");
      say("The movie was not made: " + (error && error.message ? error.message : error));
    } finally {
      state.makingMovie = false;
      if (state.viewerGeneration === generation) {
        try {
          await view.movie.showFrame(shownBefore);
          engine.restoreCamera(camera);
        } catch (error) {
          console.debug("the view was not put back", error);
        }
      }
      view.movie.release(held);
      holding(false);
      making = null;
    }
  }

  /** How many frames and seconds the movie will be, as it is set. */
  function sayLength() {
    var view = viewer();
    var count = view && view.STATE ? view.STATE.playbackFrames : 0;
    var length = byId("movie-length");
    if (!length) return;
    if (!count) {
      length.textContent = "";
      return;
    }
    var frames = framesOf(count).length;
    var fps = whole("movie-fps", 24);
    var dims = size();
    length.textContent = frames + " frames, " + (frames / fps).toFixed(1) + " s, "
      + dims[0] + " × " + dims[1];
  }

  /** What a movie is made with on this computer, or why one cannot be. */
  function loadEncoding() {
    return fetch("/api/movies", { cache: "no-store" })
      .then(function (r) {
        return r.json().catch(function () {
          return { ok: false, reason: "Movies are made by the GUI running on this computer." };
        });
      })
      .catch(function () { return { ok: false, reason: "The server did not answer." }; })
      .then(function (said) {
        var make = byId("movie-make");
        if (make) make.disabled = !said.ok;
        var by = byId("movie-by");
        if (by) by.textContent = said.ok ? said.said : (said.reason || said.error || "");
        sayLength();
        return said;
      });
  }

  function wire() {
    var form = byId("movie-form");
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      make();
    });
    form.addEventListener("input", sayLength);
    form.addEventListener("change", sayLength);
    byId("movie-cancel").addEventListener("click", function () {
      if (making) making.cancelled = true;
    });
    window.addEventListener("dashboard:viewer-page-opened", loadEncoding);
    window.addEventListener("dashboard:viewer-rendered", sayLength);
    loadEncoding();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  window.FastMDXViewerMovie = { make: make, size: size, framesOf: framesOf, timeSaid: timeSaid };
}());
