from __future__ import absolute_import

from kombu.async import (
    Hub,
    repr_flag,
    _rcb,
    READ, WRITE, ERR
)
from kombu.async.semaphore import DummyLock, LaxBoundedSemaphore

from mock import Mock, call, patch

from celery.five import range
from celery.tests.case import Case


class File(object):

    def __init__(self, fd):
        self.fd = fd

    def fileno(self):
        return self.fd

    def __eq__(self, other):
        if isinstance(other, File):
            return self.fd == other.fd
        return NotImplemented

    def __hash__(self):
        return hash(self.fd)


class test_DummyLock(Case):

    def test_context(self):
        mutex = DummyLock()
        with mutex:
            pass


class test_LaxBoundedSemaphore(Case):

    def test_acquire_release(self):
        x = LaxBoundedSemaphore(2)

        c1 = Mock()
        x.acquire(c1, 1)
        self.assertEqual(x.value, 1)
        c1.assert_called_with(1)

        c2 = Mock()
        x.acquire(c2, 2)
        self.assertEqual(x.value, 0)
        c2.assert_called_with(2)

        c3 = Mock()
        x.acquire(c3, 3)
        self.assertEqual(x.value, 0)
        self.assertFalse(c3.called)

        x.release()
        self.assertEqual(x.value, 1)
        c3.assert_called_with(3)

    def test_bounded(self):
        x = LaxBoundedSemaphore(2)
        for i in range(100):
            x.release()
        self.assertEqual(x.value, 2)

    def test_grow_shrink(self):
        x = LaxBoundedSemaphore(1)
        self.assertEqual(x.initial_value, 1)
        cb1 = Mock()
        x.acquire(cb1, 1)
        cb1.assert_called_with(1)
        self.assertEqual(x.value, 0)

        cb2 = Mock()
        x.acquire(cb2, 2)
        self.assertFalse(cb2.called)
        self.assertEqual(x.value, 0)

        cb3 = Mock()
        x.acquire(cb3, 3)
        self.assertFalse(cb3.called)

        x.grow(2)
        cb2.assert_called_with(2)
        cb3.assert_called_with(3)
        self.assertEqual(x.value, 3)
        self.assertEqual(x.initial_value, 3)

        self.assertFalse(x._waiting)
        x.grow(3)
        for i in range(x.initial_value):
            self.assertTrue(x.acquire(Mock()))
        self.assertFalse(x.acquire(Mock()))
        x.clear()

        x.shrink(3)
        for i in range(x.initial_value):
            self.assertTrue(x.acquire(Mock()))
        self.assertFalse(x.acquire(Mock()))
        self.assertEqual(x.value, 0)

        for i in range(100):
            x.release()
        self.assertEqual(x.value, x.initial_value)

    def test_clear(self):
        x = LaxBoundedSemaphore(10)
        for i in range(11):
            x.acquire(Mock())
        self.assertTrue(x._waiting)
        self.assertEqual(x.value, 0)

        x.clear()
        self.assertFalse(x._waiting)
        self.assertEqual(x.value, x.initial_value)


