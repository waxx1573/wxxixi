"""Conversation continuation eligibility; no model calls or persistent message data."""
import time
from collections import OrderedDict

class FollowupWindow:
    def __init__(self, ttl=300, capacity=512, clock=time.monotonic):
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self.replies = OrderedDict()

    def _key(self, event):
        group = str(event.get_group_id() or "")
        sender = str(event.get_sender_id() or "")
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        return (origin, group, sender) if origin and group and sender else None

    def note_reply(self, event):
        key = self._key(event)
        if key:
            self.replies[key] = self.clock()
            self.replies.move_to_end(key)
            while len(self.replies) > self.capacity:
                self.replies.popitem(last=False)

    def eligible(self, event):
        key = self._key(event)
        stamp = self.replies.get(key)
        if stamp is None:
            return False
        if self.clock() - stamp > self.ttl:
            del self.replies[key]
            return False
        return True

followup_window = FollowupWindow()
