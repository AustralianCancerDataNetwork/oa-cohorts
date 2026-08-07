import html
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol, Union

import sqlalchemy as sa
from oa_configurator import get_logger
from sqlalchemy.dialects import postgresql

from .executability import ExecStatus

logger = get_logger(__name__)

def esc(x: Any) -> str:
    return "" if x is None else html.escape(str(x))

def tag(name: str, body: str, *, cls: str | None = None) -> str:
    c = f' class="{cls}"' if cls else ""
    return f"<{name}{c}>{body}</{name}>"

def td(x: Any, *, cls: str | None = None) -> str:
    if isinstance(x, RawHTML):
        return tag("td", x.html, cls=cls)
    return tag("td", esc(x), cls=cls)

def th(x: Any) -> str:
    return tag("th", esc(x))

def tr(cells: Iterable[str]) -> str:
    return tag("tr", "".join(cells))

def table(headers: Iterable[str], rows: Iterable[Iterable[str]], *, cls: str | None = None) -> str:
    head = tr(th(h) for h in headers)
    body = "".join(tr(r) for r in rows)
    return tag("table", f"<thead>{head}</thead><tbody>{body}</tbody>", cls=cls)

class SupportsHTML(Protocol):
    # HTML protocol + raw passthrough
    def _repr_html_(self) -> str: ...

class RawHTML:
    def __init__(self, html: str):
        self.html = html

    def _repr_html_(self) -> str:
        return self.html
    
def exec_badge(status: ExecStatus) -> RawHTML:
    return RawHTML({
        ExecStatus.PASS: "<span class='badge ok'>PASS</span>",
        ExecStatus.WARN: "<span class='badge warn'>WARN</span>",
        ExecStatus.FAIL: "<span class='badge bad'>FAIL</span>",
    }[status])

HTMLChild = Union["HTMLRenderable", RawHTML, str]

class HTMLRenderable:

    """
    Mixin for composable HTML representations in notebooks.

    Subclasses override:
      - _html_title()
      - _html_header()
      - _html_inner()

    Optionally override:
      - _html_css_class()  -> e.g. "measure", "subquery", "queryrule"
    """
    _css_loaded = False
    _css_warned = False

    @classmethod
    def _ensure_css(cls):
        if cls._css_loaded:
            return

        try:
            from IPython.display import HTML, display
        except ImportError:
            if not cls._css_warned:
                logger.warning("IPython not available; HTML will render without styling")
            HTMLRenderable._css_warned = True
            return
        css_path = Path(__file__).parent / "render.css"
        css = css_path.read_text()
        style_tag = f"<style>{css}</style>"
        display(HTML(style_tag))
        HTMLRenderable._css_loaded = True

    def _html_title(self) -> str:
        return self.__class__.__name__

    def _html_header(self) -> Mapping[str, object]:
        """
        Key-value summary for the object.
        Values may be str or RawHTML.
        """
        return {}

    def _html_inner(self) -> Iterable[HTMLChild]:
        return []

    def _html_css_class(self) -> str | None:
        """
        Optional CSS class appended to render-block.
        Example: 'measure', 'subquery', 'queryrule'
        """
        return None

    def _repr_html_(self) -> str:
        self._ensure_css()
        return self.html_render_outer()

    def html_render_outer(self) -> str:
        header = self._html_header()

        header_table = ""
        if header:
            header_table = table(
                headers=["Field", "Value"],
                rows=[
                    [
                        td(k),
                        td(v if isinstance(v, RawHTML) else esc(v)),
                    ]
                    for k, v in header.items()
                ],
                cls="meta-table",
            )

        inner_html = "".join(
            c._repr_html_() if hasattr(c, "_repr_html_") else f"<div>{esc(c)}</div>"  # type: ignore
            for c in self._html_inner()
        )

        block_classes = ["render-block"]
        extra = self._html_css_class()
        if extra:
            block_classes.append(extra)

        return tag(
            "div",
            (
                tag("div", esc(self._html_title()), cls="title")
                + header_table
                + inner_html
            ),
            cls=" ".join(block_classes),
        )

def render_sql(expr: sa.ClauseElement) -> str:
    """
    Render a SQLAlchemy expression to SQL text with bound params inlined.
    Intended for debugging / display only.
    """
    try:
        compiled = expr.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
        return str(compiled)
    except Exception as e:
        return f"SQL render failed: {e}"


def sql_block(expr: sa.ClauseElement) -> RawHTML:
    """
    Convenience wrapper to render SQL in a styled <pre> block.
    """
    sql = render_sql(expr)
    return RawHTML(f"<pre class='sql-preview'>{esc(sql)}</pre>")