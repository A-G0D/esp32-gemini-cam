import io, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gemini_relay as relay
from PIL import Image


class ResizeTests(unittest.TestCase):
    def test_downscales_keeps_aspect_and_jpeg(self):
        buf = io.BytesIO(); Image.new("RGB", (1600, 1200), (9, 99, 199)).save(buf, "JPEG")
        out = Image.open(io.BytesIO(relay.maybe_resize(buf.getvalue(), 1024)))
        self.assertEqual((out.format, out.size), ("JPEG", (1024, 768)))

    def test_small_frame_not_upscaled(self):
        buf = io.BytesIO(); Image.new("RGB", (320, 240)).save(buf, "JPEG")
        self.assertEqual(Image.open(io.BytesIO(relay.maybe_resize(buf.getvalue(), 1024))).size, (320, 240))


if __name__ == "__main__":
    unittest.main()
