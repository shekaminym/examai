from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
import re
from io import BytesIO

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QFont, QImage, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import QTextEdit


MARKUP = re.compile(
    r"(?P<code>^[ \t]*(?:\x60{3,}|~{3,})[^\n]*\n.*?^[ \t]*(?:\x60{3,}|~{3,})[ \t]*$"
    r"|\x60+[^\n]*?\x60+|^[ \t]*(?:\x60{3,}|~{3,})[^\n]*\n.*\Z)"
    r"|(?P<display>(?<!\\)\$\$(?P<dollar_block>.+?)\$\$"
    r"|\\\[(?P<bracket_block>.+?)\\\])"
    r"|(?<![\\$])\$(?![\s$]|\d[\d,.]*(?:\s|$))(?P<dollar_inline>[^\n$]+?)(?<!\s)\$(?!\$|\d)"
    r"|\\\((?P<paren_inline>[^\n]+?)\\\)",
    re.MULTILINE | re.DOTALL,
)
MATH_GROUPS = ("dollar_block", "bracket_block", "dollar_inline", "paren_inline")
MATH_ALIASES = {
    "implies": "Rightarrow",
    "impliedby": "Leftarrow",
    "iff": "Leftrightarrow",
    "land": "wedge",
    "lor": "vee",
    "lnot": "neg",
    "le": "leq",
    "ge": "geq",
}
MATH_ALIAS = re.compile(r"\\(" + "|".join(MATH_ALIASES) + r")(?![A-Za-z])")


def streaming_prefix(source):
    """Hold a trailing, incomplete math span while leaving code and money alone."""
    end = 0
    for match in MARKUP.finditer(source):
        end = match.end()
    tail = source[end:]
    for match in re.finditer(r"(?<!\\)(?:\$\$|\\[([]|\$(?![\s$\d]))", tail):
        if "`" not in tail[:match.start()] and (
            match[0] != "$" or "\n" not in tail[match.end():]
        ):
            return source[:end + match.start()]
    return source


class FormulaJobs(QObject):
    ready = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.cache = OrderedDict()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="examai-math")

    def image(self, expression, display, color):
        key = expression, display, color
        future = self.cache.get(key)
        if future is None:
            if sum(not item.done() for item in self.cache.values()) >= 24:
                return None
            future = self.executor.submit(render_formula, expression, display, color=color)
            self.cache[key] = future
            future.add_done_callback(self.notify)
            if len(self.cache) > 64:
                self.cache.popitem(last=False)
        if not future.done():
            return None
        try:
            return future.result()
        except Exception:
            return None

    def notify(self, _future):
        try:
            self.ready.emit()
        except RuntimeError:
            pass  # Native Qt object may already have been destroyed.


class ResponseDocument(QTextDocument):
    def loadResource(self, resource_type, name):
        # Formula images are supplied directly with addResource.
        return None


