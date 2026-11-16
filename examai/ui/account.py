"""Account and reporting controls driven by network workers."""
import base64
import os
from pathlib import Path
import platform
import secrets
from time import monotonic

from PySide6.QtCore import QThread, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from examai.beta import BetaClient, distribution_url
from examai.branding import VERSION
from examai.windows.credentials import protect, reveal


class NetworkJob(QThread):
    result = Signal(object, str)

    def __init__(self, task, parent):
        super().__init__(parent)
        self.task = task

    def run(self):
        try:
            value = self.task()
        except Exception as exc:
            from examai.ai.llm_engine import GenerationError
            self.result.emit(None, str(exc) if isinstance(exc, GenerationError) else "Operation failed.")
        else:
            self.result.emit(value, "")


class AccountPanel(QWidget):
    session_changed = Signal()
    catalog_changed = Signal()
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.developer = os.getenv("EXAMAI_DEVELOPER") == "1"
        self.server_url = (os.getenv("EXAMAI_SERVER_URL") or distribution_url()).strip().rstrip("/")
        self.job = None
        self.closing = False
        self.claim = ""
        self.available_plans = {}
        self.device = config.get("account_device") or secrets.token_hex(16)
        try:
            token = reveal(config.get("account_session"))
        except (ValueError, OSError):
            token = ""
        try:
            saved_url = config.get("server_url").rstrip("/")
            url = (saved_url or self.server_url) if self.developer else (self.server_url or saved_url)
            self.client = BetaClient(url, token if url == saved_url else "")
        except ValueError:
            self.client = BetaClient()
        layout = QVBoxLayout(self)
        self.url = QLineEdit(self.client.url)
        self.url.setPlaceholderText("https://your-examai-server")
        if self.client.token:
            self.url.setReadOnly(True)
        layout.addWidget(self.url)
        self.url.setVisible(self.developer)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Ваше ім'я"))
        self.name_input = QLineEdit()
        self.name_input.setMaxLength(60)
        self.name_input.setPlaceholderText("Як до вас звертатися")
        layout.addWidget(self.name_input)
        self.save_name_button = self.button(layout, "Зберегти ім'я", self.save_name)
        self.google_button = self.button(layout, "Увійти через Google", self.sign_in)
        key_info = QLabel("Після входу EXAM AI використовує ваш особистий сеанс і ліміти. Окремий Gemini API Key не потрібен.")
        key_info.setWordWrap(True)
        layout.addWidget(key_info)
        self.refresh_button = self.button(layout, "Оновити обліковий запис", self.refresh)
        self.signout_button = self.button(layout, "Вийти", self.sign_out)
        self.invite = QLineEdit()
        self.invite.setPlaceholderText("Код запрошення")
        layout.addWidget(self.invite)
        self.redeem_button = self.button(layout, "Активувати запрошення", self.redeem)
        self.redeem_button.setEnabled(False)
        self.invite.textChanged.connect(lambda text: self.redeem_button.setEnabled(16 <= len(text.strip()) <= 128))
        self.timer = QTimer(self, interval=1600)
        self.timer.timeout.connect(self.poll)
        self.plans = QComboBox()
        layout.addWidget(self.plans)
        self.plans.setVisible(self.developer)
        self.checkout = QPushButton("Open sandbox checkout")
        self.checkout.setEnabled(False)
        self.checkout.clicked.connect(lambda: self.open_checkout())
        layout.addWidget(self.checkout)
        self.checkout.setVisible(self.developer)
        self.button(layout, "Sandbox payment history", self.payment_history).setVisible(self.developer)
        layout.addStretch()
        self.update_controls()

    def update_controls(self):
        configured = bool(self.client.url)
        signed_in = configured and bool(self.client.token)
        self.google_button.setVisible((configured or self.developer) and not signed_in)
        self.save_name_button.setVisible(signed_in)
        for widget in (self.refresh_button, self.signout_button, self.invite, self.redeem_button):
            widget.setVisible(signed_in)
        if not configured:
            self.status.setText("EXAM AI Cloud is unavailable. You can use your Gemini key in AI & Models.")
        elif not signed_in:
            self.status.setText("Sign in to EXAM AI Cloud")

    def report_available(self, label):
        if not self.client.url:
            label.setText("Online reports are unavailable in this build.")
        elif not self.client.token:
            label.setText("Sign in to EXAM AI Cloud to send or view reports.")
        else:
            return True
        return False

    def button(self, layout, text, callback):
        button = QPushButton(text)
        layout.addWidget(button)
        button.clicked.connect(callback)
        return button

    def launch(self, task, success, error_label=None):
        if self.closing or self.job is not None:
            return
        worker = self.job = NetworkJob(task, self)
        def complete(value, error):
            self.job = None
            if self.closing:
                return
            if error:
                (self.status if error_label is None else error_label).setText(error)
            else:
                success(value)
        worker.result.connect(complete, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def sign_in(self):
        if self.job is not None or self.timer.isActive():
            return
        current = self.url.text().strip() if self.developer else self.client.url
        if not current:
            self.status.setText("EXAM AI Cloud is not configured in this build.")
            return
        try:
            client = BetaClient(current)
            if self.client.token:
                self.status.setText("Sign out before changing the server.")
                return
            self.config.save_config({"server_url": current, "account_device": self.device})
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return
        self.client = client
        self.launch(lambda: client.request("POST", "/oauth/start", {"device": self.device}), self.open_browser)

    def open_browser(self, result):
        url = QUrl(result["url"])
        if (url.scheme() != "https" or url.host() != "accounts.google.com"
                or url.port() not in (-1, 443) or url.userInfo()):
            self.status.setText("Unexpected Google sign-in address.")
            return
        self.claim = result["claim"]
        self.timeout = monotonic() + 300
        if QDesktopServices.openUrl(url):
            self.timer.start()
        else:
            self.status.setText("Could not open Google Sign-In in your browser.")

    def poll(self):
        if monotonic() >= self.timeout:
            self.timer.stop()
            self.status.setText("Sign-in expired.")
            return
        self.launch(lambda: self.client.request("POST", "/oauth/claim", {"claim": self.claim}), self.completed)

    def completed(self, result):
        if result.get("pending"):
            return
        self.timer.stop()
        self.client.token = result["token"]
        self.update_controls()
        self.session_changed.emit()
        self.url.setReadOnly(True)
        try:
            self.config.set("account_session", protect(self.client.token))
        except (OSError, ValueError):
            self.status.setText("Signed in for this session. Windows storage is unavailable.")
        if self.name_input.text().strip():
            self.save_name()
        else:
            self.refresh()

    def save_name(self):
        name = self.name_input.text().strip()
        if not self.client.token or not name:
            self.status.setText("Введіть ім'я та увійдіть через Google.")
            return
        self.launch(lambda: self.client.request("PUT", "/account/name", {"name": name}),
                    lambda result: (self.name_input.setText(result["name"]), self.refresh()))

    def refresh(self):
        def fetch():
            catalog = self.client.request("GET", "/plans")
            account = self.client.request("GET", "/account") if self.client.token else None
            return account, catalog
        self.launch(fetch, self.show_account)

    def show_account(self, data):
        from datetime import datetime, timezone
        account, catalog = data
        status = "Signed out"
        if account:
            status = f"{account['state']} · {account['seconds'] // 60}:{account['seconds'] % 60:02d} beta time"
            if account["until"]:
                status += " · " + datetime.fromtimestamp(account["until"], timezone.utc).date().isoformat()
        if account:
            status = "\n".join(filter(None, (account.get("email"), status, account.get("plan"))))
            if not self.name_input.hasFocus():
                self.name_input.setText(account.get("name", ""))
        self.plans.clear()
        for plan in catalog["plans"]:
            self.plans.addItem(f"{plan['months']} mo / {plan['price_uah']} UAH", plan["id"])
        self.checkout.setEnabled(bool(self.developer and account and catalog["checkout_available"]))
        self.available_plans = {plan["id"]: plan for plan in catalog["plans"]}
        self.catalog_changed.emit()
        self.status.setText(status)

    def open_checkout(self, plan=None):
        plan = plan or self.plans.currentData()
        if not self.checkout.isEnabled() or plan not in self.available_plans:
            return
        def open_link(data):
            url = QUrl(data["url"])
            base = QUrl(self.client.url)
            if url.scheme() == "https" and url.host() == base.host() and url.port() == base.port():
                QDesktopServices.openUrl(url)
        self.launch(lambda: self.client.request("POST", "/payments/checkout/" + plan), open_link)

    def payment_history(self):
        self.launch(lambda: self.client.request("GET", "/payments"),
                    lambda rows: self.status.setText("\n".join(
                        f"{row['months']} mo · {row['price']} UAH · {row['status']}" for row in rows
                    ) or "No sandbox payments"))

    def redeem(self):
        code = self.invite.text().strip()
        if not 16 <= len(code) <= 128:
            self.status.setText("Вставте повний код запрошення (від 16 до 128 символів).")
            return
        self.launch(lambda: self.client.request("POST", "/invite", {"code": code}),
                    lambda result: (self.invite.clear(), self.refresh()))

    def sign_out(self):
        self.timer.stop()
        def done(_):
            self.client.token = ""
            self.name_input.clear()
            self.session_changed.emit()
            self.url.setReadOnly(False)
            self.checkout.setEnabled(False)
            self.update_controls()
            try:
                self.config.set("account_session", "")
            except OSError:
                self.status.setText("Signed out on the server. Could not clear the local file.")
            else:
                self.status.setText("Signed out")
        def end_session():
            from examai.ai.llm_engine import GenerationError
            try:
                return self.client.request("POST", "/logout")
            except GenerationError as exc:
                if str(exc) == "Session expired":
                    return None
                raise
        self.launch(end_session, done)

    def stop_jobs(self):
        self.closing = True
        self.timer.stop()
        for job in self.findChildren(NetworkJob):
            job.wait()

    def updates_panel(self):
        import os
        from examai import updates
        panel = QWidget()
        layout = QVBoxLayout(panel)
        channel = QComboBox()
        channel.addItems(["beta", "stable"])
        channel.setCurrentText(self.config.get("update_channel"))
        def select(value):
            try:
                self.config.set("update_channel", value)
            except OSError:
                self.status.setText("Could not save update channel.")
        channel.currentTextChanged.connect(select)
        layout.addWidget(channel)
        feedback = QLabel()
        feedback.setWordWrap(True)
        layout.addWidget(feedback)

        def found(manifest):
            if not manifest:
                feedback.setText("EXAM AI is up to date.")
                return
            if QMessageBox.question(panel, "EXAM AI update",
                                    f"Download and open installer {manifest['version']}?") == QMessageBox.StandardButton.Yes:
                self.launch(lambda: updates.download(manifest), updates.launch)

        def check():
            base = os.getenv("EXAMAI_UPDATE_URL", "")
            key = os.getenv("EXAMAI_UPDATE_PUBLIC_KEY", "")
            if not base or not key:
                feedback.setText("A signed update channel has not been configured.")
                return
            selected = channel.currentText()
            self.launch(lambda: updates.fetch_release(base, key, selected), found)
        self.button(layout, "Check for updates", check)
        return panel

    def reports_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        form = QWidget()
        form.setVisible(False)
        self.report_toggle = self.button(layout, "Report a Problem", lambda checked: form.setVisible(checked))
        self.report_toggle.setCheckable(True)
        layout.addWidget(form)
        layout = QVBoxLayout(form)
        kind = QComboBox()
        kind.addItems(["Bug", "Suggestion"])
        layout.addWidget(kind)
        description = QPlainTextEdit()
        description.setPlaceholderText("Describe the problem. Do not include passwords or keys.")
        layout.addWidget(description)
        screenshot = QLabel()
        screenshot.setVisible(False)
        screenshot_data = b""

        def select_screenshot():
            nonlocal screenshot_data
            name, _ = QFileDialog.getOpenFileName(panel, "Select screenshot", "", "PNG (*.png)")
            if not name:
                return
            try:
                with Path(name).open("rb") as file:
                    data = file.read(2_000_001)
                if len(data) > 2_000_000 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError
                preview = QPixmap()
                if not preview.loadFromData(data, "PNG"):
                    raise ValueError
            except (OSError, ValueError):
                status.setText("Choose a valid PNG screenshot up to 2 MB.")
                return
            screenshot_data = data
            screenshot.setPixmap(preview.scaled(320, 180, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
            screenshot.setVisible(True)
            remove.setVisible(True)
            status.clear()

        def clear_screenshot():
            nonlocal screenshot_data
            screenshot_data = b""
            screenshot.clear()
            screenshot.hide()
            remove.hide()

        self.button(layout, "Attach PNG screenshot", select_screenshot)
        layout.addWidget(screenshot)
        remove = self.button(layout, "Remove screenshot", clear_screenshot)
        remove.hide()
        diagnostics = QCheckBox("Include diagnostic summary")
        layout.addWidget(diagnostics)
        diagnostic_text = f"EXAM AI {VERSION}\nOS: {platform.system()} {platform.version()}"
        preview = QLabel(diagnostic_text)
        preview.hide()
        diagnostics.toggled.connect(preview.setVisible)
        layout.addWidget(preview)
        status = QLabel()
        status.setWordWrap(True)
        layout.addWidget(status)
        def submit():
            if not self.report_available(status):
                return
            body = {"kind": kind.currentText(), "description": description.toPlainText().strip(),
                    "screenshot": base64.b64encode(screenshot_data).decode("ascii"),
                    "diagnostics": diagnostic_text if diagnostics.isChecked() else ""}
            if not 5 <= len(body["description"]) <= 4000:
                status.setText("Enter a description of 5 to 4000 characters.")
                return

            def sent(result):
                description.clear()
                clear_screenshot()
                diagnostics.setChecked(False)
                status.setText(f"#{result['id']} · {result['status']}")

            self.launch(lambda: self.client.request("POST", "/reports", body),
                        sent, status)
        self.button(layout, "Submit report", submit)
        def my_reports():
            if self.report_available(status):
                self.launch(lambda: self.client.request("GET", "/reports"),
                            lambda rows: status.setText("\n".join(
                                f"#{row['id']} · {row['status']}" for row in rows) or "No reports"), status)

        self.button(layout, "My reports", my_reports)
        return panel