class test_Hub(Case):

    def test_repr_flag(self):
        self.assertEqual(repr_flag(READ), 'R')
        self.assertEqual(repr_flag(WRITE), 'W')
        self.assertEqual(repr_flag(ERR), '!')
        self.assertEqual(repr_flag(READ | WRITE), 'RW')
        self.assertEqual(repr_flag(READ | ERR), 'R!')
        self.assertEqual(repr_flag(WRITE | ERR), 'W!')
        self.assertEqual(repr_flag(READ | WRITE | ERR), 'RW!')

    def test_repr_callback_rcb(self):

        def f():
            pass

        self.assertEqual(_rcb(f), f.__name__)
        self.assertEqual(_rcb('foo'), 'foo')

    @patch('kombu.async.hub.poll')
    def test_start_stop(self, poll):
        hub = Hub()
        hub.start()
        poll.assert_called_with()

        hub.stop()
        hub.poller.close.assert_called_with()

    def test_init(self):
        hub = Hub()
        cb1 = Mock()
        cb2 = Mock()
        hub.on_init.extend([cb1, cb2])

        hub.init()
        cb1.assert_called_with(hub)
        cb2.assert_called_with(hub)

    def test_fire_timers(self):
        hub = Hub()
        hub.timer = Mock()
        hub.timer._queue = []
        self.assertEqual(hub.fire_timers(min_delay=42.324,
                                         max_delay=32.321), 32.321)

        hub.timer._queue = [1]
        hub.scheduler = iter([(3.743, None)])
        self.assertEqual(hub.fire_timers(), 3.743)

        e1, e2, e3 = Mock(), Mock(), Mock()
        entries = [e1, e2, e3]

        reset = lambda: [m.reset() for m in [e1, e2, e3]]

        def se():
            while 1:
                while entries:
                    yield None, entries.pop()
                yield 3.982, None
        hub.scheduler = se()

        self.assertEqual(hub.fire_timers(max_timers=10), 3.982)
        for E in [e3, e2, e1]:
            E.assert_called_with()
        reset()

        entries[:] = [Mock() for _ in range(11)]
        keep = list(entries)
        self.assertEqual(hub.fire_timers(max_timers=10, min_delay=1.13), 1.13)
        for E in reversed(keep[1:]):
            E.assert_called_with()
        reset()
        self.assertEqual(hub.fire_timers(max_timers=10), 3.982)
        keep[0].assert_called_with()

    def test_fire_timers_raises(self):
        hub = Hub()
        eback = Mock()
        eback.side_effect = KeyError('foo')
        hub.timer = Mock()
        hub.scheduler = iter([(0, eback)])
        with self.assertRaises(KeyError):
            hub.fire_timers(propagate=(KeyError, ))

        eback.side_effect = ValueError('foo')
        hub.scheduler = iter([(0, eback)])
        with patch('celery.worker.hub.logger') as logger:
            with self.assertRaises(StopIteration):
                hub.fire_timers()
            self.assertTrue(logger.error.called)

    def test_add_raises_ValueError(self):
        hub = Hub()
        hub._add = Mock()
        hub._add.side_effect = ValueError()
        hub._discard = Mock()
        hub.add([2], Mock(), READ)
        hub._discard.assert_called_with(2)

    def test_repr_active(self):
        hub = Hub()
        hub.readers = {1: Mock(), 2: Mock()}
        hub.writers = {3: Mock(), 4: Mock()}
        for value in list(hub.readers.values()) + list(hub.writers.values()):
            value.__name__ = 'mock'
        self.assertTrue(hub.repr_active())

    def test_repr_events(self):
        hub = Hub()
        hub.readers = {6: Mock(), 7: Mock(), 8: Mock()}
        hub.writers = {9: Mock()}
        for value in list(hub.readers.values()) + list(hub.writers.values()):
            value.__name__ = 'mock'
        self.assertTrue(hub.repr_events([
            (6, READ),
            (7, ERR),
            (8, READ | ERR),
            (9, WRITE),
            (10, 13213),
        ]))

    def test_callback_for(self):
        hub = Hub()
        reader, writer = Mock(), Mock()
        hub.readers = {6: reader}
        hub.writers = {7: writer}

        self.assertEqual(hub._callback_for(6, READ), reader)
        self.assertEqual(hub._callback_for(7, WRITE), writer)
        with self.assertRaises(KeyError):
            hub._callback_for(6, WRITE)
        self.assertEqual(hub._callback_for(6, WRITE, 'foo'), 'foo')

    def test_update_readers(self):
        hub = Hub()
        P = hub.poller = Mock()

        read_A = Mock()
        read_B = Mock()
        hub.update_readers({10: read_A, File(11): read_B})

        P.register.assert_has_calls([
            call(10, hub.READ | hub.ERR),
            call(File(11), hub.READ | hub.ERR),
        ], any_order=True)

        self.assertIs(hub.readers[10], read_A)
        self.assertIs(hub.readers[11], read_B)

        hub.remove(10)
        self.assertNotIn(10, hub.readers)
        hub.remove(File(11))
        self.assertNotIn(11, hub.readers)
        P.unregister.assert_has_calls([
            call(10), call(11),
        ])

    def test_can_remove_unknown_fds(self):
        hub = Hub()
        hub.poller = Mock()
        hub.remove(30)
        hub.remove(File(301))

    def test_remove__unregister_raises(self):
        hub = Hub()
        hub.poller = Mock()
        hub.poller.unregister.side_effect = OSError()

        hub.remove(313)

    def test_update_writers(self):
        hub = Hub()
        P = hub.poller = Mock()

        write_A = Mock()
        write_B = Mock()
        hub.update_writers({20: write_A, File(21): write_B})

        P.register.assert_has_calls([
            call(20, hub.WRITE),
            call(File(21), hub.WRITE),
        ], any_order=True)

        self.assertIs(hub.writers[20], write_A)
        self.assertIs(hub.writers[21], write_B)

        hub.remove(20)
        self.assertNotIn(20, hub.writers)
        hub.remove(File(21))
        self.assertNotIn(21, hub.writers)
        P.unregister.assert_has_calls([
            call(20), call(21),
        ])

    def test_enter__exit(self):
        hub = Hub()
        P = hub.poller = Mock()
        hub.init = Mock()

        on_close = Mock()
        hub.on_close.append(on_close)

        hub.init()
        try:
            hub.init.assert_called_with()

            read_A = Mock()
            read_B = Mock()
            hub.update_readers({10: read_A, File(11): read_B})
            write_A = Mock()
            write_B = Mock()
            hub.update_writers({20: write_A, File(21): write_B})
            self.assertTrue(hub.readers)
            self.assertTrue(hub.writers)
        finally:
            hub.close()
        self.assertFalse(hub.readers)
        self.assertFalse(hub.writers)

        P.unregister.assert_has_calls([
            call(10), call(11), call(20), call(21),
        ], any_order=True)

        on_close.assert_called_with(hub)

    def test_scheduler_property(self):
        hub = Hub(timer=[1, 2, 3])
        self.assertEqual(list(hub.scheduler), [1, 2, 3])
