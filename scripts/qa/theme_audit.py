#!/usr/bin/env python3
"""Screenshot /chat in every theme state and flag UX problems automatically.

Needs Playwright and PIL, which only the production venv has. Run from a tree:

    /root/fuse/venv/bin/python scripts/qa/theme_audit.py                       # combined + desktop, default matrix
    /root/fuse/venv/bin/python scripts/qa/theme_audit.py --states 'empty,t-tools-*' --viewports 1440x900
    /root/fuse/venv/bin/python scripts/qa/theme_audit.py --fixture . --states empty   # no dev server needed
    /root/fuse/venv/bin/python scripts/qa/theme_audit.py --console-baseline --out /tmp/a
    /root/fuse/venv/bin/python scripts/qa/theme_audit.py diff BEFORE_DIR AFTER_DIR
    /root/fuse/venv/bin/python scripts/qa/theme_audit.py --list-states

Default target is fuse-dev (https://127.0.0.1:8089) as the QA login
(data/dev-qa-login.json). --fixture TREE instead starts TREE's isolated
browser fixture (tests/chat_browser_fixture.py, FUSE_FIXTURE_QA_SEED=1 seeds
the same QA threads) so a worktree can audit its own code. Every non-GET
request and every request off the target origin is aborted, the page clock is
frozen just after the newest thread, Math.random is seeded and motion is
reduced, so shots are repeatable. --profile picks the matrix (no wallpaper,
dark, unless swept): quick (default) runs every state at 1440x900 and 390x844,
core states at the other sizes, the side-menu layouts and a wallpaper x
light/dark sweep; smoke is core states at the two primary sizes (a writer's
rerun); full is every state at every viewport. Passing --wallpapers or
--schemes runs the plain product instead. --plan prints the shot count only. Output: one PNG per shot
(theme__viewport__wallpaper-scheme__state.png), failures.json, summary.md;
exit 1 when any check fails. `diff` compares two output dirs pixel-exactly.
"""
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageChops
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
PY = "/root/fuse/venv/bin/python"
VIEWPORTS = ["1920x1080", "1440x900", "1280x800", "1024x768", "768x1024", "390x844", "360x740"]
SWEEP_STATES = ["long-markdown", "empty", "composer-wrapped"]
SWEEP_VIEWPORTS = ["1440x900", "390x844"]
# Profiles (the default matrix when --wallpapers/--schemes are not given):
#   full   every state at every viewport; layouts at every viewport; wallpaper sweep over SWEEP_STATES.
#   quick  every state at a desktop and a phone size (PRIMARY); CORE_STATES at the other sizes;
#          layouts and the wallpaper sweep (long-markdown, composer-wrapped) at the primary sizes.
#   smoke  CORE_STATES at the primary sizes, one layout pair, no sweep: a writer's minute-long rerun.
PRIMARY = ["1440x900", "390x844"]
CORE_STATES = ["empty", "sidebar-chats", "long-markdown", "t-tools-folded", "t-tools-unfolded", "live-tools", "jobs",
               "subagent", "model-picker", "composer-wrapped", "appearance-sheet", "inspector-tasks", "rail"]
BASELINE_VIEWPORTS = ["1440x900", "390x844", "1920x1080"]
MAX_PAGES = 4
WALLPAPER_SETTLE_MS = 400
LANE = 12  # shots that share one browser context (a fresh page each)
TEXT_SAMPLES = 400


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Skip(Exception):
    pass


@dataclass(frozen=True)
class Job:
    theme: str
    viewport: str
    wallpaper: str
    scheme: str
    state: str
    layout: str = ""

    @property
    def name(self):
        tail = f"+{self.layout}" if self.layout else ""
        return f"{self.theme}__{self.viewport}__{self.wallpaper}-{self.scheme}__{self.state}{tail}"

    @property
    def size(self):
        w, h = map(int, self.viewport.split("x"))
        return w, h


# ---------------------------------------------------------------- in-page code

INIT_JS = r"""(([views, collapsed]) => {
  // Seeded Math.random: wallpapers and anything else random draw the same frame every run.
  let s = 0x2F6E2B1;
  Math.random = () => { s = s + 0x6D2B79F5 | 0; let t = Math.imul(s ^ s >>> 15, 1 | s);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  try {
    // Shots share a context; each page starts from empty storage.
    if (window.top === window && !sessionStorage.getItem('qa-fresh')) { localStorage.clear(); sessionStorage.setItem('qa-fresh', '1'); }
    localStorage.setItem('fuse.chat.views.v1', JSON.stringify(views));
    localStorage.setItem('fuse.chat.historyCollapsed.v1', JSON.stringify({console: collapsed}));
    localStorage.setItem('fuse.chat.todos.dismissed.v1', '{}');
  } catch (_) {}
})"""

# Everything text-coloured (and every svg icon) made invisible: what is left is
# the background each text box is actually drawn on, wallpapers included.
BG_CSS = """*, *::before, *::after, *::marker { color: transparent !important; -webkit-text-fill-color: transparent !important;
  text-shadow: none !important; text-decoration-color: transparent !important; caret-color: transparent !important; }
*::placeholder { color: transparent !important; -webkit-text-fill-color: transparent !important; }
svg { visibility: hidden !important; }"""

