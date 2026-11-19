// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The sign-in page's words and focus. The form posts without this file: the store answers a form post with a redirect,
// to the dashboard when the passcode is right, else back here with ?e=1 (it did not work, whatever the reason) or ?e=2
// (too many attempts). This file says which in words, says when sign-in is not set up at all (GET /auth/status), and
// puts the focus in the field. It reads nothing else and names no household.
"use strict";

const SAID = {
  1: ["That passcode did not work. Try again."],
  2: ["Too many attempts. Try again in a few minutes."],
};
// what the person does about it, on the computer that runs the dashboard: the one place this page names a command
const NOT_SET_UP = ["Sign-in is not set up yet. On the computer that runs the dashboard, add a passcode with ",
  { code: "ff passcode add" }, ", then reload this page."];

const form = document.getElementById("sign-in");
const field = document.getElementById("passcode");
const message = document.getElementById("message");

/** the message line: `parts` are text, or { code } shown as a command; `tone` is error or notice */
function say(parts, tone) {
  message.replaceChildren(...parts.map((part) => {
    if (typeof part === "string") return document.createTextNode(part);
    const code = document.createElement("code");
    code.textContent = part.code;
    return code;
  }));
  message.className = `message ${tone}`;
}

const answer = SAID[new URLSearchParams(location.search).get("e")];
if (answer) {
  say(answer, "error");
  field.setAttribute("aria-invalid", "true");
}
field.focus();

// with no passcode set up, nothing typed here can work: the form is switched off and the page says what to do
fetch("/auth/status", { cache: "no-store", headers: { Accept: "application/json" } })
  .then((res) => (res.ok ? res.json() : null))
  .then((status) => {
    if (!status || status.configured !== false) return;
    for (const control of form.elements) control.disabled = true;
    field.removeAttribute("aria-invalid");
    say(NOT_SET_UP, "notice");
  })
  .catch(() => {
    // the store did not answer: the form stays, and a sign-in says what went wrong
  });
