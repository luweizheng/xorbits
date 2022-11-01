# -*- coding: utf-8 -*-
# Copyright 2022 XProbe Inc.
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

from ...core.adapter import MarsDataFrameLoc, register_converter, wrap_mars_callable


@register_converter(from_cls=MarsDataFrameLoc)
class DataFrameLoc:
    def __init__(self, mars_obj: "MarsDataFrameLoc"):
        self._mars_obj = mars_obj

    def __getitem__(self, item):
        return wrap_mars_callable(self._mars_obj.__getitem__)(item)