CHECKS_JS = r"""(opts) => {
const W = innerWidth, H = innerHeight;
const res = { issues: [], text: [], icons: [], counts: {} };
const cache = new Map();
const cs = (el) => { let s = cache.get(el); if (!s) { s = getComputedStyle(el); cache.set(el, s); } return s; };
const R = (r) => r ? [Math.round(r.left), Math.round(r.top), Math.round(r.right - r.left), Math.round(r.bottom - r.top)] : null;
function path(el) {
  const parts = [];
  for (let e = el, i = 0; e && e.nodeType === 1 && i < 4; e = e.parentElement, i++) {
    let s = e.tagName.toLowerCase();
    if (e.id) s += '#' + e.id;
    const hook = [...e.attributes].find((a) => a.name.startsWith('data-') && !/^data-(state|tone|kind|since|base|cc-clock|age-stamp)$/.test(a.name));
    if (hook) s += `[${hook.name}${hook.value && hook.value.length <= 24 ? `="${hook.value}"` : ''}]`;
    else if (e.classList.length) s += '.' + [...e.classList].slice(0, 2).join('.');
    const role = e.getAttribute('role'); if (role && !hook) s += `[role=${role}]`;
    parts.unshift(s);
    if (e.id || e.matches('[data-chat-log],[data-history],[data-chat-bar],[data-chat-form],[data-theme-rail]')) break;
  }
  return parts.join(' > ');
}
const snippet = (el) => String(el.innerText || el.value || el.getAttribute?.('aria-label') || el.title || '').replace(/\s+/g, ' ').trim().slice(0, 80);
const add = (check, severity, el, rect, detail, text) => res.issues.push({ check, severity, selector: path(el), text: text ?? snippet(el), rect: R(rect), detail });
function srOnly(el) {
  const s = cs(el), r = el.getBoundingClientRect();
  return (r.width <= 2 && r.height <= 2) || ((s.position === 'absolute' || s.position === 'fixed') && (r.width <= 1 || r.height <= 1))
    || /rect\(0(px)?,? 0(px)?,? 0(px)?,? 0(px)?\)/.test(s.clip) || s.clipPath === 'inset(50%)';
}
function rendered(el, aria) {
  if (cs(el).visibility !== 'visible') return false;
  for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
    const s = cs(e);
    if (s.display === 'none' || Number(s.opacity) === 0 || e.inert || s.contentVisibility === 'hidden') return false;
    if (aria && e.getAttribute('aria-hidden') === 'true') return false;
  }
  return true;
}
function opacityOf(el) { let o = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= Number(cs(e).opacity); return o; }
// The part of a box left visible by ancestors that clip overflow, and by the viewport.
function clipTo(el, r) {
  let L = Math.max(r.left, 0), T = Math.max(r.top, 0), Rr = Math.min(r.right, W), B = Math.min(r.bottom, H);
  let fixed = cs(el).position === 'fixed';
  for (let e = el.parentElement; e && !fixed && e !== document.body && e !== document.documentElement; e = e.parentElement) {
    const s = cs(e);
    if (s.overflowX !== 'visible' || s.overflowY !== 'visible') {
      const a = e.getBoundingClientRect();
      if (s.overflowX !== 'visible') { L = Math.max(L, a.left); Rr = Math.min(Rr, a.right); }
      if (s.overflowY !== 'visible') { T = Math.max(T, a.top); B = Math.min(B, a.bottom); }
    }
    if (s.position === 'fixed') fixed = true;
  }
  return Rr - L >= 1 && B - T >= 1 ? { left: L, top: T, right: Rr, bottom: B } : null;
}
const clipRect = (el) => clipTo(el, el.getBoundingClientRect());
const isBackdrop = (el) => [...el.attributes].some((a) => /^data-.*backdrop$/.test(a.name));
const LAYER = '[aria-modal="true"], [role=dialog], [role=menu], [role=listbox], dialog, [popover], [data-wallpaper-picker], [data-chat-slash]';
// Whatever an open trigger controls (aria-expanded + aria-controls) is a popup layer too.
// A docked panel in the page flow (the pinned sidebar, a docked inspector) is not one.
const POPUPS = [...document.querySelectorAll('[aria-expanded="true"][aria-controls]')]
  .flatMap((b) => [...document.querySelectorAll('#' + CSS.escape(b.getAttribute('aria-controls')))])
  .filter((e) => !e.hidden && e.getClientRects().length && /fixed|absolute/.test(getComputedStyle(e).position));
function layerOf(el) {
  const own = el.closest(LAYER);
  // the innermost layer wins: a menu inside the sidebar drawer is its own layer
  const pops = POPUPS.filter((p) => p.contains(el) && (!own || own.contains(p)));
  return pops.find((p) => !pops.some((q) => q !== p && p.contains(q))) || own || null;
}
function overlayHit(hit) { return Boolean(hit && (layerOf(hit) || isBackdrop(hit) || hit.closest('[data-chat-scene]'))); }
function scroller(el) {
  for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
    const s = cs(e);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && e.scrollHeight > e.clientHeight + 1) return e;
  }
  return null;
}
// Content partly under fixed chrome is fine while its scroller can still bring it out.
function canScrollOut(el, cy) {
  const sc = scroller(el);
  if (!sc) return false;
  const r = sc.getBoundingClientRect();
  if (cy < (r.top + r.bottom) / 2) return sc.scrollTop > 1;
  return sc.scrollTop + sc.clientHeight < sc.scrollHeight - 1;
}

// —— interactive elements: overlap, covered, small targets ——
const SEL = 'button, a[href], input:not([type=hidden]), textarea, select, [role=button], [role=tab], [role=menuitem], [role=menuitemradio], [role=menuitemcheckbox], [role=switch], [tabindex]';
const CONTAINER = '[role=region],[role=log],[role=dialog],[role=alertdialog],[role=tabpanel],[role=document],[role=main],[role=list],[role=listbox],[role=application],[role=group],[role=menu],dialog,iframe';
const items = [];
for (const el of document.querySelectorAll(SEL)) {
  const native = el.matches('button, a[href], input, textarea, select, [role]');
  if (!native && el.tabIndex < 0) continue;
  // Splitters (role=separator) are drag handles with a keyboard path, not tap targets.
  if (el.matches(CONTAINER) || el.matches('[role=separator]') || isBackdrop(el)) continue;
  if (!rendered(el, true) || srOnly(el)) continue;
  const br = el.getBoundingClientRect();
  if (!native && br.width * br.height > W * H * 0.2) continue;
  const vr = clipRect(el);
  if (!vr) continue;
  items.push({ el, br, vr, layer: layerOf(el), scroll: scroller(el), inline: cs(el).display === 'inline' });
}
res.counts.interactive = items.length;
for (let i = 0; i < items.length; i++) {
  const a = items[i];
  for (let j = i + 1; j < items.length; j++) {
    const b = items[j];
    // Inline links wrap across lines; their bounding boxes overlap neighbours without touching them.
    if (a.inline || b.inline || a.layer !== b.layer || a.el.contains(b.el) || b.el.contains(a.el)) continue;
    const ix = Math.min(a.vr.right, b.vr.right) - Math.max(a.vr.left, b.vr.left);
    const iy = Math.min(a.vr.bottom, b.vr.bottom) - Math.max(a.vr.top, b.vr.top);
    if (ix <= 2 || iy <= 2) continue;
    const box = { left: Math.max(a.vr.left, b.vr.left), top: Math.max(a.vr.top, b.vr.top) };
    box.right = box.left + ix; box.bottom = box.top + iy;
    if (a.scroll !== b.scroll && (canScrollOut(a.scroll ? a.el : b.el, (box.top + box.bottom) / 2))) continue;
    // A small control sitting wholly inside a bigger row (a star on an option) is a
    // deliberate nested target: a warning. Partial collisions fail.
    const inside = (x, y) => x.left >= y.left - 1 && x.right <= y.right + 1 && x.top >= y.top - 1 && x.bottom <= y.bottom + 1;
    const nested = inside(a.vr, b.vr) || inside(b.vr, a.vr);
    add('overlap', nested ? 'warn' : 'fail', a.el, box, `${nested ? 'nested in/over' : 'overlaps'} ${path(b.el)} "${snippet(b.el).slice(0, 40)}" by ${Math.round(ix)}x${Math.round(iy)}px`);
  }
}
for (const it of items) {
  const { el, vr } = it;
  if (cs(el).pointerEvents === 'none') continue;
  const cx = (vr.left + vr.right) / 2, cy = (vr.top + vr.bottom) / 2;
  if (cx < 0 || cy < 0 || cx >= W || cy >= H) continue;
  const hit = document.elementFromPoint(cx, cy);
  if (!hit || hit === el || el.contains(hit) || hit.contains(el)) continue;
  if (hit.tagName === 'LABEL' && (hit.control === el || hit.contains(el))) continue;
  // Covered by an open popup above its own layer (a menu over the list it opened from) is intended.
  const hl = layerOf(hit);
  if (hl && hl !== it.layer && (!it.layer || it.layer.contains(hl))) continue;
  // A menu or dialog the page moved to <body> sits above every layer, the one that opened it included.
  if (hl && hl.parentElement === document.body && hl.matches(LAYER)) continue;
  if (!it.layer && overlayHit(hit)) continue;
  if (canScrollOut(el, cy) && !(it.scroll && it.scroll.contains(hit))) continue;
  add('covered', 'fail', el, vr, `centre covered by ${path(hit)} "${snippet(hit).slice(0, 40)}"`);
}
for (const { el, br } of items) {
  const w = br.width, h = br.height;
  const inline = cs(el).display.startsWith('inline') && !cs(el).display.includes('block') && !cs(el).display.includes('flex') && !cs(el).display.includes('grid')
    && [...(el.parentElement?.childNodes || [])].some((n) => n !== el && n.nodeType === 3 && n.nodeValue.trim().length > 1);
  if (inline) continue;
  const min = Math.min(w, h);
  const size = `${Math.round(w)}x${Math.round(h)}px`;
  if (min < 24) add('small-target', 'fail', el, br, `${size} < 24px`);
  else if (opts.narrow && min < 32) add('small-target', 'fail', el, br, `${size} < 32px on a phone`);
  else if (opts.narrow && min < 44) add('small-target', 'warn', el, br, `${size} < 44px on a phone`);
}

// —— horizontal scroll ——
const se = document.scrollingElement;
if (se.scrollWidth > W + 1) add('hscroll', 'fail', se, { left: 0, top: 0, right: se.scrollWidth, bottom: 1 }, `page scrolls sideways: ${se.scrollWidth}px > ${W}px`, '');
const INTENT = 'pre, code, table, textarea, input, select, iframe, [data-chat-scene], .hljs';
for (const el of document.querySelectorAll('body *')) {
  const s = cs(el);
  if (s.overflowX !== 'auto' && s.overflowX !== 'scroll') continue;
  if (el.scrollWidth <= el.clientWidth + 1 || el.clientWidth === 0) continue;
  if (el.matches(INTENT) || el.closest('pre, table')) continue;
  // Code listings (diffs, file views, terminal output) scroll sideways on purpose.
  if (/mono|code|consol|menlo|courier/i.test(s.fontFamily) && /pre|nowrap/.test(s.whiteSpace + cs(el.firstElementChild || el).whiteSpace)) continue;
  if (/mono|code|consol|menlo|courier/i.test(s.fontFamily) && el.querySelector('[class*=line], pre, code')) continue;
  const kids = [...el.children].filter((k) => cs(k).display !== 'none');
  if (kids.length && kids.every((k) => k.matches('table, pre, code, img, canvas, svg, .chat-table, [data-wallpaper-card]'))) continue;
  if (s.scrollSnapType && s.scrollSnapType !== 'none') continue;
  if (!rendered(el, false) || !clipRect(el)) continue;
  add('hscroll', 'fail', el, el.getBoundingClientRect(), `scrolls sideways: content ${el.scrollWidth}px in ${el.clientWidth}px`);
}

// —— text: clipped, cut by the viewport, contrast samples ——
const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
const range = document.createRange();
const checkedBoxes = new Set();
const buckets = {};
const cat = (p) => p.closest('[data-chat-log] a, [data-agent-log-body] a') ? 'link'
  : p.closest('code, pre, kbd') ? 'code'
  : p.closest('[data-chat-log], [data-agent-log-body]') ? (p.closest('[data-step], [data-tool], [data-meta], [data-foot], [data-recap], [data-activity]') ? 'transcript-meta' : 'transcript')
  : p.closest('[data-history]') ? 'sidebar'
  : p.closest('[data-chat-bar]') ? 'topbar'
  : p.closest('[data-chat-form]') ? 'composer'
  : p.closest('[data-theme-rail]') ? 'rail'
  : p.closest(LAYER) ? 'popover' : 'other';
function lineRects(node) { range.selectNodeContents(node); return [...range.getClientRects()].filter((r) => r.width >= 2 && r.height >= 4); }
function textColor(p) {
  const s = cs(p);
  const fill = s.webkitTextFillColor;
  return fill && fill !== s.color && !/rgba\(0, 0, 0, 0\)/.test(fill) ? fill : s.color;
}
function parseRgba(c) { const m = String(c).match(/rgba?\(([^)]+)\)/); if (!m) return null; const v = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1]; }
const ARIA = (p) => p.closest('[aria-hidden="true"]');
let node;
while ((node = walker.nextNode())) {
  const text = node.nodeValue;
  if (!text.trim()) continue;
  const p = node.parentElement;
  if (!p || p.closest('script, style, noscript, template, title, option, [data-chat-compose-mirror]')) continue;
  const pr = p.getBoundingClientRect();
  if (pr.bottom < 0 || pr.top > H || pr.right < 0 || pr.left > W || pr.width === 0) continue;
  if (!rendered(p, false) || srOnly(p) || (p.closest('.visually-hidden, .sr-only') && srOnly(p.closest('.visually-hidden, .sr-only')))) continue;
  const rects = lineRects(node);
  if (!rects.length) continue;
  const aria = ARIA(p);
  // clipped: the nearest block box (and up to two more ancestors) clipping this text.
  if (!aria) {
    let box = p;
    while (box && cs(box).display.startsWith('inline') && cs(box).display !== 'inline-block' && cs(box).display !== 'inline-flex') box = box.parentElement;
    for (let e = box, k = 0; e && k < 3 && e !== document.body; e = e.parentElement, k++) {
      const s = cs(e);
      const cx = s.overflowX === 'hidden' || s.overflowX === 'clip', cy = s.overflowY === 'hidden' || s.overflowY === 'clip';
      // Text inside a scroller scrolls; whatever clips above the scroller is not cutting it.
      if (/auto|scroll/.test(s.overflowX + s.overflowY)) break;
      if (!cx && !cy) continue;
      if (e.matches('textarea, input, select')) break;
      const b = e.getBoundingClientRect();
      const inner = { left: b.left + e.clientLeft, top: b.top + e.clientTop };
      inner.right = inner.left + e.clientWidth; inner.bottom = inner.top + e.clientHeight;
      if (e.clientWidth === 0 || e.clientHeight === 0) break;
      // Both the scroll size and the line boxes must say so; line boxes carry half-leading,
      // so vertical needs more than 2px.
      const overX = cx && e.scrollWidth > e.clientWidth + 1 && rects.some((r) => r.right > inner.right + 1 || r.left < inner.left - 1);
      const overY = cy && e.scrollHeight > e.clientHeight + 1 && rects.some((r) => (r.bottom > inner.bottom + 2 && r.top < inner.bottom) || (r.top < inner.top - 2 && r.bottom > inner.top));
      if (!overX && !overY) continue;
      if (checkedBoxes.has(e)) break;
      checkedBoxes.add(e);
      const ellipsis = (overX && s.textOverflow === 'ellipsis') || (overY && s.webkitLineClamp && s.webkitLineClamp !== 'none');
      const full = (e.innerText || '').replace(/\s+/g, ' ').trim();
      const holder = e.closest('[title], [aria-label]');
      const exposed = holder && ((holder.getAttribute('title') || '') + ' ' + (holder.getAttribute('aria-label') || '')).length >= full.length * 0.8;
      if (ellipsis) { if (!exposed) add('clipped-text', 'warn', e, b, 'truncated with an ellipsis; no title/aria-label exposes the full text'); }
      else add('clipped-text', 'fail', e, b, `text cut ${overX ? 'horizontally' : 'vertically'} by overflow:${overX ? s.overflowX : s.overflowY} (${overX ? e.scrollWidth + '>' + e.clientWidth : e.scrollHeight + '>' + e.clientHeight}px)`);
      break;
    }
    const cut = rects.find((r) => (r.left < -1 && r.right > 0) || (r.right > W + 1 && r.left < W));
    if (cut) {
      const vis = clipTo(p, cut);
      // vis null: an ancestor already hides all of it (an ellipsized label), so the viewport is not what cuts it.
      const byAncestor = !vis || (vis.right < Math.min(cut.right, W) - 1 || vis.left > Math.max(cut.left, 0) + 1);
      let scroller = false;
      for (let a = p.parentElement; a && !scroller; a = a.parentElement) scroller = /auto|scroll/.test(cs(a).overflowX);
      if (!byAncestor && !scroller && !p.closest('pre, table') && !checkedBoxes.has(p)) { checkedBoxes.add(p); add('clipped-text', 'fail', p, cut, 'text runs off the viewport edge'); }
    }
  }
  // contrast samples
  const rgba = parseRgba(textColor(p));
  if (!rgba) continue;
  const alpha = rgba[3] * opacityOf(p);
  if (alpha < 0.05 || p.closest(':disabled, [aria-disabled="true"]') || cs(p).backgroundClip === 'text') continue;
  const size = parseFloat(cs(p).fontSize), weight = Number(cs(p).fontWeight) || 400;
  const c = cat(p);
  for (const r of rects.slice(0, 3)) {
    const v = clipTo(p, r);
    if (!v || v.right - v.left < 3 || v.bottom - v.top < 4) continue;
    // Text hidden under a sheet, pane or popup is not drawn: skip it.
    const hit = document.elementFromPoint((v.left + v.right) / 2, (v.top + v.bottom) / 2);
    if (hit && hit !== p && !p.contains(hit) && !hit.contains(p) && !hit.closest('[data-chat-scene]')) continue;
    (buckets[c] ||= []).push({ cat: c, rect: R(v), fg: [rgba[0], rgba[1], rgba[2], alpha], large: size >= 24 || (size >= 18.66 && weight >= 700),
      selector: path(p), text: text.replace(/\s+/g, ' ').trim().slice(0, 60), size: Math.round(size), icon: false });
  }
}
// form fields: typed text or the placeholder
for (const el of document.querySelectorAll('textarea, input[type=search], input[type=text], input:not([type])')) {
  if (!rendered(el, false) || srOnly(el)) continue;
  const v = clipRect(el);
  if (!v) continue;
  const s = cs(el);
  const ph = !el.value && el.placeholder;
  if (!el.value && !ph) continue;
  const rgba = parseRgba(ph ? getComputedStyle(el, '::placeholder').color : s.color);
  if (!rgba) continue;
  const lh = parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.3;
  const b = el.getBoundingClientRect();
  const box = { left: b.left + parseFloat(s.paddingLeft) + el.clientLeft, top: b.top + parseFloat(s.paddingTop) + el.clientTop };
  box.right = Math.min(b.right - parseFloat(s.paddingRight), box.left + Math.max(40, (ph ? el.placeholder.length : 40) * parseFloat(s.fontSize) * 0.5)); box.bottom = box.top + lh;
  const vv = clipTo(el, box);
  if (!vv) continue;
  // A field under a sheet or its backdrop is not what the reader sees: skip it, as text is.
  const top = document.elementFromPoint((vv.left + vv.right) / 2, (vv.top + vv.bottom) / 2);
  if (top && top !== el && !el.contains(top) && !top.contains(el) && !top.closest('[data-chat-compose-mirror]')) continue;
  (buckets.composer ||= []).push({ cat: el.closest('[data-chat-form]') ? 'composer' : 'field', rect: R(vv), fg: [rgba[0], rgba[1], rgba[2], rgba[3] * opacityOf(el)],
    large: parseFloat(s.fontSize) >= 24, selector: path(el), text: (ph ? 'placeholder: ' + el.placeholder : el.value).slice(0, 60), size: Math.round(parseFloat(s.fontSize)), icon: false });
}
// icon-only buttons: the icon's own colour against what is under it
for (const { el } of items) {
  if (snippet(el) && (el.innerText || '').trim()) continue;
  if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;  // inactive controls are exempt (WCAG 1.4.11)
  const svg = el.querySelector('svg');
  if (!svg || !rendered(svg, false)) continue;
  const v = clipRect(svg);
  if (!v || v.right - v.left < 4) continue;
  // An icon hidden under a sheet, pane or popup is not drawn: skip it, as text is.
  const over = document.elementFromPoint((v.left + v.right) / 2, (v.top + v.bottom) / 2);
  if (over && over !== el && !el.contains(over) && !over.contains(el) && !over.closest('[data-chat-scene]')) continue;
  // Every distinct stroke/fill: an icon passes on its strongest colour (a faint track under a meter is fine).
  const cols = new Map();
  for (const shape of [...svg.querySelectorAll('path, circle, rect, ellipse, line, polyline, polygon')].slice(0, 12)) {
    const ss = getComputedStyle(shape);
    const op = Number(ss.opacity) * Number(ss.strokeOpacity || 1);
    for (const c of [ss.stroke, ss.fill]) { const p = parseRgba(c); if (p && p[3] > 0) cols.set(p.join(), [p[0], p[1], p[2], p[3] * op]); }
  }
  if (!cols.size) { const p = parseRgba(cs(el).color); if (p) cols.set(p.join(), p); }
  if (!cols.size) continue;
  const o = opacityOf(svg);
  (buckets.icon ||= []).push({ cat: 'icon', rect: R(v), fg: [...cols.values()][0].slice(0, 3).concat([[...cols.values()][0][3] * o]),
    fgs: [...cols.values()].slice(0, 4).map((c) => [c[0], c[1], c[2], c[3] * o]), large: true, selector: path(el),
    text: el.getAttribute('aria-label') || el.title || '', size: 0, icon: true });
}
// stratified cap: every category gets a share, spread through the document
const cats = Object.keys(buckets);
const picked = [];
const left = Object.fromEntries(cats.map((c) => [c, buckets[c]]));
let budget = opts.cap;
while (budget > 0 && cats.some((c) => left[c].length)) {
  const live = cats.filter((c) => left[c].length);
  const share = Math.max(1, Math.floor(budget / live.length));
  for (const c of live) {
    const list = left[c];
    const take = Math.min(share, list.length, budget);
    const step = list.length / take;
    const chosen = new Set();
    for (let i = 0; i < take; i++) chosen.add(Math.floor(i * step));
    for (const i of chosen) picked.push(list[i]);
    left[c] = list.filter((_, i) => !chosen.has(i));
    budget -= chosen.size;
    if (budget <= 0) break;
  }
}
res.text = picked;
res.counts.textCandidates = cats.reduce((n, c) => n + buckets[c].length, 0);
return res;
}"""