class ResponseView(QTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        document = ResponseDocument(self)
        document.setDefaultFont(QFont('Segoe UI', 12))
        self.setDocument(document)
        self.source = ""
        self._formula_cache = {}
        self._formula_color = "#f4f4f4"
        self.formula_jobs = None
        self._last_rich_render = None

    def enable_background_formulas(self, jobs):
        self.formula_jobs = jobs
        jobs.ready.connect(self._refresh_formula_images, Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _refresh_formula_images(self):
        if self._last_rich_render is not None:
            original = self.source
            self.show_response(self._last_rich_render, streaming=True)
            self.source = original


    def set_formula_color(self, color):
        if color != self._formula_color:
            self._formula_color = color
            self._formula_cache.clear()
            if any(image.startswith("formula:") for image in re.findall(r'src="([^"]+)"', self.document().toHtml())):
                self.show_response(self.source, streaming=True)

    def setPlainText(self, text):
        self._last_rich_render = None
        self.source = text
        super().setPlainText(text)

    def show_streaming_response(self, source):
        visible = streaming_prefix(source)
        if any(match["code"] is None for match in MARKUP.finditer(visible)):
            self.show_response(visible, streaming=True)
        elif visible.startswith(self.toPlainText()):
            old_length = len(self.toPlainText())
            if len(visible) > old_length:
                scrollbar = self.verticalScrollBar()
                follow_end = scrollbar.value() >= scrollbar.maximum() - 12
                cursor = self.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                cursor.insertText(visible[old_length:])
                if follow_end:
                    scrollbar.setValue(scrollbar.maximum())
        else:
            self.setPlainText(visible)
        self.source = source

    def show_response(self, source, *, streaming=False):
        self.source = source
        self._last_rich_render = source
        document = self.document()
        scroll = self.verticalScrollBar()
        previous_position = scroll.value()
        follow_end = previous_position >= scroll.maximum() - 12
        formulas = []
        prefix = "APPFORMULAPLACEHOLDER"
        while prefix in source:
            prefix += "X"

        def placeholder(match):
            if match["code"] is not None or len(formulas) >= 24:
                return match[0]
            expression = next(match[group] for group in MATH_GROUPS if match[group] is not None)
            token = f"{prefix}{len(formulas):03d}END"
            display = match["display"] is not None
            formulas.append((token, expression, match[0], display))
            return f"\n\n{token}\n\n" if display else token

        features = (
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QTextDocument.MarkdownFeature.MarkdownNoHTML
        )
        font = document.defaultFont()
        if font.pointSizeF() <= 0:
            # A widget stylesheet can replace the document font with a pixel font.
            # Markdown uses point sizes for some formats. Convert at the widget DPI.
            pixels = font.pixelSize()
            points = pixels * 72 / max(1, self.logicalDpiY()) if pixels > 0 else 12
            font.setPointSizeF(points)
            document.setDefaultFont(font)
        document.setMarkdown(MARKUP.sub(placeholder, source), features)
        for index, (token, expression, literal, display) in enumerate(formulas):
            cursor = document.find(token)
            if cursor.isNull():
                continue
            if self.formula_jobs is not None:
                image = self.formula_jobs.image(expression, display, self._formula_color)
            else:
                cache_key = (expression, display)
                if cache_key not in self._formula_cache:
                    if len(self._formula_cache) >= 32:
                        self._formula_cache.pop(next(iter(self._formula_cache)))
                    self._formula_cache[cache_key] = (render_formula(expression, display)
                        if self._formula_color == "#f4f4f4" else
                        render_formula(expression, display, color=self._formula_color))
                image = self._formula_cache[cache_key]
            if image is None:
                cursor.insertText(expression.strip() if self.formula_jobs is not None else literal)
                continue
            url = QUrl(f"formula:{index}")
            document.addResource(QTextDocument.ResourceType.ImageResource, url, image)
            picture = QTextImageFormat()
            picture.setName(url.toString())
            picture.setProperty(QTextImageFormat.Property.ImageAltText, expression.strip())
            natural_width = image.width() / 1.5
            available_width = max(80, self.viewport().width() - 24)
            scale = min(natural_width, available_width) / image.width()
            picture.setWidth(image.width() * scale)
            picture.setHeight(image.height() * scale)
            cursor.insertImage(picture)
        scroll.setValue(scroll.maximum() if streaming and follow_end else
                        min(previous_position, scroll.maximum()) if streaming else 0)

    def copyable_text(self):
        return self.source if not self.document().isEmpty() else ""


def render_formula(expression, display, *, color="#f4f4f4"):
    expression = " ".join(expression.split())
    if not expression or len(expression) > 300:
        return None
    expression = MATH_ALIAS.sub(lambda match: "\\" + MATH_ALIASES[match[1]], expression)

    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.font_manager import FontProperties
        from matplotlib.mathtext import MathTextParser

        math = "$" + expression + "$"
        font = FontProperties(size=13 if display else 11)
        width, height, depth, _, _ = MathTextParser("path").parse(math, dpi=72, prop=font)
        figure = Figure(figsize=(width / 72, height / 72), dpi=144, facecolor="none")
        FigureCanvasAgg(figure)
        figure.text(0, depth / height, math, fontproperties=font, color=color, usetex=False)
        output = BytesIO()
        figure.savefig(output, format="png", transparent=True)
        image = QImage.fromData(output.getvalue(), "PNG")
        return None if image.isNull() else image
    except (ImportError, ValueError, RuntimeError, OverflowError, RecursionError, ZeroDivisionError):
        return None


