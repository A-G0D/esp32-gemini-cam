#!/usr/bin/env python3
# Unit tests for gemini_relay.py's pure logic: JSON extraction, display
# formatting, and resize. Run with:  python -m pytest pc/tests -q
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gemini_relay as relay


class ParseJsonTextTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(relay._parse_json_text('{"a": 1}'), {"a": 1})

    def test_fenced_with_json_tag(self):
        text = "```json\n{\"a\": 1}\n```"
        self.assertEqual(relay._parse_json_text(text), {"a": 1})

    def test_fenced_without_tag(self):
        text = "```\n{\"a\": 1}\n```"
        self.assertEqual(relay._parse_json_text(text), {"a": 1})

    def test_malformed_json_raises(self):
        # a genuinely broken response must surface, not be swallowed here -
        # the caller (one_cycle -> main's loop) is what decides to recover.
        with self.assertRaises(json.JSONDecodeError):
            relay._parse_json_text("not json at all")


class ToDisplayTests(unittest.TestCase):
    def test_template_fills_known_keys(self):
        out = relay.to_display({"target": "cat", "acquired": True}, "{target}\nACQ {acquired}")
        self.assertEqual(out, "cat\nACQ True")

    def test_template_missing_key_shows_placeholder(self):
        out = relay.to_display({"target": "cat"}, "{target}\nconf {conf}")
        self.assertEqual(out, "cat\nconf ?")

    def test_no_template_falls_back_to_key_value_lines_capped_at_4(self):
        data = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
        out = relay.to_display(data, None)
        self.assertEqual(len(out.split("\n")), 4)

    def test_non_dict_response_does_not_crash(self):
        # regression: a model that ignores the requested JSON-object shape
        # (schema enforcement isn't guaranteed on every provider) used to
        # blow up in to_display() with AttributeError on `.items()`.
        self.assertEqual(relay.to_display([1, 2, 3], "{target}"), "[1, 2, 3]")
        self.assertEqual(relay.to_display("plain text", "{target}"), "plain text")
        self.assertEqual(relay.to_display(None, "{target}"), "None")


class MaybeResizeTests(unittest.TestCase):
    def _jpeg_bytes(self, w, h):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (w, h), color="red").save(buf, "JPEG")
        return buf.getvalue()

    def test_no_width_returns_unchanged(self):
        raw = self._jpeg_bytes(100, 100)
        self.assertEqual(relay.maybe_resize(raw, None), raw)

    def test_downscales_wider_images(self):
        from PIL import Image
        raw = self._jpeg_bytes(200, 100)
        out = relay.maybe_resize(raw, 50)
        im = Image.open(io.BytesIO(out))
        self.assertEqual(im.width, 50)
        self.assertEqual(im.height, 25)


class OneCycleTests(unittest.TestCase):
    def test_full_cycle_pushes_formatted_text_to_hud(self):
        cfg = {
            "s3_capture_url": "http://s3cam.local/capture",
            "c3_msg_url": "http://c3hud.local/msg",
            "resize_width": None,
            "display_template": "{target}",
            "gemini_api_key": "k",
        }
        with mock.patch.object(relay, "http_get_bytes", return_value=b"jpegbytes") as m_get, \
             mock.patch.object(relay, "ask_model", return_value={"target": "cat"}) as m_ask, \
             mock.patch.object(relay, "http_post_text") as m_post:
            data, text = relay.one_cycle(cfg)
        m_get.assert_called_once_with(cfg["s3_capture_url"])
        m_ask.assert_called_once()
        m_post.assert_called_once_with(cfg["c3_msg_url"], "cat")
        self.assertEqual(data, {"target": "cat"})
        self.assertEqual(text, "cat")

    def test_non_dict_model_response_still_pushes_something(self):
        # end-to-end version of the ToDisplayTests regression: a malformed
        # (non-dict) model response must not blow up one_cycle().
        cfg = {
            "s3_capture_url": "http://s3cam.local/capture",
            "c3_msg_url": "http://c3hud.local/msg",
            "resize_width": None,
            "display_template": "{target}",
        }
        with mock.patch.object(relay, "http_get_bytes", return_value=b"jpegbytes"), \
             mock.patch.object(relay, "ask_model", return_value="not an object"), \
             mock.patch.object(relay, "http_post_text") as m_post:
            data, text = relay.one_cycle(cfg)
        m_post.assert_called_once_with(cfg["c3_msg_url"], "not an object")
        self.assertEqual(text, "not an object")


if __name__ == "__main__":
    unittest.main()