FOCUS_JS = r"""() => {
const el = document.activeElement;
if (!el || el === document.body || el === document.documentElement) return { body: true };
const path = (e) => { const p = []; for (let i = 0; e && e.nodeType === 1 && i < 3; e = e.parentElement, i++) {
  let s = e.tagName.toLowerCase(); if (e.id) s += '#' + e.id;
  const h = [...e.attributes].find((a) => a.name.startsWith('data-')); if (h) s += `[${h.name}]`; else if (e.classList.length) s += '.' + e.classList[0];
  p.unshift(s); } return p.join(' > '); };
window.__qaPrev = el;
const label = String(el.innerText || el.getAttribute('aria-label') || el.title || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 60);
if (el.tagName === 'IFRAME') return { selector: path(el), text: label, skip: 'iframe' };
const r = el.getBoundingClientRect();
const rect = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
let hidden = '';
if (r.width < 1 || r.height < 1) hidden = 'zero-size';
else if (r.right <= 0 || r.bottom <= 0 || r.left >= innerWidth || r.top >= innerHeight) hidden = 'off-screen';
else {
  for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
    const s = getComputedStyle(e);
    if (s.visibility !== 'visible' || Number(s.opacity) === 0 || s.display === 'none') { hidden = 'hidden ancestor'; break; }
    if (e.getAttribute('aria-hidden') === 'true') { hidden = 'inside aria-hidden'; break; }
  }
  const clip = getComputedStyle(el).clip;
  if (!hidden && /rect\(0(px)?,? 0/.test(clip)) hidden = 'clipped (sr-only)';
}
const snap = () => { const out = []; for (let e = el, i = 0; e && i < 4; e = e.parentElement, i++) { const s = getComputedStyle(e);
  out.push([s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.borderTopColor, s.borderBottomColor, s.backgroundColor, s.color, s.textDecorationLine].join('|')); }
  return out; };
const on = snap();
el.blur();
const off = snap();
el.focus({ focusVisible: true, preventScroll: true });
const indicator = on.some((v, i) => v !== off[i]);
return { selector: path(el), text: label, rect, hidden, indicator, refocused: document.activeElement === el };
}"""

# Tab reached <body>: the end of the page, or focus dropped mid-walk (its element
# vanished, or tabbable elements still follow the last stop).
LOST_JS = r"""() => { const prev = window.__qaPrev; if (!prev) return '';
  if (!prev.isConnected) return 'the last focused element was removed from the page';
  const after = [...document.querySelectorAll('a[href], button, input:not([type=hidden]), textarea, select, [tabindex]')]
    .filter((e) => e.tabIndex >= 0 && !e.disabled && e.getClientRects().length && getComputedStyle(e).visibility === 'visible'
      && !e.closest('[inert], [hidden]') && (prev.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING) && !prev.contains(e));
  return after.length ? `${after.length} tabbable elements follow it, e.g. ${after[0].tagName.toLowerCase()} ${String(after[0].getAttribute('aria-label') || after[0].innerText || '').trim().slice(0, 30)}` : ''; }"""

