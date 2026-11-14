"""Account and reporting controls driven by network workers."""
import secrets
from time import monotonic

from PySide6.QtCore import QThread, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from examai.beta import BetaClient
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
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.job = None
        self.closing = False
        self.claim = ""
        self.device = config.get("account_device") or secrets.token_hex(16)
        try:
            token = reveal(config.get("account_session"))
        except (ValueError, OSError):
            token = ""
        try:
            self.client = BetaClient(config.get("server_url"), token)
        except ValueError:
            self.client = BetaClient()
        layout = QVBoxLayout(self)
        self.url = QLineEdit(self.client.url)
        self.url.setPlaceholderText("https://your-examai-server")
        if self.client.token:
            self.url.setReadOnly(True)
        layout.addWidget(self.url)
        self.status = QLabel("EXAM AI Cloud")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.button(layout, "Continue with Google", self.sign_in)
        self.button(layout, "Refresh account and plans", self.refresh)
        self.button(layout, "Sign out", self.sign_out)
        self.invite = QLineEdit()
        self.invite.setPlaceholderText("Closed beta invite")
        layout.addWidget(self.invite)
        self.button(layout, "Redeem invite", self.redeem)
        self.timer = QTimer(self, interval=1600)
        self.timer.timeout.connect(self.poll)
        self.plans = QComboBox()
        layout.addWidget(self.plans)
        self.checkout = QPushButton("Open sandbox checkout")
        self.checkout.setEnabled(False)
        self.checkout.clicked.connect(self.open_checkout)
        layout.addWidget(self.checkout)
        self.button(layout, "Sandbox payment history", self.payment_history)
        layout.addStretch()

    def button(self, layout, text, callback):
        button = QPushButton(text)
        layout.addWidget(button)
        button.clicked.connect(callback)

    def launch(self, task, success):
        if self.closing or self.job is not None:
            return
        worker = self.job = NetworkJob(task, self)
        def complete(value, error):
            self.job = None
            if self.closing:
                return
            if error:
                self.status.setText(error)
            else:
                success(value)
        worker.result.connect(complete, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def sign_in(self):
        if self.job is not None:
            return
        current = self.url.text().strip()
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
        if url.scheme() != "https" or url.host() != "accounts.google.com":
            self.status.setText("Unexpected Google sign-in address.")
            return
        self.claim = result["claim"]
        self.timeout = monotonic() + 300
        QDesktopServices.openUrl(url)
        self.timer.start()

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
        self.url.setReadOnly(True)
        try:
            self.config.set("account_session", protect(self.client.token))
        except (OSError, ValueError):
            self.status.setText("Signed in for this session. Windows storage is unavailable.")
            return
        self.refresh()

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
        prices = " · ".join(f"{p['months']} mo / {p['price_uah']} UAH" for p in catalog["plans"])
        self.plans.clear()
        for plan in catalog["plans"]:
            self.plans.addItem(f"{plan['months']} mo / {plan['price_uah']} UAH", plan["id"])
        self.checkout.setEnabled(bool(account and catalog["checkout_available"]))
        label = "LiqPay sandbox checkout" if catalog["checkout_available"] else "Checkout unavailable"
        self.status.setText(f"{status}\n{prices}\n{label}")

    def open_checkout(self):
        plan = self.plans.currentData()
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
        self.launch(lambda: self.client.request("POST", "/invite", {"code": code}),
                    lambda result: (self.invite.clear(), self.refresh()))

    def sign_out(self):
        self.timer.stop()
        def done(_):
            self.client.token = ""
            self.url.setReadOnly(False)
            self.checkout.setEnabled(False)
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
        kind = QComboBox()
        kind.addItems(["Bug", "Suggestion"])
        layout.addWidget(kind)
        description = QPlainTextEdit()
        description.setPlaceholderText("Describe the problem. Do not include passwords or keys.")
        layout.addWidget(description)
        status = QLabel()
        status.setWordWrap(True)
        layout.addWidget(status)
        def submit():
            body = {"kind": kind.currentText(), "description": description.toPlainText()[:4000]}
            self.launch(lambda: self.client.request("POST", "/reports", body),
                        lambda result: (description.clear(), status.setText(f"#{result['id']} · {result['status']}")))
        self.button(layout, "Submit report", submit)
        self.button(layout, "My reports", lambda: self.launch(
            lambda: self.client.request("GET", "/reports"),
            lambda rows: status.setText("\n".join(f"#{row['id']} · {row['status']}" for row in rows) or "No reports")))
        return panel
