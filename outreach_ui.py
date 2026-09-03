#!/usr/bin/env python3
"""
Local review UI — stage 6, with buttons.

    python3 outreach_ui.py            # then open http://127.0.0.1:8765

Shows every claim `outreach.db` is holding next to what Gmail actually says
about it, and offers the three actions that resolve a disagreement: edit
the draft, discard it, or record that it went out. Runs on this Mac, reads
the same local SQLite file and the same Gmail app password the CLI already
uses, and serves nothing to the network.

**There is no send endpoint, and adding one would be a mistake.** The
pipeline puts drafts in Gmail precisely so that reaching a recipient
requires a human in a real mail client -- stage 6's approval gate is
enforced by the absence of a send path, not by a policy this file could
promise to follow. The UI links out to the Gmail draft instead; the Send
button stays Gmail's.

Two deliberate constraints on how it is served:

- Bound to 127.0.0.1, never 0.0.0.0. This process can delete mail and
  rewrite the dedup store, so it must not be reachable from the LAN.
- Every mutating request carries a token minted at startup and embedded in
  the page. Any site your browser has open can POST to localhost; without
  this, a page you did not write could discard your drafts.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
import time
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from outreach import reconcile, store
from outreach.gmail_draft import replace_draft, trash_draft

TOKEN = secrets.token_urlsafe(32)


# ── data ───────────────────────────────────────────────────────────────────

def _rows(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# Gmail's verdict, cached briefly. Every action re-checks Gmail for its own
# guard and the page then reloads, so an uncached build_state opened three
# IMAP connections per button press -- enough churn that Gmail started
# closing them mid-command. The TTL is short because the mailbox changes
# under us (a human sending from their phone is the whole point), and any
# mutation clears it outright.
_CACHE: dict[str, object] = {"at": 0.0, "states": {}}
_CACHE_TTL = 20.0


def invalidate() -> None:
    _CACHE["at"] = 0.0


def gmail_states(pending: list[dict]) -> dict:
    fresh = time.monotonic() - float(_CACHE["at"]) < _CACHE_TTL
    cached = _CACHE["states"]
    if fresh and isinstance(cached, dict) and set(cached) == {
        c["company_slug"] for c in pending
    }:
        return cached
    states = reconcile.inspect(pending)
    _CACHE["at"], _CACHE["states"] = time.monotonic(), states
    return states


def build_state() -> dict:
    """Everything the page renders: the store's claims, plus Gmail's verdict
    on each pending one."""
    pending = _rows(store.pending())
    states = gmail_states(pending)
    for claim in pending:
        state = states.get(claim["company_slug"])
        claim["gmail"] = asdict(state) if state else None
    from outreach import unattended
    slates = _rows(store.slates())
    for s in slates:
        for key in ("slate_json", "resolved_json", "personalization_json"):
            try:
                s[key.replace("_json", "")] = json.loads(s.get(key) or "[]")
            except (json.JSONDecodeError, TypeError):
                s[key.replace("_json", "")] = []
    return {
        "pending": pending,
        "discarded": _rows(store.discarded()),
        "contacted": _rows(store.contacted()),
        # The unattended run parks companies here when rank one was not
        # clean or the score sat in 65-69; picking is the human step the
        # design keeps, so it happens here, not in the run.
        "slates": [s for s in slates if s["status"] in ("awaiting", "approved")],
        "last_run": unattended.load_state().get("last_run"),
    }


# ── actions ────────────────────────────────────────────────────────────────

def action_discard(company: str) -> dict:
    """Delete the draft and release the claim, so the company is open again.

    Refuses when Gmail shows the message in Sent. The store cannot know
    about a send -- a human does that in Gmail -- so a claim that looks
    abandoned may actually be a completed outreach, and releasing it would
    let the pipeline write a second cold email to someone who already got
    one. That is the one thing this project's dedup exists to prevent.
    """
    state, row = store.claim_state(company)
    if state is None:
        raise ValueError(f"{company} has no claim")
    if state == "sent":
        raise ValueError(f"{company} was already sent to; cannot discard")

    seen = reconcile.inspect([dict(row)]).get(company)
    if seen and seen.state == reconcile.SENT:
        raise ValueError(
            f"Gmail shows a message to {row['contact_email']} in Sent "
            f"({seen.sent_date}). Record it as sent instead of discarding."
        )

    moved = trash_draft(to=row["contact_email"], subject=row["draft_subject"])
    store.discard_draft(company)
    invalidate()
    return {"company": company, "drafts_trashed": moved, "status": "discarded"}


def action_mark_sent(company: str) -> dict:
    """Catch the store up to a send that already happened in Gmail.

    Verified against the Sent mailbox rather than taken on trust: this
    writes to `outreach_log`, which closes the company permanently, and an
    entry made by mistake is not something the pipeline offers a way to
    undo.
    """
    state, row = store.claim_state(company)
    if state is None:
        raise ValueError(f"{company} has no claim")
    if state == "sent":
        return {"company": company, "status": "already recorded"}

    seen = reconcile.inspect([dict(row)]).get(company)
    if not seen or seen.state != reconcile.SENT:
        found = seen.state if seen else "unknown"
        raise ValueError(
            f"Gmail shows no sent message to {row['contact_email']} "
            f"(state: {found}). Not recording a send that cannot be confirmed."
        )

    # Record Gmail's Date header as sent_at, not this row's creation time:
    # reconciliation runs after the fact by definition, sometimes weeks
    # after (company-a), and the follow-up window math reads sent_at.
    from outreach.replies import _as_iso

    store.mark_sent(
        company_slug=company,
        contact_email=row["contact_email"],
        sent_at=_as_iso(seen.sent_date),
    )
    invalidate()
    return {"company": company, "status": "sent", "sent_date": seen.sent_date}


def action_update(company: str, subject: str, body: str) -> dict:
    """Rewrite the draft sitting in Gmail. Claim and dedup are untouched."""
    state, row = store.claim_state(company)
    if state is None:
        raise ValueError(f"{company} has no claim")
    if state == "sent":
        raise ValueError(f"{company} was already sent to; the draft is gone")
    if not subject.strip() or not body.strip():
        raise ValueError("subject and body cannot be empty")

    replaced = replace_draft(
        to=row["contact_email"],
        old_subject=row["draft_subject"],
        subject=subject,
        body=body,
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE pending_outreach SET draft_subject=?, updated_at=datetime('now') "
            "WHERE company_slug=?",
            (subject, company),
        )
        conn.commit()
    invalidate()
    return {"company": company, "replaced": replaced, "status": "updated"}


def action_check_replies(_: dict) -> dict:
    """Ask Gmail whether anyone wrote back, and record it.

    Kept behind a button rather than run on page load: it walks a thread per
    contacted company, and the answer changes on the timescale of days, not
    seconds.
    """
    from outreach import replies as reply_check

    contacted = [dict(r) for r in store.contacted()]
    if not contacted:
        return {"checked": 0, "replied": 0}

    states = reply_check.check(contacted)
    checked_at = reply_check.now_utc()
    checked = replied = 0
    for row in contacted:
        state = states.get(row["company_slug"])
        if not state or state.state == reply_check.UNKNOWN:
            continue
        checked += 1
        got = state.state == reply_check.REPLIED
        replied += 1 if got else 0
        store.record_reply_check(
            row["company_slug"],
            replied_at=state.replied_at if got else None,
            checked_at=checked_at,
        )
    invalidate()
    return {"checked": checked, "replied": replied}


def action_slate_pick(company: str, name: str) -> dict:
    """Record the human's pick. The next unattended run drafts to it; nothing
    is written to Gmail here."""
    row = store.approve_slate(company, name)
    invalidate()
    return {"company": company, "chosen_name": row["chosen_name"], "status": row["status"]}


def action_slate_dismiss(company: str, reason: str) -> dict:
    row = store.dismiss_slate(company, reason or "dismissed in the review UI")
    invalidate()
    return {"company": company, "status": row["status"]}


ACTIONS = {
    "slate-pick": lambda p: action_slate_pick(p["company"], p["name"]),
    "slate-dismiss": lambda p: action_slate_dismiss(p["company"], p.get("reason", "")),
    "discard": lambda p: action_discard(p["company"]),
    "mark-sent": lambda p: action_mark_sent(p["company"]),
    "update": lambda p: action_update(p["company"], p["subject"], p["body"]),
    "check-replies": action_check_replies,
}


# ── server ─────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "outreach-ui"

    def log_message(self, fmt, *args):  # quieter than the default
        print(f"  {self.command} {self.path}")

    def _send(self, code: int, payload: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path == "/":
            self._send(200, PAGE.replace("__TOKEN__", TOKEN).encode(), "text/html")
        elif self.path == "/api/state":
            try:
                self._json(200, build_state())
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        # A cross-origin page cannot set this header without a preflight
        # this server never approves, so the token is what actually keeps
        # another tab from driving these endpoints.
        if self.headers.get("X-Outreach-Token") != TOKEN:
            self._json(403, {"error": "bad or missing token"})
            return
        name = self.path.removeprefix("/api/")
        if name not in ACTIONS:
            self._json(404, {"error": "unknown action"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            self._json(200, ACTIONS[name](payload))
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Outreach drafts</title>
<style>
  :root {
    --bg: #fbfbfa; --card: #fff; --ink: #1a1a18; --muted: #6b6b66;
    --line: #e4e4e0; --accent: #2f6f4e; --warn: #9a5b1e; --danger: #9b2c2c;
    --chip: #f0f0ed;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #17171a; --card: #1f1f23; --ink: #ececea; --muted: #9a9a94;
      --line: #33333a; --accent: #6bbd8f; --warn: #d79a5b; --danger: #e08585;
      --chip: #2a2a30;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 2rem 1.25rem 4rem; background: var(--bg); color: var(--ink);
    font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
  }
  main { max-width: 860px; margin: 0 auto; }
  h1 { font-size: 1.35rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
  .sub { color: var(--muted); font-size: .875rem; margin: 0 0 1.75rem; }
  h2 {
    font-size: .78rem; text-transform: uppercase; letter-spacing: .09em;
    color: var(--muted); margin: 2.25rem 0 .75rem; font-weight: 600;
  }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 10px;
    padding: 1rem 1.1rem; margin-bottom: .75rem;
  }
  .top { display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap; }
  .slug { font-weight: 650; font-size: 1.02rem; }
  .score { color: var(--muted); font-size: .82rem; font-variant-numeric: tabular-nums; }
  .title { color: var(--muted); font-size: .875rem; margin: .2rem 0 .55rem; }
  .who { font-size: .875rem; margin-bottom: .5rem; }
  .who b { font-weight: 600; }
  .mail { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; }
  .chip {
    display: inline-block; padding: .12rem .5rem; border-radius: 999px;
    background: var(--chip); font-size: .72rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: .05em;
  }
  .chip.draft { color: var(--accent); }
  .chip.sent { color: var(--warn); }
  .chip.missing { color: var(--danger); }
  .chip.unknown { color: var(--muted); }
  .note {
    font-size: .84rem; color: var(--muted); border-left: 2px solid var(--line);
    padding-left: .7rem; margin: .6rem 0;
  }
  .note.small { font-size: .79rem; }
  .title a { color: inherit; text-decoration: none; border-bottom: 1px solid var(--line); }
  .title a:hover { border-bottom-color: var(--muted); }
  /* The draft is the thing being reviewed, so it reads like an email
     instead of hiding behind an edit button. */
  .reading {
    background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
    padding: .75rem .85rem; margin: .7rem 0 0;
  }
  .reading .subject { font-weight: 650; font-size: .89rem; margin-bottom: .5rem; }
  .reading .body {
    white-space: pre-wrap; font-size: .875rem; line-height: 1.62;
    color: var(--ink);
  }
  .vchip {
    display: inline-block; margin-left: .4rem; padding: .05rem .42rem;
    border-radius: 999px; font-size: .68rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: .04em;
    background: var(--chip); color: var(--muted); cursor: help;
  }
  .vchip.verified { color: var(--accent); }
  .vchip.catch_all, .vchip.risky, .vchip.unverified { color: var(--warn); }
  .vchip.invalid { color: var(--danger); }
  .row { display: flex; gap: .45rem; flex-wrap: wrap; margin-top: .8rem; }
  button, a.btn {
    font: inherit; font-size: .84rem; padding: .38rem .8rem; border-radius: 7px;
    border: 1px solid var(--line); background: var(--card); color: var(--ink);
    cursor: pointer; text-decoration: none; display: inline-block;
  }
  button:hover, a.btn:hover { border-color: var(--muted); }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button.danger { color: var(--danger); }
  button:disabled { opacity: .45; cursor: default; }
  textarea, input[type=text] {
    width: 100%; font: inherit; font-size: .875rem; padding: .5rem .6rem;
    border: 1px solid var(--line); border-radius: 7px; background: var(--bg);
    color: var(--ink); margin-bottom: .5rem;
  }
  textarea { min-height: 12rem; resize: vertical; line-height: 1.6; }
  .editor { margin-top: .85rem; }
  .muted { color: var(--muted); }
  .empty { color: var(--muted); font-size: .875rem; font-style: italic; }
  .flash {
    position: fixed; left: 50%; transform: translateX(-50%); bottom: 1.25rem;
    background: var(--ink); color: var(--bg); padding: .55rem 1rem;
    border-radius: 8px; font-size: .85rem; opacity: 0; transition: opacity .2s;
    pointer-events: none; max-width: 90vw;
  }
  .flash.show { opacity: 1; }
  .listrow {
    display: flex; gap: .6rem; align-items: baseline; padding: .3rem 0;
    font-size: .875rem; border-bottom: 1px solid var(--line);
  }
  .listrow:last-child { border-bottom: 0; }
</style>
</head>
<body>
<main>
  <h1>Outreach drafts</h1>
  <p class="sub">
    What <code>outreach.db</code> is holding, checked against what Gmail actually has.
    Sending stays in Gmail.
  </p>
  <div id="app"><p class="empty">Checking Gmail…</p></div>
</main>
<div class="flash" id="flash"></div>

<script>
const TOKEN = "__TOKEN__";
const esc = s => (s ?? "").replace(/[&<>"']/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

let flashTimer;
function flash(msg) {
  const el = document.getElementById("flash");
  el.textContent = msg; el.classList.add("show");
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

async function post(action, payload) {
  const res = await fetch("/api/" + action, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Outreach-Token": TOKEN},
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "request failed");
  return data;
}

const EXPLAIN = {
  draft:   "Sitting in Gmail, unsent.",
  sent:    "Gmail shows this in Sent. The store still calls it pending — record it so the company is closed for good.",
  missing: "No draft in Gmail and nothing in Sent. It was deleted; release the claim to reopen the company.",
  unknown: "Could not reach Gmail, so this is the store's view only.",
};

// stage 4's label decides whether the address is worth trusting, so it is
// spelled out rather than shown as a bare word. catch_all in particular
// reads as benign and is not: it means nothing confirmed the mailbox.
const VERIFY = {
  verified:   ["confirmed", "SMTP confirmed this exact mailbox accepts mail."],
  catch_all:  ["unconfirmed", "The domain accepts mail at any address, so nothing proves this particular mailbox exists. The name may still be wrong."],
  risky:      ["risky", "Hunter flagged this address as risky — it may bounce."],
  unverified: ["unchecked", "Verification did not run or could not reach Hunter."],
  invalid:    ["undeliverable", "Confirmed undeliverable. This should never have been drafted."],
};

// Agent 1 returns a ranked slate, not a single pick; the chosen contact is
// on the card, and the alternates it was chosen over belong next to it —
// reviewing the selection is the point of storing them.
function slateNote(c) {
  if (!c.contact_slate) return "";
  let slate;
  try { slate = JSON.parse(c.contact_slate); } catch { return ""; }
  if (!Array.isArray(slate) || !slate.length) return "";
  const rows = slate.map(s => {
    const chosen = s.name === c.contact_name ? "→ " : "&nbsp;&nbsp;&nbsp;";
    const addr = s.address ? ` · <span class="mail">${esc(s.address)}</span>` : "";
    // A probed address came from the domain's own server, not Hunter —
    // worth seeing, since a risky label there means "confirm the person".
    const src = s.address_source === "probe" ? ` <span class="muted">(probe)</span>` : "";
    // An unverified-by-choice candidate arrives with an empty label
    // (resolve_candidate_slate maps DEFERRED to ""), so falsy means
    // "no verdict to show", not "unknown verdict".
    const label = s.verify_label ? ` · ${esc(s.verify_label)}` : "";
    return `${chosen}<b>${esc(s.name)}</b> <span class="muted">${esc(s.role || "")}</span>${addr}${src}${label}`;
  }).join("<br>");
  return `<div class="note small">Slate considered:<br>${rows}</div>`;
}

// LinkedIn drafts are paste-ready text, never sent by anything here — the
// same one-rule as email, on a platform where automation is also a ToS
// violation. Rendered as click-to-copy blocks so the ritual is: send the
// email, open the contact on LinkedIn, paste.
function linkedinNote(c) {
  if (!c.linkedin_json) return "";
  let li;
  try { li = JSON.parse(c.linkedin_json); } catch { return ""; }
  const block = (label, text) => text ? `
    <div class="small muted">${label}
      <button class="copybtn" data-copy="${esc(text)}">copy</button></div>
    <div class="body small">${esc(text)}</div>` : "";
  const parts =
    block("LinkedIn connection note (≤300 chars)", li.connection_note) +
    block("Post-accept DM", li.post_accept_dm) +
    block("InMail subject", li.inmail_subject) +
    block("InMail body", li.inmail_body);
  if (!parts) return "";
  return `<div class="note">${parts}</div>`;
}

function card(c) {
  const g = c.gmail || {state: "unknown"};
  const canEdit = g.state === "draft";
  const hasBody = Boolean(g.body);
  const [vLabel, vWhy] = VERIFY[c.confidence] || [c.confidence || "unknown", ""];

  return `
  <div class="card" data-company="${esc(c.company_slug)}">
    <div class="top">
      <span class="slug">${esc(c.company_slug)}</span>
      <span class="chip ${g.state}">${g.state === "draft" ? "in drafts" : esc(g.state)}</span>
      <span class="score">score ${c.score ?? "–"}</span>
    </div>
    <div class="title">${
      c.job_url
        ? `<a href="${esc(c.job_url)}" target="_blank" rel="noopener">${esc(c.job_title)} ↗</a>`
        : esc(c.job_title)
    }</div>
    <div class="who">
      <b>${esc(c.contact_name)}</b> <span class="muted">${esc(c.contact_role || "")}</span><br>
      <span class="mail">${esc(c.contact_email)}</span>
      <span class="vchip ${esc(c.confidence || "")}" title="${esc(vWhy)}">${esc(vLabel)}</span>
    </div>
    ${vWhy ? `<div class="note small">${esc(vWhy)}</div>` : ""}
    ${c.source_notes ? `<div class="note small">Why this contact: ${esc(c.source_notes)}</div>` : ""}
    ${slateNote(c)}
    ${linkedinNote(c)}
    <div class="note">${esc(EXPLAIN[g.state])}${
      g.sent_date ? " <br>Sent " + esc(g.sent_date) + "." : ""
    }</div>
    ${c.superseded_note ? `<div class="note">${esc(c.superseded_note)}</div>` : ""}
    ${hasBody ? `
    <div class="reading">
      <div class="subject">${esc(g.subject || c.draft_subject)}</div>
      <div class="body">${esc(g.body)}</div>
    </div>` : ""}
    <div class="row">
      ${canEdit ? `<button data-act="edit">Edit</button>` : ""}
      ${g.gmail_url ? `<a class="btn" href="${esc(g.gmail_url)}" target="_blank" rel="noopener">Open in Gmail →</a>` : ""}
      ${g.state === "sent" ? `<button class="primary" data-act="mark-sent">Record as sent</button>` : ""}
      ${g.state !== "sent" ? `<button class="danger" data-act="discard">${
        g.state === "missing" ? "Release claim" : "Discard draft"
      }</button>` : ""}
    </div>
    <div class="editor" hidden>
      <input type="text" data-field="subject" value="${esc(g.subject || c.draft_subject)}">
      <textarea data-field="body">${esc(g.body || "")}</textarea>
      <div class="row">
        <button class="primary" data-act="save">Save to Gmail</button>
        <button data-act="cancel">Cancel</button>
      </div>
    </div>
  </div>`;
}

// A parked slate: the run researched and resolved it but did not draft.
// Each candidate gets a pick button; the next run drafts to the pick.
function slateCard(sl) {
  const rows = (sl.resolved.length ? sl.resolved : sl.slate).map(c => {
    const [vLabel, vWhy] = VERIFY[c.verify_label] || [c.verify_label || "unresolved", c.verify_reason || ""];
    const chosen = sl.chosen_name === c.name;
    const addr = c.address ? `<span class="mail">${esc(c.address)}</span>` : `<span class="muted">no address</span>`;
    const src = c.address_source === "probe" ? ` <span class="muted">(probe)</span>` : "";
    return `<div class="listrow">
      <b>${esc(c.name)}</b> <span class="muted">${esc(c.role || "")}</span> ${addr}${src}
      <span class="vchip ${esc(c.verify_label || "")}" title="${esc(vWhy)}">${esc(vLabel)}</span>
      ${chosen ? `<span class="chip draft">picked</span>`
               : (sl.status === "awaiting" && c.address
                   ? `<button data-act="slate-pick" data-name="${esc(c.name)}">Pick ${esc(c.name.split(" ")[0])}</button>` : "")}
      ${c.evidence ? `<div class="note small">${esc(c.evidence)}</div>` : ""}
      ${c.verify_reason && !c.address ? `<div class="note small">${esc(c.verify_reason)}</div>` : ""}
    </div>`;
  }).join("");
  return `
  <div class="card" data-company="${esc(sl.company_slug)}">
    <div class="top">
      <span class="slug">${esc(sl.company_slug)}</span>
      <span class="chip ${sl.status === "approved" ? "draft" : "unknown"}">${sl.status === "approved" ? "picked · drafts on the next run" : "awaiting your pick"}</span>
      <span class="score">score ${sl.score ?? "–"}</span>
    </div>
    <div class="title">${sl.job_url ? `<a href="${esc(sl.job_url)}" target="_blank" rel="noopener">${esc(sl.job_title)} ↗</a>` : esc(sl.job_title || "")}</div>
    ${sl.reason ? `<div class="note small">Why no draft: ${esc(sl.reason)}</div>` : ""}
    ${rows}
    ${sl.source_notes ? `<div class="note small">How they were found: ${esc(sl.source_notes)}</div>` : ""}
    <div class="row">
      <button class="danger" data-act="slate-dismiss">Dismiss</button>
    </div>
  </div>`;
}

function render(s) {
  const app = document.getElementById("app");
  const slates = s.slates.length ? s.slates.map(slateCard).join("") : `<p class="empty">Nothing waiting on you.</p>`;
  const lr = s.last_run;
  const lastRun = lr ? `<div class="note small">Last unattended run ${esc(lr.finished_at || "")} UTC · ${esc(lr.status)} · ${lr.postings} posting(s)${lr.requeued ? `, ${lr.requeued} requeued` : ""}</div>` : "";
  const pending = s.pending.length
    ? s.pending.map(card).join("")
    : `<p class="empty">No pending drafts.</p>`;
  const discarded = s.discarded.length
    ? s.discarded.map(d => `<div class="listrow">
        <b>${esc(d.company_slug)}</b>
        <span class="muted">${esc(d.job_title || "")}</span>
      </div>`).join("")
    : `<p class="empty">None.</p>`;
  const replyChip = d => {
    if (d.replied_at) return `<span class="vchip verified">replied</span>`;
    const bump = d.follow_up_at ? `<span class="vchip">bumped</span>` : "";
    if (!d.reply_checked_at) return bump + `<span class="vchip">unchecked</span>`;
    return bump + `<span class="vchip catch_all">no reply yet</span>`;
  };
  const contacted = s.contacted.length
    ? s.contacted.map(d => `<div class="listrow">
        <b>${esc(d.company_slug)}</b>
        <span class="mail muted">${esc(d.contact_email)}</span>
        <span class="muted">${esc(d.contact_role || "")}</span>
        ${replyChip(d)}
        <span class="muted">${esc(d.replied_at || d.sent_at)}</span>
      </div>`).join("")
    : `<p class="empty">None yet.</p>`;

  app.innerHTML = `
    <h2>Awaiting your pick (${s.slates.length})</h2>${lastRun}${slates}
    <h2>Pending (${s.pending.length})</h2>${pending}
    <h2>Discarded (${s.discarded.length}) · open for a future attempt</h2>
    <div class="card">${discarded}</div>
    <h2>Contacted (${s.contacted.length}) · closed permanently</h2>
    <div class="card">
      ${contacted}
      <div class="row">
        <button data-act="check-replies">Check Gmail for replies</button>
      </div>
    </div>`;
}

async function refresh() {
  const res = await fetch("/api/state");
  const data = await res.json();
  if (data.error) {
    document.getElementById("app").innerHTML =
      `<p class="empty">Error: ${esc(data.error)}</p>`;
    return;
  }
  render(data);
}

document.addEventListener("click", async e => {
  const btn = e.target.closest("button");
  if (!btn) return;

  if (btn.classList.contains("copybtn")) {
    try {
      await navigator.clipboard.writeText(btn.dataset.copy);
      btn.textContent = "copied";
      setTimeout(() => { btn.textContent = "copy"; }, 1500);
    } catch { flash("Copy failed — select the text by hand."); }
    return;
  }

  // Not scoped to a company: it walks every contacted thread at once.
  if (btn.dataset.act === "check-replies") {
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = "Checking Gmail…";
    try {
      const r = await post("check-replies", {});
      flash(r.checked
        ? `Checked ${r.checked}, ${r.replied} replied.`
        : "Nothing to check yet.");
    } catch (err) { flash(err.message); }
    btn.textContent = label;
    await refresh();
    return;
  }

  const card = btn.closest(".card");
  if (!card) return;
  const company = card.dataset.company;
  const act = btn.dataset.act;
  const editor = card.querySelector(".editor");

  if (act === "slate-pick" || act === "slate-dismiss") {
    if (act === "slate-dismiss" && !confirm(`Dismiss the ${company} slate? A higher-scoring posting later reopens it.`)) return;
    btn.disabled = true;
    try {
      if (act === "slate-pick") {
        const r = await post("slate-pick", {company, name: btn.dataset.name});
        flash(`${company}: ${r.chosen_name} picked. The next unattended run drafts it.`);
      } else {
        await post("slate-dismiss", {company, reason: "dismissed in the review UI"});
        flash(`${company} slate dismissed.`);
      }
    } catch (err) { flash(err.message); }
    await refresh();
    return;
  }

  const reading = card.querySelector(".reading");
  if (act === "edit") {
    editor.hidden = false;
    if (reading) reading.hidden = true;
    btn.disabled = true;
    card.querySelector('[data-field=body]').focus();
    return;
  }
  if (act === "cancel") { await refresh(); return; }

  if (act === "discard") {
    const missing = card.querySelector(".chip").classList.contains("missing");
    const msg = missing
      ? `Release the claim on ${company}? It becomes available to draft again.`
      : `Discard the ${company} draft? It moves to Gmail Trash and the company reopens.`;
    if (!confirm(msg)) return;
  }
  if (act === "mark-sent" &&
      !confirm(`Record ${company} as contacted? This closes it permanently — the pipeline will never draft to it again.`)) return;

  btn.disabled = true;
  try {
    if (act === "save") {
      await post("update", {
        company,
        subject: card.querySelector('[data-field=subject]').value,
        body: card.querySelector('[data-field=body]').value,
      });
      flash("Draft updated in Gmail.");
    } else if (act === "discard") {
      const r = await post("discard", {company});
      flash(`Discarded ${company} (${r.drafts_trashed} draft trashed).`);
    } else if (act === "mark-sent") {
      await post("mark-sent", {company});
      flash(`${company} recorded as contacted.`);
    }
  } catch (err) {
    flash(err.message);
  }
  await refresh();
});

refresh();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true",
                        help="do not open a browser window")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}/"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"outreach UI on {url}  (ctrl-c to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
