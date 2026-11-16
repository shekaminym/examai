"""Account and reporting controls driven by network workers."""
import base64
import os
from pathlib import Path
import platform
import secrets
from unicodedata import category
from time import monotonic

from PySide6.QtCore import QThread, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from examai.beta import BetaClient, distribution_url
from examai.branding import VERSION
from examai.windows.credentials import protect, reveal
from examai.ui.settings_window import SettingsWindow


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


class WelcomeDialog(SettingsWindow):
    def __init__(self, account, screen):
        # A native dialog owned by a hidden HWND has no taskbar entry.
        super().__init__()
        self.account = account
        self.setWindowTitle("EXAM AI · Вхід")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setModal(True)
        self.set_caption_colors("#191824", "#eeeaf7", True)
        self.setFixedWidth(440)
        self.setStyleSheet('''
            QDialog { background: #191824; }
            QLabel { color: #eeeaf7; background: transparent; font-size: 14px; }
            QLabel#WelcomeBrand { font-size: 23px; font-weight: 700; }
            QLineEdit { color: #fff; background: #252335; border: 1px solid #45415b;
                        border-radius: 10px; padding: 10px; }
            QPushButton { color: #f8f5ff; background: #302949; border: 1px solid #50466b;
                          border-radius: 10px; padding: 10px; }
            QPushButton#WelcomeGoogle { background: #644bb5; font-weight: 600; }
            QPushButton:disabled { color: #b4abc9; background: #373049; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(self.windowIcon().pixmap(36, 36))
        layout.addWidget(icon)
        brand = QLabel("EXAM AI")
        brand.setObjectName("WelcomeBrand")
        layout.addWidget(brand)
        layout.addWidget(QLabel("Введіть своє ім’я"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Як до вас звертатися")
        self.name.setAccessibleName("Ваше ім’я")
        self.name.setMaxLength(60)
        layout.addWidget(self.name)
        layout.addWidget(QLabel("Увійти за допомогою"))
        self.google = account.button(layout, "Увійти через Google", account.sign_in)
        self.google.setObjectName("WelcomeGoogle")
        self.cancel = account.button(layout, "Скасувати вхід", self.reject)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        account.button(layout, "Продовжити зі своїм Gemini API key", self.use_byok)
        self.name.textChanged.connect(self.update_controls)
        self.name.returnPressed.connect(self.google.click)
        self.update_controls()
        self.adjustSize()
        if screen:
            self.move(screen.availableGeometry().center() - self.rect().center())

    def update_controls(self):
        busy = self.account.job is not None or self.account.timer.isActive()
        valid = bool(self.name.text().strip()) and not any(
            category(char) in {"Cc", "Cf", "Cs"} for char in self.name.text()
        )
        self.google.setText("Зберегти ім’я" if self.account.client.token else "Увійти через Google")
        self.google.setEnabled(valid and not busy)
        self.name.setReadOnly(busy)
        self.cancel.setVisible(busy)

    def reject(self):
        if self.account.job is not None or self.account.timer.isActive():
            self.account.cancel_login()
            self.feedback.setText("Вхід скасовано. Можна повторити спробу.")
            return
        super().reject()

    def use_byok(self):
        self.account.cancel_login()
        self.accept()


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
        self.login_attempt = 0
        self.welcome = None
        self.startup_ready = None
        self.startup_exit = None
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
        self.url.editingFinished.connect(self.configure_developer_url)
        self.timer = QTimer(self, interval=1600)
        self.timer.timeout.connect(self.poll)
        self.invitation = QWidget()
        invite_layout = QVBoxLayout(self.invitation)
        invite_layout.setContentsMargins(0, 0, 0, 0)
        self.invite = QLineEdit()
        self.invite.setAccessibleName("Код запрошення")
        self.invite.setPlaceholderText("Код запрошення")
        invite_layout.addWidget(self.invite)
        self.redeem_button = self.button(invite_layout, "Активувати запрошення", self.redeem)
        self.redeem_button.setEnabled(False)
        self.invite.textChanged.connect(lambda text: self.redeem_button.setEnabled(16 <= len(text.strip()) <= 128))
        self.invite_feedback = QLabel()
        self.invite_feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.invite_feedback.setWordWrap(True)
        self.invite_feedback.hide()
        invite_layout.addWidget(self.invite_feedback)
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
        for widget in (self.refresh_button, self.signout_button):
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

    def launch(self, task, success, error_label=None, on_error=None):
        if self.closing or self.job is not None:
            return
        worker = self.job = NetworkJob(task, self)
        def complete(value, error):
            self.job = None
            if self.closing:
                return
            if error and on_error:
                on_error(error)
            elif error:
                (self.status if error_label is None else error_label).setText(error)
            else:
                success(value)
            if self.welcome:
                self.welcome.update_controls()
        worker.result.connect(complete, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        if self.welcome:
            self.welcome.update_controls()


    def configure_developer_url(self):
        if not self.developer or self.client.token:
            return
        try:
            client = BetaClient(self.url.text().strip())
            self.config.set("server_url", client.url)
        except (ValueError, OSError) as error:
            self.status.setText(str(error))
            return
        self.client = client
        self.update_controls()

    def show_welcome(self, parent):
        if not self.client.url:
            return
        if self.welcome is None:
            self.welcome = WelcomeDialog(self, parent.screen())
            self.welcome.finished.connect(self.welcome_finished)
            try:
                self.welcome.name.setText(reveal(self.config.get("account_name")))
            except (ValueError, OSError):
                pass
        self.welcome.show()
        self.welcome.raise_()
        self.welcome.activateWindow()

    def welcome_finished(self, result):
        ready, self.startup_ready = self.startup_ready, None
        close, self.startup_exit = self.startup_exit, None
        if ready is not None:
            if result == QDialog.DialogCode.Accepted:
                ready()
            else:
                close()

    def remember_name(self, name):
        if name:
            try:
                self.config.set("account_name", protect(name))
            except (ValueError, OSError):
                pass

    def restore_session(self, parent, ready=None):
        if not self.client.url:
            if ready:
                ready()
            return
        self.startup_ready = ready
        self.startup_exit = parent.close if ready else None
        if not self.client.token:
            self.show_welcome(parent)
            return

        def failed(error):
            if error == "Session expired":
                self.client.token = ""
                try:
                    self.config.set("account_session", "")
                except OSError:
                    pass
                self.url.setReadOnly(False)
                self.update_controls()
                self.session_changed.emit()
                self.show_welcome(parent)
            else:
                self.status.setText(error)
                self.welcome_finished(QDialog.DialogCode.Accepted)

        def restored(account):
            self.remember_name(account.get("name", ""))
            self.welcome_finished(QDialog.DialogCode.Accepted)
            self.refresh()

        self.launch(lambda: self.client.request("GET", "/account"), restored, on_error=failed)

    def cancel_login(self):
        self.login_attempt += 1
        self.timer.stop()
        self.claim = ""
        if self.welcome:
            self.welcome.update_controls()

    def sign_in(self):
        if self.job is not None or self.timer.isActive():
            return
        welcome = self.welcome
        name = welcome.name.text().strip() if welcome and welcome.isVisible() else ""
        if not name:
            self.show_welcome(self.window().parentWidget() or self.window())
            return
        welcome.feedback.clear()
        if self.client.token:
            self.complete_profile(name)
            return
        current = self.url.text().strip() if self.developer else self.client.url
        try:
            client = BetaClient(current)
            self.config.save_config({"server_url": current, "account_device": self.device})
        except (ValueError, OSError) as exc:
            welcome.feedback.setText(str(exc))
            return
        self.client = client
        attempt = self.login_attempt

        def opened(result):
            if attempt == self.login_attempt:
                self.open_browser(result)

        self.launch(lambda: client.request("POST", "/oauth/start", {"device": self.device}),
                    opened, on_error=lambda error: self.login_failed(attempt, error))

    def login_failed(self, attempt, error):
        if attempt != self.login_attempt:
            return
        self.cancel_login()
        self.welcome.feedback.setText({
            "Login expired": "Вхід скасовано або час очікування минув. Спробуйте ще раз.",
            "Session expired": "Сеанс завершився. Увійдіть повторно.",
        }.get(error, error))

    def open_browser(self, result):
        url = QUrl(result["url"])
        if (url.scheme() != "https" or url.host() != "accounts.google.com"
                or url.port() not in (-1, 443) or url.userInfo()):
            self.welcome.feedback.setText("Неочікувана адреса входу Google.")
            return
        self.claim = result["claim"]
        self.timeout = monotonic() + 300
        if QDesktopServices.openUrl(url):
            self.timer.start()
            self.welcome.feedback.setText("Завершіть вхід у браузері й поверніться до EXAM AI.")
        else:
            self.login_failed(self.login_attempt, "Не вдалося відкрити браузер.")

    def poll(self):
        attempt = self.login_attempt
        if monotonic() >= self.timeout:
            self.login_failed(attempt, "Login expired")
            return
        claim = self.claim

        def completed(result):
            if result.get("pending"):
                return
            if attempt != self.login_attempt:
                abandoned = BetaClient(self.client.url, result["token"])
                self.launch(lambda: abandoned.request("POST", "/logout"), lambda _result: None)
                return
            self.timer.stop()
            self.claim = ""
            self.client.token = result["token"]
            self.complete_profile(self.welcome.name.text().strip())

        self.launch(lambda: self.client.request("POST", "/oauth/claim", {"claim": claim}),
                    completed, on_error=lambda error: self.login_failed(attempt, error))

    def complete_profile(self, name):
        attempt = self.login_attempt
        def saved(result):
            if attempt != self.login_attempt:
                return
            self.name_input.setText(result["name"])
            self.remember_name(result["name"])
            self.update_controls()
            self.url.setReadOnly(True)
            try:
                self.config.set("account_session", protect(self.client.token))
            except (OSError, ValueError):
                self.welcome.feedback.setText("Windows не вдалося зберегти сеанс для наступного запуску.")
            self.session_changed.emit()
            self.welcome.accept()
            self.refresh()

        def failed(error):
            if attempt == self.login_attempt and error == "Session expired":
                self.client.token = ""
            self.login_failed(attempt, error)

        self.launch(lambda: self.client.request("PUT", "/account/name", {"name": name}), saved, on_error=failed)

    def save_name(self):
        name = self.name_input.text().strip()
        if not self.client.token or not name:
            self.status.setText("Введіть ім'я та увійдіть через Google.")
            return
        def saved(result):
            self.name_input.setText(result["name"])
            self.remember_name(result["name"])
            self.refresh()

        self.launch(lambda: self.client.request("PUT", "/account/name", {"name": name}), saved)

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
            self.invite_feedback.setText("Вставте повний код запрошення (від 16 до 128 символів).")
            self.invite_feedback.show()
            return
        if not self.client.token:
            self.invite_feedback.setText("Спершу увійдіть через Google в розділі «Підписки».")
            self.invite_feedback.show()
            return
        def activated(_result):
            self.invite.clear()
            self.invite_feedback.setText("Запрошення активовано.")
            self.refresh()

        self.invite_feedback.show()
        self.launch(lambda: self.client.request("POST", "/invite", {"code": code}),
                    activated, error_label=self.invite_feedback)

    def sign_out(self):
        self.timer.stop()
        def done(_):
            self.client.token = ""
            self.name_input.clear()
            self.invite_feedback.hide()
            if self.welcome:
                self.welcome.name.clear()
            self.session_changed.emit()
            self.url.setReadOnly(False)
            self.checkout.setEnabled(False)
            self.update_controls()
            try:
                self.config.save_config({"account_session": "", "account_name": ""})
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
