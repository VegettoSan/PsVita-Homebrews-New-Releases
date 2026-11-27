import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from discovery_vpk_artwork import extract_artwork, inspect_png, media_proposal
from discover_homebrew import media_image_fallback


def png(color, size=(128, 128)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def vpk(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for path, data in files.items():
            z.writestr(path, data)
    buf.seek(0)
    return buf


class ArtworkTests(unittest.TestCase):
    def test_native_icon_and_backgrounds(self):
        icon = png("red")
        pic = png("blue", (960, 544))
        bg = png("green", (840, 500))
        files = {"sce_sys/icon0.png": icon, "sce_sys/pic0.png": pic,
                 "sce_sys/livearea/contents/bg.png": bg}
        with tempfile.TemporaryDirectory() as directory:
            result = extract_artwork(vpk(files), "a"*64, directory)
            self.assertEqual(result["icon"]["sha256"], hashlib.sha256(icon).hexdigest())
            self.assertEqual([s["kind"] for s in result["screenshots"]], ["pic0", "livearea_background"])
            self.assertEqual(result["screenshots"][0]["width"], 960)
            for item in [result["icon"], *result["screenshots"]]:
                self.assertEqual(hashlib.sha256((Path(directory)/item["artifact_file"]).read_bytes()).hexdigest(), item["sha256"])

    def test_no_duplicates_and_invalid_images_do_not_block(self):
        image = png("red")
        files = {"sce_sys/icon0.png": b"not an image", "sce_sys/pic0.png": image,
                 "sce_sys/livearea/contents/bg.png": image,
                 "sce_sys/livearea/contents/bg0.png": b"broken"}
        with tempfile.TemporaryDirectory() as directory:
            result = extract_artwork(vpk(files), "b"*64, directory)
            self.assertIsNone(result["icon"])
            self.assertEqual(len(result["screenshots"]), 1)
            self.assertEqual(len(result["skipped"]), 2)

    def test_template_background_custom_name_not_gate(self):
        xml = b"<livearea><livearea-background><image>custom.png</image></livearea-background><gate><image>gate.png</image></gate></livearea>"
        files = {"sce_sys/livearea/contents/template.xml": xml,
                 "sce_sys/livearea/contents/custom.png": png("blue"),
                 "sce_sys/livearea/contents/gate.png": png("red")}
        with tempfile.TemporaryDirectory() as directory:
            result = extract_artwork(vpk(files), "c"*64, directory)
            self.assertEqual([s["path"] for s in result["screenshots"]],
                             ["sce_sys/livearea/contents/custom.png"])

    def test_template_rejects_external_or_traversal_paths(self):
        xml = b"<livearea><livearea-background><image>../secret.png</image><image>https://example.org/a.png</image></livearea-background></livearea>"
        with tempfile.TemporaryDirectory() as directory:
            result = extract_artwork(vpk({"sce_sys/livearea/contents/template.xml": xml}), "d"*64, directory)
            self.assertEqual(result["screenshots"], [])

    def test_png_dimension_limit(self):
        self.assertIsNone(inspect_png(png("red", (3000, 3000))))
        self.assertIsNone(inspect_png(b"\x89PNG\r\n\x1a\nbroken"))

    def test_existing_screenshots_take_priority_and_icon_fallback(self):
        art = {"icon": {"path": "sce_sys/icon0.png"}, "screenshots": [{"path": "sce_sys/pic0.png"}]}
        result = media_proposal({"screenshots": ["https://example.org/gameplay.png"]}, art, media_image_fallback)
        self.assertEqual(result["fallback_screenshots"], [])
        self.assertEqual(result["icon"], art["icon"])
        result = media_proposal({"icon": "https://example.org/icon.png"}, art, media_image_fallback)
        self.assertIsNone(result["icon"])
        self.assertEqual(result["fallback_screenshots"], art["screenshots"])
        self.assertTrue(result["requires_public_url"])

    def test_max_five_fallback_images(self):
        art = {"icon": None, "screenshots": [{"path": str(i)} for i in range(8)]}
        self.assertEqual(len(media_proposal({}, art, media_image_fallback)["fallback_screenshots"]), 5)


if __name__ == "__main__":
    unittest.main()
