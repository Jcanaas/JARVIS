from ui.panels.gmail import _sanitize_email_html_for_qt


def test_sanitizes_unsupported_qt_css_values():
    html = (
        '<style>div{color:#0000;box-shadow:0 0 #0000;font-size:0;}</style>'
        '<div style="color:#fff0;font-size:0px;">hello</div>'
        '<div style="color:rgba(0, 0, 0, 0.2);">world</div>'
    )

    sanitized = _sanitize_email_html_for_qt(html)

    import re

    assert not re.search(r"#[0-9a-fA-F]{3,4}\b", sanitized)
    assert "rgba(" not in sanitized
    assert "font-size" in sanitized and "1px" in sanitized
    assert "#000000" in sanitized
