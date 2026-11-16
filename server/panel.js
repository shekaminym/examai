"use strict";

const $ = (id) => document.getElementById(id);
const statuses = ["Received", "Reviewing", "Confirmed", "Fixed", "Closed"];
const dateTime = new Intl.DateTimeFormat("uk-UA", {dateStyle: "medium", timeStyle: "short"});
const units = {
  minutes: new Intl.NumberFormat("uk-UA", {style: "unit", unit: "minute", unitDisplay: "long"}),
  days: new Intl.NumberFormat("uk-UA", {style: "unit", unit: "day", unitDisplay: "long"}),
};
const labels = {
  Owner: "Власник", Admin: "Адміністратор", User: "Користувач",
  Pending: "Очікує", Expired: "Строк дії минув", Activated: "Активовано", Revoked: "Відкликано",
  BETA: "Бета-доступ", TRIAL: "Тестовий доступ", ACTIVE: "Активний", SUSPENDED: "Призупинений",
  EXPIRED: "Немає доступу", Unlimited: "Без обмежень", Calendar: "Строкова", Minutes: "За хвилинами",
  None: "Немає", Received: "Отримано", Reviewing: "На розгляді", Confirmed: "Підтверджено",
  Fixed: "Виправлено", Closed: "Закрито", Bug: "Помилка", Suggestion: "Пропозиція",
};
const errors = {
  "Administrator required": "Потрібні права адміністратора.",
  "Owner required": "Ця дія доступна власнику.",
  "Sign in to EXAM AI": "Увійдіть до EXAM AI.",
  "Session expired": "Сеанс завершився. Увійдіть знову.",
  "Device limit reached": "Уже відкрито два сеанси EXAM AI. Вийдіть з одного й повторіть вхід.",
  "Login expired": "Час очікування входу минув.",
  "Login link expired": "Строк дії посилання для входу минув.",
  "Too many sign-in attempts. Try again shortly.": "Забагато спроб входу. Спробуйте трохи пізніше.",
  "Google Sign-In is not configured on the server": "Вхід через Google ще не налаштовано.",
  "Google Sign-In is temporarily unavailable": "Вхід через Google тимчасово недоступний.",
  "Google authentication failed": "Не вдалося підтвердити обліковий запис Google.",
  "Enter a valid Google email address": "Введіть дійсну електронну адресу Google.",
  "Multiple Google accounts match this address": "Цій адресі відповідає кілька облікових записів Google.",
  "No verified Google account for this email": "За цією адресою не знайдено підтвердженого облікового запису Google.",
  "Only the owner can change the owner's access": "Змінити доступ власника може лише він сам.",
  "Operation ID already used": "Цю операцію вже виконано.",
  "Account not found": "Обліковий запис не знайдено.",
  "Amount must be positive": "Вкажіть кількість більшу за нуль.",
  "Restore access before changing time": "Відновіть доступ, щоб змінити залишок часу.",
  "Account is already in the requested state": "Обліковий запис уже має цей статус.",
  "The owner cannot be removed": "Власника не можна видалити.",
  "Administrator not found": "Адміністратора не знайдено.",
  "Check the license amount": "Перевірте тривалість доступу.",
  "Only pending invitations can be copied": "Скопіювати можна лише запрошення, яке очікує активації.",
  "Only pending invitations can be revoked": "Відкликати можна лише запрошення, яке очікує активації.",
  "This code was created before the current signing key": "Цей код створено зі старим ключем. Створіть нове запрошення.",
  "Maximum extension is 365 days": "Подовжити доступ можна щонайбільше на 365 днів.",
  "Report not found": "Звіт не знайдено.",
  "Invalid report status": "Виберіть дійсний статус звіту.",
  "Select an administrator": "Виберіть адміністратора.",
  "Not Found": "Запис не знайдено.",
  "Failed to fetch": "Не вдалося з’єднатися із сервером. Спробуйте ще раз.",
};
const auditLabels = {
  "Activated administrator": "Активовано права адміністратора",
  "Appointed administrator": "Призначено адміністратора",
  "Invited administrator": "Запрошено адміністратора",
  "Revoked administrator": "Відкликано права адміністратора",
  "Cancelled administrator invitation": "Скасовано запит на права адміністратора",
  "Activated beta invitation": "Активовано запрошення до бета-тестування",
  "redeemed beta invite": "Активовано запрошення до бета-тестування",
  "Revoked beta invitation": "Відкликано запрошення",
  "Created minutes invitation": "Створено запрошення з доступом у хвилинах",
  "Created days invitation": "Створено запрошення з доступом на кілька днів",
  "Created unlimited invitation": "Створено запрошення з безстроковим доступом",
  "Revoked one stale owner session for sign-in recovery": "Завершено старий сеанс власника для відновлення входу",
};

