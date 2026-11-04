# SPDX-License-Identifier: GPL-3.0-only
"""On-demand, adaptive guidance without changing the user's model selection."""

from .gtk import Adw, Gtk
from ..core.models import LOCAL_MODELS


class ModelGuide(Adw.Dialog):
    def __init__(self):
        super().__init__(title="Choosing a Model", content_width=500, content_height=600)
        toolbar = Adw.ToolbarView()
        self.set_child(toolbar)
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        toolbar.add_top_bar(header)
        self.close_button = Gtk.Button(label="Close")
        self.close_button.connect("clicked", lambda *_: self.close())
        header.pack_end(self.close_button)
        self.set_focus(self.close_button)
        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        introduction = Adw.PreferencesGroup(
            description="Parakeet is the default. All four models work offline on CPU after downloading."
        )
        page.add(introduction)
        details = {
            "parakeet": (
                "Recommended default",
                "Strongest accuracy in our English tests. Supports 25 European languages. Best starting point if your computer has enough memory.",
                "About 640 MiB to download in Int8; used about 1.3 GiB of memory in our test process. Larger than the alternatives.",
            ),
            "moonshine-tiny": (
                "Lightweight English option",
                "English only. Quick to load and transcribe, with the lowest memory use in our tests.",
                "About 42 MiB to download; used about 210 MiB of memory. Made more recognition errors than Parakeet, especially with spoken punctuation. CPU and Int8 only.",
            ),
            "whisper-base": (
                "Multilingual alternative",
                "Supports a wider range of languages than Parakeet. A smaller download if Parakeet is too large or does not support your language.",
                "About 104 MiB to download in Int8; used about 430 MiB of memory. Less accurate than Parakeet in our English tests.",
            ),
            "whisper-tiny": (
                "Smallest download",
                "Multilingual, with an Int8 download of about 40 MiB. Available for users who prefer to try it.",
                "Least accurate in our tests: sometimes omitted words or repeated unrelated text. Used about 500 MiB of memory, so a small download does not always mean low memory use.",
            ),
        }
        for key, spec in LOCAL_MODELS.items():
            subtitle, strengths, tradeoffs = details[key]
            group = Adw.PreferencesGroup(title=spec["name"], description=subtitle)
            page.add(group)
            group.add(Adw.ActionRow(title="Why choose it?", subtitle=strengths))
            group.add(Adw.ActionRow(title="Tradeoffs", subtitle=tradeoffs))
        note = Adw.PreferencesGroup(
            description="Figures are for Int8 models in small English tests on one desktop. Memory covers the speech process; results vary with your voice and hardware. Float32 uses more space. Try the microphone test with your own voice. Changing models keeps your other downloads; remove them separately in Preferences."
        )
        page.add(note)
