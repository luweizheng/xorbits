# Copyright 2022-2023 XProbe Inc.
# derived from copyright 1999-2021 Alibaba Group Holding Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import logging
import os
from dataclasses import dataclass
from io import UnsupportedOperation
from typing import Any, Dict, List, Optional, Union

import xoscar as mo
from xoscar.core import BufferRef

from ...lib.aio import alru_cache
from ...storage import StorageLevel
from ...typing import BandType
from ...utils import dataslots
from .core import DataManagerActor, WrappedStorageFileObject
from .handler import StorageHandlerActor

DEFAULT_TRANSFER_BLOCK_SIZE = 4 * 1024**2


logger = logging.getLogger(__name__)


class SenderManagerActor(mo.StatelessActor):
    def __init__(
        self,
        band_name: str = "numa-0",
        transfer_block_size: int = None,
        data_manager_ref: mo.ActorRefType[DataManagerActor] = None,
        storage_handler_ref: mo.ActorRefType[StorageHandlerActor] = None,
    ):
        super().__init__()
        self._band_name = band_name
        self._data_manager_ref = data_manager_ref
        self._storage_handler = storage_handler_ref
        self._transfer_block_size = transfer_block_size or DEFAULT_TRANSFER_BLOCK_SIZE

    @classmethod
    def gen_uid(cls, band_name: str):
        return f"sender_manager_{band_name}"

    async def __post_create__(self):
        if self._storage_handler is None:  # for test
            self._storage_handler = await mo.actor_ref(
                self.address, StorageHandlerActor.gen_uid("numa-0")
            )

    @staticmethod
    @alru_cache
    async def get_receiver_ref(address: str, band_name: str):
        return await mo.actor_ref(
            address=address, uid=ReceiverManagerActor.gen_uid(band_name)
        )

    async def _copy_to_receiver(
        self,
        receiver_ref: mo.ActorRefType["ReceiverManagerActor"],
        local_buffers: List,
        remote_buffers: List[BufferRef],
        session_id: str,
        data_keys: List[str],
        block_size: int,
    ):
        await mo.copy_to(local_buffers, remote_buffers, block_size=block_size)

    @staticmethod
    def _get_buffer_size(buf: Any):
        if hasattr(buf, "size"):
            return buf.size
        return len(buf)

    async def _send_data(
        self,
        receiver_ref: mo.ActorRefType["ReceiverManagerActor"],
        session_id: str,
        data_keys: List[str],
        block_size: int,
    ):
        open_reader_tasks = []
        for data_key in data_keys:
            open_reader_tasks.append(
                self._storage_handler.open_reader.delay(session_id, data_key)
            )
        readers = await self._storage_handler.open_reader.batch(*open_reader_tasks)

        local_buffers = []
        data_sizes = []
        headers = []
        for reader in readers:
            try:
                reader_buffer = reader.buffer
            except UnsupportedOperation:  # vineyard  # pragma: no cover
                logger.warning(
                    "Transfer via buffer not work for vineyard. Use file object instead."
                )
                reader_buffer = None

            if reader_buffer is not None:
                if isinstance(reader_buffer, list):  # cuda case
                    data_sizes.append(
                        [self._get_buffer_size(rb) for rb in reader_buffer]
                    )
                    local_buffers.extend(reader_buffer)
                    headers.append(reader.header)
                else:
                    data_sizes.append(
                        getattr(reader_buffer, "size", len(reader_buffer))
                    )
                    local_buffers.append(reader_buffer)
                    headers.append(None)
            else:  # file system
                data_sizes.append(None)
                local_buffers.append(reader)
                headers.append(None)

        remote_buffers = await receiver_ref.get_buffers(
            session_id, data_keys, data_sizes, headers
        )

        write_task = asyncio.create_task(
            self._copy_to_receiver(
                receiver_ref,
                local_buffers,
                remote_buffers,
                session_id,
                data_keys,
                block_size,
            )
        )

        try:
            await asyncio.shield(write_task)
        except asyncio.CancelledError:
            raise

    @mo.extensible
    async def send_batch_data(
        self,
        session_id: str,
        data_keys: List[str],
        to_send_keys: List,
        to_wait_keys: List,
        remote_band: BandType,
        block_size: int = None,
    ):
        logger.debug(
            "Begin to send data (%s, %s) to %s", session_id, data_keys, remote_band
        )

        receiver_ref: mo.ActorRefType[
            ReceiverManagerActor
        ] = await self.get_receiver_ref(remote_band[0], remote_band[1])

        if to_send_keys:
            logger.debug("Start sending %s to %s", to_send_keys, receiver_ref.address)
            block_size = block_size or self._transfer_block_size
            await self._send_data(receiver_ref, session_id, to_send_keys, block_size)
        if to_wait_keys:
            await receiver_ref.wait_transfer_done(session_id, to_wait_keys)
        logger.debug(
            "Finish sending data (%s, %s) to %s",
            session_id,
            data_keys,
            remote_band,
        )


@dataslots
@dataclass
class WritingInfo:
    writer: WrappedStorageFileObject
    size: int
    level: StorageLevel
    event: asyncio.Event
    ref_counts: int