function display(value) {
  return Object.hasOwn(labels, value) ? labels[value] : value;
}

function quantity(amount, unit) {
  return (unit === "days" ? units.days : units.minutes).format(amount);
}

function errorText(error) {
  const message = error.message;
  if (Object.hasOwn(errors, message)) return errors[message];
  return /[А-Яа-яІіЇїЄєҐґ]/u.test(message) ? message : "Не вдалося виконати запит. Спробуйте ще раз.";
}

function auditLabel(action) {
  if (Object.hasOwn(auditLabels, action)) return auditLabels[action];
  const license = /^(?:License )?(minutes|remove|days|suspend|restore|revoke|invite) (\d+)(?: \d+\s*)?$/.exec(action);
  if (license) {
    const [, kind, count] = license;
    const names = {minutes: "Додано", remove: "Списано", days: "Продовжено доступ на",
      suspend: "Доступ призупинено", restore: "Доступ відновлено", revoke: "Ліцензію відкликано",
      invite: "Створено запрошення"};
    return Number(count) && ["minutes", "remove", "days"].includes(kind)
      ? names[kind] + " " + quantity(Number(count), kind) : names[kind];
  }
  const report = /^Report #(\d+): (\w+) → (\w+); assignee (.+); bonus (\d+) min$/.exec(action);
  if (report) {
    const [, id, before, after, assignee, bonus] = report;
    const recipient = assignee === "none" ? "не призначено" : assignee;
    return `Звіт №${id}: ${display(before)} → ${display(after)} · Відповідальний: ${recipient} · Бонус: ${quantity(Number(bonus), "minutes")}`;
  }
  return action;
}
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
  for (const value of values) select.add(new Option(value ? display(value) : "Без відповідального", value));
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
    const error = new Error(typeof failure.detail === "string" ? failure.detail : "Не вдалося виконати запит. Спробуйте ще раз.");
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
      signInScreen(errorText(error));
    } else {
      notice(errorText(error), true);
      if ($("dialog").open) {
        const previous = $("dialog-content").querySelector(".dialog-error");
        previous?.remove();
        const message = node("p", errorText(error), "dialog-error");
        message.setAttribute("role", "alert");
        $("dialog-content").append(message);
      }
    }
  }
}

