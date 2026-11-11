/* Raydex Learn sync.
   Runs first in <head> of every course page. It:
   1) restores the saved progress from the account into localStorage
      BEFORE the course script reads it,
   2) watches the course's storage key and saves changes to the server,
   3) shows a small status pill (Shadow DOM, so course CSS cannot touch it).

   Courses may expose window.RAYDEX_PROGRESS() -> number to report an exact
   "items done" count; it is sent with each save. */
(function () {
  var C = window.__RL;
  if (!C) return;
  var ls;
  try { ls = window.localStorage; ls.getItem("x"); } catch (e) { ls = null; }

  var status = "anon"; // anon | saving | saved | offline | nostorage
  var setStatus = function () {};

  function score(txt) {
    try { var o = JSON.parse(txt || "{}"); return +o.xp || 0; } catch (e) { return 0; }
  }

  function doneCount() {
    if (typeof window.RAYDEX_PROGRESS !== "function") return null;
    try {
      var n = window.RAYDEX_PROGRESS();
      if (typeof n === "number" && isFinite(n)) return Math.max(0, Math.round(n));
    } catch (e) {}
    return null;
  }

  function lessonFlags() {
    if (typeof window.RAYDEX_LESSONS !== "function") return null;
    try {
      var a = window.RAYDEX_LESSONS();
      if (Array.isArray(a) && a.length > 0 && a.length <= 500) {
        return a.map(function (x) { return x ? 1 : 0; });
      }
    } catch (e) {}
    return null;
  }

  if (!ls) {
    status = "nostorage";
  } else if (C.uid) {
    var uid = String(C.uid);
    var owner = ls.getItem("rl:uid");
    var tsKey = "rl:ts:" + C.slug;
    var epKey = "rl:ep:" + C.slug;
    var local = ls.getItem(C.key);

    // Another account used this browser: do not mix their progress.
    if (owner && owner !== uid) {
      ls.removeItem(C.key);
      ls.removeItem(tsKey);
      ls.removeItem(epKey);
      local = null;
    }

    // The account owner hit "reset progress" on the server: wipe this
    // device's copy too, then take the (empty) server state.
    var serverEp = +C.reset_epoch || 0;
    var localEp = +ls.getItem(epKey) || 0;
    var wasReset = serverEp > localEp;
    if (wasReset) {
      ls.removeItem(C.key);
      ls.removeItem(tsKey);
      local = null;
      ls.setItem(epKey, String(serverEp));
    }

    var server = C.state;           // string or null
    var useServer = false;
    if (server) {
      if (wasReset) useServer = true;
      else if (!local) useServer = true;
      else if (ls.getItem(tsKey) === String(C.updated)) useServer = false; // local is in step with server
      else useServer = score(server) >= score(local);                      // changed elsewhere: more XP wins
    }
    if (useServer) {
      ls.setItem(C.key, server);
      ls.setItem(tsKey, String(C.updated));
    }
    ls.setItem("rl:uid", uid);
    status = "saved";

    var timer = null, inflight = false, dirty = false;

    var push = function (keepalive) {
      var txt = ls.getItem(C.key);
      if (txt === null) return;
      if (inflight) { dirty = true; return; }
      inflight = true; dirty = false; status = "saving"; setStatus();
      var body = { state: txt };
      var d = doneCount();
      if (d !== null) body.done = d;
      var lf = lessonFlags();
      if (lf) body.lessons = lf;
      fetch(C.api, {
        method: "POST",
        credentials: "same-origin",
        keepalive: !!keepalive && txt.length < 60000,
        headers: { "Content-Type": "application/json", "X-CSRF-Token": C.csrf },
        body: JSON.stringify(body)
      }).then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      }).then(function (j) {
        ls.setItem(tsKey, String(j.updated));
        status = "saved";
      }).catch(function () {
        status = "offline";
      }).then(function () {
        inflight = false; setStatus();
        if (dirty) schedule();
      });
    };
    window.__rlPush = push; // lets the pill's Retry button push on demand
    var schedule = function () {
      clearTimeout(timer);
      timer = setTimeout(push, 2000);
    };

    // Watch writes to the course key (same tab).
    var orig = Storage.prototype.setItem;
    Storage.prototype.setItem = function (k, v) {
      orig.apply(this, arguments);
      if (this === ls && k === C.key) schedule();
    };
    var origRemove = Storage.prototype.removeItem;
    Storage.prototype.removeItem = function (k) {
      origRemove.apply(this, arguments);
      if (this === ls && k === C.key) schedule();
    };

    // Other tabs/windows writing the same key.
    window.addEventListener("storage", function (e) {
      if (e.key === C.key) schedule();
    });

    // Save when the tab hides or closes, and when the network returns.
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden" && timer) { clearTimeout(timer); timer = null; push(true); }
    });
    window.addEventListener("pagehide", function () { if (timer) { clearTimeout(timer); timer = null; push(true); } });
    window.addEventListener("online", function () { push(); });

    // Push once at load if this device has progress the server does not.
    if (!useServer && local && (!server || ls.getItem(tsKey) !== String(C.updated))) schedule();
  }

  /* ---- status pill ---- */
  function mount() {
    var host = document.createElement("div");
    host.style.cssText = "position:fixed;left:10px;bottom:calc(10px + env(safe-area-inset-bottom,0px));z-index:2147483000";
    var root = host.attachShadow({ mode: "open" });
    root.innerHTML =
      '<style>' +
      ':host{all:initial}' +
      '.p{display:flex;align-items:center;gap:8px;font:600 12px/1 system-ui,sans-serif;' +
      'background:#10281a;color:#eaf3ec;border:1px solid #26392e;border-radius:999px;padding:7px 12px;' +
      'box-shadow:0 2px 10px rgba(0,0,0,.25);opacity:.92}' +
      'a{color:#ffc870;text-decoration:none;font-weight:700}' +
      'a:hover,a:focus-visible{text-decoration:underline}' +
      'a:focus-visible{outline:2px solid #ffc870;outline-offset:2px;border-radius:4px}' +
      'button{font:700 12px/1 system-ui,sans-serif;color:#10281a;background:#ffc870;border:0;' +
      'border-radius:999px;padding:5px 10px;cursor:pointer}' +
      '.d{width:8px;height:8px;border-radius:50%;background:#7be3a4}' +
      '.d.s{background:#ffc070}.d.o{background:#ff9d88}.d.a{background:#8aa595}' +
      '.sep{opacity:.35}' +
      '</style><div class="p"><a class="home" href="' + C.home + '">All courses</a>' +
      (C.dash ? '<span class="sep">|</span><a class="home" href="' + C.dash + '">Dashboard</a>' : "") +
      '<span class="sep">|</span><span class="d"></span><span class="t" role="status" aria-live="polite"></span><button class="r" type="button" hidden>Retry</button></div>';
    var dot = root.querySelector(".d"), txt = root.querySelector(".t");
    var retry = root.querySelector(".r");
    retry.addEventListener("click", function () {
      if (window.__rlPush) window.__rlPush();
      else location.reload();
    });
    setStatus = function () {
      retry.hidden = status !== "offline";
      if (status === "anon") {
        dot.className = "d a";
        txt.innerHTML = '<a href="' + C.login + '">Log in to save your progress</a>';
      } else if (status === "saving") { dot.className = "d s"; txt.textContent = "Saving"; }
      else if (status === "offline") { dot.className = "d o"; txt.textContent = "Saved on this device"; }
      else if (status === "nostorage") { dot.className = "d o"; txt.textContent = "Your browser blocks saving"; }
      else { dot.className = "d"; txt.textContent = "Saved to your account"; }
    };
    setStatus();
    document.body.appendChild(host);
  }
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);
})();
