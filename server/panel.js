"use strict";

const $ = (id) => document.getElementById(id);
const statuses = ["Received", "Reviewing", "Confirmed", "Fixed", "Closed"];
const dateTime = new Intl.DateTimeFormat(undefined, {dateStyle: "medium", timeStyle: "short"});
let token = "";
let device = localStorage.getItem("examaiAdminDevice") || crypto.randomUUID();
localStorage.setItem("examaiAdminDevice", device);
let data;
let confirmAction;
let imageUrl;
let currentView = "";
let refreshSequence = 0;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text != null) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function button(label, action) {
  const element = node("button", label);
  element.type = "button";
  element.addEventListener("click", () => recover(action));
  return element;
}

function options(values, label = "") {
  const select = node("select");
  select.setAttribute("aria-label", label);
  for (const value of values) select.add(new Option(value || "Unassigned", value));
  return select;
}

function field(label, input) {
  const wrapper = node("label", label);
  wrapper.append(input);
  return wrapper;
}

function time(value) {
  return value == null ? "—" : dateTime.format(value * 1000);
}

async function api(path, method = "GET", body) {
  const headers = {};
  if (token) headers.Authorization = "Bearer " + token;
  if (body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {method, headers, body: body ? JSON.stringify(body) : undefined,
    credentials: "omit", cache: "no-store", redirect: "error"});
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    const error = new Error(typeof failure.detail === "string" ? failure.detail : "Request failed. Try again.");
    error.status = response.status;
    throw error;
  }
  return response;
}

async function json(path, method, body) {
  return (await api(path, method, body)).json();
}

function signInScreen(message = "") {
  token = "";
  data = undefined;
  refreshSequence++;
  if ($("dialog").open) $("dialog").close();
  $("app").hidden = true;
  $("login").hidden = false;
  $("login-status").textContent = message;
  for (const id of ["summary", "users", "invitations", "licenses", "reports", "staff", "activity"]) {
    $(id).replaceChildren();
  }
}

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").dataset.error = error;
  $("notice").hidden = !message;
}

async function recover(action) {
  try {
    await action();
  } catch (error) {
    if (error.status === 401 || (error.status === 403 && error.message === "Administrator required")) {
      signInScreen(error.message);
    } else {
      notice(error.message || "Request failed. Try again.", true);
      if ($("dialog").open) {
        const previous = $("dialog-content").querySelector(".dialog-error");
        previous?.remove();
        const message = node("p", error.message, "dialog-error");
        message.setAttribute("role", "alert");
        $("dialog-content").append(message);
      }
    }
  }
}

function table(id, columns, rows, empty = "Nothing to show.") {
  if (!rows.length) {
    $(id).replaceChildren(node("p", empty, "empty"));
    return;
  }
  const wrapper = node("div", null, "table-wrap");
  const element = node("table");
  const header = element.createTHead().insertRow();
  for (const title of columns) {
    const cell = node("th", title);
    cell.scope = "col";
    header.append(cell);
  }
  const body = element.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const value of row) {
      const td = tr.insertCell();
      if (value instanceof Node) td.append(value);
      else td.textContent = value ?? "—";
    }
  }
  wrapper.append(element);
  $(id).replaceChildren(wrapper);
}

function view(name) {
  currentView = name;
  for (const element of document.querySelectorAll("[data-page]")) element.hidden = element.dataset.page !== name;
  for (const link of document.querySelectorAll("[data-view]")) {
    if (link.dataset.view === name) {
      link.setAttribute("aria-current", "page");
      $("heading").textContent = link.textContent;
    } else link.removeAttribute("aria-current");
  }
  notice("");
}

async function refresh() {
  const sequence = ++refreshSequence;
  const filter = $("reports-form").elements;
  const filters = {
    search: $("search-form").elements.search.value.trim(),
    status: filter.status.value,
    report_email: filter.email.value.trim().toLowerCase(),
  };
  const result = await json("/admin/v2/overview", "POST", filters);
  if (sequence !== refreshSequence) return;
  data = result;
  render();
}

