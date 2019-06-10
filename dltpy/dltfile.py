# This file is part of pydlt
# Copyright 2019  Vladimir Shapranov
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from pathlib import Path
from dltpy.gen.stored_message import StoredMessage
from dltpy.gen.payload_item import PayloadItem
from binascii import hexlify
import dltpy.native.native_dltreader
import logging
import io
import typing
import os

logger = logging.getLogger(__name__)


def hex(v):
    return hexlify(v).decode()

def get_value(p: PayloadItem):
    for i in 'str', 'uint', 'sint', 'float', 'bool':
        if hasattr(p, i):
            return getattr(p, i)
    return None

def parse_payload(pl: bytes):
    s = io.BytesIO(pl)
    ret = []
    while s.tell() < len(pl):
        start = s.tell()
        item: PayloadItem = PayloadItem.from_io(s)
        value = get_value(item)
        if value is None:
            logger.error("Can't parse payload %s at offset %d", hex(pl), start)
            return None
        if isinstance(value, PayloadItem.SizedString):
            value = value.data

        # strings are null-terminated in dlt, no need for this null in python
        if item.plt.strg:
            assert value[-1] == 0
            value = value[:-1]
        ret.append(value)
    return ret

class DltMessage:
    def __init__(self, raw: dltpy.native.native_dltreader.DltReader, raw_data: bytes = None, native=False):
        self.app: str = None
        self.ctx: str = None
        self.ts: float = None
        self.date: float = None
        self.verbose: bool = None
        self.raw_payload: bytes = None
        self._payload_cache = None
        self._raw_data = raw_data
        self.loadn(raw)

    def loadn(self, raw: dltpy.native.native_dltreader.DltReader):
        ehdr = raw.get_extended()
        bhdr = raw.get_basic()
        shdr = raw.get_storage()
        if ehdr:
            self.app = ehdr['app'].replace(b'\0', b'').decode()
            self.ctx = ehdr['ctx'].replace(b'\0', b'').decode()
            self.verbose = ehdr['verbose']

        ts = bhdr.get('tmsp', None)
        if ts:
            self.ts = 1e-4 * ts

        self.date = shdr['ts_sec'] + shdr['ts_msec'] * 1e-3
        self.raw_payload = bytes(raw.get_payload())

        # todo memory view
        self._raw_data = bytes(raw.get_message())

    def __str__(self):
        return 'DltMsg(%s,%s:%s,)' % (self.ts, self.app, self.ctx)

    def match(self, filters: typing.List[typing.Tuple[str, str]]):
        for app, ctx in filters:
            if (app is None or self.app == app) and (ctx is None or self.ctx == ctx):
                return True
        return False

    @property
    def payload(self):
        if self._payload_cache is None:
            self._payload_cache = parse_payload(self.raw_payload)
        return self._payload_cache

    @property
    def human_friendly_payload(self):
        pl = self.payload
        if pl and len(pl) == 1 and isinstance(pl[0], bytes):
            pl = pl[0]
            try:
                pl = pl.decode()
                if len(pl) > 1 and pl[-1] == '\0':
                    pl = pl[:-1]
                pl = pl.strip()
            except UnicodeDecodeError:
                pass
        return pl


class DltReader:
    def __init__(self, fd, filters=None, capure_raw=False, expect_storage_header=True):
        #TODO optional capture_raw
        filters = filters or []
        self.fd = fd
        self.capture_raw = capure_raw
        self.rdr = dltpy.native.native_dltreader.DltReader(expect_storage_header, filters)

    def get_next_message(self):
        while not self.rdr.read():
            buf = self.rdr.get_buffer()
            logger.warning("Buffer to be read: %d", len(buf))
            l = self.fd.readinto(buf)
            if not l:
                logger.warning("readinto returned 0, EOF")
                return None
            self.rdr.update_buffer(l)
        m = DltMessage(self.rdr, None, True)
        self.rdr.consume_message()
        return m


    def __iter__(self) -> DltMessage:
        while True:
            ret = self.get_next_message()
            if ret is None:
                return
            if not ret.verbose:
                continue
            yield ret


class DltFile:
    def __init__(self, fn: Path, filters: typing.List[typing.Tuple[str, str]] = None, capture_raw=False):
        if not isinstance(fn, Path):
            fn = Path(fn)
        self._fn = fn
        self._f_len = fn.stat().st_size
        self.fd = fn.open('rb')
        self.filters = filters
        self._capture_raw = capture_raw


    def get_next_message(self) -> DltMessage:
        ret = None
        while self.fd.tell() != self._f_len:
            start_offset = self.fd.tell()
            try:
                sm = StoredMessage.from_io(self.fd)
            except:
                logger.exception("Can't parse message at offset %d, will try to recover", start_offset)
                start_offset += 1
                while start_offset < self._f_len:
                    self.fd.seek(start_offset)
                    buf = self.fd.read(4)
                    if buf == b'DLT\x01':
                        logger.warning("DLT signature found at offset %d, continue", start_offset)
                        self.fd.seek(start_offset)
                        break
                    else:
                        start_offset += 1
                continue
            raw_data = None
            if self._capture_raw:
                end_offset = self.fd.tell()
                self.fd.seek(start_offset)
                raw_data = self.fd.read(end_offset - start_offset)
                self.fd.seek(end_offset)
            msg = DltMessage(sm, raw_data)
            if msg.verbose:
                if not self.filters is None:
                    if not msg.match(self.filters):
                        continue
                ret = msg
                break

        return ret

    def __iter__(self) -> DltMessage:
        while True:
            ret = self.get_next_message()
            if ret is None:
                return
            yield ret

if True:
    DltFile = DltReader
