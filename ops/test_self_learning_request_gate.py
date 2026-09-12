import asyncio
import unittest
from self_learning_request_gate import BackgroundGate

class GateTests(unittest.IsolatedAsyncioTestCase):
 async def test_serializes_multiple_adapter_calls(self):
  gate=BackgroundGate();active=0;peak=0
  async def worker():
   nonlocal active,peak
   async with gate.slot():
    active+=1;peak=max(peak,active);await asyncio.sleep(.005);active-=1
  await asyncio.gather(*(worker() for _ in range(12)))
  self.assertEqual(peak,1);self.assertEqual(gate.pending,0)
 async def test_foreground_defers_background(self):
  gate=BackgroundGate(foreground_grace=.03);gate.foreground()
  start=asyncio.get_running_loop().time()
  async with gate.slot():pass
  self.assertGreaterEqual(asyncio.get_running_loop().time()-start,.025)
 async def test_capacity_and_cancel_release(self):
  gate=BackgroundGate(capacity=1)
  async with gate.slot():
   with self.assertRaises(RuntimeError):
    async with gate.slot():pass
  async def worker():
   async with gate.slot():await asyncio.sleep(10)
  task=asyncio.create_task(worker());await asyncio.sleep(.01);task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertEqual(gate.pending,0)
  async with gate.slot():pass
 async def test_timeout_does_not_leak_slot(self):
  gate=BackgroundGate(wait_timeout=.02,foreground_grace=.2);gate.foreground()
  with self.assertRaises(TimeoutError):
   async with gate.slot():pass
  self.assertFalse(gate.lock.locked());self.assertEqual(gate.pending,0)
 async def test_rate_limit_cooldown_and_propagation(self):
  gate=BackgroundGate(cooldown=.03)
  with self.assertRaises(RuntimeError):
   async with gate.slot():raise RuntimeError('gateway_concurrency_limit')
  start=asyncio.get_running_loop().time()
  async with gate.slot():pass
  self.assertGreaterEqual(asyncio.get_running_loop().time()-start,.025)

if __name__=='__main__':unittest.main()
