// @ts-check
(() => {
  "use strict";

  /** @typedef {{guildId:number,userId:number}} CinemaBoot */
  /** @typedef {{media_type?:string,tmdb_id?:number,title?:string,poster_url?:string,backdrop_url?:string,overview?:string,year?:number,rating?:number,progress_ratio?:number,progress_seconds?:number,duration_seconds?:number,completed?:boolean,watchlisted?:boolean,season_number?:number,episode_number?:number,metadata?:Record<string,any>,result_kind?:string,series_id?:number,still_url?:string,series_title?:string,source_label?:string,playable?:boolean}} MediaItem */

  /** @type {CinemaBoot} */
  const BOOT = window.__DANK_CINEMA_BOOT__ || { guildId: 0, userId: 0 };
  const AUTH_QUERY = window.location.search || "";
  const API_BASE = `/cinema/${BOOT.guildId}/api`;
  const app = document.getElementById("app");
  if (!app) return;

  const state = {
    home: null,
    library: null,
    profile: null,
    feeds: null,
    notifications: null,
    activeView: "",
    searchController: null,
    searchTimer: null,
    details: new Map(),
    seasons: new Map(),
  };

  const icon = {
    home: "⌂",
    search: "⌕",
    library: "▤",
    feeds: "≋",
    profile: "●",
    bell: "♢",
    info: "ⓘ",
    play: "▶",
    add: "+",
    check: "✓",
    discord: "◈",
  };

  function authUrl(path) {
    const join = path.includes("?") ? "&" : "?";
    return `${path}${AUTH_QUERY ? join + AUTH_QUERY.slice(1) : ""}`;
  }

  function autoQualityMode() {
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
    /** @type {any} */
    const nav = navigator;
    const connection = nav.connection || nav.mozConnection || nav.webkitConnection || {};
    const saveData = connection.saveData === true;
    const effective = String(connection.effectiveType || "").toLowerCase();
    const memory = Number(nav.deviceMemory || 0);
    const cores = Number(nav.hardwareConcurrency || 0);
    const width = Math.max(window.innerWidth || 0, document.documentElement.clientWidth || 0);

    if (
      reduced
      || saveData
      || ["slow-2g", "2g"].includes(effective)
      || (memory > 0 && memory <= 2)
      || (cores > 0 && cores <= 2)
    ) return "lite";

    if (
      effective === "3g"
      || (memory > 0 && memory <= 4)
      || (cores > 0 && cores <= 4)
      || width < 720
    ) return "standard";

    return "high";
  }

  function applyVisualQuality(preference = "auto") {
    const requested = String(preference || "auto").toLowerCase();
    const resolved = ["high", "standard", "lite"].includes(requested)
      ? requested
      : autoQualityMode();
    document.documentElement.dataset.qualityPreference = requested;
    document.documentElement.dataset.quality = resolved;
    return resolved;
  }

  async function api(path, options = {}) {
    const response = await fetch(authUrl(API_BASE + path), {
      credentials: "same-origin",
      headers: {
        "Accept": "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
      ...options,
    });
    if (!response.ok) {
      const text = (await response.text()).trim();
      throw new Error(text || `Cinema request failed (${response.status}).`);
    }
    return response.json();
  }

  function node(tag, className = "", text = "") {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== "") element.textContent = String(text);
    return element;
  }

  function button(label, className = "btn", onClick = null) {
    const el = node("button", className, label);
    el.type = "button";
    if (onClick) el.addEventListener("click", onClick);
    return el;
  }

  function safeImage(url) {
    const clean = String(url || "");
    return clean.startsWith("https://image.tmdb.org/")
      || clean.startsWith("https://cdn.discordapp.com/")
      || clean.startsWith("https://media.discordapp.net/")
      ? clean
      : "";
  }

  function tmdbImageVariant(url, size) {
    const clean = safeImage(url);
    if (!clean.startsWith("https://image.tmdb.org/")) return clean;
    return clean.replace(/\/t\/p\/(?:w\d+|original)\//, `/t/p/${size}/`);
  }

  function configureArtwork(image, url, kind = "poster") {
    const clean = safeImage(url);
    if (!clean) return false;
    if (!clean.startsWith("https://image.tmdb.org/")) {
      image.src = clean;
      return true;
    }
    const variants = kind === "backdrop"
      ? [["w300", 300], ["w780", 780], ["w1280", 1280]]
      : kind === "profile"
        ? [["w185", 185], ["w300", 300]]
        : kind === "still"
          ? [["w300", 300], ["w780", 780]]
          : [["w185", 185], ["w342", 342], ["w500", 500], ["w780", 780]];
    image.src = tmdbImageVariant(clean, variants[Math.min(1, variants.length - 1)][0]);
    image.srcset = variants
      .map(([size, width]) => `${tmdbImageVariant(clean, size)} ${width}w`)
      .join(", ");
    image.sizes = kind === "backdrop"
      ? "100vw"
      : kind === "still"
        ? "(max-width: 620px) calc(100vw - 46px), 200px"
        : kind === "profile"
          ? "88px"
          : "(max-width: 620px) 44vw, (max-width: 1199px) 22vw, 190px";
    return true;
  }

  function initials(name) {
    const parts = String(name || "?").trim().split(/\s+/).filter(Boolean);
    return (parts[0]?.[0] || "?") + (parts.length > 1 ? parts.at(-1)?.[0] || "" : "");
  }

  function formatDuration(minutes) {
    const value = Number(minutes || 0);
    if (!value) return "";
    const h = Math.floor(value / 60);
    const m = value % 60;
    return h ? `${h}h ${m}m` : `${m}m`;
  }

  function formatSeconds(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    const h = Math.floor(value / 3600);
    const m = Math.floor((value % 3600) / 60);
    const s = Math.floor(value % 60);
    return h
      ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
      : `${m}:${String(s).padStart(2, "0")}`;
  }

  function toast(message, kind = "") {
    let stack = document.querySelector(".toast-stack");
    if (!stack) {
      stack = node("div", "toast-stack");
      document.body.appendChild(stack);
    }
    const item = node("div", `toast ${kind}`.trim(), message);
    stack.appendChild(item);
    window.setTimeout(() => item.remove(), 4200);
  }

  function pageError(title, message, retry) {
    const wrap = node("div", "page");
    const card = node("div", "state-card");
    const h = node("h2", "", title);
    const p = node("p", "", message);
    card.append(h, p);
    if (retry) card.appendChild(button("Try Again", "btn primary", retry));
    wrap.appendChild(card);
    return wrap;
  }

  function skeletonPage() {
    const page = node("main", "page");
    const hero = node("div", "hero skeleton");
    hero.style.minHeight = "420px";
    page.appendChild(hero);
    const grid = node("div", "loading-grid section");
    for (let i = 0; i < 6; i += 1) grid.appendChild(node("div", "loading-card skeleton"));
    page.appendChild(grid);
    return page;
  }

  function currentRoute() {
    const raw = (location.hash || "#home").slice(1);
    const [pathPart, queryPart = ""] = raw.split("?");
    const parts = pathPart.split("/").filter(Boolean);
    const params = new URLSearchParams(queryPart);
    return { parts, params };
  }

  function go(hash) {
    const target = hash.startsWith("#") ? hash : `#${hash}`;
    if (location.hash === target) {
      renderRoute();
    } else {
      location.hash = target;
    }
  }

  function openExternal(url) {
    const clean = String(url || "");
    if (!/^https:\/\//i.test(clean)) return;
    window.open(clean, "_blank", "noopener,noreferrer");
  }

  function headerButton(label, route, activeView) {
    const btn = button(label, `nav-btn ${activeView === route ? "active" : ""}`);
    btn.addEventListener("click", () => go(route));
    return btn;
  }

  function renderShell(content, activeView = "home") {
    app.textContent = "";
    const home = state.home || {};
    const discord = home.discord || state.profile?.discord || {};
    const unread = Number(home.notifications_unread || 0);

    const topbar = node("header", "topbar");
    const inner = node("div", "topbar-inner");
    const brand = node("a", "brand-lockup");
    brand.href = "#home";
    brand.setAttribute("aria-label", "Dank Cinema home");
    const lockup = node("img", "brand-lockup-img");
    lockup.src = "/movie/assets/dank-cinema-brand.webp?v=art-system-v5";
    lockup.alt = "Dank Cinema — A feature of The 420 Lobby";
    lockup.width = 1200;
    lockup.height = 278;
    lockup.decoding = "async";
    brand.appendChild(lockup);

    const nav = node("nav", "desktop-nav");
    nav.setAttribute("aria-label", "Cinema navigation");
    nav.append(
      headerButton("Home", "home", activeView),
      headerButton("Search", "search", activeView),
      headerButton("My Stuff", "library", activeView),
      headerButton("Feeds", "feeds", activeView),
    );

    const actions = node("div", "top-actions");
    const searchBtn = button(icon.search, "icon-btn", () => go("search"));
    searchBtn.setAttribute("aria-label", "Search");
    const bell = button(icon.bell, `icon-btn ${unread ? "badge-dot" : ""}`, () => go("notifications"));
    bell.setAttribute("aria-label", "Notifications");
    bell.dataset.count = String(Math.min(99, unread));
    const avatar = button("", "icon-btn avatar-btn", () => go("profile"));
    avatar.setAttribute("aria-label", "Profile");
    const avatarUrl = safeImage(discord.avatar_url);
    if (avatarUrl) {
      const image = node("img");
      image.src = avatarUrl;
      image.alt = "";
      image.loading = "lazy";
      avatar.appendChild(image);
    } else {
      avatar.appendChild(node("span", "avatar-fallback", initials(discord.user_name)));
    }
    actions.append(searchBtn, bell, avatar);
    inner.append(brand, nav, actions);
    topbar.appendChild(inner);

    const bottom = node("nav", "bottom-nav");
    bottom.setAttribute("aria-label", "Cinema mobile navigation");
    for (const [route, glyph, label] of [
      ["home", icon.home, "Home"],
      ["search", icon.search, "Search"],
      ["library", icon.library, "My Stuff"],
      ["feeds", icon.feeds, "Feeds"],
      ["profile", icon.profile, "Profile"],
    ]) {
      const b = button(`${glyph}\n${label}`, activeView === route ? "active" : "");
      b.style.whiteSpace = "pre-line";
      b.addEventListener("click", () => go(route));
      bottom.appendChild(b);
    }

    app.append(topbar, content, bottom);
  }

  function itemArt(item) {
    const metadata = item?.metadata && typeof item.metadata === "object" ? item.metadata : {};
    return safeImage(
      item.poster_url
      || item.still_url
      || metadata.poster_url
      || metadata.still_url
      || metadata.series_poster_url
      || ""
    );
  }

  function itemBackdrop(item) {
    const metadata = item?.metadata && typeof item.metadata === "object" ? item.metadata : {};
    return safeImage(item.backdrop_url || metadata.backdrop_url || itemArt(item));
  }

  function itemTitle(item) {
    return String(item?.title || item?.series_title || item?.metadata?.title || "Untitled");
  }

  function itemKind(item) {
    return String(item?.media_type || item?.result_kind || item?.metadata?.media_type || "");
  }

  function detailsTarget(item) {
    const kind = itemKind(item);
    const tmdbId = Number(item?.tmdb_id || item?.metadata?.tmdb_id || item?.metadata?.catalog_id || 0);
    if ((kind === "movie" || kind === "tv") && tmdbId > 0) return `details/${kind}/${tmdbId}`;
    if (kind === "episode") {
      const seriesId = Number(item?.series_id || item?.metadata?.series_id || 0);
      const season = Number(item?.season_number || 0);
      const episode = Number(item?.episode_number || 0);
      if (seriesId > 0) {
        const params = new URLSearchParams();
        if (season > 0) params.set("season", String(season));
        if (episode > 0) params.set("episode", String(episode));
        const query = params.toString();
        return `details/tv/${seriesId}${query ? `?${query}` : ""}`;
      }
    }
    return "";
  }

  function metadataLine(item) {
    const parts = [];
    if (item.year) parts.push(String(item.year));
    if (Number(item.rating || 0) > 0) parts.push(`★ ${Number(item.rating).toFixed(1)}`);
    if (item.media_type === "episode") {
      parts.push(`S${Number(item.season_number || 0)} E${Number(item.episode_number || 0)}`);
    } else if (item.media_type) {
      parts.push(item.media_type === "tv" ? "Series" : "Movie");
    }
    if (item.source_label) parts.push(String(item.source_label));
    return parts.join(" • ");
  }

  async function toggleWatchlist(item, desired) {
    const mediaType = itemKind(item);
    const tmdbId = Number(item?.tmdb_id || item?.metadata?.tmdb_id || item?.metadata?.catalog_id || 0);
    if (!["movie", "tv"].includes(mediaType) || tmdbId <= 0) {
      toast("Watchlist is available for canonical movie and TV titles.", "error");
      return;
    }
    try {
      await api("/library", {
        method: "POST",
        body: JSON.stringify({
          action: "watchlist",
          media_type: mediaType,
          tmdb_id: tmdbId,
          title: itemTitle(item),
          enabled: desired,
          metadata: {
            poster_url: itemArt(item),
            backdrop_url: itemBackdrop(item),
            year: Number(item.year || 0),
            media_type: mediaType,
            adult: Boolean(item.adult || item?.metadata?.adult),
          },
        }),
      });
      state.library = null;
      state.home = null;
      toast(desired ? "Added to Watchlist." : "Removed from Watchlist.");
      await ensureHome(true);
      renderRoute();
    } catch (error) {
      toast(error.message || "Watchlist update failed.", "error");
    }
  }

  function mediaCard(item, options = {}) {
    const card = node("article", "media-card");
    const art = node("div", "card-art");
    const artUrl = itemArt(item);
    const kind = itemKind(item);
    if (artUrl) {
      const image = node("img");
      configureArtwork(image, artUrl, kind === "episode" ? "still" : "poster");
      image.alt = `${itemTitle(item)} artwork`;
      image.loading = "lazy";
      image.decoding = "async";
      art.appendChild(image);
    }
    if (kind === "episode") {
      art.appendChild(node("span", "card-badge", `S${item.season_number || 0} E${item.episode_number || 0}`));
    } else if (item.playable) {
      art.appendChild(node("span", "card-badge", "Playable"));
    }
    const ratio = Number(item.progress_ratio || 0);
    if (ratio > 0 && ratio < 1) {
      const progress = node("div", "card-progress");
      const fill = node("span");
      fill.style.width = `${Math.round(ratio * 100)}%`;
      progress.appendChild(fill);
      art.appendChild(progress);
    }

    const copy = node("div", "card-copy");
    copy.append(
      node("div", "card-title", itemTitle(item)),
      node("div", "card-meta", metadataLine(item)),
    );
    card.append(art, copy);

    const target = detailsTarget(item);
    if (target) {
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.addEventListener("click", (event) => {
        if (event.target.closest("button")) return;
        go(target);
      });
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          go(target);
        }
      });
    } else if (item.result_kind === "playable_source" || item.source_label) {
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.addEventListener("click", () => go(`search?q=${encodeURIComponent(itemTitle(item))}`));
    }

    if (["movie", "tv"].includes(kind) && Number(item.tmdb_id || 0) > 0) {
      const hover = node("div", "card-hover");
      const add = button(item.watchlisted ? icon.check : icon.add, `mini-action ${item.watchlisted ? "active" : ""}`);
      add.setAttribute("aria-label", item.watchlisted ? "Remove from Watchlist" : "Add to Watchlist");
      add.addEventListener("click", (event) => {
        event.stopPropagation();
        toggleWatchlist(item, !item.watchlisted);
      });
      hover.appendChild(add);
      card.appendChild(hover);
    }
    return card;
  }

  function mediaRail(title, items, key = "") {
    if (!Array.isArray(items) || !items.length) return null;
    const section = node("section", "section");
    if (key) section.dataset.section = key;
    const head = node("div", "section-head");
    const text = node("div");
    text.appendChild(node("h2", "section-title", title));
    head.appendChild(text);
    const rail = node("div", "rail");
    items.forEach((item) => rail.appendChild(mediaCard(item)));
    section.append(head, rail);
    return section;
  }

  async function ensureHome(force = false) {
    if (state.home && !force) return state.home;
    state.home = await api("/home");
    const quality = state.home?.profile?.preferences?.visual_quality || "auto";
    applyVisualQuality(quality);
    return state.home;
  }

  function renderHero(hero) {
    if (!hero) return null;
    const wrap = node("section", "hero");
    const media = node("div", "hero-media");
    const art = safeImage(hero.backdrop_url || hero.poster_url);
    if (art) {
      const image = node("img");
      configureArtwork(image, art, "backdrop");
      image.alt = "";
      image.fetchPriority = "high";
      image.decoding = "async";
      media.appendChild(image);
    }
    const grain = node("div", "hero-grain");
    const content = node("div", "hero-content");
    content.appendChild(node("div", "hero-kicker", hero.kind === "session" ? "● Live Cinema" : "Dank Cinema"));
    content.appendChild(node("h1", "hero-title", hero.title || "Dank Cinema"));
    if (hero.subtitle) content.appendChild(node("div", "hero-copy", hero.subtitle));
    const actions = node("div", "hero-actions");
    const action = hero.action || {};
    if (action.kind === "watch_url" && action.url) {
      actions.appendChild(button(action.label || "Watch", "btn primary", () => { location.href = action.url; }));
    } else if (action.kind === "details" && action.media_type && action.tmdb_id) {
      actions.appendChild(button(action.label || "More Info", "btn primary", () => go(`details/${action.media_type}/${action.tmdb_id}`)));
    }
    content.appendChild(actions);
    wrap.append(media, grain, content);
    return wrap;
  }

  async function renderHome() {
    renderShell(skeletonPage(), "home");
    try {
      const home = await ensureHome();
      const page = node("main", "page");
      const hero = renderHero(home.hero);
      if (hero) page.appendChild(hero);

      if (Array.isArray(home.active_sessions) && home.active_sessions.length > 1) {
        const liveItems = home.active_sessions.slice(1).map((room) => ({
          ...room,
          media_type: room.media_type || "movie",
          tmdb_id: room.tmdb_id || 0,
          result_kind: "session",
        }));
        const live = mediaRail("Live Watch Parties", liveItems, "live");
        if (live) page.appendChild(live);
      }

      for (const section of home.sections || []) {
        const rail = mediaRail(section.title, section.items, section.key);
        if (rail) page.appendChild(rail);
      }

      if (!hero && !(home.sections || []).length) {
        page.appendChild(pageError(
          "Nothing to show yet",
          "Dank Cinema has no library, discovery, feed, or active-session content to display right now.",
          () => { state.home = null; renderHome(); }
        ).firstChild);
      }
      renderShell(page, "home");
    } catch (error) {
      renderShell(pageError("Cinema Home could not load", error.message || "Try again.", () => { state.home = null; renderHome(); }), "home");
    }
  }

  function searchResultCard(item) {
    if (item.result_kind === "playable_source") {
      const card = node("article", "state-card");
      const kicker = node("div", "feed-category", "Playable source");
      const title = node("h3", "", itemTitle(item));
      const source = node("div", "feed-meta", item.source_label || "Cinema source");
      const action = button("View matching titles", "btn secondary", () => {
        const input = document.querySelector(".search-input");
        if (!input) return;
        input.value = itemTitle(item);
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
      });
      card.append(kicker, title, source, action);
      return card;
    }
    return mediaCard(item);
  }

  async function renderSearch() {
    const page = node("main", "page search-shell");
    const top = node("div", "search-top");
    const input = node("input", "search-input");
    input.type = "search";
    input.placeholder = "Search movies, TV, known episodes, Watchlist, and playable sources…";
    input.autocomplete = "off";
    input.setAttribute("aria-label", "Search Dank Cinema");
    const results = node("div", "result-grid");
    page.append(top, results);
    top.appendChild(input);
    renderShell(page, "search");

    const route = currentRoute();
    const initial = route.params.get("q") || "";
    input.value = initial;

    const perform = async () => {
      const query = input.value.trim();
      if (state.searchController) state.searchController.abort();
      if (!query) {
        results.textContent = "";
        results.appendChild(node("div", "result-empty", "Start typing to search the real Cinema catalog, your library, and playable sources."));
        return;
      }
      const controller = new AbortController();
      state.searchController = controller;
      results.textContent = "";
      for (let i = 0; i < 8; i += 1) results.appendChild(node("div", "loading-card skeleton"));
      try {
        const data = await api(`/search?q=${encodeURIComponent(query)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        results.textContent = "";
        const rows = Array.isArray(data.results) ? data.results : [];
        if (!rows.length) {
          results.appendChild(node(
            "div",
            "result-empty",
            data.notice || `No real Cinema results matched “${query}”.`,
          ));
          return;
        }
        rows.forEach((item) => results.appendChild(searchResultCard(item)));
      } catch (error) {
        if (error.name === "AbortError") return;
        results.textContent = "";
        results.appendChild(node("div", "result-empty", error.message || "Search failed."));
      }
    };

    input.addEventListener("input", () => {
      if (state.searchTimer) clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(perform, 260);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        if (state.searchTimer) clearTimeout(state.searchTimer);
        perform();
      }
    });
    if (initial) perform();
    setTimeout(() => input.focus(), 30);
  }

  async function ensureLibrary(force = false) {
    if (state.library && !force) return state.library;
    state.library = await api("/library");
    return state.library;
  }

  async function renderLibrary(tab = "continue") {
    const page = node("main", "page");
    const title = node("h1", "", "My Stuff");
    const sub = node("p", "section-sub", "Your real Watchlist, progress, and viewing history across devices.");
    const tabs = node("div", "library-tabs");
    const content = node("div");
    page.append(title, sub, tabs, content);
    renderShell(page, "library");
    try {
      const library = await ensureLibrary();
      const options = [
        ["continue", "Continue Watching", library.continue_watching || []],
        ["watchlist", "Watchlist", library.watchlist || []],
        ["history", "Recently Watched", library.recently_watched || []],
        ["again", "Watch Again", library.watch_again || []],
      ];
      const selected = options.find((row) => row[0] === tab) || options[0];
      options.forEach(([key, label]) => {
        tabs.appendChild(button(label, `tab-btn ${selected[0] === key ? "active" : ""}`, () => renderLibrary(key)));
      });
      const rows = selected[2];
      if (!rows.length) {
        content.appendChild(node("div", "state-card", selected[0] === "watchlist"
          ? "Your Watchlist is empty. Add movies or shows from Search or Details."
          : selected[0] === "continue"
            ? "Nothing is waiting to be resumed."
            : "No viewing history is available yet."));
      } else {
        const grid = node("div", "result-grid");
        rows.forEach((item) => grid.appendChild(mediaCard(item)));
        content.appendChild(grid);
      }
    } catch (error) {
      content.appendChild(node("div", "state-card", error.message || "Library could not load."));
    }
  }

  async function setWatchlistFromDetails(detailsData, enabled) {
    const details = detailsData.details;
    await toggleWatchlist({
      media_type: details.media_type,
      tmdb_id: details.tmdb_id,
      title: details.title,
      poster_url: details.poster_url,
      backdrop_url: details.backdrop_url,
      year: details.year,
      adult: Boolean(details.adult),
      watchlisted: !enabled,
    }, enabled);
  }

  function renderCast(cast) {
    if (!Array.isArray(cast) || !cast.length) return null;
    const panel = node("section", "panel");
    panel.appendChild(node("h2", "", "Cast"));
    const row = node("div", "people-row");
    cast.slice(0, 14).forEach((person) => {
      const wrap = node("div", "person");
      const image = node("img");
      const url = safeImage(person.profile_url);
      if (url) configureArtwork(image, url, "profile");
      image.alt = "";
      image.loading = "lazy";
      wrap.append(
        image,
        node("div", "person-name", person.name || ""),
        node("div", "person-role", person.character || ""),
      );
      row.appendChild(wrap);
    });
    panel.appendChild(row);
    return panel;
  }

  function latestEpisodeProgress(rows) {
    const episodes = Array.isArray(rows) ? rows.slice() : [];
    episodes.sort((a, b) => String(b.last_watched_at || "").localeCompare(String(a.last_watched_at || "")));
    return episodes[0] || null;
  }

  async function playInTheater(item, hostSession, control = null) {
    const roomId = String(hostSession?.room_id || "");
    const mediaType = itemKind(item);
    if (!roomId || !hostSession?.is_host) {
      toast("Start or host a Cinema session in Discord before replacing Theater playback.", "error");
      return;
    }
    const payload = {
      room_id: roomId,
      media_type: mediaType,
      tmdb_id: Number(item?.tmdb_id || 0),
    };
    if (mediaType === "episode") {
      payload.series_id = Number(item?.series_id || item?.metadata?.series_id || 0);
      payload.season_number = Number(item?.season_number || 0);
      payload.episode_number = Number(item?.episode_number || 0);
    }
    if (!["movie", "episode"].includes(mediaType)) {
      toast("Choose a movie or a specific TV episode to play.", "error");
      return;
    }

    if (control) control.disabled = true;
    try {
      const response = await api("/play", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (!response?.watch_url) throw new Error("Cinema did not return a Theater link.");
      location.href = response.watch_url;
    } catch (error) {
      toast(error.message || "Cinema playback could not start.", "error");
      if (control) control.disabled = false;
    }
  }

  async function loadSeason(seriesId, seasonNumber, host, hostSession = null, focusEpisode = 0) {
    const key = `${seriesId}:${seasonNumber}`;
    let data = state.seasons.get(key);
    if (!data) {
      host.textContent = "";
      host.appendChild(node("div", "state-card", "Loading episodes…"));
      data = await api(`/season/${seriesId}/${seasonNumber}`);
      state.seasons.set(key, data);
    }
    host.textContent = "";
    const list = node("div", "episode-list");
    const episodes = Array.isArray(data.episodes) ? data.episodes : [];
    if (!episodes.length) {
      host.appendChild(node("div", "state-card", "No real episode metadata is available for this season."));
      return;
    }
    episodes.forEach((ep) => {
      const card = node("article", "episode-card");
      const episodeNumber = Number(ep.episode_number || 0);
      if (focusEpisode > 0 && episodeNumber === Number(focusEpisode)) {
        card.classList.add("episode-current");
        card.setAttribute("aria-current", "true");
      }
      const still = node("div", "episode-still");
      const art = safeImage(ep.still_url);
      if (art) {
        const image = node("img");
        configureArtwork(image, art, "still");
        image.alt = "";
        image.loading = "lazy";
        image.decoding = "async";
        still.appendChild(image);
      }
      const copy = node("div");
      copy.appendChild(node("div", "episode-title", `E${ep.episode_number} • ${ep.title}`));
      if (ep.overview) copy.appendChild(node("div", "episode-copy", ep.overview));
      const meta = [ep.runtime ? `${ep.runtime}m` : "", ep.air_date || "", ep.rating ? `★ ${Number(ep.rating).toFixed(1)}` : ""].filter(Boolean).join(" • ");
      if (meta) copy.appendChild(node("div", "episode-meta", meta));
      const progress = ep.progress || {};
      const label = progress.completed
        ? "Watched"
        : Number(progress.progress_seconds || 0) > 0
          ? `Resume at ${formatSeconds(progress.progress_seconds)}`
          : "";
      const stateEl = node("div", "episode-state");
      if (label) stateEl.appendChild(node("div", "episode-progress-label", label));
      if (hostSession?.is_host) {
        const playLabel = Number(progress.progress_seconds || 0) > 0 && !progress.completed
          ? "▶ Resume in Theater"
          : "▶ Play in Theater";
        const play = button(playLabel, "btn secondary episode-play");
        play.addEventListener("click", () => playInTheater({
          ...ep,
          media_type: "episode",
          series_id: Number(seriesId),
        }, hostSession, play));
        stateEl.appendChild(play);
      }
      card.append(still, copy, stateEl);
      list.appendChild(card);
    });
    host.appendChild(list);
  }

  async function renderDetails(mediaType, tmdbId, requestedSeason = null, requestedEpisode = null) {
    const cacheKey = `${mediaType}:${tmdbId}`;
    const page = node("main", "page");
    page.appendChild(node("div", "hero skeleton"));
    renderShell(page, "");
    try {
      let data = state.details.get(cacheKey);
      if (!data) {
        data = await api(`/details/${mediaType}/${tmdbId}`);
        state.details.set(cacheKey, data);
      }
      const d = data.details;
      page.textContent = "";

      const hero = node("section", "details-hero");
      const bg = node("div", "details-bg");
      const backdrop = safeImage(d.backdrop_url);
      if (backdrop) {
        const image = node("img");
        configureArtwork(image, backdrop, "backdrop");
        image.alt = "";
        image.fetchPriority = "high";
        bg.appendChild(image);
      }
      const content = node("div", "details-content");
      const poster = node("div", "details-poster");
      const posterUrl = safeImage(d.poster_url);
      if (posterUrl) {
        const image = node("img");
        configureArtwork(image, posterUrl, "poster");
        image.alt = `${d.title} poster`;
        image.loading = "eager";
        image.decoding = "async";
        poster.appendChild(image);
      }
      const copy = node("div");
      copy.appendChild(node("h1", "details-title", d.title || "Untitled"));
      if (d.tagline) copy.appendChild(node("div", "details-tagline", d.tagline));
      const meta = node("div", "details-meta");
      [
        d.year ? String(d.year) : "",
        d.rating ? `★ ${Number(d.rating).toFixed(1)}` : "",
        d.runtime ? formatDuration(d.runtime) : "",
        d.status || "",
        ...(d.genres || []).slice(0, 5),
      ].filter(Boolean).forEach((value) => meta.appendChild(node("span", "meta-chip", value)));
      copy.appendChild(meta);
      if (d.overview) copy.appendChild(node("div", "details-overview", d.overview));
      const actions = node("div", "hero-actions");
      if (data.active_session?.watch_url) {
        actions.appendChild(button("▶ Return to Theater", "btn primary", () => { location.href = data.active_session.watch_url; }));
      } else if (d.media_type === "movie" && data.host_session?.is_host) {
        const resume = Number(data.library?.progress_seconds || 0) > 0 && !data.library?.completed;
        const play = button(resume ? "▶ Resume in Theater" : "▶ Play in Theater", "btn primary");
        play.addEventListener("click", () => playInTheater(d, data.host_session, play));
        actions.appendChild(play);
      } else if (d.media_type === "tv" && data.host_session?.is_host && data.continue_episode) {
        const ep = data.continue_episode;
        const season = Number(ep.season_number || 0);
        const number = Number(ep.episode_number || 0);
        const resume = Number(ep.progress_seconds || 0) > 0 && !ep.completed;
        const play = button(
          resume ? `▶ Resume S${season} E${number}` : `▶ Play S${season} E${number}`,
          "btn primary"
        );
        play.addEventListener("click", () => playInTheater(ep, data.host_session, play));
        actions.appendChild(play);
      }
      const inWatchlist = Boolean(data.library?.watchlisted);
      actions.appendChild(button(inWatchlist ? "✓ In Watchlist" : "+ Watchlist", "btn secondary", () => setWatchlistFromDetails(data, !inWatchlist)));
      if (d.trailer_url) actions.appendChild(button("Trailer", "btn secondary", () => openExternal(d.trailer_url)));
      if (!data.active_session?.watch_url && !data.host_session?.is_host && data.discord?.discord_url) {
        actions.appendChild(button("Open Discord to Play", "btn discord", () => openExternal(data.discord.discord_url)));
      }
      copy.appendChild(actions);
      content.append(poster, copy);
      hero.append(bg, content);
      page.appendChild(hero);

      if (d.media_type === "tv") {
        const tvPanel = node("section", "panel section");
        const toolbar = node("div", "season-toolbar");
        const left = node("div");
        left.appendChild(node("h2", "", "Episodes"));
        const latest = latestEpisodeProgress(data.episode_progress);
        const continuation = data.continue_episode || null;
        if (continuation) {
          const season = Number(continuation.season_number || 0);
          const episode = Number(continuation.episode_number || 0);
          const resume = Number(continuation.progress_seconds || 0) > 0 && !continuation.completed;
          const label = resume
            ? `Continue S${season} E${episode}`
            : `Next S${season} E${episode}`;
          left.appendChild(node("div", "section-sub", label));
        }
        const select = node("select", "season-select");
        (d.seasons || []).filter((season) => Number(season.season_number) > 0).forEach((season) => {
          const option = node("option");
          option.value = String(season.season_number);
          option.textContent = `${season.name} • ${season.episode_count} episodes`;
          select.appendChild(option);
        });
        toolbar.append(left, select);
        const episodeHost = node("div");
        tvPanel.append(toolbar, episodeHost);
        page.appendChild(tvPanel);
        const initialSeason = Number(requestedSeason || continuation?.season_number || latest?.season_number || select.value || 1);
        select.value = String(initialSeason);
        select.addEventListener("change", () => loadSeason(d.tmdb_id, Number(select.value), episodeHost, data.host_session, 0).catch((error) => {
          episodeHost.textContent = "";
          episodeHost.appendChild(node("div", "state-card", error.message || "Episodes failed to load."));
        }));
        await loadSeason(
          d.tmdb_id,
          initialSeason,
          episodeHost,
          data.host_session,
          Number(requestedEpisode || 0),
        );
      }

      const detailsGrid = node("div", "details-grid");
      const main = node("div");
      const castPanel = renderCast(d.cast);
      if (castPanel) main.appendChild(castPanel);
      if ((d.recommendations || []).length) {
        const rec = mediaRail("Similar Titles", d.recommendations, "similar");
        if (rec) main.appendChild(rec);
      }
      const side = node("aside", "panel");
      side.appendChild(node("h3", "", d.media_type === "tv" ? "Series Details" : "Movie Details"));
      if ((d.directors || []).length) side.appendChild(node("p", "section-sub", `Director: ${d.directors.join(", ")}`));
      if ((d.creators || []).length) side.appendChild(node("p", "section-sub", `Created by: ${d.creators.join(", ")}`));
      side.appendChild(node("p", "section-sub", data.library?.completed ? "Watched" : Number(data.library?.progress_seconds || 0) > 0 ? `Progress: ${formatSeconds(data.library.progress_seconds)}` : "No saved playback progress yet."));

      const sources = Array.isArray(data.sources) ? data.sources : [];
      const sourceTitle = node("h3", "", "Available Cinema Sources");
      sourceTitle.style.marginTop = "18px";
      side.appendChild(sourceTitle);
      if (!sources.length) {
        side.appendChild(node("p", "section-sub", d.media_type === "tv"
          ? "Choose an episode to search connected playback sources for that exact SxxExx release."
          : "No connected playback source currently matches this title. Discord can still accept a host-supplied magnet or .torrent."));
      } else {
        sources.slice(0, 6).forEach((source) => {
          const row = node("div", "source-card");
          row.style.marginTop = "8px";
          row.append(
            node("div", "feed-title", source.source_label || "Cinema source"),
            node("div", "feed-meta", [
              source.health ? `Health: ${source.health}` : "",
              Number(source.seeds || 0) ? `${source.seeds} seeds` : "",
              source.title || "",
            ].filter(Boolean).join(" • ")),
          );
          side.appendChild(row);
        });
      }
      detailsGrid.append(main, side);
      page.appendChild(detailsGrid);

      renderShell(page, "");
      if (Number(requestedEpisode || 0) > 0) {
        requestAnimationFrame(() => {
          const current = page.querySelector(".episode-current");
          if (!current) return;
          const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
          current.scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" });
        });
      }
    } catch (error) {
      renderShell(pageError(
        "Title details could not load",
        error.message || "Try again.",
        () => renderDetails(mediaType, tmdbId, requestedSeason, requestedEpisode),
      ), "");
    }
  }

  async function renderProfile() {
    const page = node("main", "page");
    renderShell(skeletonPage(), "profile");
    try {
      const data = state.profile || await api("/profile");
      state.profile = data;
      const layout = node("div", "profile-layout");
      const profileCard = node("aside", "panel profile-card");
      const avatar = node("div", "profile-avatar");
      const avatarUrl = safeImage(data.discord?.avatar_url);
      if (avatarUrl) {
        const image = node("img");
        image.src = avatarUrl;
        image.alt = "";
        avatar.appendChild(image);
      } else {
        avatar.appendChild(node("div", "avatar-fallback", initials(data.discord?.user_name)));
      }
      profileCard.append(
        avatar,
        node("div", "profile-name", data.discord?.user_name || "Discord User"),
        node("div", "profile-server", data.discord?.guild_name || "Discord"),
      );

      const settings = node("section", "panel");
      settings.appendChild(node("h1", "", "Cinema Profile"));
      settings.appendChild(node("p", "section-sub", "These preferences are stored with your Discord-linked Cinema profile and follow you across devices."));
      const prefs = data.preferences || {};
      const form = node("div", "form-grid");

      function selectField(label, key, values) {
        const field = node("div", "field");
        field.appendChild(node("label", "", label));
        const select = node("select");
        select.dataset.pref = key;
        values.forEach(([value, text]) => {
          const option = node("option");
          option.value = value;
          option.textContent = text;
          if (String(prefs[key]) === String(value)) option.selected = true;
          select.appendChild(option);
        });
        field.appendChild(select);
        return field;
      }
      form.append(
        selectField("Visual quality", "visual_quality", [["auto","Auto"],["high","High"],["standard","Standard"],["lite","Lite"]]),
        selectField("Playback speed", "playback_speed", [["0.5","0.5×"],["0.75","0.75×"],["1","1×"],["1.25","1.25×"],["1.5","1.5×"],["1.75","1.75×"],["2","2×"]]),
      );

      for (const [label, key, placeholder] of [
        ["Preferred source", "preferred_source", "Optional source name"],
        ["Default audio language", "default_audio_language", "Example: English"],
        ["Default subtitle language", "default_subtitle_language", "Example: English"],
      ]) {
        const field = node("div", "field");
        field.appendChild(node("label", "", label));
        const input = node("input");
        input.dataset.pref = key;
        input.placeholder = placeholder;
        input.value = String(prefs[key] || "");
        field.appendChild(input);
        form.appendChild(field);
      }

      const autoplay = node("div", "switch-row");
      autoplay.appendChild(node("div", "", "Autoplay next episode"));
      const check = node("input");
      check.type = "checkbox";
      check.checked = Boolean(prefs.autoplay_next);
      check.dataset.pref = "autoplay_next";
      autoplay.appendChild(check);

      const save = button("Save Cinema Preferences", "btn primary", async () => {
        const payload = {};
        settings.querySelectorAll("[data-pref]").forEach((field) => {
          if (field instanceof HTMLInputElement && field.type === "checkbox") payload[field.dataset.pref] = field.checked;
          else payload[field.dataset.pref] = field.value;
        });
        if ("playback_speed" in payload) payload.playback_speed = Number(payload.playback_speed);
        try {
          const response = await api("/profile", { method: "POST", body: JSON.stringify(payload) });
          state.profile = { ...data, preferences: response.preferences };
          applyVisualQuality(response.preferences?.visual_quality || "auto");
          toast("Cinema preferences saved.");
        } catch (error) {
          toast(error.message || "Preferences could not be saved.", "error");
        }
      });
      settings.append(form, autoplay, save);
      layout.append(profileCard, settings);
      page.textContent = "";
      page.appendChild(layout);
      renderShell(page, "profile");
    } catch (error) {
      renderShell(pageError("Cinema Profile could not load", error.message || "Try again.", () => renderProfile()), "profile");
    }
  }

  function categoryLabel(value) {
    return {
      movies: "Movies",
      tv: "TV Shows",
      anime: "Anime",
      documentaries: "Documentaries",
      custom: "Custom",
    }[String(value)] || "Custom";
  }

  function sourceHealthLabel(source) {
    return {
      online: "Online",
      offline: "Offline",
      unchecked: "Not checked",
      reference: "Reference link",
      disabled: "Disabled",
    }[String(source?.health_state || "")] || (source?.enabled ? "Not checked" : "Disabled");
  }

  async function feedAction(payload) {
    const result = await api("/feeds", { method: "POST", body: JSON.stringify(payload) });
    state.feeds = result;
    return result;
  }

  function openFeedEditor(source = null) {
    const backdrop = node("div", "modal-backdrop");
    const modal = node("section", "modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    const head = node("div", "modal-head");
    head.appendChild(node("h2", "", source ? "Edit Media Source" : "Add Media Source"));
    const close = button("×", "modal-close", () => backdrop.remove());
    close.setAttribute("aria-label", "Close source editor");
    head.appendChild(close);

    const form = node("div", "form-grid");
    const fields = {};
    function field(label, key, value = "", type = "text") {
      const wrap = node("div", "field");
      wrap.appendChild(node("label", "", label));
      let control;
      if (type === "category") {
        control = node("select");
        ["movies","tv","anime","documentaries","custom"].forEach((category) => {
          const option = node("option");
          option.value = category;
          option.textContent = categoryLabel(category);
          if (category === value) option.selected = true;
          control.appendChild(option);
        });
      } else if (type === "provider") {
        control = node("select");
        [["feed","RSS / Atom Feed"],["json","Structured Search API"],["external","Reference Search Link"]].forEach(([val,text]) => {
          const option = node("option");
          option.value = val;
          option.textContent = text;
          if (val === value) option.selected = true;
          control.appendChild(option);
        });
      } else {
        control = node("input");
        control.type = "text";
        control.value = value;
      }
      fields[key] = control;
      wrap.appendChild(control);
      return wrap;
    }
    form.append(
      field("Name", "label", source?.label || ""),
      field("Category", "category", source?.category || "custom", "category"),
      field("Type", "provider_type", source?.provider_type || "feed", "provider"),
      field("HTTPS endpoint", "endpoint_url", source?.endpoint_url || ""),
    );
    const save = button("Save Source", "btn primary", async () => {
      save.disabled = true;
      try {
        await feedAction({
          action: "save",
          source_id: source?.source_id || "",
          label: fields.label.value,
          category: fields.category.value,
          provider_type: fields.provider_type.value,
          endpoint_url: fields.endpoint_url.value,
        });
        backdrop.remove();
        toast("Media source saved.");
        renderFeeds();
      } catch (error) {
        toast(error.message || "Source could not be saved.", "error");
      } finally {
        save.disabled = false;
      }
    });
    modal.append(head, form, save);
    backdrop.appendChild(modal);
    backdrop.addEventListener("click", (event) => { if (event.target === backdrop) backdrop.remove(); });
    document.body.appendChild(backdrop);
    setTimeout(() => fields.label.focus(), 30);
  }

  async function renderFeeds() {
    const page = node("main", "page");
    renderShell(skeletonPage(), "feeds");
    try {
      const data = state.feeds || await api("/feeds");
      state.feeds = data;
      page.textContent = "";
      const head = node("div", "section-head");
      const title = node("div");
      title.append(node("h1", "", "Feed Center"), node("p", "section-sub", "Real RSS, structured search, and reference sources organized by media type."));
      head.appendChild(title);
      if (data.can_manage) head.appendChild(button("+ Add Source", "btn primary", () => openFeedEditor()));
      page.appendChild(head);

      const sources = Array.isArray(data.sources) ? data.sources : [];
      if (!sources.length) {
        page.appendChild(node("div", "state-card", data.can_manage ? "No Feed Sources Added. Add a real RSS, JSON, or reference source to begin discovery." : "No enabled feed sources are available."));
      } else {
        for (const category of data.categories || []) {
          const rows = sources.filter((source) => source.category === category);
          if (!rows.length) continue;
          const section = node("section", "section");
          section.appendChild(node("h2", "section-title", categoryLabel(category)));
          const grid = node("div", "feed-grid");
          rows.forEach((source) => {
            const card = node("article", "feed-card");
            card.append(
              node("div", "feed-category", categoryLabel(source.category)),
              node("div", "feed-title", source.label),
            );
            const capability = [
              source.discovery_capable ? "Discovery" : "",
              source.search_capable ? "Search" : "",
              source.playback_capable ? "Playback" : "",
            ].filter(Boolean).join(" • ");
            card.appendChild(node("div", "feed-meta", capability || "Reference"));
            const supported = Array.isArray(source.supported_media_types)
              ? source.supported_media_types.map(categoryLabel).filter(Boolean)
              : [];
            if (supported.length) {
              card.appendChild(node("div", "feed-meta", `Supports: ${supported.join(", ")}`));
            }
            const healthState = String(source.health_state || "");
            const statusClass = healthState === "online"
              ? "online"
              : healthState === "offline" || healthState === "disabled"
                ? "offline"
                : "";
            const status = node("div", `status-pill ${statusClass}`.trim());
            status.append(node("span", "status-dot"), node("span", "", sourceHealthLabel(source)));
            card.appendChild(status);
            if (source.last_refresh_at) {
              card.appendChild(node("div", "feed-meta", `Last refresh: ${new Date(source.last_refresh_at * 1000).toLocaleString()}`));
            }
            if (source.last_refresh_error) card.appendChild(node("div", "feed-meta", source.last_refresh_error));
            if (Array.isArray(source.newly_discovered) && source.newly_discovered.length) {
              const discovered = node("div", "feed-meta", `Newly discovered: ${source.newly_discovered.slice(0, 4).join(" • ")}`);
              card.appendChild(discovered);
            }
            if (data.can_manage) {
              const actions = node("div", "hero-actions");
              if (source.provider_type !== "external") {
                actions.appendChild(button("Refresh", "btn secondary", async () => {
                  try {
                    await feedAction({ action: "refresh", source_id: source.source_id });
                    toast("Source refreshed.");
                    renderFeeds();
                  } catch (error) {
                    toast(error.message || "Refresh failed.", "error");
                  }
                }));
              }
              actions.append(
                button("Edit", "btn secondary", () => openFeedEditor(source)),
                button(source.enabled ? "Disable" : "Enable", "btn secondary", async () => {
                  try { await feedAction({ action: "toggle", source_id: source.source_id }); renderFeeds(); }
                  catch (error) { toast(error.message || "Source update failed.", "error"); }
                }),
                button("Delete", "btn danger", async () => {
                  if (!confirm(`Delete ${source.label}?`)) return;
                  try { await feedAction({ action: "remove", source_id: source.source_id }); renderFeeds(); }
                  catch (error) { toast(error.message || "Source deletion failed.", "error"); }
                }),
              );
              card.appendChild(actions);
            }
            grid.appendChild(card);
          });
          section.appendChild(grid);
          page.appendChild(section);
        }
      }
      renderShell(page, "feeds");
    } catch (error) {
      renderShell(pageError("Feed Center could not load", error.message || "Try again.", () => { state.feeds = null; renderFeeds(); }), "feeds");
    }
  }

  async function renderNotifications() {
    const page = node("main", "page");
    renderShell(skeletonPage(), "");
    try {
      const data = await api("/notifications");
      state.notifications = data;
      const notifications = Array.isArray(data.notifications) ? data.notifications : [];
      const unreadCount = notifications.filter((item) => !item.read_at).length;
      state.home = {
        ...(state.home || {}),
        notifications_unread: unreadCount,
      };
      page.textContent = "";
      page.appendChild(node("h1", "", "Notifications"));
      const list = node("div", "notification-list section");
      const rows = notifications;
      if (!rows.length) {
        list.appendChild(node("div", "state-card", "No Cinema notifications right now."));
      } else {
        rows.forEach((notification) => {
          const item = node("article", `notification ${notification.read_at ? "" : "unread"}`.trim());
          const copy = node("div");
          copy.append(
            node("div", "notification-title", notification.title || "Cinema"),
            node("div", "notification-body", notification.body || ""),
            node("div", "notification-time", notification.created_at ? new Date(notification.created_at).toLocaleString() : ""),
          );
          item.appendChild(copy);
          const action = notification.action && typeof notification.action === "object"
            ? notification.action
            : {};
          if (action.kind === "room" && action.watch_url) {
            item.appendChild(button("Join Theater", "btn primary", async () => {
              if (!notification.read_at) {
                try {
                  await api("/notifications", {
                    method: "POST",
                    body: JSON.stringify({ notification_id: notification.id }),
                  });
                } catch (_) {
                  // A transient inbox write must not block a still-valid room invite.
                }
              }
              location.href = action.watch_url;
            }));
          }
          if (!notification.read_at) {
            item.appendChild(button("Mark Read", "btn ghost", async () => {
              try {
                await api("/notifications", { method: "POST", body: JSON.stringify({ notification_id: notification.id }) });
                renderNotifications();
              } catch (error) {
                toast(error.message || "Notification update failed.", "error");
              }
            }));
          }
          list.appendChild(item);
        });
      }
      page.appendChild(list);
      renderShell(page, "");
    } catch (error) {
      renderShell(pageError("Notifications could not load", error.message || "Try again.", () => renderNotifications()), "");
    }
  }

  async function renderRoute() {
    const { parts, params } = currentRoute();
    const view = parts[0] || "home";
    state.activeView = view;
    if (view === "home") return renderHome();
    if (view === "search") return renderSearch();
    if (view === "library") return renderLibrary(params.get("tab") || "continue");
    if (view === "feeds") return renderFeeds();
    if (view === "profile") return renderProfile();
    if (view === "notifications") return renderNotifications();
    if (view === "details" && ["movie", "tv"].includes(parts[1]) && Number(parts[2]) > 0) {
      return renderDetails(
        parts[1],
        Number(parts[2]),
        params.get("season"),
        params.get("episode"),
      );
    }
    go("home");
  }

  window.addEventListener("hashchange", renderRoute);
  window.addEventListener("pageshow", () => {
    if (document.visibilityState !== "hidden") {
      if (document.documentElement.dataset.qualityPreference === "auto") applyVisualQuality("auto");
      renderRoute();
    }
  });
  window.addEventListener("resize", () => {
    if (document.documentElement.dataset.qualityPreference === "auto") applyVisualQuality("auto");
  }, { passive: true });
  /** @type {any} */
  const runtimeNavigator = navigator;
  if (runtimeNavigator.connection && typeof runtimeNavigator.connection.addEventListener === "function") {
    runtimeNavigator.connection.addEventListener("change", () => {
      if (document.documentElement.dataset.qualityPreference === "auto") applyVisualQuality("auto");
    });
  }

  applyVisualQuality("auto");
  renderRoute();
})();