function render() {
  $("actor").textContent = data.role;
  $("staff-form").hidden = data.role !== "Owner";
  $("summary").replaceChildren(...[
    ["Registered accounts", "users"], ["Pending invitations", "pending"],
    ["Requests today", "requests"], ["Open reports", "reports"],
  ].map(([label, key]) => {
    const card = node("div", null, "metric");
    card.append(node("small", label), node("strong", data.summary[key]));
    return card;
  }));

  table("users", ["Email", "Role", "Status", "Minutes", "License", "Requests today", "Last active", "Actions"],
    data.users.map((user) => {
      const actions = node("div", null, "actions");
      if (user.email) {
        if (user.role !== "Owner" || data.role === "Owner") {
          actions.append(button("License", () => {
            $("license-form").elements.email.value = user.email;
            view("licenses");
          }));
        }
        actions.append(button("View Reports", async () => {
          $("reports-form").elements.email.value = user.email;
          $("reports-form").elements.status.value = "";
          view("reports");
          await refresh();
        }));
      }
      return [user.email || "Unverified email", user.role, node("span", user.state, "tag"),
        minutes(user.seconds), user.license, user.requests, time(user.last_active), actions];
    }), "No accounts match this email.");

  table("licenses", ["Email", "License", "Remaining", "Expires", "Status"], data.users.map(user =>
    [user.email || "Unverified email", user.license, minutes(user.seconds), time(user.until), user.state]));

  table("invitations", ["Google email", "Access", "Status", "Code expires", "Actions"],
    data.invitations.map(invite => {
      const controls = node("div", null, "actions");
      if (invite.status === "Pending") {
        controls.append(button("Copy Invite", async () => {
          const value = await json("/admin/v2/invitations/" + invite.id + "/code");
          await showCode(value.code, false);
        }), button("Revoke", () => confirm("Revoke invitation", "The code for " + invite.email + " will stop working.",
          () => json("/admin/v2/invitations/" + invite.id, "DELETE"), "Invitation revoked.")));
      }
      const access = invite.kind === "unlimited" ? "Unlimited" : invite.amount + " " + invite.kind;
      return [invite.email, access, node("span", invite.status, "tag"), time(invite.expires), controls];
    }), "No invitations have been created in this panel.");

  table("staff", ["Google account", "Role", "Actions"], data.staff.map(member => {
    let action = "";
    if (data.role === "Owner" && member.role !== "Owner") {
      const pending = ["Pending", "Expired"].includes(member.role);
      action = button(pending ? "Cancel Request" : "Remove Admin", () => confirm(
        pending ? "Cancel administrator request" : "Remove administrator",
        "Remove administrator access for " + (member.email || "this Google account") + "?",
        () => pending ? json("/admin/v2/pending-staff", "DELETE", {email: member.email})
          : json("/admin/v2/staff", "DELETE", {subject: member.sub}),
        "Administrator list updated."));
    }
    return [member.email || "Google account", node("span", member.role, "tag"), action];
  }));

  table("activity", ["Time", "Action", "Actor", "Account"], data.audit.map(entry => [
    time(entry.created), entry.action, entry.actor || "—", entry.target || entry.pending_email || "—",
  ]));
  const reports = data.reports.map(reportCard);
  $("reports").replaceChildren(...(reports.length ? reports : [node("p", "No matching reports.", "empty")]));
}

function minutes(seconds) {
  return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
}

function dialog(title, contents, action) {
  confirmAction = action;
  $("dialog-title").textContent = title;
  $("dialog-content").replaceChildren(...contents);
  $("dialog-submit").hidden = !action;
  $("dialog-close").textContent = action ? "Cancel" : "Close";
  $("dialog").showModal();
}

