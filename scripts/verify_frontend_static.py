from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "app" / "static" / "index.html"
CSS_PATH = ROOT / "app" / "static" / "styles.css"


def main() -> int:
    html = HTML_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")

    required_ids = [
        "app-title",
        "status",
        "lock-app",
        "session-sidebar",
        "session-list",
        "new-session-btn",
        "chat-title",
        "current-session-label",
        "chat-log",
        "chat-input",
        "send-btn",
        "refresh-history",
        "chat-debug",
        "pref-language",
        "pref-theme",
        "save-settings",
        "cfg-provider",
        "save-cfg",
        "tool-list",
        "save-skill",
        "logs-output",
        # tab panes
        "tab-settings",
        "tab-api",
        "tab-tools",
        "tab-skills",
        "tab-logs",
    ]
    missing = [item for item in required_ids if f'id="{item}"' not in html]

    checks = {
        "app_header": 'class="app-header"' in html,
        "chat_panel": 'class="chat-panel"' in html,
        "right_panel": 'class="right-panel"' in html,
        "tab_bar": 'class="tab-bar"' in html,
        "msg_wrap_css": ".msg-wrap" in css,
        "msg_bubble_css": ".msg-bubble" in css,
        "bubble_user_token": "--bubble-user:" in css,
        "compose_inner_css": ".compose-inner" in css,
        "chat_empty_css": ".chat-empty" in css,
        "status_pill_css": ".status-pill" in css,
        "tab_btn_css": ".tab-btn" in css,
        "tab_pane_css": ".tab-pane" in css,
        "dark_tokens": '[data-theme="dark"]' in css,
        "cache_bust": "20260417f" in html,
    }

    print("missing_ids:", missing)
    for key, value in checks.items():
        print(f"{key}: {'OK' if value else 'FAIL'}")

    ok = not missing and all(checks.values())
    print("\nFRONTEND_STATIC_OK" if ok else "\nFRONTEND_STATIC_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
