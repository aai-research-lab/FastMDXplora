"""The sidebar's icons, one set for the GUI and the standalone dashboard.

Line icons on a 24-unit grid, stroked in the text's own colour so every
scheme colours them, written into the page as inline SVG (the GUI runs on
machines with no route to the internet, so nothing is fetched). The GUI's
template names an icon with a marker, ``<!--icon:overview:nav-icon-->``,
which the server replaces with the SVG; the standalone dashboard writes
them with :func:`icon`.
"""

from __future__ import annotations

import re

__all__ = ["ICONS", "icon", "with_icons"]

ICONS: dict[str, str] = {
    "studies": ('<rect x="3" y="3" width="7" height="7" rx="1.5"/>'
                '<rect x="14" y="3" width="7" height="7" rx="1.5"/>'
                '<rect x="3" y="14" width="7" height="7" rx="1.5"/>'
                '<rect x="14" y="14" width="7" height="7" rx="1.5"/>'),
    "overview": ('<path d="M4 13a8 8 0 0 1 16 0"/><path d="M12 13l4-4"/>'
                 '<circle cx="12" cy="13" r="1.3"/><path d="M4 18h16"/>'),
    "viewer": ('<circle cx="6" cy="7" r="2.2"/><circle cx="18" cy="7" r="2.2"/>'
               '<circle cx="12" cy="17" r="2.2"/><path d="M8 8l2.8 7M16 8l-2.8 7M8.2 7h7.6"/>'),
    "analysis": '<path d="M4 19V5"/><path d="M4 19h16"/><path d="M7 15l4-5 3 3 5-6"/>',
    "report": ('<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/>'
               '<path d="M10 12h5M10 16h5"/>'),
    "files": ('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5'
              'a2 2 0 0 1-2-2z"/>'),
    # A robot's head, its antenna up: the Agent, not a sparkle.
    "agent": ('<rect x="4.5" y="8" width="15" height="11" rx="3"/><path d="M12 8V5"/>'
              '<circle cx="12" cy="4" r="1.2"/><path d="M9.5 12.5v1.5M14.5 12.5v1.5"/>'
              '<path d="M2.5 12.5v3M21.5 12.5v3"/>'),
    "config": ('<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/>'
               '<circle cx="10" cy="17" r="2"/>'),
    "chevron": '<path d="M8 10l4 4 4-4"/>',
    "new": '<path d="M12 5v14M5 12h14"/>',
    "recent": '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
    "beside": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14 4v16"/>',
    "pause": '<path d="M9 6v12M15 6v12"/>',
    "play": '<path d="M8 5.5v13l10.5-6.5z"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/>',
    # A gear of eight teeth around its hub, the usual sign of settings.
    "gear": ('<path d="M19.2 10.3l2.3.4v2.6l-2.3.4-.9 2.2 1.4 1.9-1.9 1.9-1.9-1.4-2.2.9-.4 2.3'
             'h-2.6l-.4-2.3-2.2-.9-1.9 1.4-1.9-1.9 1.4-1.9-.9-2.2-2.3-.4v-2.6l2.3-.4.9-2.2'
             '-1.4-1.9 1.9-1.9 1.9 1.4 2.2-.9.4-2.3h2.6l.4 2.3 2.2.9 1.9-1.4 1.9 1.9-1.4 1.9z"/>'
             '<circle cx="12" cy="12" r="3"/>'),
    # FastMDXplora's own mark: a three-atom molecule going right, each atom
    # leaving its trail, as a trajectory is the path of each atom in time.
    # Made by scripts/make_mark.py, which also writes the tab's icon.
    "mark": ('<circle cx="18" cy="10.5" r="2.8"/><circle cx="12.5" cy="5" r="2"/>'
             '<circle cx="11" cy="16.5" r="2"/>'
             '<path d="M13.91 6.41L16.02 8.52M12.52 15.2L15.87 12.32"/>'
             '<path d="M2 5h6.1M2 16.5h4.6M5.5 10.5h7.3"/>'),
    # The sidebar, folded or shown: a window with its left column.
    "sidebar": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    "panel": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    "download": '<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 20h14"/>',
    "chat": ('<path d="M5 5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-7l-4.5 3.5V17H5a2 2 0 0 1-2-2V7'
             'a2 2 0 0 1 2-2z"/>'),
    "cards": ('<rect x="3.5" y="4" width="7" height="7" rx="1.5"/><rect x="13.5" y="4" width="7" '
              'height="7" rx="1.5"/><rect x="3.5" y="14" width="7" height="6" rx="1.5"/>'
              '<rect x="13.5" y="14" width="7" height="6" rx="1.5"/>'),
    "table": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.5h18M3 14.8h18M9 9.5V20"/>',
    "tag": '<path d="M3.5 12.2V4h8.2l8.8 8.8-8.2 8.2z"/><circle cx="8" cy="8.5" r="1.4"/>',
    "erase": ('<path d="M8.5 20H20M4.6 15.4l9.9-9.9a2 2 0 0 1 2.8 0l2.2 2.2a2 2 0 0 1 0 2.8L12 18'
              'H7.2z"/><path d="M9.5 10.5l5 5"/>'),
    # A folder searched: Look in another folder.
    "lookin": ('<path d="M11 19H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v2"/>'
               '<circle cx="16.5" cy="15.5" r="3"/><path d="M18.7 17.7l2.3 2.3"/>'),
    # An archive opened, its contents coming out: Open a shared study.
    "unpack": ('<rect x="3" y="4" width="18" height="4" rx="1"/>'
               '<path d="M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8"/>'
               '<path d="M12 11v6M9.5 14.5L12 17l2.5-2.5"/>'),
    "copy": ('<rect x="8" y="8" width="12" height="12" rx="2"/>'
             '<path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>'),
}

_MARKER = re.compile(r"<!--icon:([a-z]+):([A-Za-z0-9 _-]+)-->")


def icon(name: str, cls: str = "nav-icon") -> str:
    """One icon as inline SVG, hidden from screen readers (its link or
    button carries the words)."""
    return (f'<svg class="{cls}" viewBox="0 0 24 24" width="16" height="16" fill="none" '
            'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def with_icons(html: str) -> str:
    """A page with each ``<!--icon:name:class-->`` marker made the icon."""
    return _MARKER.sub(lambda m: icon(m.group(1), m.group(2)), html)