function confirm(title, message, action, success) {
  dialog(title, [node("p", message)], async () => {
    const result = await action();
    $("dialog").close();
    await refresh();
    notice(typeof success === "function" ? success(result) : success);
  });
}

async function showCode(code, created) {
  const input = node("input");
  input.readOnly = true;
  input.value = code;
  const copy = button("Copy Invite", async () => {
    try {
      await navigator.clipboard.writeText(code);
      copy.textContent = "Copied";
    } catch {
      input.focus();
      input.select();
    }
  });
  dialog(created ? "Invitation created" : "Invitation code",
    [field("Send this code privately to the invited tester", input), copy], null);
  input.select();
}

function reportCard(report) {
  const card = node("article", null, "report");
  const header = node("div", null, "actions");
  header.append(node("h2", report.kind + " #" + report.id), node("span", report.status, "tag"));
  card.append(header, node("small", [report.email || "Unverified email", time(report.created),
    report.assignee && "Assigned to " + report.assignee].filter(Boolean).join(" · ")), node("p", report.description));
  const controls = node("div", null, "actions");
  for (const kind of (report.attachments || "").split(",").filter(Boolean)) {
    controls.append(button(kind === "screenshot" ? "View Screenshot" : "Download Diagnostics",
      () => attachment(report.id, kind)));
  }
  controls.append(button("Update Report", () => {
    const status = options(statuses, "Status");
    status.value = report.status;
    const members = data.staff.filter(member => member.email && ["Owner", "Admin"].includes(member.role))
      .map(member => member.email);
    const assignee = options(["", ...members], "Assign to");
    assignee.value = members.includes(report.assignee) ? report.assignee : "";
    const bonus = node("input");
    Object.assign(bonus, {type: "number", min: "0", max: "365", value: "0", required: true});
    let operation;
    dialog("Update report #" + report.id, [
      field("Status", status), field("Assign to", assignee), field("Bonus minutes", bonus),
    ], async () => {
      const payload = {status: status.value, assignee: assignee.value, bonus: bonus.valueAsNumber};
      if (!operation || operation.payload !== JSON.stringify(payload)) {
        operation = {payload: JSON.stringify(payload), id: crypto.randomUUID()};
      }
      await json("/admin/v2/reports/" + report.id, "POST", {...payload, operation_id: operation.id});
      $("dialog").close();
      await refresh();
      notice("Report updated.");
    });
  }));
  controls.append(button("History", async () => {
    const history = await json("/admin/v2/reports/" + report.id + "/history");
    const entries = history.map(entry => node("p",
      [time(entry.created), entry.actor, entry.action].filter(Boolean).join(" · ")));
    dialog("Report history #" + report.id, entries.length ? entries : [node("p", "No changes yet.")], null);
  }));
  card.append(controls);
  return card;
}

async function attachment(id, kind) {
  const response = await api("/admin/reports/" + id + "/" + encodeURIComponent(kind));
  const blob = await response.blob();
  if (kind === "screenshot") {
    imageUrl = URL.createObjectURL(new Blob([blob], {type: "image/png"}));
    const image = node("img");
    image.src = imageUrl;
    image.alt = "Screenshot attached to report #" + id;
    dialog("Report #" + id, [image], null);
  } else {
    const url = URL.createObjectURL(blob);
    const link = node("a");
    link.href = url;
    link.download = "report-" + id + "-diagnostics.txt";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

function form(id, action) {
  $(id).addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    if (submit?.disabled) return;
    if (submit) submit.disabled = true;
    await recover(() => action(Object.fromEntries(new FormData(event.target))));
    if (submit) submit.disabled = false;
  });
}

for (const link of document.querySelectorAll("[data-view]")) {
  link.addEventListener("click", () => {
    view(link.dataset.view);
    recover(refresh);
  });
}

