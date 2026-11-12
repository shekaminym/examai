"""Checks the main-thread mailbox without starting the user interface."""
from __future__ import annotations

import unittest

import numpy as np

from audio_vad import Utterance


from main import DecodeMailbox


class DecodeMailboxTests(unittest.TestCase):
    def test_final_replaces_preview_and_is_selected_first(self):
        preview = Utterance(np.ones(4000, dtype=np.float32), 16000, 7, False)
        final = Utterance(np.ones(6000, dtype=np.float32), 16000, 7, True)
        mailbox = DecodeMailbox()
        mailbox.submit(preview)
        mailbox.submit(final)
        self.assertEqual(mailbox.get(), final)
        mailbox.close(discard_finals=False)
        self.assertIsNone(mailbox.get())

    def test_close_can_drop_an_unwanted_final_after_stop(self):
        final = Utterance(np.ones(6000, dtype=np.float32), 16000, 1, True)
        mailbox = DecodeMailbox()
        mailbox.submit(final)
        mailbox.close(discard_finals=True)
        self.assertIsNone(mailbox.get())


if __name__ == "__main__":
    unittest.main(verbosity=2)
