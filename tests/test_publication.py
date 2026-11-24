import tempfile
import unittest
from pathlib import Path

from radar.store import Invalid
from radar.verify import verify


class ArtifactGate(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name)
        self.index = self.out / "index.html"

    def page(self, body):
        self.index.write_text('<!doctype html><html lang="es"><title>RADAR 9</title>' + body + "</html>",
                              encoding="utf-8")

    def test_missing_base_asset_and_anchor_block_publication(self):
        for link in ("/otra/", "/radar9/perdida/", "#ausente"):
            with self.subTest(link=link), self.assertRaises(Invalid):
                self.page(f'<a href="{link}">Abrir</a>')
                verify(self.out)
        self.page('<a href="/radar9/#lector">Leer</a><main id="lector">Texto</main>')
        self.assertEqual(verify(self.out), 1)

    def test_artifact_must_not_ship_dotfiles_or_external_scripts(self):
        self.page('<script src="https://example.org/widget.js"></script>')
        with self.assertRaises(Invalid):
            verify(self.out)
        self.page("<main>Texto</main>")
        (self.out / ".env").touch()
        with self.assertRaises(Invalid):
            verify(self.out)
