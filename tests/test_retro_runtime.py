import unittest

import numpy as np

from ui.widgets.retro import _StreamingS16Resampler, _advance_frame_debt


class FramePacingTests(unittest.TestCase):
    def test_long_stall_drops_old_debt_instead_of_fast_forwarding(self):
        due, remaining = _advance_frame_debt(0.0, 2.0, 60.0, max_catchup=4)

        self.assertEqual(due, 4)
        self.assertLess(remaining, 1.0)


class StreamingResamplerTests(unittest.TestCase):
    def test_irregular_chunks_match_one_continuous_stream(self):
        source_rate, target_rate = 65536, 48000
        t = np.arange(source_rate * 2, dtype=np.float64) / source_rate
        mono = (np.sin(2 * np.pi * 997 * t) * 12000).astype(np.int16)
        stereo = np.column_stack((mono, mono)).reshape(-1)

        whole = _StreamingS16Resampler(source_rate, target_rate)
        expected = np.frombuffer(whole.process(stereo.tobytes()), dtype=np.int16)

        chunked = _StreamingS16Resampler(source_rate, target_rate)
        pieces = []
        cursor = 0
        for frames in (148, 616, 1096, 1100) * 50:
            end = min(cursor + frames * 2, stereo.size)
            if end <= cursor:
                break
            pieces.append(chunked.process(stereo[cursor:end].tobytes()))
            cursor = end
        if cursor < stereo.size:
            pieces.append(chunked.process(stereo[cursor:].tobytes()))
        actual = np.frombuffer(b"".join(pieces), dtype=np.int16)

        self.assertLessEqual(abs(actual.size - expected.size), 2)
        count = min(actual.size, expected.size)
        self.assertLess(float(np.max(np.abs(
            actual[:count].astype(np.int32) - expected[:count].astype(np.int32)
        ))), 4.0)


if __name__ == "__main__":
    unittest.main()
