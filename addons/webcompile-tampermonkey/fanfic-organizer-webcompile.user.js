// ==UserScript==
// @name         Fanfic Organizer — Web compile crawler
// @namespace    https://github.com/BlakeASmith/fanfic-organizer
// @version      1.0.0
// @description  Crawl rendered pages (incl. JS sites) and export a JSON bundle for Fanfic Organizer multi-page EPUB import
// @author       Fanfic Organizer
// @match        *://*/*
// @grant        GM_download
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  const STORAGE_KEY = "fo_webcompile_job";
  const BUNDLE_VERSION = 1;
  const GENERATOR = "fanfic-organizer-webcompile";

  function hostKey(url) {
    try {
      let host = new URL(url).hostname.toLowerCase();
      if (host.startsWith("www.")) host = host.slice(4);
      return host;
    } catch {
      return "";
    }
  }

  function pageKey(url) {
    try {
      const u = new URL(url);
      u.hash = "";
      // Drop trailing slash except root.
      if (u.pathname.length > 1 && u.pathname.endsWith("/")) {
        u.pathname = u.pathname.replace(/\/+$/, "");
      }
      return u.toString();
    } catch {
      return String(url || "").split("#")[0];
    }
  }

  function normalizeDomain(value) {
    let t = String(value || "").trim().toLowerCase();
    if (!t) return "";
    if (t.includes("://")) return hostKey(t);
    t = t.split("/")[0];
    if (t.startsWith("www.")) t = t.slice(4);
    return t;
  }

  function linkAllowed(url, job) {
    const mode = job.expand || "same_domain";
    if (mode === "none") return false;
    if (mode === "free") return true;
    const host = hostKey(url);
    if (!host) return false;
    if (mode === "same_domain") {
      return (job.seedHosts || []).includes(host);
    }
    if (mode === "domains") {
      const allowed = (job.domains || []).map(normalizeDomain).filter(Boolean);
      return allowed.some((d) => host === d || host.endsWith("." + d));
    }
    return false;
  }

  function extractLinks(doc, baseUrl) {
    const out = [];
    const seen = new Set();
    const skipExt =
      /\.(css|js|mjs|json|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|otf|mp3|mp4|pdf|zip|xml|rss|atom)(\?|$)/i;
    doc.querySelectorAll("a[href]").forEach((a) => {
      const href = (a.getAttribute("href") || "").trim();
      if (!href || href.startsWith("#")) return;
      let abs;
      try {
        abs = new URL(href, baseUrl).toString();
      } catch {
        return;
      }
      let u;
      try {
        u = new URL(abs);
      } catch {
        return;
      }
      if (u.protocol !== "http:" && u.protocol !== "https:") return;
      if (skipExt.test(u.pathname)) return;
      const key = pageKey(abs);
      if (!key || seen.has(key)) return;
      seen.add(key);
      out.push(key);
    });
    return out;
  }

  function loadJob() {
    try {
      return GM_getValue(STORAGE_KEY, null);
    } catch {
      return null;
    }
  }

  function saveJob(job) {
    GM_setValue(STORAGE_KEY, job);
  }

  function clearJob() {
    GM_setValue(STORAGE_KEY, null);
  }

  function downloadBundle(job) {
    const payload = {
      version: BUNDLE_VERSION,
      generator: GENERATOR,
      title: job.title || document.title || "",
      author: job.author || "",
      seed_url: job.seedUrl || "",
      pages: (job.pages || []).map((p) => ({
        url: p.url,
        title: p.title || "",
        html: p.html,
      })),
    };
    const text = JSON.stringify(payload, null, 2);
    const name =
      "fanfic-organizer-webcompile-" +
      new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-") +
      ".json";
    const blob = new Blob([text], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    if (typeof GM_download === "function") {
      GM_download({ url, name, saveAs: true });
    } else {
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
    }
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  function captureCurrent(job) {
    const url = pageKey(location.href);
    const html = document.documentElement.outerHTML;
    const title = document.title || "";
    job.pages = job.pages || [];
    job.seen = job.seen || [];
    if (!job.seen.includes(url)) {
      job.seen.push(url);
      job.pages.push({ url, title, html });
    }
    // Enqueue links if still expanding.
    const depth = (job.depthByUrl && job.depthByUrl[url]) || 0;
    if (job.expand !== "none" && depth < (job.maxDepth || 2)) {
      job.queue = job.queue || [];
      extractLinks(document, url).forEach((link) => {
        if (job.seen.includes(link)) return;
        if (job.queue.some((q) => q.url === link)) return;
        if (!linkAllowed(link, job)) return;
        if (job.pages.length + job.queue.length >= (job.maxPages || 50)) return;
        job.queue.push({ url: link, depth: depth + 1 });
        job.depthByUrl = job.depthByUrl || {};
        job.depthByUrl[link] = depth + 1;
      });
    }
    saveJob(job);
    return job;
  }

  function continueCrawl() {
    const job = loadJob();
    if (!job || !job.active) return;

    const here = pageKey(location.href);
    // Capture this page if not yet stored.
    if (!(job.seen || []).includes(here)) {
      captureCurrent(job);
    }

    const refreshed = loadJob();
    if (!refreshed) return;

    if (
      refreshed.pages.length >= (refreshed.maxPages || 50) ||
      !(refreshed.queue || []).length
    ) {
      refreshed.active = false;
      saveJob(refreshed);
      showBanner(
        "Crawl finished (" +
          refreshed.pages.length +
          " pages). Export the bundle, then import it in Fanfic Organizer."
      );
      return;
    }

    const next = refreshed.queue.shift();
    saveJob(refreshed);
    showBanner(
      "Web compile: " +
        refreshed.pages.length +
        " captured, next → " +
        next.url
    );
    location.href = next.url;
  }

  function showBanner(text) {
    let el = document.getElementById("fo-webcompile-banner");
    if (!el) {
      el = document.createElement("div");
      el.id = "fo-webcompile-banner";
      el.style.cssText =
        "position:fixed;z-index:2147483647;left:12px;right:12px;bottom:12px;" +
        "padding:10px 14px;background:#1a1a1a;color:#f5f5f5;font:14px/1.4 system-ui;" +
        "border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.35);";
      document.documentElement.appendChild(el);
    }
    el.textContent = text;
  }

  function promptStart() {
    const seed = pageKey(location.href);
    const expand =
      prompt(
        "Link expansion: same_domain | domains | free | none",
        "same_domain"
      ) || "same_domain";
    let domains = [];
    if (expand === "domains") {
      const raw = prompt("Allowed domains (comma-separated)", hostKey(seed));
      domains = String(raw || "")
        .split(",")
        .map(normalizeDomain)
        .filter(Boolean);
    }
    const maxPages = parseInt(prompt("Max pages", "30") || "30", 10) || 30;
    const maxDepth = parseInt(prompt("Max depth", "2") || "2", 10) || 2;
    const title = prompt("Book title (optional)", document.title || "") || "";
    const job = {
      active: true,
      seedUrl: seed,
      seedHosts: [hostKey(seed)],
      expand: expand.trim(),
      domains,
      maxPages,
      maxDepth,
      title,
      author: "",
      pages: [],
      seen: [],
      queue: [],
      depthByUrl: { [seed]: 0 },
    };
    saveJob(job);
    captureCurrent(job);
    continueCrawl();
  }

  function addThisPage() {
    let job = loadJob();
    if (!job) {
      job = {
        active: false,
        seedUrl: pageKey(location.href),
        seedHosts: [hostKey(location.href)],
        expand: "none",
        domains: [],
        maxPages: 200,
        maxDepth: 0,
        title: document.title || "",
        author: "",
        pages: [],
        seen: [],
        queue: [],
        depthByUrl: {},
      };
    }
    job.expand = "none";
    job.active = false;
    captureCurrent(job);
    showBanner("Added page (" + (loadJob().pages || []).length + " in bundle).");
  }

  function exportNow() {
    const job = loadJob();
    if (!job || !(job.pages || []).length) {
      alert("No pages captured yet.");
      return;
    }
    job.active = false;
    saveJob(job);
    downloadBundle(job);
    showBanner("Bundle exported (" + job.pages.length + " pages).");
  }

  function stopAndClear() {
    clearJob();
    const el = document.getElementById("fo-webcompile-banner");
    if (el) el.remove();
    alert("Web compile job cleared.");
  }

  GM_registerMenuCommand("Web compile: start crawl from this page", promptStart);
  GM_registerMenuCommand("Web compile: add this page only", addThisPage);
  GM_registerMenuCommand("Web compile: export JSON bundle", exportNow);
  GM_registerMenuCommand("Web compile: stop / clear", stopAndClear);

  // Resume automatic crawl after navigation.
  setTimeout(continueCrawl, 600);
})();
