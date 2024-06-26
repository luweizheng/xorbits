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

import numpy as np
import pandas as pd
import pytest

from .... import dataframe as md
from ....tests.core import require_hadoop
from ...utils import PD_VERSION_GREATER_THAN_2_10

TEST_DIR = "/tmp/test"


@require_hadoop
@pytest.fixture(scope="module")
def setup_hdfs():
    import pyarrow

    hdfs = pyarrow.fs.HadoopFileSystem(host="localhost", port=8020)
    file = hdfs.get_file_info(TEST_DIR)
    if file.type == pyarrow.fs.FileType.Directory:
        hdfs.delete_dir(TEST_DIR)
    if file.type == pyarrow.fs.FileType.File:
        hdfs.delete_file(TEST_DIR)
    try:
        yield hdfs
    finally:
        file = hdfs.get_file_info(TEST_DIR)
        if file.type == pyarrow.fs.FileType.Directory:
            hdfs.delete_dir(TEST_DIR)
        if file.type == pyarrow.fs.FileType.File:
            hdfs.delete_file(TEST_DIR)


@require_hadoop
def test_to_parquet_execution(setup, setup_hdfs):
    hdfs = setup_hdfs

    test_df = pd.DataFrame(
        {
            "a": np.arange(10).astype(np.int64, copy=False),
            "b": [f"s{i}" for i in range(10)],
            "c": np.random.rand(10),
        }
    )
    df = md.DataFrame(test_df, chunk_size=5)

    dir_name = f"hdfs://localhost:8020{TEST_DIR}/test_to_parquet/"
    hdfs.create_dir(dir_name)
    df.to_parquet(dir_name).execute()

    if PD_VERSION_GREATER_THAN_2_10:
        test_df = test_df.convert_dtypes(dtype_backend="pyarrow")

    result = md.read_parquet(dir_name).to_pandas()
    pd.testing.assert_frame_equal(result.reset_index(drop=True), test_df)

    # test wildcard
    dir_name = f"hdfs://localhost:8020{TEST_DIR}/test_to_parquet2/*.parquet"
    hdfs.create_dir(dir_name.rsplit("/", 1)[0])
    df.to_parquet(dir_name).execute()

    result = md.read_parquet(dir_name.rsplit("/", 1)[0]).to_pandas()
    pd.testing.assert_frame_equal(result.reset_index(drop=True), test_df)