function table(id, columns, rows, empty = "Записів немає.") {
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
  $("actor").textContent = display(data.role);
  $("staff-form").hidden = data.role !== "Owner";
  $("summary").replaceChildren(...[
    ["Зареєстровані користувачі", "users"], ["Очікують активації", "pending"],
    ["Запити за сьогодні", "requests"], ["Відкриті звіти", "reports"],
  ].map(([label, key]) => {
    const card = node("div", null, "metric");
    card.append(node("small", label), node("strong", data.summary[key]));
    return card;
  }));

  table("users", ["Електронна пошта", "Роль", "Статус", "Хвилини", "Ліцензія", "Запити за сьогодні", "Остання активність", "Дії"],
    data.users.map((user) => {
      const actions = node("div", null, "actions");
      if (user.email) {
        if (user.role !== "Owner" || data.role === "Owner") {
          actions.append(button("Ліцензія", () => {
            $("license-form").elements.email.value = user.email;
            view("licenses");
          }));
        }
        actions.append(button("Переглянути звіти", async () => {
          $("reports-form").elements.email.value = user.email;
          $("reports-form").elements.status.value = "";
          view("reports");
          await refresh();
        }));
      }
      return [user.email || "Пошту не підтверджено", display(user.role), node("span", display(user.state), "tag"),
        minutes(user.seconds), display(user.license), user.requests, time(user.last_active), actions];
    }), "За цією адресою користувачів не знайдено.");

  table("licenses", ["Електронна пошта", "Ліцензія", "Залишок", "Діє до", "Статус"], data.users.map(user =>
    [user.email || "Пошту не підтверджено", display(user.license), minutes(user.seconds), time(user.until), display(user.state)]));

  table("invitations", ["Електронна пошта Google", "Доступ", "Статус", "Код діє до", "Дії"],
    data.invitations.map(invite => {
      const controls = node("div", null, "actions");
      if (invite.status === "Pending") {
        controls.append(button("Скопіювати код", async () => {
          const value = await json("/admin/v2/invitations/" + invite.id + "/code");
          await showCode(value.code, false);
        }), button("Відкликати", () => confirm("Відкликати запрошення", "Код для " + invite.email + " перестане діяти.",
          () => json("/admin/v2/invitations/" + invite.id, "DELETE"), "Запрошення відкликано.")));
      }
      const access = invite.kind === "unlimited" ? "Без обмежень" : quantity(invite.amount, invite.kind);
      return [invite.email, access, node("span", display(invite.status), "tag"), time(invite.expires), controls];
    }), "Запрошень ще немає.");

  table("staff", ["Обліковий запис Google", "Роль", "Дії"], data.staff.map(member => {
    let action = "";
    if (data.role === "Owner" && member.role !== "Owner") {
      const pending = ["Pending", "Expired"].includes(member.role);
      action = button(pending ? "Скасувати запит" : "Забрати права адміністратора", () => confirm(
        pending ? "Скасувати запит на права адміністратора" : "Забрати права адміністратора",
        "Забрати права адміністратора для " + (member.email || "цього облікового запису Google") + "?",
        () => pending ? json("/admin/v2/pending-staff", "DELETE", {email: member.email})
          : json("/admin/v2/staff", "DELETE", {subject: member.sub}),
        "Список адміністраторів оновлено."));
    }
    return [member.email || "Обліковий запис Google", node("span", display(member.role), "tag"), action];
  }));

  table("activity", ["Час", "Дія", "Хто виконав", "Обліковий запис"], data.audit.map(entry => [
    time(entry.created), auditLabel(entry.action), entry.actor || "—", entry.target || entry.pending_email || "—",
  ]));
  const reports = data.reports.map(reportCard);
  $("reports").replaceChildren(...(reports.length ? reports : [node("p", "Звітів за цим фільтром немає.", "empty")]));
}

function minutes(seconds) {
  return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
}

function dialog(title, contents, action) {
  confirmAction = action;
  $("dialog-title").textContent = title;
  $("dialog-content").replaceChildren(...contents);
  $("dialog-submit").hidden = !action;
  $("dialog-close").textContent = action ? "Скасувати" : "Закрити";
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
  const copy = button("Скопіювати код", async () => {
    try {
      await navigator.clipboard.writeText(code);
      copy.textContent = "Скопійовано";
    } catch {
      input.focus();
      input.select();
    }
  });
  dialog(created ? "Запрошення створено" : "Код запрошення",
    [field("Передайте цей код учаснику приватно", input), copy], null);
  input.select();
}