START_JS = r"""() => { document.activeElement?.blur?.(); const s = document.createElement('span'); s.tabIndex = 0; s.id = 'qa-walk-start';
  document.body.prepend(s); s.focus(); }"""


# ---------------------------------------------------------------- contrast

_LUM = {}


def _lum(c):
    v = _LUM.get(c)
    if v is None:
        def ch(x):
            x /= 255
            return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
        v = _LUM[c] = 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])
    return v


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def contrast(bg: Image.Image, sample):
    """10th-percentile contrast of the text colour (alpha-blended) over every
    background pixel under its line box."""
    x, y, w, h = sample["rect"]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(bg.width, x + w), min(bg.height, y + h)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    crop = bg.crop((x0, y0, x1, y1))
    n = crop.width * crop.height
    if n > 600:
        f = (600 / n) ** 0.5
        crop = crop.resize((max(2, int(crop.width * f)), max(2, int(crop.height * f))), Image.NEAREST)
    pixels = list(crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata())
    best = None
    for r, g, b, a in sample.get("fgs") or [sample["fg"]]:
        vals = []
        for px in pixels:
            fg = (round(r * a + px[0] * (1 - a)), round(g * a + px[1] * (1 - a)), round(b * a + px[2] * (1 - a)))
            vals.append((_ratio(fg, px[:3]), px[:3], fg))
        vals.sort(key=lambda v: v[0])
        got = vals[len(vals) // 10]
        if best is None or got[0] > best[0]:
            best = got
    return best


# ---------------------------------------------------------------- page helpers

class Page:
    """What a state function gets: the page plus helpers that drive it by data-* hooks."""

    def __init__(self, env, page, job):
        self.env, self.page, self.job = env, page, job
        self.escape = None  # (trigger handle, popup handle) checked after the shot

    @property
    def narrow(self):
        return self.job.size[0] <= 800

    async def open(self, title=None, live=False, perm=False, error=False, collapsed=None):
        url = self.env.base + "/chat"
        if title:
            tid = self.env.threads.get(title)
            if not tid:
                raise Skip(f"thread {title!r} not in this account")
            url += f"?t={tid}"
        if live:
            await _live_routes(self.page, self.env.now)
        if perm or error:
            await _rewrite_thread(self.page, self.env.now, perm=perm, error=error)
        await self.page.goto(url, wait_until="domcontentloaded")
        await self.ready(bool(title))
        if not title and self.narrow and await self._drawer_open():
            # A phone lands on the chat list; New chat is how it reaches the empty composer.
            await self.click("[data-history] [data-chat-new]", 600)

    async def _drawer_open(self):
        return await self.page.evaluate("""() => { const h = document.querySelector('[data-history]'); if (!h) return false;
            const r = h.getBoundingClientRect(); return r.width > 120 && r.right > 100 && r.left < innerWidth - 50 && getComputedStyle(h).visibility === 'visible'; }""")

    async def _press(self, loc, sel):
        try:
            await loc.click(timeout=5000)
        except Exception as error:
            hit, modal = await loc.evaluate("""(e) => { const r = e.getBoundingClientRect(); const h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                const modal = Boolean(h && (h.closest('[data-theme-rail], [role=dialog], [aria-modal="true"], dialog') || [...h.attributes].some((a) => /^data-.*backdrop$/.test(a.name))));
                return [h ? h.tagName.toLowerCase() + (h.id ? '#' + h.id : '') + ' ' + [...h.classList].slice(0, 2).join('.') : 'nothing', modal]; }""")
            if modal:
                raise Skip(f"{sel} is under an open panel or sheet ({hit})")
            record(self.env, self.job, "unclickable", "fail", sel, "", None, f"visible but a click does not reach it (hit: {hit}); {type(error).__name__}")
            raise Skip(f"{sel} could not be clicked (hit {hit})")

    async def ready(self, thread=True):
        p = self.page
        for attempt in (1, 2):
            for js, ms in (("() => document.querySelector('[data-sync-state=\"live\"]')", 8000),
                           ("() => { const l = document.querySelector('[data-history-loading]'); return !l || !l.isConnected || !l.getClientRects().length; }", 8000)):
                try:
                    await p.wait_for_function(js, timeout=ms)
                except Exception:
                    pass
            if not thread:
                break
            try:
                await p.wait_for_function("() => { const l = document.querySelector('[data-chat-log]'); return l && !l.hidden && l.children.length > 0; }", timeout=12000)
                break
            except Exception:
                # A transcript that stalls (the dev server busy) gets one reload, then is a harness error.
                if attempt == 2:
                    raise RuntimeError("the transcript did not load, even after a reload")
                await p.reload(wait_until="domcontentloaded")
        await p.wait_for_timeout(150)

    async def visible(self, sel):
        loc = self.page.locator(sel)
        n = await loc.count()
        for i in range(min(n, 30)):
            if await loc.nth(i).is_visible():
                return loc.nth(i)
        return None

    async def need(self, sel, why="", proxy=True):
        self.via_menu = None
        loc = await self.visible(sel)
        if loc is None and proxy and await self.page.locator(f"[data-theme-proxy='{sel}']").count():
            # A narrow bar hides the control and a menu item stands in for it.
            try:
                return await self.need(f"[data-theme-proxy='{sel}']", why, proxy=False)
            except Skip:
                pass
        if loc is None:
            # A control that lives in a closed menu (a theme's ⋯): open the menu first.
            menu = await self.page.evaluate("""(sel) => { for (const e of document.querySelectorAll(sel)) { const m = e.closest('[role=menu][id], [data-theme-menu][id]');
                if (m) return m.id; } return ''; }""", sel)
            opener = menu and await self.visible(f'[aria-controls="{menu}"]')
            if opener:
                await opener.click()
                await self.page.wait_for_timeout(200)
                loc = await self.visible(sel)
                if loc is not None:
                    self.via_menu = await opener.element_handle()
        if loc is None:
            raise Skip(f"{sel} absent or hidden{(' (' + why + ')') if why else ''}")
        return loc

    async def click(self, sel, wait=250):
        loc = await self.need(sel)
        await self._press(loc, sel)
        await self.page.wait_for_timeout(wait)
        return loc

    async def sidebar(self):
        """Make the history list visible: expand it on desktop, open the drawer on phones."""
        if not await self._drawer_open():
            await self.click("[data-chat-bar] [data-history-toggle]", 500)

    async def popover(self, trigger, wait=300):
        """Click a trigger; remember it so Escape must close what opened and give focus back."""
        loc = await self.need(trigger)
        await loc.focus()
        await self._press(loc, trigger)
        await self.page.wait_for_timeout(wait)
        # Opened from a menu item: focus goes back to the menu's own trigger.
        self.escape = self.via_menu or await loc.element_handle()

    async def thread_scroll(self, frac):
        await self.page.evaluate("""(f) => { const t = [...document.querySelectorAll('[data-chat-thread], [data-agent-view]')].find((e) => !e.hidden);
            if (t) t.scrollTop = f >= 1 ? t.scrollHeight : Math.round((t.scrollHeight - t.clientHeight) * f); }""", frac)
        await self.page.wait_for_timeout(300)

    async def unfold(self):
        n = await self.page.evaluate("""() => { let n = 0; for (let pass = 0; pass < 4; pass++) {
            const els = [...document.querySelectorAll('[data-chat-log] [data-step-toggle][aria-expanded="false"]:not([data-open-agent]), [data-chat-log] [data-substep-toggle][aria-expanded="false"], [data-chat-log] [data-prev-toggle][aria-expanded="false"]')];
            if (!els.length) break; for (const e of els.slice(0, 150 - n)) { e.click(); n++; } if (n >= 150) break; } return n; }""")
        if not n:
            raise Skip("no folded tool rows ([data-step-toggle]) in this transcript")
        await self.page.wait_for_timeout(500)

    async def type(self, text):
        box = await self.need("[data-chat-input]")
        await box.click()  # the slash menu opens on input after a pointer focus, not on .focus() alone
        await box.fill("")  # a draft kept from an earlier state would prefix this text
        lines = text.split("\n")
        for i, line in enumerate(lines):
            await box.type(line, delay=0)
            if i < len(lines) - 1:
                await self.page.keyboard.press("Shift+Enter")
        await self.page.wait_for_timeout(400)
        return box


async def _live_routes(page, now):
    """dev_qa_shot --live: the open run and half the subagents look running (browser-only JSON rewrite)."""
    async def thread(route):
        if route.request.method != "GET":
            return await route.fallback()
        resp = await route.fetch()
        def fix(data):
            last = next((m for m in reversed(data.get("messages") or []) if m.get("role") == "assistant"), None)
            if last:
                run = dict(data.get("run") or {})
                run.update(id=run.get("id") or "qa-live", status="running", message_id=last["id"], message=last,
                           created_at=iso(now - timedelta(seconds=75)), updated_at=iso(now), cancel_requested=False)
                data["run"] = run
            return data
        await route.fulfill(response=resp, body=_map_json(await resp.text(), fix))

    async def runs(route):
        if route.request.method != "GET":
            return await route.fallback()
        resp = await route.fetch()
        def fix(data):
            data.update(status="running", created_at=iso(now - timedelta(seconds=47)), updated_at=iso(now), cancel_requested=False)
            data["compaction"] = {**(data.get("compaction") or {}), "meter": {"used": 18400, "limit": 128000, "run_id": data.get("id")}}
            return data
        await route.fulfill(response=resp, body=_map_json(await resp.text(), fix))

    async def agents(route):
        if route.request.method != "GET":
            return await route.fallback()
        resp = await route.fetch()
        def fix(data):
            for i, row in enumerate(data.get("agents") or []):
                if i % 2 == 0:
                    row.update(status="running", current="thinking", quiet_s=2, summary="", updated_at=iso(now))
            data["live_count"] = sum(1 for row in data.get("agents") or [] if row.get("status") == "running")
            return data
        await route.fulfill(response=resp, body=_map_json(await resp.text(), fix))

    await page.route("**/chat/threads/*/agents*", agents)
    await page.route(_is_thread_url, thread)
    await page.route("**/chat/runs/**", runs)


def _is_thread_url(url):
    path = url.split("?")[0].rstrip("/")
    return "/chat/threads/" in path and path.split("/chat/threads/")[1].count("/") == 0


def _map_json(text, fn):
    """Apply fn to a JSON body, or to every JSON line of an NDJSON snapshot stream."""
    try:
        return json.dumps(fn(json.loads(text)))
    except json.JSONDecodeError:
        out = []
        for line in text.splitlines():
            if line.strip():
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and "messages" in obj:
                        obj = fn(obj)
                    elif isinstance(obj, dict) and isinstance(obj.get("thread"), dict):
                        obj["thread"] = fn(obj["thread"])
                    line = json.dumps(obj)
                except json.JSONDecodeError:
                    pass
            out.append(line)
        return "\n".join(out) + "\n"


PERM_ASK = {
    "id": "qa-perm-1", "kind": "tool_permission", "status": "pending", "perm_kind": "shell", "tool": "run_terminal_command",
    "host": "qa-box", "path": "", "cwd": "/srv/fuse-api", "command": "rm -rf build && make release VERSION=2.4.0",
    "secret": False, "requester": "Main agent", "decision": "", "grant_scope": "", "grant_kind": "", "grant_value": "",
    "description": "Deletes the build directory, then builds and tags the release.",
}


async def _rewrite_thread(page, now, perm=False, error=False):
    """Browser-only states the fixtures lack: a pending permission ask, a provider error on the last reply."""
    ask = {**PERM_ASK, "expires_at": iso(now + timedelta(minutes=4))}

    async def thread(route):
        if route.request.method != "GET":
            return await route.fallback()
        resp = await route.fetch()
        def fix(data):
            if perm:
                data["bypass_permissions"] = False
                data["auto_permissions"] = False
            if error:
                last = next((m for m in reversed(data.get("messages") or []) if m.get("role") == "assistant"), None)
                if last:
                    last["error"] = "Upstream returned 502 Bad Gateway after 3 retries (provider: qa/fixture-model)"
            return data
        await route.fulfill(response=resp, body=_map_json(await resp.text(), fix))

    async def perms(route):
        if route.request.method != "GET":
            return await route.fallback()
        resp = await route.fetch()
        def fix(data):
            data["bypass"] = False
            data["auto"] = False
            data["pending"] = [ask]
            return data
        await route.fulfill(response=resp, body=_map_json(await resp.text(), fix))

    await page.route(_is_thread_url, thread)
    if perm:
        await page.route("**/chat/threads/*/permissions*", perms)


# ---------------------------------------------------------------- states

TOOLS = "QA · Tools, subagents and commands"
TRANSCRIPTS = {
    "tools": TOOLS,
    "rows": "QA · Claude Code rows",
    "edits": "QA · File edits and output",
    "summary": "QA · Summary lines",
    "multihost": "QA · Multi-host command",
    "delivery": "QA · Report delivery",
}
STATES = {}


def state(name, themes=None):
    def wrap(fn):
        STATES[name] = (fn, themes)
        return fn
    return wrap


@state("empty")
async def _empty(s):
    await s.open()


@state("empty-sidebar-collapsed")
async def _empty_collapsed(s):
    if s.narrow:
        raise Skip("phones always start with the drawer closed: same as 'empty'")
    await s.page.add_init_script("try { localStorage.setItem('fuse.chat.historyCollapsed.v1', JSON.stringify({console: true})) } catch (_) {}")
    await s.open()


@state("sidebar-chats")
async def _sidebar_chats(s):
    await s.open(TOOLS)
    await s.sidebar()


@state("sidebar-projects")
async def _sidebar_projects(s):
    await s.open(TOOLS)
    await s.sidebar()
    await s.click('[data-history-tab="projects"]', 600)


@state("sidebar-filters")
async def _sidebar_filters(s):
    # Native <select> popups render outside the page, so this shows the filters focused, not dropped down.
    await s.open(TOOLS)
    await s.sidebar()
    await s.click('[data-history-tab="projects"]', 600)
    sel = await s.need("[data-project-filter]", "filters show on the Projects tab")
    await sel.focus()
    await s.page.wait_for_timeout(200)


@state("sidebar-search")
async def _sidebar_search(s):
    await s.open(TOOLS)
    await s.sidebar()
    await s.click("[data-history-search-toggle]")
    box = await s.need("[data-history-search]")
    await box.fill("QA")
    await s.page.wait_for_timeout(500)


@state("sidebar-archived")
async def _sidebar_archived(s):
    await s.open(TOOLS)
    await s.sidebar()
    await s.click("[data-history-archived]", 600)


@state("sidebar-row-menu")
async def _sidebar_row_menu(s):
    await s.open(TOOLS)
    await s.sidebar()
    row = await s.need('[data-history-list] [data-history-item] [data-row-open][aria-current="true"]')
    await row.hover()
    item = s.page.locator('[data-history-list] [data-history-item]').filter(has=s.page.locator('[data-row-open][aria-current="true"]')).locator("[data-row-more]")
    if not await item.count():
        raise Skip("no [data-row-more] on the open row")
    await item.first.focus()
    await item.first.click(force=True)
    await s.page.wait_for_timeout(400)
    s.escape = await item.first.element_handle()


@state("account-menu")
async def _account(s):
    await s.open()
    await s.sidebar()
    await s.popover("[data-history] [data-account-toggle]")


@state("long-markdown")
async def _long_md(s):
    await s.open("QA · Long markdown")


@state("long-markdown-top")
async def _long_md_top(s):
    await s.open("QA · Long markdown")
    await s.thread_scroll(0)


@state("heavy-scrolled")
async def _heavy(s):
    await s.open("QA · Heavy transcript")
    await s.page.wait_for_timeout(1500)
    await s.thread_scroll(0.5)


@state("compacted")
async def _compacted(s):
    await s.open("QA · Compacted session")
    await s.thread_scroll(0)


@state("long-title")
async def _long_title(s):
    await s.open("QA · Long title that must truncate in sidebar and header")


@state("project-session")
async def _project(s):
    await s.open("QA · Project session")


@state("t-artifacts")
async def _artifacts_thread(s):
    await s.open("QA · Artifacts")


def _transcript_states():
    for key, title in TRANSCRIPTS.items():
        async def folded(s, title=title):
            await s.open(title)

        async def unfolded(s, title=title):
            await s.open(title)
            await s.unfold()
        STATES[f"t-{key}-folded"] = (folded, None)
        STATES[f"t-{key}-unfolded"] = (unfolded, None)


_transcript_states()


@state("live-run")
async def _live_run(s):
    await s.open("QA · Claude Code rows", live=True)
    await s.page.wait_for_timeout(500)


@state("live-tools")
async def _live_tools(s):
    await s.open(TOOLS, live=True)
    await s.page.wait_for_timeout(500)


async def _show_work(s):
    """The agents/commands panel: Combined's rail, Desktop's inspector, Console's strip."""
    rail = await s.visible("[data-theme-rail]")
    strip = await s.visible("[data-work-strip]")
    if strip is None and rail is None:
        if await s.visible("[data-theme-pill]"):
            await s.click("[data-theme-pill]", 500)
        elif await s.visible("[data-theme-inspector]"):
            await s.click("[data-theme-inspector]", 500)
    fold = await s.visible('[data-agents-list] [data-agents-fold][aria-expanded="false"]')
    if fold:
        await fold.click()
        await s.page.wait_for_timeout(300)
    if await s.visible("[data-work-strip]") is None:
        raise Skip("[data-work-strip] stays hidden")


# —— layouts: the side menus collapsed and expanded, applied after a state ——
# sb-open / sb-closed: the history sidebar (desktop: expanded or collapsed; phone: drawer open or shut).
# work-closed / work-open / work-expanded: the agents, tasks and background-commands panel (Combined's
# rail, Desktop's inspector, Console's strip) shut; open with the agent tree folded; open with the
# tree unfolded and the first background command's output shown.
LAYOUTS = [f"{sb}+{work}" for sb in ("sb-open", "sb-closed") for work in ("work-closed", "work-open", "work-expanded")]
LAYOUT_STATES = ["t-tools-folded", "live-tools", "subagent"]


async def _work_state(s):
    return await s.page.evaluate("""() => { const root = document.querySelector('[data-chat]');
        const strip = document.querySelector('[data-work-strip]'); const rail = document.querySelector('[data-theme-rail]');
        const shown = (e) => Boolean(e && !e.hidden && e.getClientRects().length && e.getBoundingClientRect().width > 40 && getComputedStyle(e).visibility === 'visible');
        return { rail: Boolean(rail), state: root?.dataset.themeRailState || '', railShown: shown(rail), strip: shown(strip) }; }""")


async def _toggle_work(s):
    for sel in ("[data-theme-pill]", "[data-theme-inspector]"):
        if await s.visible(sel):
            await s.click(sel, 500)
            return True
    return False


async def _agents_fold(s, expanded):
    fold = await s.visible(f'[data-agents-list] [data-agents-fold][aria-expanded="{"false" if expanded else "true"}"]')
    if fold:
        await s._press(fold, "[data-agents-fold]")
        await s.page.wait_for_timeout(300)


async def apply_layout(s, layout):
    parts = set(layout.split("+"))
    if s.narrow and "sb-open" in parts and "work-closed" not in parts:
        raise Skip("phones show one side panel at a time (the drawer or the agents panel)")
    work = await _work_state(s)
    if "work-closed" in parts:
        if work["rail"] and work["state"] in ("open", "inline"):
            if await s.visible("[data-theme-inspector-close]"):
                await s.click("[data-theme-inspector-close]", 500)
            else:
                await _toggle_work(s)
        elif not work["rail"]:
            await _agents_fold(s, False)
    elif "work-open" in parts or "work-expanded" in parts:
        if work["rail"] and work["state"] not in ("open", "inline"):
            await _toggle_work(s)
        if not (await _work_state(s))["strip"]:
            raise Skip("the agents/commands panel ([data-work-strip]) stays hidden here")
        await _agents_fold(s, "work-expanded" in parts)
        if "work-expanded" in parts:
            job = await s.visible("[data-jobs-list] [data-show-job]")
            if job:
                await s._press(job, "[data-show-job]")
                await s.page.wait_for_timeout(400)
    # The sidebar last: on a phone its drawer covers the page.
    if "sb-open" in parts:
        await s.sidebar()
    if "sb-closed" in parts and await s._drawer_open():
        tog = await s.visible("[data-history] [data-history-toggle]") or await s.visible("[data-chat-bar] [data-history-toggle]")
        if tog is None:
            raise Skip("no visible [data-history-toggle] to collapse the sidebar")
        await s._press(tog, "[data-history-toggle]")
        w, h = s.job.size
        await s.page.mouse.move(w / 2, h / 2)  # off the left edge, or the collapsed list peeks back in
        await s.page.wait_for_timeout(500)


@state("jobs")
async def _jobs(s):
    await s.open(TOOLS, live=True)
    await _show_work(s)


@state("subagent")
async def _subagent(s):
    await s.open(TOOLS)
    await _show_work(s)
    await s.click("[data-agents-list] [data-agent-id]", 1200)
    await s.ready(False)


@state("subagent-failed")
async def _subagent_failed(s):
    await s.open("QA · Report delivery")
    await s.thread_scroll(0)
    opener = s.page.locator("[data-chat-log] [data-open-agent]")
    n = await opener.count()
    if n < 2:
        raise Skip("fewer than two [data-open-agent] links in Report delivery")
    await opener.nth(1).scroll_into_view_if_needed()
    await opener.nth(1).click()
    await s.page.wait_for_timeout(1200)


@state("permissions-popover")
async def _perms(s):
    await s.open(TOOLS)
    await s.popover("[data-perm-chip]")


@state("permission-request")
async def _perm_request(s):
    await s.open(TOOLS, perm=True)
    await s.page.wait_for_timeout(500)
    await s.need("[data-perm-queue] [data-perm-card]", "faked pending ask did not render")


@state("error-command")
async def _error_command(s):
    await s.open(TOOLS)
    err = await s.need('[data-chat-log] [data-state="error"]')
    toggle = err.locator('[data-step-toggle][aria-expanded="false"]')
    await err.scroll_into_view_if_needed()
    if await toggle.count():
        await toggle.first.click()
        await s.page.wait_for_timeout(400)
    await err.scroll_into_view_if_needed()


@state("error-provider")
async def _error_provider(s):
    await s.open("QA · Compacted session", error=True)
    await s.need("[data-chat-log] [data-err]", "faked message error did not render")


@state("model-picker")
async def _picker(s):
    await s.open(TOOLS)
    await s.popover("[data-chat-picker] button")


@state("context-ring")
async def _ring(s):
    await s.open("QA · Claude Code rows", live=True)
    await s.popover("[data-chat-context]")


@state("context-sheet")
async def _context_sheet(s):
    await s.open(TOOLS)
    await s.popover("[data-memory-toggle]")


@state("skills-sheet")
async def _skills(s):
    await s.open(TOOLS)
    await s.popover("[data-skills-toggle]")


@state("appearance-sheet")
async def _appearance(s):
    await s.open(TOOLS)
    await s.popover("[data-view-toggle]", 700)


@state("attach-menu")
async def _attach(s):
    await s.open()
    btn = await s.need("[data-chat-attach]")
    try:
        async with s.page.expect_file_chooser(timeout=1500):
            await btn.click()
    except Exception:
        await s.page.wait_for_timeout(300)
        s.escape = await btn.element_handle()
        return
    raise Skip("+ opens the native file chooser; there is no in-page menu")


@state("projects-dialog")
async def _projects_dialog(s):
    await s.open()
    trig = await s.visible("[data-theme-project]")
    if trig:
        await s.popover("[data-theme-project]", 900)
    else:
        await s.page.evaluate("window.FuseChatActions.run('project')")
        await s.page.wait_for_timeout(900)


@state("artifacts-pane")
async def _artifact_pane(s):
    await s.open("QA · Artifacts")
    await s.popover("[data-artifact-toggle]", 1500)


@state("todos")
async def _todos(s):
    await s.open(TOOLS)
    await s.popover("[data-todo-header]")


@state("slash-menu")
async def _slash(s):
    await s.open()
    await s.type("/")
    await s.need("[data-chat-slash]")


@state("theme-picker")
async def _theme_picker(s):
    await s.open()
    box = await s.type("/theme")
    await s.page.locator("[data-chat-slash-list] #chat-slash-theme").wait_for(timeout=5000)
    await box.press("Enter")
    await s.need("[data-wallpaper-picker]")
    await s.page.wait_for_timeout(WALLPAPER_SETTLE_MS)


COMPOSER_TEXT = (
    "Please review the retry policy in app/retry.py.\n"
    "Check the backoff doubles from 0.5s and caps at 30s.\n"
    "This line is deliberately long so it wraps inside the composer at every width: "
    "it names app/sources/key_pool.py, the sticky rotation, prompt_cache_key and the 400 retry without it.\n"
    "Then run the unit tests.\n"
    "Thanks!"
)


@state("composer-wrapped")
async def _composer(s):
    await s.open()
    await s.type(COMPOSER_TEXT)


@state("focus-walk")
async def _focus(s):
    await s.open(TOOLS)
    s.focus_walk = True


@state("more-menu", themes=("desktop",))
async def _more(s):
    await s.open(TOOLS)
    await s.popover("[data-theme-more]")


@state("title-menu", themes=("desktop",))
async def _title_menu(s):
    await s.open(TOOLS)
    await s.popover("[data-theme-title-menu]")


async def _inspector(s, title, card=None, live=False, before=None):
    await s.open(title, live=live)
    if await s.visible("[data-theme-rail]") is None:
        await s.click("[data-theme-inspector]", 600)
    if before:
        await before(s)
    if card:
        target = await s.need(f'[data-theme-card="{card}"]', f"no {card} card for this thread")
        await target.scroll_into_view_if_needed()


@state("inspector-empty", themes=("desktop",))
async def _insp_empty(s):
    await s.open()
    await s.click("[data-theme-inspector]", 600)


@state("inspector-tasks", themes=("desktop",))
async def _insp_tasks(s):
    await _inspector(s, TOOLS, "tasks", live=True)


@state("inspector-terminal", themes=("desktop",))
async def _insp_term(s):
    # Terminal opens the inspector at its card (on a phone the open sheet would cover the ⋯ menu).
    await s.open(TOOLS, live=True)
    await s.click("[data-theme-terminal]", 600)
    target = await s.need('[data-theme-card="terminal"]', "no terminal card for this thread")
    await target.scroll_into_view_if_needed()


@state("inspector-diff", themes=("desktop",))
async def _insp_diff(s):
    await _inspector(s, "QA · File edits and output", "diff")


@state("inspector-plan", themes=("desktop",))
async def _insp_plan(s):
    await _inspector(s, TOOLS, "plan", live=True)


@state("inspector-preview", themes=("desktop",))
async def _insp_preview(s):
    await s.open("QA · Artifacts")
    await s.click("[data-artifact-toggle]", 1500)


@state("rail", themes=("combined",))
async def _rail(s):
    await s.open(TOOLS)
    if await s.visible("[data-theme-rail]") is None:
        await s.click("[data-theme-pill]", 600)


@state("rail-live", themes=("combined",))
async def _rail_live(s):
    await s.open("QA · Claude Code rows", live=True)
    if await s.visible("[data-theme-rail]") is None:
        await s.click("[data-theme-pill]", 600)


# ---------------------------------------------------------------- runner

class Env:
    def __init__(self, args):
        self.args = args
        self.base = args.base.rstrip("/")
        self.out = Path(args.out)
        self.threads = {}
        self.now = None
        self.storage = None
        self.wallpapers = []
        self.records = []
        self.skips = []
        self.errors = []
        self.done = 0
        self.total = 0
        self.started = time.time()


async def login(env, browser):
    ctx = await browser.new_context(ignore_https_errors=True)
    page = await ctx.new_page()
    if env.args.fixture:
        await page.goto(env.base + "/")
    else:
        creds = json.loads((ROOT / "data" / "dev-qa-login.json").read_text())
        await page.goto(env.base + "/login")
        await page.fill("input[name=email]", creds["email"])
        await page.fill("input[name=password]", creds["password"])
        await page.click("button[type=submit]")
    await page.wait_for_url("**/chat**")
    sync = await page.evaluate("fetch('/chat/sync').then(r => r.json())")
    for t in sync.get("threads") or []:
        env.threads.setdefault(t["title"], t["id"])
    stamps = [t.get("updated_at") or "" for t in sync.get("threads") or []]
    newest = max([s for s in stamps if s] or [iso(datetime.now(timezone.utc))])
    # Ten minutes after the newest thread: relative ages read the same every run.
    env.now = datetime.strptime(newest, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) + timedelta(minutes=10)
    await page.wait_for_function("() => window.FuseChatAppearance && window.FuseBackdrop", timeout=15000)
    env.wallpapers = await page.evaluate("FuseChatAppearance.wallpapers")
    env.backdrops = await page.evaluate("[...document.querySelectorAll('[data-chat-backdrop-swatches] [data-chat-backdrop]')].map((e) => e.dataset.chatBackdrop)")
    state = await ctx.storage_state()
    env.storage = {"cookies": state["cookies"], "origins": []}
    await ctx.close()
    if not any(t.startswith("QA · ") for t in env.threads):
        raise SystemExit("no 'QA · ' fixture threads: run scripts.dev_qa_account (or --fixture with FUSE_FIXTURE_QA_SEED)")


def record(env, job, check, severity, selector="", text="", rect=None, detail=""):
    env.records.append({"theme": job.theme, "viewport": job.viewport, "wallpaper": job.wallpaper, "scheme": job.scheme,
                        "state": job.state, "check": check, "severity": severity, "selector": selector, "text": text,
                        "rect": rect, "detail": detail, "shot": job.name + ".png"})


async def run_checks(env, s, shot_png):
    job, page = s.job, s.page
    data = await page.evaluate(CHECKS_JS, {"narrow": job.size[0] <= 480, "cap": TEXT_SAMPLES})
    for it in data["issues"]:
        record(env, job, it["check"], it["severity"], it["selector"], it["text"], it["rect"], it["detail"])
    style = await page.add_style_tag(content=BG_CSS)
    await page.wait_for_timeout(60)
    bg_png = await page.screenshot(animations="disabled", caret="hide")
    await style.evaluate("e => e.remove()")
    bg = Image.open(io.BytesIO(bg_png)).convert("RGB")
    for smp in data["text"]:
        got = contrast(bg, smp)
        if not got:
            continue
        ratio, back, fore = got
        need = 3.0 if smp["large"] or smp["icon"] else 4.5
        if ratio < need:
            what = "icon" if smp["icon"] else f"{smp['size']}px {smp['cat']} text"
            record(env, job, "contrast", "fail", smp["selector"], smp["text"], smp["rect"],
                   f"{ratio:.2f}:1 < {need}:1 ({what}; fg rgb{tuple(fore)} on rgb{tuple(back)}, 10th pct)")
    return data["counts"]


async def focus_walk(env, s):
    page, job = s.page, s.job
    await page.evaluate(START_JS)
    first = None
    prev = "start"
    for i in range(60):
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(40)
        if i == 0:
            await page.evaluate("document.getElementById('qa-walk-start')?.remove()")
        info = await page.evaluate(FOCUS_JS)
        if info.get("body"):
            lost = await page.evaluate(LOST_JS)
            if lost:
                record(env, job, "focus-lost", "fail", "body", "", None, f"Tab stop {i + 1} landed on <body> after {prev}: {lost}")
            break
        if info.get("skip"):
            continue
        key = (info["selector"], info["text"], tuple(info["rect"]))
        if first is None:
            first = key
        elif key == first:
            break
        prev = f"{info['selector']} {info['text'][:30]!r}"
        if os.environ.get("THEME_AUDIT_DEBUG"):
            print("  tab", i + 1, prev, info["rect"], info["hidden"], info["indicator"], info.get("refocused"))
        if info["hidden"]:
            record(env, job, "focus-invisible", "fail", info["selector"], info["text"], info["rect"], f"Tab stop {i + 1}: focused element is {info['hidden']}")
        elif not info["indicator"]:
            record(env, job, "focus-indicator", "fail", info["selector"], info["text"], info["rect"], f"Tab stop {i + 1}: no visible change between focused and blurred")


async def escape_check(env, s):
    page, job, trigger = s.page, s.job, s.escape
    sel = await trigger.evaluate("(e) => e.getAttribute('aria-controls') || ''")
    off = await page.evaluate("""(id) => { const p = id && document.getElementById(id); if (!p || p.hidden) return null;
        const r = p.getBoundingClientRect(); const area = r.width * r.height; if (area < 4) return null;
        const w = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0)), h = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
        return (w * h) / area < 0.9 ? [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)] : null; }""", sel)
    if off:
        record(env, job, "popup-offscreen", "fail", f"#{sel}", "", off, "the opened popup is less than 90% inside the viewport")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(350)
    res = await trigger.evaluate("""(t, id) => { const a = document.activeElement; const pop = id && document.getElementById(id);
        const open = Boolean(pop && !pop.hidden && pop.getClientRects().length);
        // A trigger the page redrew is back when focus is on its replacement (same tag and name).
        const name = (e) => e.tagName + '|' + String(e.getAttribute('aria-label') || e.title || e.innerText || '').trim();
        const redrawn = !t.isConnected && a && a !== document.body && name(a) === name(t);
        return { back: Boolean(a && (a === t || t.contains(a) || redrawn)), active: a ? (a.tagName.toLowerCase() + (a.id ? '#' + a.id : '') + ' ' + String(a.getAttribute('aria-label') || a.innerText || '').trim().slice(0, 40)) : 'none', open,
          label: String(t.getAttribute('aria-label') || t.title || t.innerText || '').trim().slice(0, 40), connected: t.isConnected }; }""", sel)
    if res["open"]:
        record(env, job, "escape-close", "fail", f"#{sel}", res["label"], None, "Escape did not close the popup")
    elif not res["back"]:
        record(env, job, "focus-return", "fail", "", res["label"], None,
               f"after Escape focus is on {res['active']!r}, not the trigger{'' if res['connected'] else ' (trigger was re-rendered)'}")


async def new_context(env, browser, job):
    w, h = job.size
    # Phones are touch devices ((hover: none), (pointer: coarse)); Console keeps its baseline's mouse.
    ctx = await browser.new_context(ignore_https_errors=True, viewport={"width": w, "height": h}, device_scale_factor=1,
                                    has_touch=w <= 480 and job.theme != "console", reduced_motion="reduce", color_scheme=job.scheme if job.scheme in ("light", "dark") else "dark",
                                    storage_state=env.storage)
    domain = env.base.split("://", 1)[1].split(":")[0].split("/")[0]
    await ctx.add_cookies([{"name": "fuse_chat_view", "value": job.theme, "domain": domain, "path": "/"}])
    look = {"palette": "paper" if job.scheme == "light" else "fuse", "backdrop": job.wallpaper, "strength": 28, "restore": ""}
    await ctx.add_init_script(script=f"({INIT_JS})({json.dumps([{job.theme: look}, False])})")
    await ctx.clock.set_fixed_time(env.now)
    aborted = []

    async def guard(route):
        req = route.request
        url = req.url
        if url.startswith(("data:", "blob:")):
            return await route.fallback()
        fonts = url.startswith(("https://fonts.googleapis.com/", "https://fonts.gstatic.com/"))
        if (not url.startswith(env.base) and not fonts) or req.method not in ("GET", "HEAD"):
            aborted.append(f"{req.method} {url}")
            return await route.abort()
        return await route.fallback()
    await ctx.route("**/*", guard)
    return ctx, aborted


async def run_lanes(env, browser, lanes):
    """A worker: one lane at a time, one context per lane, a fresh page per shot."""
    while lanes:
        lane = lanes.pop(0)
        ctx, aborted = await new_context(env, browser, lane[0])
        try:
            for job in lane:
                await run_job(env, ctx, aborted, job)
        finally:
            await ctx.close()


async def run_job(env, ctx, aborted, job):
    t0 = time.time()
    fn, _ = STATES[job.state]
    aborted.clear()
    page = await ctx.new_page()
    logs = []
    page.on("console", lambda m: logs.append(("console", m.text, (m.location or {}).get("url", ""))) if m.type == "error" else None)
    page.on("pageerror", lambda e: logs.append(("pageerror", str(e), "")))
    s = Page(env, page, job)
    s.focus_walk = False
    status = "ok"
    marks = []
    try:
        await fn(s)
        marks.append(("state", time.time()))
        if job.layout:
            await apply_layout(s, job.layout)
            s.escape = None
        if job.wallpaper != "none":
            await page.wait_for_function("() => { const c = document.querySelector('[data-chat-scene-fx]'); return !c || (!c.hidden && c.width > 0); }", timeout=8000)
            await page.wait_for_timeout(WALLPAPER_SETTLE_MS)
        await page.evaluate("document.fonts.ready.then(() => true)")
        if s.focus_walk and env.args.checks:
            await focus_walk(env, s)
        png = await page.screenshot(animations="disabled", caret="hide")
        (env.out / f"{job.name}.png").write_bytes(png)
        marks.append(("shot", time.time()))
        if env.args.checks:
            await run_checks(env, s, png)
            marks.append(("checks", time.time()))
            if s.escape is not None:
                await escape_check(env, s)
            for kind, text, url in logs:
                harness = aborted and ("Failed to load resource" in text or "Failed to fetch" in text or "ERR_FAILED" in text)
                if harness:
                    continue
                if "status of 5" in text:  # the server under audit load, not the theme
                    record(env, job, "server-error", "warn", url, text[:300], None, kind)
                    continue
                record(env, job, "console-error" if kind == "console" else "page-error", "fail", url, text[:300], None, kind)
    except Skip as why:
        status = f"skip: {why}"
        env.skips.append({"theme": job.theme, "state": job.state, "viewport": job.viewport, "reason": str(why)})
    except Exception as error:  # a state that crashed is a harness failure, reported but not fatal
        status = f"error: {type(error).__name__}: {str(error).splitlines()[0][:200]}"
        env.errors.append({"shot": job.name, "error": status})
    finally:
        await page.close()
    env.done += 1
    fails = sum(1 for r in env.records if r["shot"] == job.name + ".png" and r["severity"] == "fail")
    steps = " ".join(f"{k}={v - prev:.1f}" for (k, v), prev in zip(marks, [t0] + [m[1] for m in marks])) if os.environ.get("THEME_AUDIT_DEBUG") else ""
    print(f"[{env.done}/{env.total}] {job.name} {time.time() - t0:.1f}s fails={fails} {status if status != 'ok' else ''}{steps}", flush=True)


def _split(value):
    return [v.strip() for v in value.split(",") if v.strip()]


def plan(env):
    a = env.args
    themes = _split(a.themes)
    viewports = _split(a.viewports) if a.viewports else VIEWPORTS
    wanted = _split(a.states) if a.states else ["*"]
    states = lambda pool: [n for n in pool if any(fnmatch.fnmatch(n, w) for w in wanted)]  # noqa: E731
    all_wp = ["none"] + [w["id"] for w in env.wallpapers]
    jobs = []
    if a.console_baseline:
        light = next((w["id"] for w in env.wallpapers if w["scheme"] == "light"), None)
        dark = next((w["id"] for w in env.wallpapers if w["scheme"] == "dark"), None)
        looks = [(w, "light" if w == light else "dark") for w in (dark, light) if w]
        for vp in BASELINE_VIEWPORTS:
            for st in states(STATES):
                jobs.append(Job("console", vp, "none", "dark", st))
            for wp, sc in looks:  # the two wallpapers only over the sweep states
                for st in states(SWEEP_STATES):
                    jobs.append(Job("console", vp, wp, sc, st))
        return _themed(jobs)

    layouts = LAYOUTS if a.layouts == "all" else [] if a.layouts in (None, "none") else _split(a.layouts)
    bad = [x for x in layouts if x not in LAYOUTS]
    if bad:
        raise SystemExit(f"unknown layouts {bad}; choose from {LAYOUTS}")

    def wallpapers(value):
        if value == "all":
            return all_wp
        if value == "every":
            return env.backdrops
        return _split(value)
    if a.wallpapers or a.schemes:
        wps = wallpapers(a.wallpapers or "none")
        schemes = _split(a.schemes or "dark")
        for th in themes:
            for vp in viewports:
                for wp in wps:
                    for sc in schemes:
                        for st in states(STATES):
                            for lay in layouts or [""]:
                                jobs.append(Job(th, vp, wp, sc, st, lay))
        return _themed(jobs)
    full, smoke = a.profile == "full", a.profile == "smoke"
    primary = viewports if full or a.viewports else [v for v in PRIMARY if v in viewports]
    if a.layouts is not None:
        sweep = layouts
    else:
        sweep = ["sb-open+work-open", "sb-closed+work-closed"] if smoke else LAYOUTS
    sweep_states = [] if smoke else SWEEP_STATES if full else ["long-markdown", "composer-wrapped"]
    for th in themes:
        for vp in viewports:
            pool = STATES if vp in primary and not smoke else CORE_STATES
            if smoke and vp not in primary:
                continue
            for st in states(pool):
                jobs.append(Job(th, vp, "none", "dark", st))
            if vp in primary:
                for st in states(LAYOUT_STATES):
                    for lay in sweep:
                        jobs.append(Job(th, vp, "none", "dark", st, lay))
        for vp in [v for v in SWEEP_VIEWPORTS if v in viewports]:
            for wp in all_wp:
                for sc in ("light", "dark"):
                    if (wp, sc) == ("none", "dark"):
                        continue
                    for st in states(sweep_states):
                        jobs.append(Job(th, vp, wp, sc, st))
    return _themed(jobs)


def _themed(jobs):
    out = []
    for job in jobs:
        only = STATES[job.state][1]
        if only and job.theme not in only:
            continue
        out.append(job)
    return out


def summarize(env, jobs, elapsed):
    recs = env.records
    fails = [r for r in recs if r["severity"] == "fail"]
    warns = [r for r in recs if r["severity"] == "warn"]
    lines = [f"# Theme audit {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
             f"Target {env.base}; {len(jobs)} shots planned, {len(env.skips)} skipped, {len(env.errors)} harness errors; "
             f"{len(fails)} failures, {len(warns)} warnings; {elapsed / 60:.1f} min.",
             f"Clock frozen at {iso(env.now)}. Wallpapers: {', '.join(w['id'] for w in env.wallpapers)}.", ""]
    by_tc = Counter((r["theme"], r["check"]) for r in fails)
    checks = sorted({c for _, c in by_tc})
    themes = sorted({t for t, _ in by_tc})
    if by_tc:
        lines += ["## Failures per theme and check", "", "| theme | " + " | ".join(checks) + " | total |",
                  "|---|" + "---|" * (len(checks) + 1)]
        for t in themes:
            row = [str(by_tc.get((t, c), 0)) for c in checks]
            lines.append(f"| {t} | " + " | ".join(row) + f" | {sum(by_tc[(t, c)] for c in checks)} |")
        lines.append("")
    by_state = Counter((r["theme"], r["state"]) for r in fails)
    lines += ["## Failures per state", ""] + [f"- {t} / {s}: {n}" for (t, s), n in sorted(by_state.items(), key=lambda kv: -kv[1])] + [""]
    offenders = defaultdict(lambda: {"n": 0, "shots": set(), "example": None})
    for r in fails:
        key = (r["theme"], r["check"], r["selector"], r["detail"].split(" (")[0] if r["check"] != "contrast" else "")
        o = offenders[key]
        o["n"] += 1
        o["shots"].add(r["shot"])
        o["example"] = o["example"] or r
    top = sorted(offenders.items(), key=lambda kv: -len(kv[1]["shots"]))[:40]
    lines += ["## Top offenders (by number of shots)", ""]
    for (t, c, sel, _), o in top:
        ex = o["example"]
        lines.append(f"- **{t} {c}** `{sel}` \"{ex['text'][:50]}\" in {len(o['shots'])} shots ({o['n']}x): {ex['detail'][:160]} — e.g. {ex['shot']}")
    if warns:
        wc = Counter((r["theme"], r["check"]) for r in warns)
        lines += ["", "## Warnings", ""] + [f"- {t} {c}: {n}" for (t, c), n in sorted(wc.items())]
    if env.skips:
        sk = Counter((s["theme"], s["state"], s["reason"]) for s in env.skips)
        lines += ["", "## Skipped states", ""] + [f"- {t} / {s}: {r} ({n} shots)" for (t, s, r), n in sorted(sk.items())]
    if env.errors:
        lines += ["", "## Harness errors", ""] + [f"- {e['shot']}: {e['error']}" for e in env.errors]
    (env.out / "summary.md").write_text("\n".join(lines) + "\n")
    (env.out / "failures.json").write_text(json.dumps({"failures": fails, "warnings": warns, "skips": env.skips,
                                                       "errors": env.errors, "clock": iso(env.now)}, indent=1))
    return len(fails)


# ---------------------------------------------------------------- fixture server

def _free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def start_fixture(tree, port):
    tree = Path(tree).resolve()
    while not _free(port):
        port += 1
    env = {**os.environ, "FUSE_CHAT_TEST_MODE": "1", "FUSE_BROWSER_PORT": str(port), "FUSE_FIXTURE_QA_SEED": "1"}
    log = open(f"/tmp/theme-audit-fixture-{port}.log", "w")
    proc = subprocess.Popen([PY, "tests/chat_browser_fixture.py"], cwd=tree, env=env, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 120
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"fixture exited {proc.returncode}; see {log.name}")
        try:
            urllib.request.urlopen(base + "/static/app.css", timeout=2)
            return proc, base
        except Exception:
            time.sleep(0.5)
    stop_fixture(proc)
    raise SystemExit(f"fixture did not answer in 120s; see {log.name}")


def stop_fixture(proc):
    if proc and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)


