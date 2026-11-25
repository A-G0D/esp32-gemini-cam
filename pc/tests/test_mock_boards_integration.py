#!/usr/bin/env python3
# Integration test: runs gemini_relay.one_cycle() against REAL HTTP servers
# (mock_boards.py standing in for the S3 cam + C3 HUD) over real sockets.
# Only ask_model (the Gemini/Groq call) is mocked - that's the one external
# paid API in the loop. Everything else - http_get_bytes fetching a real
# JPEG over the wire, maybe_resize decoding it, http_post_text POSTing to a
# real socket and getting a real 200/503 back - is exercised for real. This
# is the piece pure-mock unit tests can't catch: wrong URL scheme, timeout
# plumbing, a non-200 status, wire-level bytes vs. mocked-call assumptions.
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gemini_relay as relay
import mock_boards


class MockRigIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s3_srv, _ = mock_boards.start_server(mock_boards.S3CamHandler, port=0)
        cls.c3_srv, _ = mock_boards.start_server(mock_boards.C3HudHandler, port=0)
        cls.s3_port = cls.s3_srv.server_address[1]
        cls.c3_port = cls.c3_srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.s3_srv.shutdown()
        cls.c3_srv.shutdown()

    def setUp(self):
        mock_boards.S3CamHandler.ready = True
        mock_boards.S3CamHandler.jpeg = None
        mock_boards.C3HudHandler.last_text = None

    def _cfg(self):
        return {
            "s3_capture_url": "http://127.0.0.1:%d/capture" % self.s3_port,
            "c3_msg_url": "http://127.0.0.1:%d/msg" % self.c3_port,
            "resize_width": None,
            "display_template": "{target}",
            "gemini_api_key": "unused",
        }

    def test_full_cycle_over_real_sockets(self):
        # Gemini/Groq is the only thing still mocked - everything else is a
        # real GET/POST round trip against mock_boards' HTTP servers.
        with mock.patch.object(relay, "ask_model", return_value={"target": "cat"}):
            data, text = relay.one_cycle(self._cfg())

        self.assertEqual(data, {"target": "cat"})
        self.assertEqual(text, "cat")
        # prove the POST actually landed on the C3 mock's socket, not just
        # that our code believes it sent something.
        self.assertEqual(mock_boards.C3HudHandler.last_text, "cat")

    def test_s3_not_ready_yet_surfaces_as_http_error(self):
        # real board returns 503 until it's caught a sharp frame - confirm
        # the relay's urllib call actually raises on that, over a real
        # socket, the way main()'s except urllib.error.URLError expects.
        mock_boards.S3CamHandler.ready = False
        import urllib.error
        with mock.patch.object(relay, "ask_model", return_value={"target": "cat"}):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                relay.one_cycle(self._cfg())
        self.assertEqual(ctx.exception.code, 503)
        # and nothing got pushed to the HUD for a cycle that never got a frame
        self.assertIsNone(mock_boards.C3HudHandler.last_text)


if __name__ == "__main__":
    unittest.main()
