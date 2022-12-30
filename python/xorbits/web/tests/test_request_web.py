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

import urllib.request


def test_web_ui(setup_with_web):
    sess = setup_with_web
    endpoint = sess.get_web_endpoint()
    req = urllib.request.Request(endpoint)
    response = urllib.request.urlopen(req)
    assert response.code == 200
    assert b"Xorbits" in response.read()