# ---------------------------------------------------------------- diff

def diff(before, after):
    before, after = Path(before), Path(after)
    out = after / "diff"
    a = {p.name for p in before.glob("*.png")}
    b = {p.name for p in after.glob("*.png")}
    same = changed = 0
    for name in sorted(a & b):
        x = Image.open(before / name).convert("RGBA")
        y = Image.open(after / name).convert("RGBA")
        if x.size == y.size and ImageChops.difference(x, y).getbbox() is None:
            same += 1
            continue
        changed += 1
        out.mkdir(exist_ok=True)
        if x.size == y.size:
            d = ImageChops.difference(x, y).convert("RGB")
            box = d.getbbox()
            mask = d.convert("L").point(lambda v: 255 if v else 0)
            red = Image.new("RGB", x.size, (255, 0, 40))
            Image.composite(red, Image.blend(x.convert("RGB"), Image.new("RGB", x.size), 0.6), mask).save(out / name)
            print(f"DIFF {name} bbox={box}")
        else:
            print(f"DIFF {name} size {x.size} -> {y.size}")
    for name in sorted(a - b):
        print(f"MISSING in after: {name}")
    for name in sorted(b - a):
        print(f"NEW in after: {name}")
    total = changed + len(a ^ b)
    print(f"{same} identical, {changed} changed, {len(a - b)} missing, {len(b - a)} new")
    return 1 if total else 0