function reportCard(report) {
  const card = node("article", null, "report");
  const header = node("div", null, "actions");
  header.append(node("h2", display(report.kind) + " №" + report.id), node("span", display(report.status), "tag"));
  card.append(header, node("small", [report.email || "Пошту не підтверджено", time(report.created),
    report.assignee && "Відповідальний: " + report.assignee].filter(Boolean).join(" · ")), node("p", report.description));
  const controls = node("div", null, "actions");
  for (const kind of (report.attachments || "").split(",").filter(Boolean)) {
    controls.append(button(kind === "screenshot" ? "Переглянути знімок екрана" : "Завантажити діагностику",
      () => attachment(report.id, kind)));
  }
  controls.append(button("Оновити звіт", () => {
    const status = options(statuses, "Статус");
    status.value = report.status;
    const members = data.staff.filter(member => member.email && ["Owner", "Admin"].includes(member.role))
      .map(member => member.email);
    const assignee = options(["", ...members], "Assign to");
    assignee.value = members.includes(report.assignee) ? report.assignee : "";
    const bonus = node("input");
    Object.assign(bonus, {type: "number", min: "0", max: "365", value: "0", required: true});
    let operation;
    dialog("Оновити звіт №" + report.id, [
      field("Статус", status), field("Відповідальний", assignee), field("Бонусні хвилини", bonus),
    ], async () => {
      const payload = {status: status.value, assignee: assignee.value, bonus: bonus.valueAsNumber};
      if (!operation || operation.payload !== JSON.stringify(payload)) {
        operation = {payload: JSON.stringify(payload), id: crypto.randomUUID()};
      }
      await json("/admin/v2/reports/" + report.id, "POST", {...payload, operation_id: operation.id});
      $("dialog").close();
      await refresh();
      notice("Звіт оновлено.");
    });
  }));
  controls.append(button("Історія", async () => {
    const history = await json("/admin/v2/reports/" + report.id + "/history");
    const entries = history.map(entry => node("p",
      [time(entry.created), entry.actor, auditLabel(entry.action)].filter(Boolean).join(" · ")));
    dialog("Історія звіту №" + report.id, entries.length ? entries : [node("p", "Змін ще немає.")], null);
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
    image.alt = "Знімок екрана зі звіту №" + id;
    dialog("Звіт №" + id, [image], null);
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
for (const status of statuses) $("reports-form").elements.status.add(new Option(display(status), status));

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
  $("amount-label").textContent = licenseForm.action.value === "days" ? "Дні" : "Хвилини";
  licenseForm.amount.max = licenseForm.action.value === "days" ? "365" : "10080";
});
form("license-form", ({email, action, amount}) => {
  const title = licenseForm.action.selectedOptions[0].text;
  const operation_id = crypto.randomUUID();
  const detail = amount ? " · " + quantity(Number(amount), action) : "";
  confirm(title, email + detail, () => json("/admin/v2/licenses", "POST",
    {email, action, amount: Number(amount || 0), operation_id}), "Ліцензію оновлено.");
});
form("staff-form", ({email}) => confirm("Додати адміністратора",
  "Надати права адміністратора для " + email + " після підтвердження облікового запису Google?",
  () => json("/admin/v2/staff", "POST", {email}),
  result => result.role === "Pending" ? "Очікується перший підтверджений вхід через Google." : "Список адміністраторів оновлено."));

$("sign-in").addEventListener("click", async () => {
  const popup = window.open("about:blank", "_blank", "popup");
  if (!popup) {
    $("login-status").textContent = "Дозвольте спливні вікна й повторіть вхід через Google.";
    return;
  }
  $("sign-in").disabled = true;
  $("login-status").textContent = "Очікуємо на вхід через Google…";
  try {
    const auth = await json("/oauth/start", "POST", {device});
    const url = new URL(auth.url);
    if (url.origin !== "https://accounts.google.com") throw new Error("Неочікувана адреса входу Google.");
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
      if (popup.closed) throw new Error("Вікно входу через Google закрито.");
    }
    throw new Error("Час очікування входу минув.");
  } catch (error) {
    if (token) await api("/logout", "POST").catch(() => {});
    signInScreen(errorText(error));
    popup.close();
  } finally {
    $("sign-in").disabled = false;
  }
});

$("sign-out").addEventListener("click", async () => {
  try {
    await api("/logout", "POST");
    signInScreen("Ви вийшли з облікового запису.");
  } catch (error) {
    signInScreen(error.status === 401 ? "Ви вийшли з облікового запису." : "Ви вийшли з панелі. Серверний сеанс не вдалося завершити.");
  }
});
