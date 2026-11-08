# SPDX-License-Identifier: GPL-3.0-only
"""Desktop-role resolution must honor preferences without executing speech."""

import unittest
from unittest.mock import Mock, patch

from dictadesk.core.application_roles import APPLICATION_ROLES, application_role
from dictadesk.core.commands import recognize, registry
from dictadesk.core.voice_commands import match_applications
from dictadesk.desktop.applications import configured_application, preferred_applications


class App:
    def __init__(
        self, ident, name="Application", command="example %U", hidden=False, nodisplay=False
    ):
        self.ident, self.name, self.command = ident, name, command
        self.hidden, self.nodisplay = hidden, nodisplay

    def get_id(self):
        return self.ident

    def get_display_name(self):
        return self.name

    get_name = get_display_name

    def get_commandline(self):
        return self.command

    def get_executable(self):
        return self.command.split()[0]

    def get_is_hidden(self):
        return self.hidden

    def get_nodisplay(self):
        return self.nodisplay


class ApplicationDefaultsTests(unittest.TestCase):
    def test_every_role_and_alias_is_in_the_actual_command_registry(self):
        registered = {spec.ident: spec for spec in registry()}
        for role in APPLICATION_ROLES:
            canonical = tuple(role.aliases[0].split())
            spec = registered["open-" + role.ident]
            self.assertIn("Preferred Applications", spec.description)
            for phrase in role.spoken_names:
                query = tuple(phrase.split())
                self.assertEqual(application_role(query), role)
                for verb in ("open", "launch"):
                    self.assertEqual(recognize(verb + " " + phrase), ("open", canonical))
        self.assertIsNone(application_role(("browser", "and", "run", "something")))

    def test_mime_roles_use_cinnamon_associations_and_follow_changes(self):
        first = App("first.desktop")
        second = App("second.desktop")
        for role in APPLICATION_ROLES:
            if not role.content_types:
                continue
            lookup = Mock(side_effect=[first, second])
            self.assertEqual(
                preferred_applications(role, [first, second], default_lookup=lookup), [first]
            )
            self.assertEqual(
                preferred_applications(role, [first, second], default_lookup=lookup), [second]
            )
            self.assertEqual(lookup.call_args.args, (role.content_types[0], False))

    def test_missing_default_does_not_guess_an_installed_browser(self):
        role = application_role(("browser",))
        for returned in (
            None,
            App("not-installed.desktop"),
            App("deleted.desktop", hidden=True),
        ):
            with self.assertRaisesRegex(ValueError, "No default web browser"):
                preferred_applications(
                    role,
                    [App("firefox.desktop"), App("deleted.desktop", hidden=True)],
                    default_lookup=lambda *_: returned,
                )

    def test_mime_fallback_and_registered_non_menu_handler(self):
        role = application_role(("browser",))
        handler = App("handler.desktop", nodisplay=True)
        lookup = Mock(side_effect=[None, handler])
        self.assertEqual(
            preferred_applications(role, [handler], default_lookup=lookup), [handler]
        )
        self.assertEqual(lookup.call_args.args, ("x-scheme-handler/https", False))

    def test_terminal_and_calculator_use_configured_desktop_entry(self):
        wrong = App("org.gnome.Terminal.desktop", "Terminal", "gnome-terminal")
        selected = App("preferred.desktop", "Preferred", "preferred-terminal --new-window %U")
        for name in ("terminal", "calculator"):
            role = application_role((name,))
            with patch(
                "subprocess.Popen",
                side_effect=AssertionError("Do not execute settings or speech"),
            ):
                matches = preferred_applications(
                    role,
                    [wrong, selected],
                    setting_lookup=lambda _: "/usr/bin/preferred-terminal --new-window",
                )
            self.assertEqual(matches, [selected])

    def test_configured_command_does_not_launch_an_unrelated_terminal_or_hidden_helper(self):
        role = application_role(("terminal",))
        apps = [
            App("org.gnome.Terminal.desktop", command="gnome-terminal"),
            App("hidden.desktop", command="custom-terminal --special", nodisplay=True),
        ]
        for setting in (
            "missing-terminal",
            "custom-terminal --special",
            "gnome-terminal; touch /tmp/unsafe",
            "'bad quote",
        ):
            with self.assertRaisesRegex(ValueError, "configured terminal"):
                preferred_applications(role, apps, setting_lookup=lambda _: setting)
        self.assertEqual(
            preferred_applications(role, apps, setting_lookup=lambda _: None), [apps[0]]
        )

    def test_executable_and_desktop_ids_resolve_real_names_without_menu_labels(self):
        nemo = App("nemo.desktop", "Files", "/usr/bin/nemo %U")
        nautilus = App("org.gnome.Nautilus.desktop", "Files", "flatpak run org.gnome.Nautilus")
        xed = App("org.x.editor.desktop", "Text Editor", "/usr/bin/xed %U")
        helper = App("nemo-autostart.desktop", "Nemo", "nemo-desktop", nodisplay=True)
        apps = [helper, nautilus, xed, nemo]
        for query, result in (("nemo", nemo), ("nautilus", nautilus), ("xed", xed)):
            self.assertEqual(match_applications((query,), apps), [result])
        self.assertEqual(match_applications(("flatpak",), apps), [])
        # Even when the executable is a wrapper, the real desktop ID is usable.
        self.assertEqual(
            match_applications(
                ("nemo",), [App("nemo.desktop", "Files", "env WRAPPER=1 launch-files")]
            )[0].get_id(),
            "nemo.desktop",
        )

    def test_setting_reader_prefers_active_desktop_and_never_starts_a_process(self):
        source, schema, settings = Mock(), Mock(), Mock()
        schema.has_key.return_value = True
        source.lookup.return_value = schema
        settings.get_string.return_value = "chosen-terminal"
        for desktop, first in (
            ("X-Cinnamon", "org.cinnamon.desktop.default-applications.terminal"),
            ("GNOME", "org.gnome.desktop.default-applications.terminal"),
            ("MATE", "org.mate.applications-terminal"),
        ):
            source.reset_mock()
            with (
                patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": desktop}),
                patch(
                    "dictadesk.desktop.applications.Gio.SettingsSchemaSource.get_default",
                    return_value=source,
                ),
                patch(
                    "dictadesk.desktop.applications.Gio.Settings.new_full",
                    return_value=settings,
                ),
                patch("subprocess.Popen", side_effect=AssertionError("No subprocesses")),
            ):
                self.assertEqual(configured_application("terminal"), "chosen-terminal")
            self.assertEqual(source.lookup.call_args.args, (first, True))

    def test_other_desktops_do_not_inherit_cinnamon_settings(self):
        with (
            patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "KDE"}),
            patch(
                "dictadesk.desktop.applications.Gio.SettingsSchemaSource.get_default"
            ) as source,
        ):
            self.assertIsNone(configured_application("terminal"))
            source.assert_not_called()