# ---------------------------------------------------------------- main

async def main(args):
    proc = None
    if args.fixture:
        proc, args.base = start_fixture(args.fixture, args.port)
    env = Env(args)
    env.out.mkdir(parents=True, exist_ok=True)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--font-render-hinting=none", "--disable-lcd-text"])
            await login(env, browser)
            jobs = plan(env)
            env.total = len(jobs)
            if args.plan:
                print(f"{len(jobs)} shots:", dict(Counter(j.theme + " " + j.viewport for j in jobs)))
                return 0
            print(f"{len(jobs)} shots -> {env.out} (clock {iso(env.now)}, {min(args.concurrency, MAX_PAGES)} pages)", flush=True)
            groups = defaultdict(list)
            for job in jobs:
                groups[(job.theme, job.viewport, job.wallpaper, job.scheme)].append(job)
            lanes = [g[i:i + LANE] for g in groups.values() for i in range(0, len(g), LANE)]
            lanes.sort(key=len, reverse=True)
            await asyncio.gather(*(run_lanes(env, browser, lanes) for _ in range(min(args.concurrency, MAX_PAGES))))
            await browser.close()
    finally:
        stop_fixture(proc)
    elapsed = time.time() - env.started
    n = summarize(env, jobs, elapsed)
    print(f"done in {elapsed / 60:.1f} min: {n} failures, {len(env.skips)} skips, {len(env.errors)} harness errors -> {env.out}/summary.md")
    return 1 if (n and args.checks) or env.errors else 0