class ReceiverManagerActor(mo.StatelessActor):
    def __init__(
        self,
        quota_refs: Dict,
        storage_handler_ref: mo.ActorRefType[StorageHandlerActor] = None,
    ):
        self._quota_refs = quota_refs
        self._storage_handler = storage_handler_ref
        self._writing_infos: Dict[tuple, WritingInfo] = dict()
        self._lock = asyncio.Lock()

    async def get_buffers(
        self,
        session_id: str,
        data_keys: List,
        data_sizes: List[Union[int, List[int]]],
        headers: List,
    ) -> List:
        res = []
        for data_key, data_size, header in zip(data_keys, data_sizes, headers):
            writer = self._writing_infos[(session_id, data_key)].writer
            if isinstance(data_size, list):  # cuda case
                writer.set_buffers_by_sizes(data_size)
            if header:
                writer.set_file_header(header)

            try:
                writer_buf = writer.buffer
            except UnsupportedOperation:  # pragma: no cover
                writer_buf = None
            if writer_buf is not None:
                if isinstance(writer_buf, list):  # cuda case
                    res.extend([mo.buffer_ref(self.address, buf) for buf in writer_buf])
                else:
                    res.append(mo.buffer_ref(self.address, writer_buf))
            else:  # file system
                res.append(mo.file_object_ref(self.address, writer))
        return res

    async def handle_transmission_done(self, session_id: str, data_keys: List):
        close_tasks = [
            self._writing_infos[(session_id, data_key)].writer.close()
            for data_key in data_keys
        ]
        await asyncio.gather(*close_tasks)
        async with self._lock:
            for data_key in data_keys:
                event = self._writing_infos[(session_id, data_key)].event
                event.set()
                self._decref_writing_key(session_id, data_key)

    async def handle_transmission_cancellation(self, session_id: str, data_keys: List):
        data_keys_to_be_deleted = []
        async with self._lock:
            for data_key in data_keys:
                if (session_id, data_key) in self._writing_infos:
                    if self._writing_infos[(session_id, data_key)].ref_counts == 1:
                        info = self._writing_infos[(session_id, data_key)]
                        await self._quota_refs[info.level].release_quota(info.size)
                        data_keys_to_be_deleted.append(data_key)
                        await info.writer.clean_up()
                        info.event.set()
                        self._decref_writing_key(session_id, data_key)
        return data_keys_to_be_deleted

    @classmethod
    def gen_uid(cls, band_name: str):
        return f"receiver_manager_{band_name}"

    def _decref_writing_key(self, session_id: str, data_key: str):
        self._writing_infos[(session_id, data_key)].ref_counts -= 1
        if self._writing_infos[(session_id, data_key)].ref_counts == 0:
            del self._writing_infos[(session_id, data_key)]

    async def wait_transfer_done(self, session_id, data_keys):
        await asyncio.gather(
            *[self._writing_infos[(session_id, key)].event.wait() for key in data_keys]
        )
        async with self._lock:
            for data_key in data_keys:
                self._decref_writing_key(session_id, data_key)

    async def create_writers(
        self,
        session_id: str,
        data_keys: List[str],
        data_sizes: List[int],
        level: StorageLevel,
        sub_infos: List,
        band_name: str,
    ):
        tasks = dict()
        key_to_sub_infos = dict()
        data_key_to_size = dict()
        being_processed = []
        for data_key, data_size, sub_info in zip(data_keys, data_sizes, sub_infos):
            data_key_to_size[data_key] = data_size
            if (session_id, data_key) not in self._writing_infos:
                being_processed.append(False)
                tasks[data_key] = self._storage_handler.open_writer.delay(
                    session_id,
                    data_key,
                    data_size,
                    level,
                    request_quota=False,
                    band_name=band_name,
                )
                key_to_sub_infos[data_key] = sub_info
            else:
                being_processed.append(True)
                self._writing_infos[(session_id, data_key)].ref_counts += 1
        if tasks:
            writers = await self._storage_handler.open_writer.batch(
                *tuple(tasks.values())
            )
            for data_key, writer in zip(tasks, writers):
                self._writing_infos[(session_id, data_key)] = WritingInfo(
                    writer, data_key_to_size[data_key], level, asyncio.Event(), 1
                )
                if key_to_sub_infos[data_key] is not None:
                    writer._sub_key_infos = key_to_sub_infos[data_key]
        return being_processed

    async def open_writers(
        self,
        session_id: str,
        data_keys: List[str],
        data_sizes: List[int],
        level: StorageLevel,
        sub_infos: List,
        band_name: str,
    ):
        async with self._lock:
            await self._storage_handler.request_quota_with_spill(level, sum(data_sizes))
            future = asyncio.create_task(
                self.create_writers(
                    session_id, data_keys, data_sizes, level, sub_infos, band_name
                )
            )
            try:
                return await future
            except asyncio.CancelledError:
                await self._quota_refs[level].release_quota(sum(data_sizes))
                future.cancel()
                raise

    async def add_in_process_writers(
        self,
        session_id: str,
        data_keys: List[Union[str, tuple]],
        data_sizes: List[int],
        sub_infos: List,
        writers: List[Optional[WrappedStorageFileObject]],
        level: StorageLevel,
    ) -> List[bool]:
        """
        This method is invoked only when the caller process matches the receiver's process.
        To prevent deadlocks, the `writers` are opened directly within the caller's storage
        handler before being passed to this function.
        """
        is_transferring: List[bool] = []
        async with self._lock:
            for data_key, data_size, sub_info, writer in zip(
                data_keys, data_sizes, sub_infos, writers
            ):
                if (session_id, data_key) not in self._writing_infos:
                    is_transferring.append(False)

                    self._writing_infos[(session_id, data_key)] = WritingInfo(
                        writer, data_size, level, asyncio.Event(), 1
                    )
                    if sub_info is not None:
                        writer._sub_key_infos = sub_info
                else:
                    is_transferring.append(True)
        return is_transferring

    def get_pid(self):
        return os.getpid()
