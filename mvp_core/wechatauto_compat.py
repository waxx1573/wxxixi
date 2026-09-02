"""Compatibility fixes for message WALs produced by newer Weixin clients."""

import os
import struct
import time

from wechatauto import db as _db


def merge_wal_latest_generation(self, dst, wal_path, key, from_frame):
    """Merge only the newest WAL salt generation.

    Weixin 4.1.13 can leave older generations in the same WAL.  Comparing
    every frame with the WAL header (the upstream behavior) either discards
    all current frames or applies stale pages and fails SQLite integrity.
    """
    if not os.path.exists(dst):
        return 0

    with open(wal_path, "rb") as wal:
        wal.read(self.WAL_HEADER_SZ)
        wal_size = os.path.getsize(wal_path)
        frame_count = max(0, (wal_size - self.WAL_HEADER_SZ) // self.WAL_FRAME_SZ)
        headers = []
        for index in range(frame_count):
            wal.seek(self.WAL_HEADER_SZ + index * self.WAL_FRAME_SZ)
            header = wal.read(24)
            if len(header) != 24:
                break
            headers.append(header)
        if not headers:
            return from_frame
        # WCDB may retain several generations, and they are not guaranteed to
        # be ordered. The generation counter is the first four salt bytes.
        latest_salt = max((header[8:16] for header in headers),
                          key=lambda salt: salt[:4])

    applied = from_frame
    with open(dst, "r+b") as out, open(wal_path, "rb") as wal:
        for index, header in enumerate(headers):
            applied = index + 1
            if index < from_frame or header[8:16] != latest_salt:
                continue
            wal.seek(self.WAL_HEADER_SZ + index * self.WAL_FRAME_SZ + 24)
            page = wal.read(_db.PAGE_SZ)
            if len(page) != _db.PAGE_SZ:
                break
            pgno = struct.unpack(">I", header[:4])[0]
            plain = _db._decrypt_page(key, page, pgno)
            if pgno == 1:
                if len(key) == 48:
                    if plain[16:18] != b"\x01\x01":
                        continue
                elif plain[:16] != b"SQLite format 3\x00":
                    continue
            elif plain[0] not in (0, 2, 5, 10, 13):
                continue
            out.seek((pgno - 1) * _db.PAGE_SZ)
            out.write(plain)
        out.flush()
    return applied


def _input_box_has_text_within_bounds(self, box=None):
    """Detect input text only inside the input field, not the toolbar below."""
    if box is None:
        box = self.get_input_box()
    if not box:
        return False
    x0, y0, x1, y1 = box
    y_end = min(y1, y0 + 200)
    if y_end <= y0 + 10:
        y_end = y1
    rel = (x0 + 20, y0 + 10, x1 - 20, y_end)
    img = self._grab_screen(self._rel_to_screen(rel))
    px = img.load()
    dark = sum(
        1
        for y in range(0, img.size[1], 2)
        for x in range(0, img.size[0], 2)
        if sum(px[x, y]) < 450
    )
    return dark > 20


def _probe_input_box_relaxed(self):
    """Probe the input box accepting the short 40-50px field on Weixin 4.1.x.

    Upstream 1.2.0.3 required h >= 150 and y1 >= 85% of the render height,
    which misses the real input box (510..560 on a 660px-tall window) and
    falls into a 15-second recalibration path.  This copy keeps the thin
    divider check but relaxes the height/position thresholds.
    """
    cx = (self.right_pane_left + self.render_w) // 2
    sx = self.origin_x + cx
    for probe_off in (150, 120, 200, 250, 350, 100, 450):
        probe_y = self.render_h - probe_off
        if probe_y <= int(self.render_h * 0.50):
            continue
        sy = self.origin_y + probe_y
        row_img = self._grab_screen(
            (self.origin_x + self.right_pane_left, sy,
             self.origin_x + self.render_w, sy + 1))
        pxx = row_img.load()
        w = row_img.size[0]
        white = sum(1 for x in range(0, w, 2) if sum(pxx[x, 0]) / 3 > 240)
        if white / max(1, (w + 1) // 2) < 0.8:
            continue
        scan_top = self.origin_y + int(self.render_h * 0.50)
        col = self._grab_screen((sx, scan_top, sx + 1, sy + 1))
        pc = col.load()
        dy_probe = sy - scan_top
        y0 = dy_probe
        while y0 > 0 and sum(pc[0, y0]) / 3 > 240:
            y0 -= 1
        y0 += 1
        y1 = dy_probe
        while y1 < col.size[1] - 1 and sum(pc[0, y1]) / 3 > 240:
            y1 += 1
        g = y0 - 1
        while g >= 0 and sum(pc[0, g]) / 3 <= 240:
            g -= 1
        divider_h = (y0 - 1) - g
        y0_abs = scan_top + y0 - self.origin_y
        y1_abs = scan_top + y1 - self.origin_y
        if (1 <= divider_h <= 4
                and y1_abs - y0_abs >= 40
                and y0_abs >= int(self.render_h * 0.45)
                and y1_abs >= int(self.render_h * 0.84)):
            return (self.right_pane_left, y0_abs, self.render_w, y1_abs)
    return None


def install() -> None:
    """Install the narrow runtime patch before WeChatDB is used."""
    _db.WeChatDB._merge_wal = merge_wal_latest_generation
    from wechatauto import guia as _guia

    def _input_text_known_box(self, text, box=None, fast=False):
        """Input text without re-probing after paste.

        Upstream 1.2.0.3 calls ``_input_box_has_text()`` with no box after
        pasting; the probe expects an all-white empty input box, so the typed
        text makes it fail and triggers a 15-second layout recalibration.
        Reuse the box detected before typing instead.
        """
        if fast and box:
            if self.focus_input(box):
                self.set_clipboard(text)
                self._input.key(_guia.VK_A, ctrl=True)
                self._input.key(_guia.VK_DELETE)
                self._input.key(_guia.VK_V, ctrl=True)
                time.sleep(0.35)
                if self._input_box_has_text(box):
                    return True
            return False
        box = None
        for attempt in range(1, 7):
            box = self.get_input_box()
            if not box:
                _guia.wxlog.debug(f'未探测到输入框（attempt={attempt}），重试')
                time.sleep(0.5)
                continue
            if not self.focus_input(box):
                time.sleep(0.3)
                continue
            self.set_clipboard(text)
            self._input.key(_guia.VK_A, ctrl=True)
            self._input.key(_guia.VK_DELETE)
            self._input.key(_guia.VK_V, ctrl=True)
            time.sleep(0.8)
            if self._input_box_has_text(box):
                self._last_input_box = box
                _guia.wxlog.debug(
                    f'输入文字（剪贴板粘贴）成功，attempt={attempt}'
                )
                return True
            _guia.wxlog.debug(
                f'剪贴板粘贴未确认（attempt={attempt}），重试'
            )
            time.sleep(0.3)
        _guia.wxlog.debug('剪贴板粘贴多次未生效，尝试拼音组合输入')
        self.focus_input(box)
        self._input.key(_guia.VK_A, ctrl=True)
        self._input.key(_guia.VK_DELETE)
        self._input.type_pinyin(self._to_pinyin(text))
        self._input.key(_guia.VK_RETURN)
        time.sleep(0.8)
        return self._input_box_has_text(box)

    _guia.WeChatGUI._input_box_has_text = _input_box_has_text_within_bounds
    _guia.WeChatGUI._probe_input_box = _probe_input_box_relaxed
    _guia.WeChatGUI.input_text = _input_text_known_box