$("dialog-close").addEventListener("click", () => $("dialog").close());
$("dialog").addEventListener("close", () => {
  if (imageUrl) URL.revokeObjectURL(imageUrl);
  imageUrl = undefined;
  confirmAction = undefined;
  $("dialog-content").replaceChildren();
});
form("dialog-form", () => confirmAction?.());
form("search-form", refresh);
form("reports-form", refresh);
for (const status of statuses) $("reports-form").elements.status.add(new Option(status));

const inviteForm = $("invite-form").elements;
inviteForm.kind.addEventListener("change", () => {
  $("custom-amount").hidden = inviteForm.kind.value !== "custom";
});
let inviteDraft;
form("invite-form", async ({email, kind, validity, minutes: custom}) => {
  const days = kind.endsWith("d");
  const amount = kind === "unlimited" ? 0 : Number(kind === "custom" ? custom : kind.replace(/d$/, ""));
  const body = {email, kind: days ? "days" : kind === "unlimited" ? kind : "minutes", amount, validity: Number(validity)};
  const payload = JSON.stringify(body);
  if (inviteDraft?.payload !== payload) inviteDraft = {payload, id: crypto.randomUUID()};
  const invite = await json("/admin/v2/invitations", "POST", {...body, operation_id: inviteDraft.id});
  inviteDraft = undefined;
  await refresh();
  await showCode(invite.code, true);
});

const licenseForm = $("license-form").elements;
licenseForm.action.addEventListener("change", () => {
  const enabled = ["days", "minutes", "remove"].includes(licenseForm.action.value);
  licenseForm.amount.disabled = !enabled;
  $("license-amount").hidden = !enabled;
  $("amount-label").textContent = licenseForm.action.value === "days" ? "Days" : "Minutes";
  licenseForm.amount.max = licenseForm.action.value === "days" ? "365" : "10080";
});
form("license-form", ({email, action, amount}) => {
  const title = licenseForm.action.selectedOptions[0].text;
  const operation_id = crypto.randomUUID();
  const quantity = amount ? " · " + amount + " " + $("amount-label").textContent.toLowerCase() : "";
  confirm(title, email + quantity, () => json("/admin/v2/licenses", "POST",
    {email, action, amount: Number(amount || 0), operation_id}), "License updated.");
});
form("staff-form", ({email}) => confirm("Add administrator",
  "Grant administrator access to " + email + " after Google account verification?",
  () => json("/admin/v2/staff", "POST", {email}),
  result => result.role === "Pending" ? "Pending first verified Google sign-in." : "Administrator list updated."));

$("sign-in").addEventListener("click", async () => {
  const popup = window.open("about:blank", "_blank", "popup");
  if (!popup) {
    $("login-status").textContent = "Allow pop-ups and try Google Sign-In again.";
    return;
  }
  $("sign-in").disabled = true;
  $("login-status").textContent = "Waiting for Google Sign-In…";
  try {
    const auth = await json("/oauth/start", "POST", {device});
    const url = new URL(auth.url);
    if (url.origin !== "https://accounts.google.com") throw new Error("Unexpected Google sign-in address.");
    popup.opener = null;
    popup.location = url.href;
    for (let attempt = 0; attempt < 120; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      const claim = await json("/oauth/claim", "POST", {claim: auth.claim});
      if (claim.token) {
        token = claim.token;
        await refresh();
        $("login").hidden = true;
        $("app").hidden = false;
        view(currentView || "dashboard");
        popup.close();
        return;
      }
      if (popup.closed) throw new Error("Google Sign-In was closed.");
    }
    throw new Error("Sign-in expired.");
  } catch (error) {
    if (token) await api("/logout", "POST").catch(() => {});
    signInScreen(error.message);
    popup.close();
  } finally {
    $("sign-in").disabled = false;
  }
});

$("sign-out").addEventListener("click", async () => {
  try {
    await api("/logout", "POST");
    signInScreen("Signed out.");
  } catch (error) {
    signInScreen(error.status === 401 ? "Signed out." : "Signed out of this page. The server session could not be closed.");
  }
});