def cli(argv):
    if argv[:1] == ["diff"]:
        if len(argv) != 3:
            raise SystemExit("usage: theme_audit.py diff BEFORE_DIR AFTER_DIR")
        return diff(argv[1], argv[2])
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default="https://127.0.0.1:8089")
    ap.add_argument("--fixture", metavar="TREE", help="start TREE/tests/chat_browser_fixture.py instead of using --base")
    ap.add_argument("--port", type=int, default=8110, help="fixture port (next free one is used if taken)")
    ap.add_argument("--themes", default="combined,desktop")
    ap.add_argument("--viewports", help=f"comma list, default {','.join(VIEWPORTS)}")
    ap.add_argument("--wallpapers", help="all (none + every /theme wallpaper) | none | every (all Appearance backdrops) | comma list")
    ap.add_argument("--schemes", help="light,dark")
    ap.add_argument("--states", help="comma list of names or globs (see --list-states)")
    ap.add_argument("--profile", choices=("smoke", "quick", "full"), default="quick",
                    help="smoke: core states at 1440x900 + 390x844 (~1 min); quick (default): every state at those two, "
                    "core states at the rest, layouts + a wallpaper sweep; full: every state at every viewport")
    ap.add_argument("--layouts", help="side menus collapsed/expanded applied after each state: all | none | comma list of "
                    + ", ".join(LAYOUTS) + f". Default: all over {', '.join(LAYOUT_STATES)} at every viewport")
    ap.add_argument("--out", default=f"/tmp/fuse-theme-audit/{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    ap.add_argument("--concurrency", type=int, default=MAX_PAGES)
    ap.add_argument("--no-checks", dest="checks", action="store_false", help="screenshots only")
    ap.add_argument("--console-baseline", action="store_true",
                    help="console only, checks off: every state at 1440/390/1920 with no wallpaper, plus a dark and a light wallpaper over the sweep states")
    ap.add_argument("--list-states", action="store_true")
    ap.add_argument("--plan", action="store_true", help="log in, print the shot count, run nothing")
    args = ap.parse_args(argv)
    if args.list_states:
        for name, (_, only) in STATES.items():
            print(name + (f"  ({', '.join(only)} only)" if only else ""))
        print("layouts:", ", ".join(LAYOUTS))
        return 0
    if args.console_baseline:
        args.themes, args.checks = "console", False
    return asyncio.run(main(args))


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
