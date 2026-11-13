import re
from io import BytesIO

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QImage, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import QTextEdit


MARKUP = re.compile(
    r"(?P<code>^[ \t]*(?:\x60{3,}|~{3,})[^\n]*\n.*?^[ \t]*(?:\x60{3,}|~{3,})[ \t]*$"
    r"|\x60+[^\n]*?\x60+)"
    r"|(?P<display>(?<!\\)\$\$(?P<dollar_block>.+?)\$\$"
    r"|\\\[(?P<bracket_block>.+?)\\\])"
    r"|(?<![\\$])\$(?![\s$])(?P<dollar_inline>[^\n$]+?)(?<!\s)\$(?!\$|\d)"
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

    def setPlainText(self, text):
        self.source = text
        super().setPlainText(text)

    def show_streaming_response(self, source):
        # Plain text is cheap while a formula is still incomplete. Once a
        # closing delimiter arrives, render it without waiting for the model.
        has_complete_formula = any(
            match["display"] is not None or any(match[group] is not None for group in MATH_GROUPS[2:])
            for match in MARKUP.finditer(source)
        )
        if has_complete_formula:
            self.show_response(source, streaming=True)
        else:
            self.setPlainText(source)

    def show_response(self, source, *, streaming=False):
        self.source = source
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
            cache_key = (expression, display)
            if cache_key not in self._formula_cache:
                if len(self._formula_cache) >= 32:
                    self._formula_cache.pop(next(iter(self._formula_cache)))
                self._formula_cache[cache_key] = render_formula(expression, display)
            image = self._formula_cache[cache_key]
            if image is None:
                cursor.insertText(literal)
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


def render_formula(expression, display):
    expression = " ".join(expression.split())
    if not expression or len(expression) > 300:
        return None
    expression = MATH_ALIAS.sub(lambda match: "\\" + MATH_ALIASES[match[1]], expression)

    try:
        from matplotlib import rc_context
        from matplotlib.font_manager import FontProperties
        from matplotlib.mathtext import math_to_image

        output = BytesIO()
        with rc_context({"savefig.transparent": True, "text.usetex": False}):
            math_to_image(
                "$" + expression + "$", output, dpi=144, format="png",
                prop=FontProperties(size=13 if display else 11), color="#f4f4f4",
            )
        image = QImage.fromData(output.getvalue(), "PNG")
        return None if image.isNull() else image
    except (ImportError, ValueError, RuntimeError, OverflowError, RecursionError):
        return None


