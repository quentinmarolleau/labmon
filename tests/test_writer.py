import threading
import time

from labmon.writer import PointWriter


class FakeClient:
    def __init__(self):
        self.batches = []
        self.closed = threading.Event()

    def write(self, batch):
        self.batches.append(list(batch))

    def close(self):
        self.closed.set()


def test_write_does_not_block_the_caller():
    client = FakeClient()
    writer = PointWriter(client)

    start = time.monotonic()
    for i in range(50):
        writer.write(i)
    elapsed = time.monotonic() - start

    writer.close()
    assert elapsed < 0.1


def test_close_flushes_all_queued_points_and_closes_client():
    client = FakeClient()
    writer = PointWriter(client)

    for i in range(10):
        writer.write(i)
    writer.close()

    written = [point for batch in client.batches for point in batch]
    assert written == list(range(10))
    assert client.closed.is_set()
